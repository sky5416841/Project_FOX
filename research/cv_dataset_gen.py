"""
C 階段第一步：自動產生「K 線圖像分類」資料集

CV 的 80% 是資料準備。這支程式把 A 階段的演算法當「自動標註器」：
  滑動視窗切出一段段 K 線 → 用迴歸斜率判定該段趨勢(上升/下降/盤整)
  → 渲染成「無座標軸的乾淨 K 線圖」(CNN 的輸入) → 按類別存進資料夾。

產出的結構（標準影像分類格式，之後可直接餵 PyTorch/Keras 的 ImageFolder）：
  data_cv/up/    上升趨勢的圖
  data_cv/down/  下降趨勢的圖
  data_cv/range/ 盤整的圖

任務定位（誠實）：這是教模型「辨識眼前這段長得像漲/跌/盤整」(描述性、可學)，
  不是預測未來(那是另一個更難、且我們回測已知無 edge 的問題)。先把 CV 管線跑通。
"""
import os
import ccxt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── 參數 ──────────────────────────────────────────────────────────────────
SYMBOLS    = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
TIMEFRAME  = "1h"
BARS       = 1000          # 每個商品抓幾根
WINDOW     = 100           # 每張圖含幾根 K 線
STRIDE     = 8             # 視窗每次滑動幾根（小=樣本多但重疊高）
IMG_PX     = 128           # 輸出圖邊長（像素），CNN 不需高解析
SLOPE_UP   = 0.05          # 視窗內斜率正規化 > +0.05% → 上升
SLOPE_DN   = -0.05         # < -0.05% → 下降；之間 → 盤整
OUT_DIR    = "data_cv"


def fetch(symbol):
    ex = ccxt.binance({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=BARS)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def label_window(seg):
    """用收盤價的迴歸斜率(以均價正規化成 %/根)判定趨勢類別。"""
    y = seg["close"].values
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    slope_pct = slope / y.mean() * 100      # 每根 K 線約漲跌幾 %
    if slope_pct > SLOPE_UP:
        return "up"
    if slope_pct < SLOPE_DN:
        return "down"
    return "range"


def render(seg, path):
    """把一段 K 線畫成無座標軸的乾淨圖（只有蠟燭，CNN 的純視覺輸入）。"""
    fig, ax = plt.subplots(figsize=(IMG_PX / 100, IMG_PX / 100), dpi=100)
    for i, (_, r) in enumerate(seg.iterrows()):
        c = "#000000" if r["close"] >= r["open"] else "#bbbbbb"  # 黑=漲 灰=跌（去顏色干擾，留型態）
        ax.plot([i, i], [r["low"], r["high"]], color=c, linewidth=0.5)
        ax.plot([i, i], [r["open"], r["close"]], color=c, linewidth=1.6)
    ax.axis("off")                                # 去掉座標軸/邊框/文字
    ax.margins(0.01)
    fig.savefig(path, dpi=100, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


def main():
    for cls in ["up", "down", "range"]:
        os.makedirs(os.path.join(OUT_DIR, cls), exist_ok=True)

    counts = {"up": 0, "down": 0, "range": 0}
    for sym in SYMBOLS:
        try:
            df = fetch(sym)
        except Exception as e:
            print(f"  {sym} 抓取失敗：{e}")
            continue
        tag = sym.split("/")[0]
        made = 0
        for start in range(0, len(df) - WINDOW, STRIDE):
            seg = df.iloc[start:start + WINDOW].reset_index(drop=True)
            cls = label_window(seg)
            fname = f"{tag}_{start:04d}.png"
            render(seg, os.path.join(OUT_DIR, cls, fname))
            counts[cls] += 1
            made += 1
        print(f"  {sym}: 產生 {made} 張")

    total = sum(counts.values())
    print(f"\n✓ 完成，共 {total} 張 → {OUT_DIR}/")
    for cls, n in counts.items():
        print(f"   {cls:<6} {n:>4} 張 ({n/total*100:.0f}%)")
    print("\n下一步：用這個資料集訓練 CNN 分類器（需裝 torch 或 tensorflow）。")


if __name__ == "__main__":
    main()
