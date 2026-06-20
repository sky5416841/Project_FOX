"""
CHoCH 多商品 × 多時間框驗證 —— 那道「微光」是真的還是雜訊？

choch_research.py 在單一商品/1h 上發現：CHoCH-看漲-做多是唯一「信賴區間沒
完全躺在 0 以下」的訊號（-0.34%/筆, CI[-0.84,+0.17], 但只有 25 筆、不顯著）。

本檔擴大樣本來定生死：12 個流動性高的幣 × 3 個時間框，看 CHoCH-多的淨期望值
95%CI 是否真的離開 0 轉正。

★ 反 p-hacking 紀律：
  - 參數固定（持有 10 根、成本 0.2%、PIVOT_K=5），只加資料、不調參找好看的。
  - 每個時間框結果原樣列出（多時間框本身增加假陽性，誠實面對）。
  - 主要看『全部彙總』那個大樣本數字，單一時間框漂亮不算數。
"""
import ccxt
import numpy as np
import pandas as pd

from choch_research import detect_choch, stat, HORIZON, COST_PCT

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT",
           "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "LTC/USDT", "TRX/USDT"]
TIMEFRAMES = ["15m", "1h", "4h"]
LIMIT = 1000


def fetch(sym, tf):
    ex = ccxt.binance({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(sym, timeframe=tf, limit=LIMIT)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def backtest_bull(df):
    """回傳該 df 所有 CHoCH-看漲-做多 的扣費淨報酬%。"""
    c = df["close"].values
    rets = []
    for t, d, _ in detect_choch(df):
        if d == "bull" and t + HORIZON < len(df):
            rets.append((c[t + HORIZON] - c[t]) / c[t] * 100 - COST_PCT)
    return rets


def main():
    print(f"=== CHoCH-看漲-做多 多商品×多時間框驗證 | {len(SYMBOLS)}幣 | 持有{HORIZON}根 成本{COST_PCT}% ===")
    print("（無未來函數；固定參數只加資料；下面每行原樣列出，不挑好看的）\n")

    all_rets = []
    for tf in TIMEFRAMES:
        tf_rets = []
        for sym in SYMBOLS:
            try:
                tf_rets += backtest_bull(fetch(sym, tf))
            except Exception as e:
                print(f"  {sym} {tf} 失敗：{e}")
        all_rets += tf_rets
        stat(f"時間框 {tf}", tf_rets)

    print("-" * 78)
    stat("★ 全部彙總", all_rets)

    if all_rets:
        a = np.array(all_rets); n = len(a)
        m = a.mean(); se = a.std() / np.sqrt(n); t = m / se if se else 0
        lo = m - 1.96 * se
        print()
        if t > 1.96:
            print(f"判讀：{n} 筆、t={t:+.2f} → 顯著為正！CHoCH-多可能真有 edge（但仍須警惕多時間框假陽性、實盤打折）。")
        elif lo > 0:
            print(f"判讀：CI 下界 {lo:+.3f} 剛過 0，邊緣正向，需再擴大/換期間確認。")
        else:
            print(f"判讀：{n} 筆下 95%CI 仍橫跨 0（下界 {lo:+.3f}）→ 那道微光是雜訊，CHoCH-多沒有可靠 edge。")


if __name__ == "__main__":
    main()
