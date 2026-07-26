"""
risk_sizer.py — 進場護欄 + 部位/槓桿保命計算機
================================================
AI 在紀律上唯一該扮演的角色:**護欄**(強制你守 R2/R3/R4),不是代替你交易。
進場前跑一次,輸入進場/停損/停利/想開幾倍,它即時:
  · 算該開多大部位(讓「打到停損」剛好只虧設定風險%)      → 顧 R2
  · 算爆倉價,檢查『停損會不會先於爆倉觸發』               → 顧 R3(合約第一殺手)
  · 算賺賠比,檢查 ≥ 1.5                                    → 顧 R4
  · 最後給 GO / 不要進 的判定 + 這個停損下的最高安全槓桿

★ 把『600U 之死(10x+暴力急殺,爆倉跑在停損前)』變成進場前就擋下的規則。
  風控練習用,非投資建議。配 DISCIPLINE_SYSTEM.md 使用。

用法:
  python risk_sizer.py            # 互動護欄(進場前跑這個)
  python risk_sizer.py --demo     # 用內建範例參數跑一次
"""
import argparse

MAINT      = 0.005     # 維持保證金率(粗估)
SHOCK_MULT = 2.0       # 「暴力插針」假設 = 停損距離的幾倍
RISK_CAP   = 2.0       # 單筆風險上限%(超過就警告 R2)
RR_MIN     = 1.5       # 賺賠比下限(R4)


def compute(account, risk_pct, side, entry, stop, tp, leverage):
    stop_dist = abs(entry - stop) / entry
    if stop_dist <= 0:
        return None
    risk_amt = account * risk_pct / 100
    notional = risk_amt / stop_dist
    qty = notional / entry
    margin = notional / leverage
    if side == "long":
        liq = entry * (1 - 1 / leverage + MAINT)
        liq_dist = (entry - liq) / entry
        stop_first = stop > liq
    else:
        liq = entry * (1 + 1 / leverage - MAINT)
        liq_dist = (liq - entry) / entry
        stop_first = stop < liq
    max_safe_lev = 1 / (stop_dist * SHOCK_MULT + MAINT)
    rr = (abs(tp - entry) / abs(entry - stop)) if tp else None
    # 停損/停利有沒有放對邊:做多→停損在下、停利在上;做空→停損在上、停利在下
    if side == "long":
        sl_ok = stop < entry
        tp_ok = (tp > entry) if tp else True
    else:
        sl_ok = stop > entry
        tp_ok = (tp < entry) if tp else True
    return dict(stop_dist=stop_dist, risk_amt=risk_amt, notional=notional, qty=qty,
                margin=margin, liq=liq, liq_dist=liq_dist, stop_first=stop_first,
                max_safe_lev=max_safe_lev, rr=rr, sl_ok=sl_ok, tp_ok=tp_ok)


def _price(x):
    """價格自適應格式:大幣兩位小數帶千分位,小幣多給幾位,不用科學記號。"""
    if abs(x) >= 100:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:,.4f}"
    return f"{x:.6f}"


def report(account, risk_pct, side, entry, stop, tp, leverage, r):
    print("=" * 62)
    print(f"  進場護欄檢查（{side.upper()}）")
    print("=" * 62)
    print(f"  帳戶 / 單筆風險 : ${account:,.0f} / {risk_pct:.1f}% = ${r['risk_amt']:,.2f}")
    print(f"  進場 / 停損     : {_price(entry)} → {_price(stop)}  (停損距離 {r['stop_dist']*100:.1f}%)")
    if tp:
        print(f"  停利            : {_price(tp)}")
    print(f"  槓桿            : {leverage:g}x")
    print("-" * 62)
    print(f"  ➜ 建議部位      : {r['qty']:.4f} 顆（名目 ${r['notional']:,.0f}，保證金 ${r['margin']:,.0f}）")
    print(f"    打到停損只虧   : ${r['risk_amt']:,.2f}")
    print(f"  ➜ 爆倉價        : {_price(r['liq'])}（距進場 {r['liq_dist']*100:.1f}%）")
    print("=" * 62)

    # ── 逐條護欄 ────────────────────────────────────────────
    checks = []
    # R1 停損/停利放對邊(做空停損要在進場之上、停利之下)
    side_ok = r["sl_ok"] and r["tp_ok"]
    if not side_ok:
        want = "停損在進場之上、停利在進場之下" if side == "short" else "停損在進場之下、停利在進場之上"
        checks.append(("R1", False, f"停損/停利放錯邊！{side.upper()} 應該 {want}"))
    # R2 風險 ≤ 上限
    ok2 = risk_pct <= RISK_CAP
    checks.append(("R2", ok2, f"單筆風險 {risk_pct:.1f}% "
                   + ("≤ 上限 2%，可控" if ok2 else f"> 上限 {RISK_CAP:.0f}%，太大！降風險%")))
    # R3 停損先於爆倉(最致命)
    ok3 = r["stop_first"]
    checks.append(("R3", ok3, ("停損先於爆倉，插針有緩衝" if ok3
                   else f"爆倉({r['liq_dist']*100:.1f}%)比停損({r['stop_dist']*100:.1f}%)近 → "
                        "急殺會跳過停損直接爆倉(600U 死法)！降槓桿")))
    # R4 賺賠比
    if r["rr"] is not None:
        ok4 = r["rr"] >= RR_MIN
        checks.append(("R4", ok4, f"賺賠比 {r['rr']:.2f} "
                       + (f"≥ {RR_MIN}，OK" if ok4 else f"< {RR_MIN}，賺太少/賠太多，別進")))
    else:
        checks.append(("R4", None, "沒填停利，無法檢查賺賠比（建議補上）"))

    print("  護欄逐條：")
    for rid, ok, msg in checks:
        icon = "✅" if ok else ("🔴" if ok is False else "⚪")
        print(f"    {icon} [{rid}] {msg}")
    print(f"  這個停損下，最高安全槓桿 ≈ {r['max_safe_lev']:.1f}x（撐得過 {SHOCK_MULT:.0f}× 插針）")
    print("-" * 62)

    fails = [rid for rid, ok, _ in checks if ok is False]
    if "R1" in fails:
        print("  🚫 判定：不要進！停損/停利放錯邊，這筆方向邏輯是壞的。")
    elif "R3" in fails:
        print("  🚫 判定：不要進！停損擋不住爆倉，這筆是在賭一根不插針。")
    elif fails:
        print(f"  ⚠️ 判定：可進但有破口（{', '.join(fails)}）— 先修好再進，別將就。")
    else:
        print("  ✅ 判定：GO。三道護欄都過，這筆風險是可控的。剩下交給紀律：進場後別亂動。")
    print("=" * 62)


def _num(prompt, default=None):
    while True:
        raw = input(prompt).strip()
        if not raw and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print("    請輸入數字")


def guard():
    print("進場護欄 — 進場前填一下（[]內是預設，直接 Enter 用預設）\n")
    account = _num("帳戶 USDT [600]: ", 600.0)
    risk_pct = _num("單筆風險 % [1]: ", 1.0)
    side = input("方向 long/short [long]: ").strip().lower() or "long"
    if side not in ("long", "short"):
        side = "long"
    entry = _num("進場價: ")
    stop = _num("停損價: ")
    tp_raw = input("停利價（可留空，但強烈建議填以檢查賺賠比）: ").strip()
    try:
        tp = float(tp_raw) if tp_raw else None
    except ValueError:
        tp = None
    leverage = _num("槓桿倍數 [10]: ", 10.0)
    print()
    r = compute(account, risk_pct, side, entry, stop, tp, leverage)
    if r is None:
        print("停損距離為 0，無法計算。")
        return
    report(account, risk_pct, side, entry, stop, tp, leverage, r)


def demo():
    account, risk_pct, side, entry, stop, tp, leverage = 600.0, 1.0, "long", 100.0, 97.0, 105.0, 10.0
    r = compute(account, risk_pct, side, entry, stop, tp, leverage)
    report(account, risk_pct, side, entry, stop, tp, leverage, r)


def main():
    ap = argparse.ArgumentParser(description="進場護欄 + 部位/槓桿計算")
    ap.add_argument("--demo", action="store_true", help="用內建範例參數跑一次")
    args = ap.parse_args()
    if args.demo:
        demo()
    else:
        try:
            guard()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")


if __name__ == "__main__":
    main()
