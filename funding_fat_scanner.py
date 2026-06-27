"""
funding_fat_scanner.py — 肥幣風控掃描器

承接 funding_regime.py：整體市場觀望時，仍可能有「單一肥幣」值得做資金費農場。
但高資金費通常 = 高風險。本工具把高正資金費的幣抓出來，每個標上：

  · 年化資金費%   —— 能收多少（短永續 + 多現貨，收正資金費）
  · 24h 成交額    —— 流動性夠不夠（薄 = 進出滑點大、難對沖）
  · |24h 漲跌%|   —— 波動/軋空風險（你空合約那條腿怕暴拉）
  · 紅黃綠燈      —— 綜合風控判定，給人腦做最後決策

★ 只看「正」資金費（多單擁擠 → 空方收錢）；負費的反向操作對散戶較難(要借現貨)。
⚠ 啟發式門檻、年化假設 8h 一期（與 funding_regime 一致）；非投資建議，給風控練習用。
"""
import ccxt

PERIODS_YEAR = 365 * 24 / 8          # 8h 一期 → 約 1095
MIN_ANNUAL = 30.0                    # 年化資金費 ≥ 此% 才算「肥」
# 風控門檻（USDT 計）
VOL_SAFE, VOL_THIN = 200e6, 20e6     # 24h 成交額：≥安全 / <此為薄
CHG_CALM, CHG_WILD = 6.0, 15.0       # |24h 漲跌%|：<平靜 / >劇烈


def verdict(ann, vol, chg):
    """綜合紅黃綠燈：流動性 + 波動主導風險。"""
    if vol < VOL_THIN or chg > CHG_WILD:
        return "🔴 陷阱", "流動性太薄或波動劇烈 → 易軋空/滑點，肥是風險的價格"
    if vol >= VOL_SAFE and chg < CHG_CALM:
        return "🟢 相對安全", "量大且穩，這種才是冷市裡值得細看的對象"
    return "🟡 注意", "中間地帶，可看但要壓低槓桿、留足保證金"


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    print("抓取全市場資金費與行情…")
    rates = ex.fetch_funding_rates()
    tickers = ex.fetch_tickers()

    rows = []
    for sym, r in rates.items():
        fr = r.get("fundingRate")
        if fr is None:
            continue
        ann = fr * PERIODS_YEAR * 100          # 年化%
        if ann < MIN_ANNUAL:                   # 只要肥的正資金費
            continue
        t = tickers.get(sym, {})
        vol = float(t.get("quoteVolume") or 0)         # 24h 成交額(USDT)
        chg = abs(float(t.get("percentage") or 0))     # |24h 漲跌%|
        tag, why = verdict(ann, vol, chg)
        rows.append((ann, sym, vol, chg, tag, why))

    rows.sort(reverse=True)                     # 年化由高到低
    print("=" * 92)
    print(f"  肥幣風控掃描（年化資金費 ≥ {MIN_ANNUAL:.0f}%，僅正費）　共 {len(rows)} 個")
    print("=" * 92)
    print(f"  {'幣種':<22}{'年化%':>8}{'24h成交額':>14}{'|24h漲跌|':>10}   風控判定")
    print("-" * 92)
    for ann, sym, vol, chg, tag, why in rows:
        base = sym.split(":")[0]
        print(f"  {base:<22}{ann:>7.0f}%{vol/1e6:>12.0f}M{chg:>9.1f}%   {tag}")

    g = sum(1 for r in rows if r[4].startswith("🟢"))
    y = sum(1 for r in rows if r[4].startswith("🟡"))
    red = sum(1 for r in rows if r[4].startswith("🔴"))
    print("-" * 92)
    print(f"  綜合：🟢相對安全 {g}　🟡注意 {y}　🔴陷阱 {red}")
    print("  ⚠ 提醒：年化高≠賺得到。要實際收，得處理對沖、爆倉保證金、現貨借貸、結算細節，")
    print("     每一項都可能吃掉甚至超過這裡的年化。低槓桿讓對沖活得久 > 追高年化。")


if __name__ == "__main__":
    main()
