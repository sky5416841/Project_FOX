"""
risk_sizer.py — 部位大小 + 槓桿保命計算機

輸入帳戶、單筆風險%、進場價、停損價、槓桿，輸出：
  · 該開多大部位（讓「打到停損」剛好只虧設定的風險%）
  · 爆倉價，以及最致命的檢查：『停損會先觸發，還是爆倉先到？』
  · 在這個停損下，最高能用幾倍槓桿才安全
  · 虧損不對稱提醒

★ 把『600U 之死(10x+暴力急殺，爆倉跑在停損前)』變成可量化的規則。
  風控練習用，非投資建議。改最上面參數試不同情境。
"""

ACCOUNT    = 600.0      # 帳戶 (USDT)
RISK_PCT   = 1.0        # 每筆願意虧帳戶的 %（1~2 是常見紀律）
SIDE       = "long"     # long / short
ENTRY      = 100.0      # 進場價
STOP       = 97.0       # 停損價
LEVERAGE   = 10.0       # 槓桿
MAINT      = 0.005      # 維持保證金率(粗估)
SHOCK_MULT = 2.0        # 「暴力插針」假設 = 停損距離的幾倍


def main():
    risk_amt = ACCOUNT * RISK_PCT / 100
    stop_dist = abs(ENTRY - STOP) / ENTRY               # 停損距離(比例)
    if stop_dist <= 0:
        print("停損距離為 0，無法計算"); return

    notional = risk_amt / stop_dist                     # 打到停損剛好虧 risk_amt
    qty = notional / ENTRY
    margin = notional / LEVERAGE

    # 爆倉價（粗估）
    if SIDE == "long":
        liq = ENTRY * (1 - 1 / LEVERAGE + MAINT)
        liq_dist = (ENTRY - liq) / ENTRY
        stop_first = STOP > liq                          # 停損價在爆倉價之上 → 先觸發(好)
    else:
        liq = ENTRY * (1 + 1 / LEVERAGE - MAINT)
        liq_dist = (liq - ENTRY) / ENTRY
        stop_first = STOP < liq

    max_safe_lev = 1 / (stop_dist * SHOCK_MULT + MAINT)  # 撐得過 SHOCK_MULT 倍插針的最高槓桿

    print("=" * 60)
    print(f"  部位 + 槓桿保命計算（{SIDE.upper()}）")
    print("=" * 60)
    print(f"  帳戶 / 單筆風險   : ${ACCOUNT:,.0f}  /  {RISK_PCT:.1f}%  = ${risk_amt:,.2f}")
    print(f"  進場 / 停損       : ${ENTRY:.2f} → ${STOP:.2f}   (停損距離 {stop_dist*100:.1f}%)")
    print(f"  槓桿              : {LEVERAGE:.0f}x")
    print("-" * 60)
    print(f"  建議部位          : {qty:.4f} 顆（名目 ${notional:,.0f}，保證金 ${margin:,.0f}）")
    print(f"  打到停損 → 虧      : ${risk_amt:,.2f}（帳戶 {RISK_PCT:.1f}%，可控）")
    print("-" * 60)
    print(f"  爆倉價            : ${liq:.2f}（距進場 {liq_dist*100:.1f}%）")
    if stop_first:
        print(f"  ✅ 停損({stop_dist*100:.1f}%) 先於 爆倉({liq_dist*100:.1f}%) → 停損有空間工作")
    else:
        print(f"  🔴 危險！爆倉({liq_dist*100:.1f}%) 比 停損({stop_dist*100:.1f}%) 還近")
        print(f"     → 暴力急殺會跳過停損直接爆倉(這就是 600U 的死法)")
    print(f"  這個停損下，最高安全槓桿 ≈ {max_safe_lev:.1f}x"
          f"（撐得過 {SHOCK_MULT:.0f}× 停損距離的插針）")
    print("-" * 60)
    dd = RISK_PCT
    print(f"  虧損不對稱：虧 {dd:.0f}% 要賺 {dd/(1-dd/100):.1f}% 回本；"
          f"虧 50% 要賺 100% → 絕不大賠 > 多賺")
    print("=" * 60)


if __name__ == "__main__":
    main()
