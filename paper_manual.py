"""
手動模擬倉 (Manual Paper Trading) — 練紀律用
=============================================
你自己下單的紙上模擬倉:抓幣安即時真價、模擬合約(含爆倉)、串進場護欄。
跟 PO3/SMC 那種「自動」交易員不同——這個是**你手動開倉**,目的是練 R1~R8
的紀律(尤其 R5 不移停損、R6 不加碼、R8 不報復),機器只負責忠實記帳+結算。

流程:
  open   → 輸入 symbol/方向/槓桿/停損/停利 → 抓即時價當進場 → 跑護欄 → 確認開倉
  status → 抓即時價,顯示每個持倉的浮盈/距停損停利/爆倉;自動結算已觸發的
  close  → 手動平倉(記得:平完去紀律日誌記一筆!)
  history→ 已平倉紀錄 + 勝率/期望

帳本 paper_manual_state.json / paper_manual_closed.csv(原子寫入,gitignore)。
配 risk_sizer.py(護欄) + discipline_journal.py(紀律日誌)。非投資建議。
"""
import argparse
import json
import os
import time
from datetime import datetime

import ccxt
import pandas as pd

import risk_sizer as rs

STATE_FILE = "paper_manual_state.json"
CLOSED_CSV = "paper_manual_closed.csv"
START_EQUITY = 600.0
FEE = 0.0005          # 單邊 taker ~0.05%


# ----------------------------------------------------------------- 帳本
def _default():
    return {"equity": START_EQUITY, "open": [], "next_id": 1,
            "closed_count": 0, "realized_pnl": 0.0, "fees_paid": 0.0}


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            print("  [WARN] 帳本毀損 → 用預設重啟")
    return _default()


def save_state(s):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


def append_closed(row):
    tmp = CLOSED_CSV + ".tmp"
    df = pd.DataFrame([row])
    if os.path.exists(CLOSED_CSV):
        df = pd.concat([pd.read_csv(CLOSED_CSV), df], ignore_index=True)
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, CLOSED_CSV)


# ----------------------------------------------------------------- 行情
def make_exchange():
    return ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})


def live_price(ex, symbol):
    for a in range(3):
        try:
            return float(ex.fetch_ticker(symbol)["last"])
        except Exception:
            if a == 2:
                raise
            time.sleep(0.8)


def _norm(sym):
    sym = sym.upper().strip()
    if "/" not in sym:
        sym = sym.replace("USDT", "") + "/USDT"
    return sym


# ----------------------------------------------------------------- 結算
def _exit_pnl(pos, exit_price):
    """淨損益 = 價差 − 平倉費 − 開倉費(兩邊都算進單筆,R 才誠實)。"""
    d = 1 if pos["side"] == "long" else -1
    gross = (exit_price - pos["entry"]) * pos["qty"] * d
    close_fee = pos["notional"] * (exit_price / pos["entry"]) * FEE
    open_fee = pos.get("open_fee", 0.0)
    net = gross - close_fee - open_fee
    return net, close_fee + open_fee


def settle(state, ex, verbose=True):
    """抓即時價,對每個持倉判斷有沒有打到 SL/TP/爆倉,有就平掉。回傳被平掉的清單。"""
    still, closed = [], []
    for p in state["open"]:
        try:
            px = live_price(ex, p["symbol"])
        except Exception:
            still.append(p); continue
        long = p["side"] == "long"
        reason, exit_px = None, None
        if long:
            if px <= p["liq"]:   reason, exit_px = "爆倉", p["liq"]
            elif px <= p["sl"]:  reason, exit_px = "停損", p["sl"]
            elif px >= p["tp"]:  reason, exit_px = "停利", p["tp"]
        else:
            if px >= p["liq"]:   reason, exit_px = "爆倉", p["liq"]
            elif px >= p["sl"]:  reason, exit_px = "停損", p["sl"]
            elif px <= p["tp"]:  reason, exit_px = "停利", p["tp"]
        if reason:
            net, cfee = _exit_pnl(p, exit_px)
            state["equity"] += net
            state["realized_pnl"] += net
            state["fees_paid"] += cfee
            state["closed_count"] += 1
            r_mult = net / p["risk_amt"] if p.get("risk_amt") else 0
            row = {"id": p["id"], "symbol": p["symbol"], "side": p["side"],
                   "entry": p["entry"], "exit": round(exit_px, 6), "reason": reason,
                   "qty": p["qty"], "net_pnl": round(net, 2), "R": round(r_mult, 2),
                   "opened_at": p["opened_at"],
                   "closed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            append_closed(row)
            closed.append(row)
            if verbose:
                print(f"  ⚡ #{p['id']} {p['symbol']} {reason}@{exit_px:g} → {net:+.2f} ({r_mult:+.2f}R)")
        else:
            still.append(p)
    state["open"] = still
    return closed


# ----------------------------------------------------------------- 開倉
def add_position(state, symbol, side, entry, leverage, sl, tp, r):
    """把一筆已算好護欄(r)的部位加進帳本(不印字、不抓價),CLI 與網頁共用。"""
    open_fee = r["notional"] * FEE
    pos = {"id": state["next_id"], "symbol": _norm(symbol), "side": side,
           "entry": round(entry, 6), "qty": round(r["qty"], 8),
           "notional": round(r["notional"], 2), "leverage": leverage,
           "sl": round(sl, 6), "tp": round(tp, 6), "liq": round(r["liq"], 6),
           "risk_amt": round(r["risk_amt"], 2), "open_fee": round(open_fee, 6),
           "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    state["next_id"] += 1
    state["open"].append(pos)
    save_state(state)
    return pos


def open_position(state, ex, symbol, side, leverage, sl, tp, risk_pct=1.0):
    symbol = _norm(symbol)
    entry = live_price(ex, symbol)
    r = rs.compute(state["equity"], risk_pct, side, entry, sl, tp, leverage)
    if r is None:
        print("停損距離為 0，無法開倉。"); return None
    rs.report(state["equity"], risk_pct, side, entry, sl, tp, leverage, r)  # 護欄提示
    pos = add_position(state, symbol, side, entry, leverage, sl, tp, r)
    print(f"  ✅ 已開倉 #{pos['id']} {symbol} {side} @ {entry:g}"
          f"（名目 ${r['notional']:.0f}，開倉費 ${pos['open_fee']:.2f} 於平倉時計入）")
    return pos


def close_manual(state, ex, pid):
    for p in list(state["open"]):
        if p["id"] == pid:
            px = live_price(ex, p["symbol"])
            net, cfee = _exit_pnl(p, px)
            state["equity"] += net; state["realized_pnl"] += net
            state["fees_paid"] += cfee; state["closed_count"] += 1
            r_mult = net / p["risk_amt"] if p.get("risk_amt") else 0
            append_closed({"id": p["id"], "symbol": p["symbol"], "side": p["side"],
                           "entry": p["entry"], "exit": round(px, 6), "reason": "手動平倉",
                           "qty": p["qty"], "net_pnl": round(net, 2), "R": round(r_mult, 2),
                           "opened_at": p["opened_at"],
                           "closed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            state["open"].remove(p); save_state(state)
            print(f"  ✅ 手動平倉 #{pid} @ {px:g} → {net:+.2f} ({r_mult:+.2f}R)")
            print("  📓 記得去紀律日誌記一筆:這筆你有沒有守 R1~R8?")
            return
    print(f"  找不到持倉 #{pid}")


# ----------------------------------------------------------------- 顯示
def show_status(state, ex):
    settle(state, ex)
    save_state(state)
    eq = state["equity"]
    print("=" * 68)
    print(f"  手動模擬倉  權益 ${eq:,.2f}（{eq-START_EQUITY:+,.2f}）  "
          f"已平 {state['closed_count']}  累計淨損益 {state['realized_pnl']:+.2f}  費 ${state['fees_paid']:.2f}")
    print("=" * 68)
    if not state["open"]:
        print("  目前無持倉。用 `python paper_manual.py open` 開一筆。")
        return
    print(f"  {'#':>2} {'市場':<10}{'方向':<6}{'進場':>10}{'現價':>10}"
          f"{'浮盈':>9}{'距停損':>8}{'距停利':>8}")
    print("-" * 68)
    for p in state["open"]:
        px = live_price(ex, p["symbol"])
        d = 1 if p["side"] == "long" else -1
        upnl = (px - p["entry"]) * p["qty"] * d
        to_sl = (px - p["sl"]) / px * 100 * (1 if p["side"] == "long" else -1)
        to_tp = (p["tp"] - px) / px * 100 * (1 if p["side"] == "long" else -1)
        print(f"  {p['id']:>2} {p['symbol']:<10}{p['side']:<6}{p['entry']:>10g}{px:>10g}"
              f"{upnl:>+9.2f}{to_sl:>7.1f}%{to_tp:>7.1f}%")
    print("-" * 68)
    print("  平倉:python paper_manual.py close <#>   平完去紀律日誌記一筆")


def show_history(state):
    if not os.path.exists(CLOSED_CSV):
        print("還沒有已平倉紀錄。"); return
    df = pd.read_csv(CLOSED_CSV)
    n = len(df); wins = int((df["net_pnl"] > 0).sum())
    print("=" * 60)
    print(f"  已平倉 {n} 筆  勝率 {wins/n*100:.0f}%  "
          f"累計淨損益 {df['net_pnl'].sum():+.2f}  平均 {df['R'].mean():+.2f}R")
    print("=" * 60)
    print(df.tail(15).to_string(index=False))


# ----------------------------------------------------------------- CLI
def _num(prompt, default=None):
    while True:
        raw = input(prompt).strip()
        if not raw and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print("    請輸入數字")


def _open_interactive(state, ex):
    print("開一筆手動模擬倉（會先跑護欄）\n")
    symbol = input("市場 [BTC/USDT]: ").strip() or "BTC/USDT"
    side = input("方向 long/short [long]: ").strip().lower() or "long"
    if side not in ("long", "short"):
        side = "long"
    lev = _num("槓桿 [10]: ", 10.0)
    risk = _num("單筆風險% [1]: ", 1.0)
    try:
        cur = live_price(ex, _norm(symbol))
        print(f"  （{_norm(symbol)} 現價 ≈ {cur:g}，進場就用現價）")
    except Exception as e:
        print(f"  抓價失敗:{e}"); return
    sl = _num("停損價: ")
    tp = _num("停利價: ")
    open_position(state, ex, symbol, side, lev, sl, tp, risk)


def main():
    ap = argparse.ArgumentParser(description="手動模擬倉(練紀律)")
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["open", "status", "close", "history"])
    ap.add_argument("id", nargs="?", type=int, help="close 用:持倉編號")
    args = ap.parse_args()
    state = load_state()

    if args.cmd == "history":
        show_history(state); return
    ex = make_exchange()
    if args.cmd == "open":
        try:
            _open_interactive(state, ex)
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
    elif args.cmd == "close":
        if args.id is None:
            print("用法:python paper_manual.py close <#>"); return
        close_manual(state, ex, args.id)
    else:
        show_status(state, ex)


if __name__ == "__main__":
    main()
