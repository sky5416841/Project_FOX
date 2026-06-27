"""
funding_arb_planner.py — 資金費套利「部署計算機」

輸入資本、幣價、年化資金費、槓桿，輸出一個 delta 中性對沖部位的完整計畫：
  · 兩腿數量(多現貨 + 空永續，等名目)
  · 用掉多少保證金、合約腿的爆倉價與「緩衝距離」
  · 預估年化資金費收入、一次性手續費、扣費後首年淨報酬
  · 紅黃綠燈安全判定（緩衝 vs 該幣典型波動）

★ 機制教學 / 風控練習用，非投資建議。數字理想化(忽略基差/借貸/滑點/結算細節)。
  改最上面的參數即可試不同情境。
"""

# ── 情境參數（改這裡）─────────────────────────────────────────
CAPITAL      = 1000.0     # 總資本 (USDT)
PRICE        = 100.0      # 幣價 (USDT)
ANNUAL_FUND  = 12.0       # 年化資金費 (%)，正=空方收錢
LEVERAGE     = 2.0        # 合約腿槓桿（套利建議 1.5~2，別貪）
DAILY_VOL    = 5.0        # 該幣典型單日波動 (%)，用來評估爆倉緩衝夠不夠

PERP_FEE     = 0.0004     # 永續吃單手續費(開/平各一次)
SPOT_FEE     = 0.0010     # 現貨手續費(買/賣各一次)
MAINT_MARGIN = 0.005      # 維持保證金率(粗估，爆倉價會略保守於 1+1/L)


def main():
    L = LEVERAGE
    notional = CAPITAL / (1 + 1 / L)          # 兩腿等名目 N
    spot_cost = notional                       # 現貨全額付清
    perp_margin = notional / L                 # 合約保證金
    coin_qty = notional / PRICE                # 兩腿各買/空這麼多顆

    # 合約(空)爆倉價：價格上漲到保證金耗盡。粗估 ≈ entry × (1 + 1/L − maint)
    liq_price = PRICE * (1 + 1 / L - MAINT_MARGIN)
    buffer_pct = (liq_price / PRICE - 1) * 100  # 距爆倉還有多少 %

    # 報酬與成本
    annual_income = notional * ANNUAL_FUND / 100
    fees = notional * (PERP_FEE * 2 + SPOT_FEE * 2)   # 開+平、兩條腿
    net_year = annual_income - fees
    roi = net_year / CAPITAL * 100

    # 安全判定：爆倉緩衝要遠大於單日波動(留給插針)
    ratio = buffer_pct / DAILY_VOL if DAILY_VOL else 99
    if ratio >= 4:   tag = "🟢 緩衝充足"
    elif ratio >= 2: tag = "🟡 偏緊，盯著點、準備補保證金"
    else:            tag = "🔴 危險，一根插針就可能爆 → 降槓桿"

    print("=" * 60)
    print("  資金費套利部署計畫（delta 中性：多現貨 + 空永續）")
    print("=" * 60)
    print(f"  資本            : ${CAPITAL:,.0f}")
    print(f"  幣價 / 年化資金費 : ${PRICE:,.2f}  /  {ANNUAL_FUND:.1f}%")
    print(f"  槓桿(合約腿)     : {L:.1f}x")
    print("-" * 60)
    print(f"  每條腿數量       : {coin_qty:.4f} 顆（名目 ${notional:,.0f}）")
    print(f"  ├ 多現貨付清     : ${spot_cost:,.0f}")
    print(f"  └ 空永續保證金   : ${perp_margin:,.0f}")
    print(f"  合約腿爆倉價     : ${liq_price:,.2f}   緩衝 +{buffer_pct:.0f}%（單日波動約 {DAILY_VOL:.0f}%）")
    print(f"  安全判定         : {tag}")
    print("-" * 60)
    print(f"  年化資金費收入   : +${annual_income:,.2f}")
    print(f"  一次性手續費     : -${fees:,.2f}")
    print(f"  → 扣費後首年淨   : ${net_year:,.2f}   （資本報酬率 {roi:.1f}%）")
    print("=" * 60)
    print("  讀法：年化看起來低很正常 —— 它是『市場中性、低風險』換來的。")
    print("  真正的命門是上面那條『緩衝』：寧可少賺，也要讓對沖活過暴漲插針。")


if __name__ == "__main__":
    main()
