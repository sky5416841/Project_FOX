"""
A 階段原型 v2：自動畫趨勢線 + 初步「進出場判斷」（演算法版，非 AI 黑箱）

v1 → v2 的進步：
  1. 趨勢線改成「連最近的真實轉折點」(更接近交易員手畫)，不再是粗略回歸。
  2. 從「只畫線」進化到「讀訊號」：判斷現價相對支撐/壓力的位置，
     輸出「接近支撐(潛在做多)/跌破支撐/接近壓力/突破壓力/通道中段」等狀態。

提醒：趨勢線是描述過去結構，這裡的訊號是「規則化的觀察」，不是預測，
      更不是進場依據——要當策略仍須回測驗證淨期望值。
"""
import ccxt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ── 參數 ──────────────────────────────────────────────────────────────────
SYMBOL      = "BTC/USDT"
TIMEFRAME   = "1h"
LIMIT       = 200
PIVOT_K     = 5          # 轉折點視窗
USE_PIVOTS  = 3          # 用最近幾個轉折點連趨勢線
NEAR_PCT    = 0.6        # 現價距離線 < 0.6% 視為「接近」
BREAK_PCT   = 0.3        # 收盤越過線 0.3% 以上視為「突破/跌破」
OUT_PNG     = "trendline_BTCUSDT.png"


def fetch_ohlcv() -> pd.DataFrame:
    ex  = ccxt.binance({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=LIMIT)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def find_pivots(df: pd.DataFrame, k: int = PIVOT_K):
    """偵測轉折高/低點（左右各 k 根內的極值）。"""
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    for i in range(k, len(df) - k):
        if h[i] == h[i - k : i + k + 1].max():
            highs.append(i)
        if l[i] == l[i - k : i + k + 1].min():
            lows.append(i)
    return highs, lows


def trendline(idxs, prices, use=USE_PIVOTS):
    """用最近 use 個轉折點擬合一條趨勢線，回傳 (斜率, 截距)。"""
    pick = idxs[-use:]
    if len(pick) < 2:
        return None
    slope, intercept = np.polyfit(pick, prices[pick], 1)
    return float(slope), float(intercept)


def line_y(line, x):
    return line[0] * x + line[1]


def read_signal(df, sup, res):
    """讀現價相對支撐/壓力的位置，回傳一句白話狀態。"""
    x  = len(df) - 1
    px = df["close"].values[-1]
    msgs = []
    if sup:
        sy = line_y(sup, x)
        diff = (px - sy) / sy * 100
        if diff < -BREAK_PCT:
            msgs.append(f"⚠ 已跌破支撐 ({diff:+.2f}%) → 趨勢轉弱/停損訊號")
        elif abs(diff) <= NEAR_PCT:
            msgs.append(f"接近支撐 ({diff:+.2f}%) → 潛在反彈做多區")
    if res:
        ry = line_y(res, x)
        diff = (px - ry) / ry * 100
        if diff > BREAK_PCT:
            msgs.append(f"↑ 已突破壓力 ({diff:+.2f}%) → 動能轉強訊號")
        elif abs(diff) <= NEAR_PCT:
            msgs.append(f"接近壓力 ({diff:+.2f}%) → 潛在回落/停利區")
    if not msgs:
        msgs.append("通道中段 → 觀望，無明確訊號")
    return msgs


def main():
    df = fetch_ohlcv()
    highs, lows = find_pivots(df)
    res = trendline(highs, df["high"].values)
    sup = trendline(lows,  df["low"].values)
    signals = read_signal(df, sup, res)

    print(f"K線 {len(df)} 根｜轉折高 {len(highs)}、轉折低 {len(lows)}")
    print(f"現價 {df['close'].values[-1]:,.1f}")
    print("訊號：" + "；".join(signals))

    # ── 畫圖 ──────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 7))
    for i, row in df.iterrows():
        c = "#26a69a" if row["close"] >= row["open"] else "#ef5350"
        ax.plot([i, i], [row["low"], row["high"]], color=c, linewidth=0.8)
        ax.plot([i, i], [row["open"], row["close"]], color=c, linewidth=3.2)

    ax.scatter(highs, df["high"].values[highs], marker="v", color="#d32f2f", s=35, zorder=5, label="轉折高")
    ax.scatter(lows,  df["low"].values[lows],   marker="^", color="#1976d2", s=35, zorder=5, label="轉折低")

    # 趨勢線只從「使用到的第一個轉折點」畫到最後，貼合近期結構
    xmax = len(df) - 1
    if res:
        x0 = highs[-USE_PIVOTS:][0]
        ax.plot([x0, xmax], [line_y(res, x0), line_y(res, xmax)], "--", color="#d32f2f", lw=1.8, label="壓力線")
    if sup:
        x0 = lows[-USE_PIVOTS:][0]
        ax.plot([x0, xmax], [line_y(sup, x0), line_y(sup, xmax)], "--", color="#1976d2", lw=1.8, label="支撐線")

    # 標出現價 + 訊號文字框
    ax.scatter([xmax], [df["close"].values[-1]], color="#fbc02d", s=70, zorder=6, edgecolors="black", label="現價")
    ax.text(0.012, 0.04, "訊號：\n" + "\n".join(signals),
            transform=ax.transAxes, fontsize=10, va="bottom",
            bbox=dict(boxstyle="round", fc="#fffde7", ec="#fbc02d", alpha=0.95))

    ax.set_title(f"{SYMBOL} {TIMEFRAME} — 自動趨勢線 + 進出場讀訊號 (A v2)", fontsize=13)
    ax.set_xlabel("K 線序號"); ax.set_ylabel("價格 (USDT)")
    ax.legend(loc="upper right", prop={"size": 9})
    ax.grid(True, alpha=0.15)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=110)
    print(f"✓ 已輸出：{OUT_PNG}")


if __name__ == "__main__":
    main()
