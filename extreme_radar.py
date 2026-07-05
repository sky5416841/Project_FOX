"""
extreme_radar.py — 抄底 / 逃頂 極端雷達（手動交易的紀律參謀）

複盤鐵證:中間地帶(RSI 25-40 的小跌)大失血；真極端(RSI<15 深超賣)才有機會。
所以這支只標『真正的極端』，幫你避開最會賠的中間、把抄底時機挑得更嚴。

判定(多條件同時，才算真極端，不是隨便一根紅K):
  抄底候選 = RSI 低 + 價格明顯低於均線 + 近期跌深 (+ 資金費極負=空方擁擠更佳)
  逃頂候選 = RSI 高 + 價格明顯高於均線 + 近期漲兇 (+ 資金費極正=多方擁擠更佳)

★ 誠實聲明:這是『紀律輔助』，不是賺錢訊號。極端≠保證反轉，只是機率比中間好一點、
  且樣本小。務必配合低槓桿 + risk_sizer 的 1% 風控。抄底抄在刀口上照樣會被砍。
"""
import ccxt
import numpy as np

TF = "4h"
RSI_LEN = 14
SYMBOLS_LIMIT = 60           # 掃成交量前幾大的幣
# 極端門檻
RSI_LOW, RSI_HIGH = 30, 70   # 進入觀察
RSI_XLOW, RSI_XHIGH = 22, 78 # 真極端
EMA_FAR = 6.0                # 偏離 EMA50 超過幾 %


def rsi(c, n=RSI_LEN):
    d = np.diff(c)
    up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    ru = up[:n].mean(); rd = dn[:n].mean()
    for i in range(n, len(d)):
        ru = (ru * (n - 1) + up[i]) / n
        rd = (rd * (n - 1) + dn[i]) / n
    return 100 - 100 / (1 + ru / rd) if rd > 0 else 100.0


def ema(c, n):
    k = 2 / (n + 1); e = c[0]
    for x in c[1:]:
        e = x * k + e * (1 - k)
    return e


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    print("抓全市場資金費與成交量…")
    fr = ex.fetch_funding_rates()
    tk = ex.fetch_tickers()
    # 取成交量前 N 大的 USDT 永續
    perps = [(s, tk[s].get("quoteVolume") or 0) for s in tk
             if s.endswith(":USDT") and tk.get(s)]
    perps = [s for s, _ in sorted(perps, key=lambda x: -x[1])[:SYMBOLS_LIMIT]]

    dips, tops = [], []
    for s in perps:
        try:
            o = ex.fetch_ohlcv(s, TF, limit=60)
        except Exception:
            continue
        c = np.array([x[4] for x in o])
        if len(c) < 55:
            continue
        r = rsi(c); e50 = ema(c, 50)
        dev = (c[-1] / e50 - 1) * 100                  # 偏離 EMA50 %
        chg = (c[-1] / c[-6] - 1) * 100                # 近 5 根(≈20h)變動
        ann_fund = (fr.get(s, {}).get("fundingRate") or 0) * 1095 * 100
        base = s.split("/")[0]

        # 抄底:RSI 低 + 低於均線 + 跌
        if r <= RSI_LOW and dev <= -EMA_FAR and chg < 0:
            score = (RSI_LOW - r) + abs(dev) + (5 if r <= RSI_XLOW else 0) + (5 if ann_fund < -20 else 0)
            dips.append((score, base, r, dev, chg, ann_fund))
        # 逃頂:RSI 高 + 高於均線 + 漲
        if r >= RSI_HIGH and dev >= EMA_FAR and chg > 0:
            score = (r - RSI_HIGH) + dev + (5 if r >= RSI_XHIGH else 0) + (5 if ann_fund > 40 else 0)
            tops.append((score, base, r, dev, chg, ann_fund))

    def show(title, rows, xlab):
        print("\n" + "=" * 66); print(f"  {title}（{len(rows)} 個）"); print("=" * 66)
        if not rows:
            print("  目前沒有符合的極端 —— 沒有就是沒有,別硬找(這才是紀律)")
            return
        print(f"  {'幣種':<12}{'RSI':>6}{'偏離EMA':>9}{'近20h':>8}{'年化資金費':>11}   {xlab}")
        for sc, b, r, dev, chg, af in sorted(rows, reverse=True):
            flag = "🔥深極端" if sc >= 15 else "· 一般極端"
            print(f"  {b:<12}{r:>6.0f}{dev:>8.1f}%{chg:>7.1f}%{af:>10.0f}%   {flag}")

    show("🩸 抄底候選（深超賣 / 別在中間接刀）", dips, "強度")
    show("🚀 逃頂候選（深超買 / 別追高）", tops, "強度")
    print("\n" + "-" * 66)
    print("  ⚠ 極端≠保證反轉,只是機率比中間好一點且樣本小。")
    print("  用法:當參謀挑『時機』,不是訊號。務必低槓桿 + 1% 風控(risk_sizer.py)。")
    print("  抄底抄在刀口上照樣會被砍 —— 這工具只幫你別抄在中間絞肉機。")


if __name__ == "__main__":
    main()
