"""
CHoCH (Change of Character) 結構破壞點 — 偵測 + 視覺化 + 回測

CHoCH 定義（Smart Money Concepts）：市場結構反轉的第一個破口。
- 看漲 CHoCH：下跌結構（高點低點都一波比一波低）中，收盤『突破上一個轉折高』
- 看跌 CHoCH：上升結構中，收盤『跌破上一個轉折低』

本檔做三件事：
  1. 偵測 CHoCH（★無未來函數：第 t 根只用已確認的轉折點 idx ≤ t-k）
  2. 把 CHoCH 點畫在 BTC 圖上（視覺 sanity check）
  3. 跨 5 商品回測「在 CHoCH 進場、持有固定根數」扣費後賺不賺，並與隨機進場對照

延續紀律：看淨期望值 95%CI、t 檢定、與基準比；好看 ≠ 有 edge。
"""
import ccxt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

SYMBOLS   = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
TIMEFRAME = "1h"
LIMIT     = 1000
PIVOT_K   = 5
HORIZON   = 10
COST_PCT  = 0.20


def fetch(sym):
    ex = ccxt.binance({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(sym, timeframe=TIMEFRAME, limit=LIMIT)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def all_pivots(df, k=PIVOT_K):
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    for i in range(k, len(df) - k):
        if h[i] == h[i - k:i + k + 1].max():
            highs.append(i)
        if l[i] == l[i - k:i + k + 1].min():
            lows.append(i)
    return highs, lows


def detect_choch(df, k=PIVOT_K):
    """回傳 [(t, 'bull'/'bear', broken_level)]；★無未來函數。"""
    highs, lows = all_pivots(df, k)
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    out = []
    for t in range(2 * k + 5, len(df)):
        ch = [j for j in highs if j + k <= t]      # 已確認的轉折高
        cl = [j for j in lows if j + k <= t]
        if len(ch) < 2 or len(cl) < 2:
            continue
        lsh, psh = ch[-1], ch[-2]                  # 最近 / 前一個 轉折高
        lsl, psl = cl[-1], cl[-2]
        down = h[lsh] < h[psh] and l[lsl] < l[psl]  # 低高點+低低點 = 下跌結構
        up   = h[lsh] > h[psh] and l[lsl] > l[psl]  # 高高點+高低點 = 上升結構
        if down and c[t] > h[lsh] and c[t - 1] <= h[lsh]:
            out.append((t, "bull", h[lsh]))         # 下跌結構被向上突破
        elif up and c[t] < l[lsl] and c[t - 1] >= l[lsl]:
            out.append((t, "bear", l[lsl]))         # 上升結構被向下跌破
    return out


def stat(label, rets):
    n = len(rets)
    if n == 0:
        print(f"{label:<18} 無觸發"); return
    a = np.array(rets); m = a.mean(); se = a.std() / np.sqrt(n)
    wr = (a > 0).mean() * 100; t = m / se if se else 0
    flag = "✅有edge" if t > 1.96 else ("❌負" if t < -1.96 else "⚪不顯著")
    print(f"{label:<18} 筆數={n:<4} 勝率={wr:4.0f}%  淨報酬={m:+.2f}%/筆  "
          f"95%CI[{m-1.96*se:+.2f},{m+1.96*se:+.2f}]  t={t:+.2f}  {flag}")


def main():
    dfs = {}
    for s in SYMBOLS:
        try:
            dfs[s] = fetch(s)
        except Exception as e:
            print(f"{s} 抓取失敗：{e}")

    # ── 跨商品回測 ────────────────────────────────────────────────────────
    bull, bear, base = [], [], []
    n_choch = 0
    for s, df in dfs.items():
        c = df["close"].values
        sigs = detect_choch(df)
        n_choch += len(sigs)
        for t, d, _ in sigs:
            if t + HORIZON >= len(df):
                continue
            if d == "bull":
                bull.append((c[t + HORIZON] - c[t]) / c[t] * 100 - COST_PCT)   # 做多
            else:
                bear.append((c[t] - c[t + HORIZON]) / c[t] * 100 - COST_PCT)   # 做空
        for t in range(2 * PIVOT_K + 5, len(df) - HORIZON):                    # 對照：每根做多
            base.append((c[t + HORIZON] - c[t]) / c[t] * 100 - COST_PCT)

    print(f"=== CHoCH 回測 | {len(dfs)}商品 {TIMEFRAME} 各{LIMIT}根 | 持有{HORIZON}根 | 成本{COST_PCT}% ===")
    print(f"（無未來函數；共偵測到 {n_choch} 個 CHoCH 事件）\n")
    stat("CHoCH 看漲做多", bull)
    stat("CHoCH 看跌做空", bear)
    stat("對照:每根做多", base)
    print("\n判讀：要『顯著為正』且『贏過對照組』才算 CHoCH 真有預測力。")

    # ── 視覺化 BTC 的 CHoCH 點 ────────────────────────────────────────────
    df = dfs["BTC/USDT"]; sigs = detect_choch(df)
    fig, ax = plt.subplots(figsize=(15, 7))
    for i, r in df.iterrows():
        col = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=col, lw=0.7)
        ax.plot([i, i], [r["open"], r["close"]], color=col, lw=2.6)
    for t, d, lvl in sigs:
        if d == "bull":
            ax.scatter(t, df["close"].values[t], marker="^", s=90, color="#00c853", zorder=6, edgecolors="black")
            ax.hlines(lvl, t - 12, t, color="#00c853", ls=":", lw=1)
        else:
            ax.scatter(t, df["close"].values[t], marker="v", s=90, color="#d50000", zorder=6, edgecolors="black")
            ax.hlines(lvl, t - 12, t, color="#d50000", ls=":", lw=1)
    ax.set_title("BTC/USDT 1h — CHoCH 結構破壞點偵測（▲看漲 ▼看跌；虛線=被破壞的結構）", fontsize=12)
    ax.set_xlabel("K 線序號"); ax.set_ylabel("價格 (USDT)"); ax.grid(alpha=0.15)
    fig.tight_layout(); fig.savefig("choch_BTCUSDT.png", dpi=110)
    print(f"\n✓ 已輸出 choch_BTCUSDT.png（BTC 偵測到 {len(sigs)} 個 CHoCH）")


if __name__ == "__main__":
    main()
