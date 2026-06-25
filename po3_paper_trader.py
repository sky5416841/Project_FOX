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
import json
import time
import argparse
from datetime import datetime, timezone

import ccxt
import pandas as pd

import po3_engine as eng
import data_pipeline as dp

# === 帳戶與風控設定 ===
MARKETS      = [("BTC/USDT", "5m"), ("ETH/USDT", "5m")]
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
def try_open(state, ex, symbol, tf) -> None:
    """該市場若無持倉,就看最新棒有沒有 PO3 訊號 → 開虛擬倉。"""
    if any(p["symbol"] == symbol for p in state["open"]):
        return                                   # 同市場一次只持一倉
    df = fetch_live(ex, symbol, tf)
    sig = eng.get_live_signal(df)
    if sig is None:
        return

    # 第二步:Delta 訂單流過濾器 —— 掃針有了,還要確認「力道背離」才算真操縱
    try:
        trades = dp.fetch_trades(symbol, limit=1000, exchange=ex)
        flt = dp.delta_breakout_filter(sig["signal"], trades)
        print(f"  · {symbol} 偵測到 {sig['signal']} 掃針 → Delta 過濾:{flt['reason']} "
              f"(失衡 {flt['ratio']:+.0%})")
        if not flt["passed"]:
            return                               # 力道不背離 → 放棄,不開倉
    except Exception as ex_:
        print(f"  [WARN] Delta 過濾失敗,保守跳過 {symbol} → {ex_}")
        return

    # 第三步:OBI 訂單簿失衡牆 —— 進場前確認主力已在目標方向佈好掛單陣地
    try:
        ob = dp.fetch_order_book(symbol, exchange=ex)
        obi = dp.obi_filter(sig["signal"], ob)
        print(f"  · {symbol} OBI 牆檢查:{obi['reason']}")
        if not obi["passed"]:
            return                               # 沒有掛單牆掩護 → 放棄
    except Exception as ex_:
        print(f"  [WARN] OBI 過濾失敗,保守跳過 {symbol} → {ex_}")
        return

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
            try_open(state, ex, symbol, tf)
        except Exception as ex_:
            print(f"  [WARN] open {symbol} → {ex_}")
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
