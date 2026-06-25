"""
A 階段：趨勢線訊號「回測」—— 驗證自動畫線的訊號到底賺不賺。

問題：照趨勢線進場(突破壓力做多 / 接近支撐反彈做多)，扣費後是正期望嗎？
方法：逐根 K 線往前走，每根只用「當下已知」的資料判斷，模擬進出場、扣成本、統計。

★ 最重要的一件事：避免「未來函數 (lookahead bias)」
   轉折點需要左右各 k 根才能確認，所以站在第 t 根時，
   只能用 index ≤ t-k 的轉折點（那時才「已成形」）。
   偷看未來的回測一定很賺，但實盤會打回原形——這是回測最常見的致命錯。

提醒：即使回測為正，也只是「歷史上」成立；樣本、過擬合、實盤滑價都還要再打折。
"""
import ccxt
import numpy as np
import pandas as pd

# ── 參數 ──────────────────────────────────────────────────────────────────
SYMBOL      = "BTC/USDT"
TIMEFRAME   = "1h"
LIMIT       = 1000        # 抓多一點歷史（~42 天）
PIVOT_K     = 5
USE_PIVOTS  = 3
NEAR_PCT    = 0.6
BREAK_PCT   = 0.3
HORIZON     = 10          # 進場後持有幾根 K 線就平倉（固定出場，最單純）
COST_PCT    = 0.20        # 來回成本%：手續費0.1% + 滑價~0.1%（保守估）


def fetch():
    ex = ccxt.binance({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=LIMIT)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def all_pivots(df, k=PIVOT_K):
    """一次算出全部轉折點的 index（回測時再依時間過濾「已確認」的）。"""
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    for i in range(k, len(df) - k):
        if h[i] == h[i - k:i + k + 1].max():
            highs.append(i)
        if l[i] == l[i - k:i + k + 1].min():
            lows.append(i)
    return highs, lows


def line_through(idxs, prices, use=USE_PIVOTS):
    pick = idxs[-use:]
    if len(pick) < 2:
        return None
    s, b = np.polyfit(pick, prices[pick], 1)
    return float(s), float(b)


def backtest(df, rule):
    """rule ∈ {'breakout','bounce','baseline'}。回傳每筆淨報酬%列表。"""
    highs, lows = all_pivots(df)
    close = df["close"].values
    n = len(df)
    rets = []
    t = PIVOT_K + USE_PIVOTS + 5
    while t < n - HORIZON - 1:
        # 只用「已確認」的轉折點：pivot index j 需 j + PIVOT_K <= t
        conf_h = [j for j in highs if j + PIVOT_K <= t]
        conf_l = [j for j in lows if j + PIVOT_K <= t]
        res = line_through(conf_h, df["high"].values) if len(conf_h) >= 2 else None
        sup = line_through(conf_l, df["low"].values) if len(conf_l) >= 2 else None

        entry = False
        if rule == "baseline":
            entry = True                                   # 對照組：每根都做多，看市場本身漂移
        elif rule == "breakout" and res:
            ry, ry_prev = res[0]*t + res[1], res[0]*(t-1) + res[1]
            if close[t] > ry * (1 + BREAK_PCT/100) and close[t-1] <= ry_prev:
                entry = True                               # 收盤剛站上壓力線
        elif rule == "bounce" and sup:
            sy = sup[0]*t + sup[1]
            diff = (close[t] - sy) / sy * 100
            if 0 <= diff <= NEAR_PCT:
                entry = True                               # 現價貼著支撐線上方

        if entry:
            ret = (close[t + HORIZON] - close[t]) / close[t] * 100 - COST_PCT
            rets.append(ret)
            t += HORIZON                                   # 不重疊：平倉後才找下一單
        else:
            t += 1
    return rets


def report(name, rets):
    n = len(rets)
    if n == 0:
        print(f"{name:<16} 無觸發"); return
    arr = np.array(rets)
    wr = (arr > 0).mean() * 100
    mean = arr.mean(); sd = arr.std(ddof=0); se = sd/np.sqrt(n)
    t = mean/se if se else 0
    lo, hi = mean - 1.96*se, mean + 1.96*se
    flag = "✅可能有edge" if t > 1.96 else ("❌負期望" if t < -1.96 else "⚪不顯著")
    print(f"{name:<16} 筆數={n:<4} 勝率={wr:4.0f}%  平均淨報酬={mean:+.2f}%/筆  "
          f"95%CI[{lo:+.2f},{hi:+.2f}]  t={t:+.2f}  {flag}")


def main():
    df = fetch()
    print(f"=== 趨勢線訊號回測 | {SYMBOL} {TIMEFRAME} {len(df)}根 | 持有{HORIZON}根 | 來回成本{COST_PCT}% ===")
    print("（已避開未來函數：每根只用已確認的轉折點）\n")
    report("突破壓力做多", backtest(df, "breakout"))
    report("接近支撐做多", backtest(df, "bounce"))
    report("對照:每根都做多", backtest(df, "baseline"))
    print("\n判讀：訊號組要『顯著為正』且『明顯贏過對照組』才算真有 edge；")
    print("      若跟對照組差不多，代表訊號沒加值，只是搭了市場順風/逆風。")


if __name__ == "__main__":
    main()
