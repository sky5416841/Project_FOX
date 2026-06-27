"""
regime_detector.py — 市場狀態偵測器（趨勢 vs 震盪）

「大贏小賠／移動停利」需要『趨勢』來餵；震盪盤是它最慘的時候。
這支用 Kaufman 效率比率(Efficiency Ratio) 判斷每個幣現在是趨勢還是震盪：

  ER = |淨移動| / Σ|每根變動|     （0~1）
  · ER 接近 1 → 走得直、是趨勢（適合做趨勢/讓利潤奔跑）
  · ER 接近 0 → 來回鋸、是震盪（趨勢策略會被磨死 → 該空手或改打法）

掃主流幣，給每個幣評級，最後給「現在整體適不適合做趨勢」的總判定。
⚠ 啟發式門檻、非投資建議。ER 是相對參考，不是未來保證。
"""
import ccxt
import numpy as np

MAJORS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
          "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT"]
TF, WINDOW = "4h", 30          # 用近 30 根 4h（約 5 天）判狀態
TREND_ER, CHOP_ER = 0.45, 0.30


def efficiency_ratio(closes):
    net = abs(closes[-1] - closes[0])
    path = np.abs(np.diff(closes)).sum()
    return net / path if path > 0 else 0.0


def scan_regime(ex=None):
    """回傳 (rows, summary)，供 CLI 與網頁共用（不印字）。
    rows: [{symbol, er, chg, state}]; summary: {avg, n_trend, n, verdict}。"""
    ex = ex or ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    rows, ers = [], []
    for s in MAJORS:
        try:
            o = ex.fetch_ohlcv(s, TF, limit=WINDOW + 1)
        except Exception:
            continue
        closes = np.array([x[4] for x in o[-WINDOW:]])
        if len(closes) < WINDOW:
            continue
        er = efficiency_ratio(closes)
        chg = (closes[-1] / closes[0] - 1) * 100
        ers.append(er)
        state = "📈 趨勢" if er >= TREND_ER else ("🌀 震盪" if er <= CHOP_ER else "➖ 中性")
        rows.append({"symbol": s.split("/")[0], "er": round(er, 2),
                     "chg_pct": round(chg, 1), "state": state})
    summary = {}
    if ers:
        avg = float(np.mean(ers))
        n_trend = sum(1 for e in ers if e >= TREND_ER)
        if avg >= TREND_ER:   v = "🟢 整體趨勢盤 → 趨勢/大贏小賠策略的好環境"
        elif avg <= CHOP_ER:  v = "🔴 整體震盪盤 → 趨勢策略會流血，建議空手或改區間打法"
        else:                 v = "🟡 混沌 → 只挑個別趨勢幣，整體別 all in 趨勢"
        summary = {"avg": round(avg, 2), "n_trend": n_trend, "n": len(ers), "verdict": v}
    return rows, summary


def main():
    print(f"市場狀態偵測（{TF}，近 {WINDOW} 根 ≈ {WINDOW*4//24} 天）")
    print("=" * 56)
    print(f"  {'幣種':<12}{'效率比率':>10}{'淨變動':>10}   狀態")
    print("-" * 56)
    rows, summary = scan_regime()
    for r in rows:
        arrow = "↑" if r["chg_pct"] >= 0 else "↓"
        print(f"  {r['symbol']:<12}{r['er']:>10.2f}{r['chg_pct']:>9.1f}%{arrow}   {r['state']}")
    print("-" * 56)
    if summary:
        print(f"  全市場平均 ER {summary['avg']}　趨勢幣 {summary['n_trend']}/{summary['n']}")
        print(f"  總判定：{summary['verdict']}")
    print("=" * 56)
    print("  用法：要用『移動停利/讓利潤奔跑』前，先看這裡是不是趨勢環境。")
    print("  震盪盤硬做趨勢 = 被假突破磨死（exit_style_lab 已示範）。")


if __name__ == "__main__":
    main()
