"""
PO3 第一課:盤整區 (Accumulation Box) 偵測器

把「看起來像在盤整」的盤感,翻成數學定義(Quant Developer 的核心工作):
  · 滑動窗口(30根)算 rolling 高低 → 震幅% = (max-min)/min
  · ATR 收斂:當前 ATR < 較長期 ATR 均值(動能衰退、市場觀望)
  · 兩者同時成立 → 標記為盤整中,記下 box_high(賣方流動性)/box_low(買方流動性)

★ 純技能/視覺練習:目標是「框得對不對」,不是賺錢。盤整框出來後,
  下一步才是偵測「衝出方塊(假突破/操縱)」。
"""
import ccxt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

SYMBOL, TF, LIMIT = "BTC/USDT", "5m", 500
WINDOW, ATR_LEN = 30, 14
RANGE_MULT = 0.95      # 震幅 < 近期典型震幅 × 此值 視為「收斂」(放寬以抓到大盤整區)
MIN_BARS = 8           # 盤整至少持續幾根才算數


def fetch():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    raw = ex.fetch_ohlcv(SYMBOL, TF, limit=LIMIT)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def atr(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def main():
    df = fetch()
    df["rmax"] = df["high"].rolling(WINDOW).max()
    df["rmin"] = df["low"].rolling(WINDOW).min()
    df["rng"]  = (df["rmax"] - df["rmin"]) / df["rmin"]
    df["atr"]  = atr(df, ATR_LEN)
    df["atr_base"] = df["atr"].rolling(100).mean()
    # 自適應門檻:震幅 < 近 200 根震幅中位數 × RANGE_MULT
    rng_med = df["rng"].rolling(200, min_periods=50).median()
    # 盤整 = 30根震幅明顯比近期典型窄(去掉過嚴的 ATR 雙重條件，先抓得到大盤整區)
    df["accum"] = (df["rng"] < rng_med * RANGE_MULT)
    df["accum"] = df["accum"].fillna(False)

    # 連續的 accum 合併成方塊
    boxes, i = [], 0
    a = df["accum"].values
    while i < len(df):
        if a[i]:
            j = i
            while j < len(df) and a[j]:
                j += 1
            if j - i >= MIN_BARS:
                seg = df.iloc[i:j]
                boxes.append((i, j - 1, float(seg["high"].max()), float(seg["low"].min())))
            i = j
        else:
            i += 1

    print(f"{SYMBOL} {TF} {len(df)}根 → 偵測到 {len(boxes)} 個盤整區(Accumulation Box)")

    fig, ax = plt.subplots(figsize=(16, 8))
    for i, r in df.iterrows():
        col = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=col, lw=0.6)
        ax.plot([i, i], [r["open"], r["close"]], color=col, lw=2.0)
    for x0, x1, bh, bl in boxes:
        ax.add_patch(Rectangle((x0, bl), x1 - x0, bh - bl,
                               facecolor="#42a5f5", alpha=0.18, edgecolor="#1565c0", lw=1.2))
        ax.hlines([bh, bl], x0, x1, color="#1565c0", lw=0.8, ls=":")
    ax.add_patch(Rectangle((0, 0), 0, 0, facecolor="#42a5f5", alpha=0.4, label="盤整區 Accumulation Box"))
    ax.set_title(f"{SYMBOL} {TF} — PO3 第一課:盤整區偵測(藍框=散戶停損池)", fontsize=13)
    ax.set_xlabel("K 線序號"); ax.set_ylabel("價格"); ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.12)
    fig.tight_layout(); fig.savefig("assets/po3_accumulation.png", dpi=110)
    print("✓ 已輸出 assets/po3_accumulation.png")


if __name__ == "__main__":
    main()
