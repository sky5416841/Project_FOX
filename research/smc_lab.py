"""
SMC 偵測器 + 視覺化(第 1 步)—— 把機構訂單流概念抓出來、畫在圖上

實作 Smart Money Concepts 的核心元件(自己寫,可審計,非 TA-Lib):
  · FVG (Fair Value Gap，合理價值缺口)：3 根 K 的失衡缺口
  · Swing 結構 + BOS (Break of Structure，結構破壞)
  · Liquidity Sweep (流動性掠奪)：刺穿前高/前低後收回
  · ROP (Rejection Order Block)：長上影線拒絕 + 陰線破實體(由使用者提供的定義)

★ 無未來函數:每個訊號只用「截至當根」的資料判定。
⚠️ 這步只是「抓得對不對」的視覺確認；有沒有 edge,要等下一步嚴謹回測(我預期
   它跟 CHoCH 同家族、大概率樣本外失效,但做出來+測一次,是技能與作品的價值)。
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

SYMBOL, TIMEFRAME, LIMIT, PIVOT_K = "BTC/USDT", "1m", 400, 5


def fetch():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    raw = ex.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=LIMIT)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def detect_fvg(df):
    """3 根 K 的缺口。空方FVG: 第3根高 < 第1根低; 多方FVG: 第3根低 > 第1根高。"""
    H, L = df["high"].values, df["low"].values
    out = []
    for i in range(2, len(df)):
        if H[i] < L[i-2]:
            out.append((i, "bear", H[i], L[i-2]))     # (索引, 型別, 缺口下緣, 上緣)
        elif L[i] > H[i-2]:
            out.append((i, "bull", H[i-2], L[i]))
    return out


def find_pivots(df, k=PIVOT_K):
    H, L = df["high"].values, df["low"].values
    highs, lows = [], []
    for i in range(k, len(df) - k):
        if H[i] == H[i-k:i+k+1].max():
            highs.append(i)
        if L[i] == L[i-k:i+k+1].min():
            lows.append(i)
    return highs, lows


def detect_bos(df, highs, lows, k=PIVOT_K):
    """BOS：收盤突破「已確認」的前一個轉折高(多方)/轉折低(空方)。無未來函數。"""
    C, H, L = df["close"].values, df["high"].values, df["low"].values
    out = []
    for t in range(k+2, len(df)):
        ch = [j for j in highs if j + k <= t]
        cl = [j for j in lows if j + k <= t]
        if ch and C[t] > H[ch[-1]] and C[t-1] <= H[ch[-1]]:
            out.append((t, "bull", H[ch[-1]]))
        if cl and C[t] < L[cl[-1]] and C[t-1] >= L[cl[-1]]:
            out.append((t, "bear", L[cl[-1]]))
    return out


def detect_sweep(df, highs, lows, k=PIVOT_K):
    """流動性掠奪：影線刺穿前高/前低,但收盤縮回(抓掉停損後反轉)。"""
    C, H, L = df["close"].values, df["high"].values, df["low"].values
    out = []
    for t in range(k+2, len(df)):
        ch = [j for j in highs if j + k <= t]
        cl = [j for j in lows if j + k <= t]
        if ch and H[t] > H[ch[-1]] and C[t] < H[ch[-1]]:
            out.append((t, "bear", H[t]))            # 刺穿前高後收回 → 看空
        if cl and L[t] < L[cl[-1]] and C[t] > L[cl[-1]]:
            out.append((t, "bull", L[t]))
    return out


def is_rop_pattern(df, t):
    """看空 ROP(使用者定義):前一根長上影線(>=50%總長)+ 當根陰線收盤跌破前一根實體。"""
    if t < 1:
        return False
    o1, h1, l1, c1 = df.loc[t-1, ["open", "high", "low", "close"]]
    o0, c0 = df.loc[t, ["open", "close"]]
    rng = h1 - l1
    if rng <= 0:
        return False
    upper_wick = h1 - max(o1, c1)
    cond_B = upper_wick / rng >= 0.50                 # 長上影線(高檔被拒絕)
    cond_C = (c0 < o0) and (c0 < min(o1, c1))         # 當根陰線、收盤破前一根實體
    return bool(cond_B and cond_C)


def main():
    df = fetch()
    fvg = detect_fvg(df)
    highs, lows = find_pivots(df)
    bos = detect_bos(df, highs, lows)
    sweep = detect_sweep(df, highs, lows)
    rop = [t for t in range(1, len(df)) if is_rop_pattern(df, t)]

    print(f"{SYMBOL} {TIMEFRAME} {len(df)}根 偵測結果：")
    print(f"  FVG缺口 {len(fvg)} | 轉折高/低 {len(highs)}/{len(lows)} | BOS {len(bos)} | 流動性掠奪 {len(sweep)} | ROP訊號 {len(rop)}")

    # ── 視覺化 ──
    fig, ax = plt.subplots(figsize=(16, 8))
    for i, r in df.iterrows():
        col = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=col, lw=0.6)
        ax.plot([i, i], [r["open"], r["close"]], color=col, lw=2.2)
    # FVG 畫成半透明方塊
    for idx, typ, lo, hi in fvg:
        c = "#ef5350" if typ == "bear" else "#26a69a"
        ax.add_patch(Rectangle((idx-2, lo), 3, hi-lo, color=c, alpha=0.12))
    # 掠奪 / ROP 標記
    for t, d, lvl in sweep:
        ax.scatter(t, df["high"].values[t] if d == "bear" else df["low"].values[t],
                   marker="x", s=60, color="#9c27b0", zorder=5)
    for t in rop:
        ax.scatter(t, df["high"].values[t] * 1.0005, marker="v", s=110,
                   color="#d50000", edgecolors="black", zorder=6)
    ax.scatter([], [], marker="v", color="#d50000", label="ROP 看空訊號")
    ax.scatter([], [], marker="x", color="#9c27b0", label="流動性掠奪")
    ax.add_patch(Rectangle((0, 0), 0, 0, color="#ef5350", alpha=0.3, label="空方FVG"))
    ax.add_patch(Rectangle((0, 0), 0, 0, color="#26a69a", alpha=0.3, label="多方FVG"))
    ax.set_title(f"{SYMBOL} {TIMEFRAME} — SMC 偵測器：FVG缺口 + 流動性掠奪 + ROP形態", fontsize=13)
    ax.set_xlabel("K 線序號"); ax.set_ylabel("價格"); ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.12)
    fig.tight_layout(); fig.savefig("smc_BTCUSDT.png", dpi=110)
    print("✓ 已輸出 smc_BTCUSDT.png")


if __name__ == "__main__":
    main()
