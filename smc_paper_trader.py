"""
smc_paper_trader.py — SMC 7步驟自動紙上交易 + ML 數據收集(獨立解耦)

每 INTERVAL 秒對每個市場跑一次 SMC 教練分析；當「7 步驟全部完成」→ 依偏向開虛擬倉
(SL/目標照面板)，追蹤到 SL/TP 結算，並把當下特徵 + 勝負寫進 ml_lab/smc_ml_features.csv。

★ 刻意解耦:自己一本帳(smc_paper_state.json / smc_paper_closed.csv)、自己的 ML 檔，
  完全不碰 engine_core 沙盒、PO3 孵化器、或任何使用者帳號資料。

⚠ 誠實:SMC 已驗證扣費後無 edge。此為「誠實收集數據/作品/教學」用，非賺錢訊號，
  預期長期負期望(目的是看 regression、累積可分析的樣本)。
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone

import ccxt
import pandas as pd
import smc_coach as smc

MARKETS      = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
                "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT"]
MAIN_TF      = "15m"
START_EQUITY = 10_000.0
RISK_PCT     = 0.01
TAKER_FEE    = 0.0004
INTERVAL     = 300
MAX_RUNTIME  = 6 * 3600  # 每跑滿 6 小時優雅退出 → 看門狗重啟,釋放累積記憶體
STATE_FILE   = "smc_paper_state.json"
CLOSED_CSV   = "smc_paper_closed.csv"
ML_CSV       = os.path.join("ml_lab", "smc_ml_features.csv")
FEAT_COLS    = ["bias", "n_struct", "n_fvg", "n_ob", "n_align", "er"]


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"equity": START_EQUITY, "open": [], "closed_count": 0,
            "realized_pnl": 0.0, "fees_paid": 0.0}


def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def make_ex():
    return ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})


def now_iso():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def log_ml_feature(trade_id, symbol, s, side, entry, sl, tp):
    """開倉時記一筆 ML 特徵(label 待結算回填)。"""
    row = {"trade_id": trade_id, "datetime": now_iso(), "symbol": symbol, "side": side,
           "entry": round(entry, 4), "sl": round(sl, 4), "tp": round(tp, 4),
           **{c: s.get(c) for c in FEAT_COLS}, "label": "", "resolved_at": ""}
    cols = ["trade_id", "datetime", "symbol", "side", "entry", "sl", "tp"] + FEAT_COLS + ["label", "resolved_at"]
    df = pd.DataFrame([row], columns=cols)
    df.to_csv(ML_CSV, mode="a", header=not os.path.exists(ML_CSV), index=False, encoding="utf-8-sig")


def backfill_ml_label(trade_id, label):
    """結算時把真實 1/0 回填到 ML 檔。"""
    if not os.path.exists(ML_CSV):
        return
    d = pd.read_csv(ML_CSV)
    d["label"] = d["label"].astype(object)
    d["resolved_at"] = d["resolved_at"].astype(object)
    m = d["trade_id"] == trade_id
    if m.any():
        d.loc[m, "label"] = label
        d.loc[m, "resolved_at"] = now_iso()
        d.to_csv(ML_CSV, index=False, encoding="utf-8-sig")


def try_open(state, ex, symbol):
    if any(p["symbol"] == symbol for p in state["open"]):
        return
    _, panel = smc.build_coach(ex, symbol=symbol, main_tf=MAIN_TF, draw=False)
    s = panel["summary"]
    if not s["all_pass"]:
        return                                       # 7 步驟未全過 → 不進場
    side = "SHORT" if s["bias"] == "空" else "LONG"
    entry, sl, tp = s["price"], s["sl"], s["tp"]
    risk = abs(entry - sl)
    if risk <= 0:
        return
    risk_usd = state["equity"] * RISK_PCT
    qty = risk_usd / risk
    notional = qty * entry
    open_fee = notional * TAKER_FEE
    state["fees_paid"] += open_fee
    state["equity"] -= open_fee
    trade_id = int(time.time() * 1000)
    state["open"].append({
        "trade_id": trade_id, "symbol": symbol, "side": side, "qty": qty,
        "entry": entry, "sl": sl, "tp": tp, "risk_usd": round(risk_usd, 2),
        "open_fee": round(open_fee, 4), "opened_at": now_iso()})
    log_ml_feature(trade_id, symbol, s, side, entry, sl, tp)
    print(f"  ▶ 開倉 {symbol} {side}  進場 {entry:.4f}  SL {sl:.4f}  TP {tp:.4f}  "
          f"(7步全過, ER {s['er']:.2f}, 對齊 {s['n_align']}/4)")


def settle(state, ex):
    still = []
    for p in state["open"]:
        mark = float(ex.fetch_ticker(p["symbol"])["last"])
        long_ = p["side"] == "LONG"
        hit_tp = mark >= p["tp"] if long_ else mark <= p["tp"]
        hit_sl = mark <= p["sl"] if long_ else mark >= p["sl"]
        if not (hit_tp or hit_sl):
            still.append(p); continue
        exit_price = p["tp"] if hit_tp else p["sl"]
        direction = 1 if long_ else -1
        gross = direction * (exit_price - p["entry"]) * p["qty"]
        close_fee = abs(exit_price * p["qty"]) * TAKER_FEE
        net = gross - close_fee
        state["equity"] += net
        state["fees_paid"] += close_fee
        state["realized_pnl"] += net
        state["closed_count"] += 1
        label = 1 if hit_tp else 0
        backfill_ml_label(p["trade_id"], label)
        pd.DataFrame([{**{k: p[k] for k in ("symbol", "side", "entry", "sl", "tp", "qty", "opened_at")},
                       "closed_at": now_iso(), "exit": exit_price,
                       "outcome": "WIN" if hit_tp else "LOSS", "net_pnl": round(net, 4),
                       "equity_after": round(state["equity"], 2)}]).to_csv(
            CLOSED_CSV, mode="a", header=not os.path.exists(CLOSED_CSV), index=False, encoding="utf-8-sig")
        print(f"  ■ 平倉 {p['symbol']} {'WIN' if hit_tp else 'LOSS'} @ {exit_price:.4f}  "
              f"淨 {net:+.2f}  權益 ${state['equity']:,.2f}")
    state["open"] = still


def tick(state, ex):
    for sym in MARKETS:
        try:
            try_open(state, ex, sym)
        except Exception as ex_:
            print(f"  [WARN] open {sym} → {ex_}")
    try:
        settle(state, ex)
    except Exception as ex_:
        print(f"  [WARN] settle → {ex_}")
    save_state(state)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--once", action="store_true"); args = ap.parse_args()
    ex = make_ex(); state = load_state()
    print(f"[SMC 紙上交易員] 權益 ${state['equity']:,.2f}　市場 {len(MARKETS)} 個　進場=7步全過")
    print("⚠ SMC 無 edge,此為誠實收集數據用,預期長期賠\n")
    if args.once:
        print(f"[{now_iso()}] tick(once)"); tick(state, ex)
    else:
        started = time.time()
        while True:
            print(f"[{now_iso()}] tick"); tick(state, ex)
            print(f"  狀態:權益 ${state['equity']:,.2f} 持倉 {len(state['open'])} 已平 {state['closed_count']}")
            if time.time() - started > MAX_RUNTIME:   # 防記憶體漏:定時優雅退出,看門狗重啟
                print(f"[{now_iso()}] 已跑滿 {MAX_RUNTIME/3600:.0f}h,優雅退出讓看門狗重啟(釋放記憶體)")
                return
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
