"""
PO3 獨立紙上交易迴圈 (Standalone Paper Trader)

把 po3_engine.get_live_signal 接成一個「全自動虛擬交易員」:
  每 INTERVAL 秒 → 抓即時 K 線 → 有 PO3 掃針訊號就在自己的帳本開倉
  → 用標記價自動判定 SL/TP → 結算進歷史績效。

★ 刻意「解耦」設計:
  · 完全不碰 engine_core.py 的常駐 daemon,也不寫它的 state/DB
    → 不汙染目前正在跑的 score>=20 regression 實驗。
  · 自己一本獨立帳本(po3_paper_state.json + po3_paper_closed.csv)。
  · 含手續費模型(誠實扣費是本專案鐵則,見 QUANT_RESEARCH.md)。

⚠ 提醒:這是紙上錢、作品/教學用。PO3/ROP 類訊號扣費後負期望已被
  n=2041 t=-12.27 釘死 → 預期這隻交易員長期會賠,目的是「親眼看 regression」。
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone

import ccxt
import pandas as pd

import numpy as np
import po3_engine as eng
import data_pipeline as dp
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_lab"))
import live_logger as mllog

# === 帳戶與風控設定 ===
MARKETS      = [(s, tf) for s in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT",
                                  "BNB/USDT", "XRP/USDT", "DOGE/USDT", "AVAX/USDT",
                                  "LINK/USDT", "LTC/USDT", "DOT/USDT", "TRX/USDT"]
                for tf in ["5m", "15m"]]   # 12幣×2時框=24市場,擴大孵化吞吐(不動特徵邏輯→不漂移)
START_EQUITY = 10_000.0
RISK_PCT     = 0.01      # 每筆風險 = 權益 × 1%(打到 SL 約虧 1R)
LEVERAGE     = 5         # 僅影響保證金占用顯示;盈虧由 qty×價差決定
TAKER_FEE    = 0.0004    # 合約吃單手續費(開+平各一次)
INTERVAL     = 300       # 輪詢秒數(5 分鐘 = 一根 5m K 棒)
STATE_FILE   = "po3_paper_state.json"
CLOSED_CSV   = "po3_paper_closed.csv"


# ---------------------------------------------------------------- 帳本
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"equity": START_EQUITY, "open": [], "closed_count": 0,
            "realized_pnl": 0.0, "fees_paid": 0.0}


def save_state(s: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def append_closed(row: dict) -> None:
    df = pd.DataFrame([row])
    df.to_csv(CLOSED_CSV, mode="a", header=not os.path.exists(CLOSED_CSV),
              index=False, encoding="utf-8-sig")


# ---------------------------------------------------------------- 行情
def make_exchange():
    return ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})


def fetch_live(ex, symbol, tf, limit=500) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, tf, limit=limit)
    # 丟掉最後一根「未收完」的 K 棒,只用已完成的(避免偷看未完成價)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])
    return df.iloc[:-1].reset_index(drop=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 交易邏輯
def _capture_orderflow(ex, symbol, side):
    """掃針當下即時捕獲 Delta / CVD 斜率 / OBI(只在偵測到掃針時呼叫,故正常輪詢不變慢)。"""
    trades = dp.fetch_trades(symbol, limit=1000, exchange=ex)
    cvd = dp.calculate_delta_and_cvd(trades)
    cvd_series = cvd["cvd"]
    # CVD 斜率:對累積序列做一次線性擬合的斜率(每筆成交的平均淨流入)
    slope = 0.0
    if len(cvd_series) >= 2:
        x = np.arange(len(cvd_series))
        slope = float(np.polyfit(x, cvd_series.values, 1)[0])
    ob = dp.fetch_order_book(symbol, exchange=ex)
    obi = dp.calculate_obi(ob)
    delta_flt = dp.delta_breakout_filter(side, trades)
    obi_flt = dp.obi_filter(side, ob)
    return {"delta": cvd["delta"], "cvd_slope": slope, "obi_ratio": obi["ratio"],
            "delta_flt": delta_flt, "obi_flt": obi_flt}


def process_market(state, ex, symbol, tf) -> None:
    """一個市場一輪:抓K線 → 回填舊標籤 → 偵測掃針 → 即時捕獲訂單流 → 記ML特徵 → (過關才)開倉。"""
    df = fetch_live(ex, symbol, tf)
    # 延遲打標:先把這個市場過往未結算的 ML 樣本用最新 K 線回填 1/0(便宜,只算本地)
    try:
        n = mllog.backfill(symbol, tf, df)
        if n:
            print(f"  · {symbol} 回填 {n} 筆 ML 標籤")
    except Exception as ex_:
        print(f"  [WARN] {symbol} ML 回填失敗 → {ex_}")

    if any(p["symbol"] == symbol for p in state["open"]):
        return                                   # 同市場一次只持一倉(但上面的回填照常)
    sig = eng.get_live_signal(df)
    if sig is None:
        return                                   # 無掃針 → 不抓訂單流,輪詢維持輕量

    # 掃針結構出現 → 即時捕獲訂單流一次(Delta/CVD/OBI),過濾與孵化共用,不重複打 API
    try:
        of = _capture_orderflow(ex, symbol, sig["signal"])
    except Exception as ex_:
        print(f"  [WARN] {symbol} 訂單流捕獲失敗,跳過此次掃針 → {ex_}")
        return
    passed = of["delta_flt"]["passed"] and of["obi_flt"]["passed"]
    print(f"  · {symbol} 偵測到 {sig['signal']} 掃針 | Delta {of['delta_flt']['reason']}"
          f" | OBI {of['obi_flt']['reason']} → {'開倉' if passed else '僅記錄不開倉'}")

    # ★ ML 孵化:無論過不過濾,都把這筆掃針特徵(含訂單流)寫進 live_ml_features.csv
    try:
        if mllog.log_sweep(symbol, tf, df, sig, of, opened=passed):
            print(f"    ↳ 已孵化 1 筆 ML 樣本(label 待回填)")
    except Exception as ex_:
        print(f"  [WARN] {symbol} ML 孵化寫入失敗 → {ex_}")

    if not passed:
        return                                   # 雙過濾未過 → 只記錄、不開倉

    entry, sl, tp = sig["entry_price"], sig["sl"], sig["tp"]
    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0:
        return
    risk_usd = state["equity"] * RISK_PCT
    qty = risk_usd / risk_per_unit               # 部位大小 = 風險金額 / 每單位風險
    notional = qty * entry
    open_fee = notional * TAKER_FEE
    state["fees_paid"] += open_fee
    state["equity"] -= open_fee

    pos = {"symbol": symbol, "tf": tf, "side": sig["signal"], "qty": qty,
           "entry": entry, "sl": sl, "tp": tp, "notional": round(notional, 2),
           "risk_usd": round(risk_usd, 2), "open_fee": round(open_fee, 4),
           "opened_at": now_iso(),
           "box_low": sig["box_low"], "box_high": sig["box_high"]}
    state["open"].append(pos)
    print(f"  ▶ 開倉 {symbol} {sig['signal']}  進場 {entry}  SL {sl}  TP {tp}  "
          f"qty {qty:.4f}  名目 ${notional:,.0f}  手續費 -${open_fee:.2f}")


def settle(state, ex) -> None:
    """對每個持倉抓最新標記價,判定是否打到 SL/TP,結算進歷史。"""
    still_open = []
    for p in state["open"]:
        ticker = ex.fetch_ticker(p["symbol"])
        mark = float(ticker["last"])
        long_, hit_tp, hit_sl = (p["side"] == "LONG"), False, False
        if long_:
            hit_tp, hit_sl = mark >= p["tp"], mark <= p["sl"]
        else:
            hit_tp, hit_sl = mark <= p["tp"], mark >= p["sl"]

        if not (hit_tp or hit_sl):
            still_open.append(p)
            continue

        exit_price = p["tp"] if hit_tp else p["sl"]   # 觸價以目標價結算
        direction = 1 if long_ else -1
        gross = direction * (exit_price - p["entry"]) * p["qty"]
        close_fee = abs(exit_price * p["qty"]) * TAKER_FEE
        net = gross - close_fee
        r_mult = net / p["risk_usd"]

        state["equity"] += net
        state["fees_paid"] += close_fee
        state["realized_pnl"] += net
        state["closed_count"] += 1
        outcome = "WIN" if hit_tp else "LOSS"
        row = {**{k: p[k] for k in ("symbol", "tf", "side", "entry", "sl", "tp",
                                    "qty", "notional", "risk_usd", "opened_at")},
               "closed_at": now_iso(), "exit": exit_price, "outcome": outcome,
               "gross_pnl": round(gross, 4), "fees": round(p["open_fee"] + close_fee, 4),
               "net_pnl": round(net, 4), "R": round(r_mult, 3),
               "equity_after": round(state["equity"], 2)}
        append_closed(row)
        print(f"  ■ 平倉 {p['symbol']} {outcome} @ {exit_price}  "
              f"淨 {net:+.2f}({r_mult:+.2f}R)  權益 ${state['equity']:,.2f}")
    state["open"] = still_open


def tick(state, ex) -> None:
    for symbol, tf in MARKETS:
        try:
            process_market(state, ex, symbol, tf)
        except Exception as ex_:
            print(f"  [WARN] process {symbol} → {ex_}")
    try:
        settle(state, ex)
    except Exception as ex_:
        print(f"  [WARN] settle → {ex_}")
    save_state(state)


def print_status(state) -> None:
    print(f"  狀態:權益 ${state['equity']:,.2f}  持倉 {len(state['open'])}  "
          f"已平 {state['closed_count']}  累計淨損益 {state['realized_pnl']:+.2f}  "
          f"累計手續費 ${state['fees_paid']:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只跑一輪(測試用)")
    args = ap.parse_args()

    ex = make_exchange()
    state = load_state()
    print(f"[PO3 紙上交易員] 起始權益 ${state['equity']:,.2f}  市場 {MARKETS}")
    print("⚠ 紙上錢/作品用;此類訊號扣費後負期望已證實 → 預期長期賠,看 regression 用\n")

    if args.once:
        print(f"[{now_iso()}] tick(once)")
        tick(state, ex); print_status(state); return

    while True:
        print(f"[{now_iso()}] tick")
        tick(state, ex); print_status(state)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
