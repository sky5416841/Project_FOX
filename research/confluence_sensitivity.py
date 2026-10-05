# -*- coding: utf-8 -*-
"""敏感度分析:confluence_backtest.py 的規則參數(分形視窗N、Fib容許誤差)是我自己合理猜的,
不是複製對方真實用的數字。這裡掃過幾組合理參數,看「無edge」這個結論穩不穩,
還是換個參數就可能翻盤(那就代表原本的結論很脆弱、不能信)。
資料只抓一次存成CSV快取,避免每個參數組合重複打API。
"""
import warnings; warnings.filterwarnings("ignore")
import os, time
import numpy as np
import pandas as pd
import ccxt
from scipy import stats

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TF = "15m"
YEARS = 2
TAKER_FEE = 0.0005
CACHE = {"BTC/USDT": "_cache_btc_15m.csv", "ETH/USDT": "_cache_eth_15m.csv"}


def fetch_history(ex, symbol, tf, years):
    ms_per_bar = 15 * 60 * 1000
    total_bars = int(years * 365 * 24 * 60 / 15)
    since = ex.milliseconds() - total_bars * ms_per_bar
    all_rows = []
    while True:
        rows = ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
        if not rows:
            break
        all_rows += rows
        since = rows[-1][0] + ms_per_bar
        if len(rows) < 1000 or since > ex.milliseconds():
            break
        time.sleep(0.2)
    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "vol"])
    return df.drop_duplicates("ts").reset_index(drop=True)


def load_or_fetch(ex, symbol):
    path = CACHE[symbol]
    if os.path.exists(path):
        return pd.read_csv(path)
    df = fetch_history(ex, symbol, TF, YEARS)
    df.to_csv(path, index=False)
    return df


def find_swings(df, n):
    h, l = df["high"].values, df["low"].values
    N = len(df)
    is_high = np.zeros(N, dtype=bool)
    is_low = np.zeros(N, dtype=bool)
    for i in range(n, N - n):
        wh = h[i - n:i + n + 1]; wl = l[i - n:i + n + 1]
        if h[i] == wh.max() and np.argmax(wh) == n:
            is_high[i] = True
        if l[i] == wl.min() and np.argmin(wl) == n:
            is_low[i] = True
    return is_high, is_low


def volume_profile(seg, n_bins=24):
    lo, hi = seg["low"].min(), seg["high"].max()
    if hi <= lo:
        return None
    bins = np.linspace(lo, hi, n_bins + 1)
    vol_at_bin = np.zeros(n_bins)
    for _, r in seg.iterrows():
        blo, bhi = r["low"], r["high"]
        if bhi <= blo:
            continue
        i0 = max(0, np.searchsorted(bins, blo, side="right") - 1)
        i1 = min(n_bins - 1, np.searchsorted(bins, bhi, side="right") - 1)
        span = max(i1 - i0 + 1, 1)
        vol_at_bin[i0:i1 + 1] += r["vol"] / span
    poc_i = int(np.argmax(vol_at_bin))
    total = vol_at_bin.sum()
    if total <= 0:
        return None
    target = total * 0.70
    lo_i = hi_i = poc_i
    acc = vol_at_bin[poc_i]
    while acc < target and (lo_i > 0 or hi_i < n_bins - 1):
        eh = vol_at_bin[hi_i + 1] if hi_i < n_bins - 1 else -1
        el = vol_at_bin[lo_i - 1] if lo_i > 0 else -1
        if eh >= el:
            hi_i += 1; acc += vol_at_bin[hi_i]
        else:
            lo_i -= 1; acc += vol_at_bin[lo_i]
    poc = (bins[poc_i] + bins[poc_i + 1]) / 2
    return dict(poc=poc, val=bins[lo_i], vah=bins[hi_i + 1])


def backtest(df, swing_n, fib_tol, sl_buffer=0.0015):
    is_high, is_low = find_swings(df, swing_n)
    swing_low_idx = np.where(is_low)[0]
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    trades = []
    last_low_i = None
    ptr = 0
    for i in range(len(df)):
        while ptr < len(swing_low_idx) and swing_low_idx[ptr] < i:
            last_low_i = swing_low_idx[ptr]; ptr += 1
        if not is_high[i] or last_low_i is None:
            continue
        leg_lo_i, leg_hi_i = last_low_i, i
        if leg_hi_i - leg_lo_i < swing_n * 2:
            continue
        leg_low, leg_high = l[leg_lo_i], h[leg_hi_i]
        if leg_high <= leg_low:
            continue
        fib50 = leg_high - 0.5 * (leg_high - leg_low)
        vp = volume_profile(df.iloc[leg_lo_i:leg_hi_i + 1])
        if vp is None:
            continue
        band_lo, band_hi = vp["val"] * (1 - fib_tol), vp["poc"] * (1 + fib_tol)
        if not (band_lo <= fib50 <= band_hi):
            continue
        entry_zone, sl, tp = fib50, vp["val"] * (1 - sl_buffer), leg_high
        if entry_zone <= sl:
            continue
        rr = (tp - entry_zone) / (entry_zone - sl)
        entry_i = None
        for j in range(leg_hi_i + 1, min(leg_hi_i + 200, len(df))):
            if l[j] <= entry_zone and c[j] >= sl:
                entry_i = j; break
            if l[j] <= sl:
                break
        if entry_i is None:
            continue
        result = None
        for k in range(entry_i, min(entry_i + 500, len(df))):
            if l[k] <= sl:
                result = "loss"; break
            if h[k] >= tp:
                result = "win"; break
        if result is None:
            continue
        stop_pct = (entry_zone - sl) / entry_zone
        fee_r = 2 * TAKER_FEE / stop_pct if stop_pct > 0 else np.nan
        net_r = (rr if result == "win" else -1.0) - fee_r
        trades.append(dict(result=result, net_r=net_r, rr=rr))
    return pd.DataFrame(trades)


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    dfs = {}
    for sym in SYMBOLS:
        print("載入 %s..." % sym)
        dfs[sym] = load_or_fetch(ex, sym)

    print("\n=== 敏感度分析:不同參數組合下的結果 ===")
    print("%-6s %-8s %8s %8s %10s %8s  %s" % ("N", "FibTol", "n", "勝率%", "淨R/筆", "t值", "判讀"))
    results = []
    for swing_n in [3, 5, 8]:
        for fib_tol in [0.05, 0.10, 0.20]:
            all_tr = []
            for sym in SYMBOLS:
                tr = backtest(dfs[sym], swing_n, fib_tol)
                all_tr.append(tr)
            trades = pd.concat(all_tr, ignore_index=True)
            n = len(trades)
            if n < 20:
                print("%-6d %-8.2f %8d  (樣本太少)" % (swing_n, fib_tol, n))
                continue
            win = (trades["result"] == "win").mean()
            net = trades["net_r"].mean()
            t, _ = stats.ttest_1samp(trades["net_r"], 0)
            tag = "✅正顯著" if (net > 0 and t > 1.64) else ("🟡正" if net > 0 else "❌負")
            print("%-6d %-8.2f %8d %7.1f%% %+9.3f %+7.2f  %s" % (swing_n, fib_tol, n, win * 100, net, t, tag))
            results.append((swing_n, fib_tol, n, win, net, t))

    print("\n=== 額外測試:比照PO3發現,篩高R:R子集會不會救回來? ===")
    for swing_n, fib_tol in [(5, 0.10), (5, 0.20)]:
        all_tr = []
        for sym in SYMBOLS:
            all_tr.append(backtest(dfs[sym], swing_n, fib_tol))
        trades = pd.concat(all_tr, ignore_index=True)
        for rr_min in [2, 3, 4]:
            hi = trades[trades["rr"] >= rr_min]
            n = len(hi)
            if n < 15:
                print("  N=%d Tol=%.2f RR>=%d: n=%d 太少" % (swing_n, fib_tol, rr_min, n)); continue
            win = (hi["result"] == "win").mean(); net = hi["net_r"].mean()
            t, _ = stats.ttest_1samp(hi["net_r"], 0)
            tag = "✅正顯著" if (net > 0 and t > 1.64) else ("🟡正" if net > 0 else "❌負")
            print("  N=%d Tol=%.2f RR>=%d: n=%4d 勝率%.0f%% 淨R%+.3f t=%+.2f %s" % (
                swing_n, fib_tol, rr_min, n, win * 100, net, t, tag))

    print("\n=== 總結 ===")
    all_neg = all(r[4] < 0 for r in results)
    print("9組參數組合中,全部為負: %s" % ("是 → 結論穩健,不是參數挑錯" if all_neg else "否 → 結果對參數敏感,需更小心解讀"))


if __name__ == "__main__":
    main()
