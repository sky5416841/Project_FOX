# -*- coding: utf-8 -*-
"""機械化回測「Structure(BOS) + Fibonacci 50% + FRVP(成交量分佈)」匯合進場法。

依據使用者提供的教學影片(9/24 MGC交易回顧)重建規則,機械化定義如下(誠實標註:
這是對示範邏輯的合理近似,不是逐幀複製,因為單一影片沒有給出精確數字門檻):

1. 用分形法(N=5)找擺動高/低點,偵測結構突破(BOS):新高突破近期擺動高點=多頭結構。
2. 該次突破的「衝量段」= 從前一個擺動低點 到 這個新高點。
3. 算該衝量段的 Fibonacci 50% 回撤位。
4. 算該衝量段的成交量分佈(用K棒成交量按價格分bin近似),抓 POC/VAH/VAL(70%量能區)。
5. 匯合條件:Fib50% 落在 VAL~POC 之間(兩個獨立工具同意這是「便宜區」)。
6. 進場:價格回踩到這個匯合區、且該根K棒收盤在區間之上(不破,像個反彈確認)。
7. 停損:VAL下方一點緩衝。停利:衝量段的高點(近似「拉回測前高/VAH」的目標)。
8. 只做多(比照使用者守則)。手續費比照之前的模型(2*0.0005/停損距離)。

拉 2 年 15m 歷史資料(BTC/ETH),機械掃過全部歷史,不用等未來發生。
"""
import warnings; warnings.filterwarnings("ignore")
import time
import numpy as np
import pandas as pd
import ccxt
from scipy import stats

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TF = "15m"
YEARS = 2
SWING_N = 5          # 分形擺動點:左右各N根都更低/更高
FIB_TOL = 0.10        # Fib50% 落在衝量段幅度的 ±10% 內都算「靠近」
SL_BUFFER = 0.0015    # 停損緩衝
TAKER_FEE = 0.0005


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
    df = df.drop_duplicates("ts").reset_index(drop=True)
    return df


def find_swings(df, n=SWING_N):
    """分形法找擺動高/低點,回傳兩個布林陣列。"""
    h, l = df["high"].values, df["low"].values
    N = len(df)
    is_high = np.zeros(N, dtype=bool)
    is_low = np.zeros(N, dtype=bool)
    for i in range(n, N - n):
        window_h = h[i - n:i + n + 1]
        window_l = l[i - n:i + n + 1]
        if h[i] == window_h.max() and np.argmax(window_h) == n:
            is_high[i] = True
        if l[i] == window_l.min() and np.argmin(window_l) == n:
            is_low[i] = True
    return is_high, is_low


def volume_profile(seg, n_bins=24):
    """近似成交量分佈:每根K棒的量平均分攤到 open~close(含high/low範圍簡化用該棒高低)。
    回傳 POC, VAH, VAL(70%量能區)。"""
    lo, hi = seg["low"].min(), seg["high"].max()
    if hi <= lo:
        return None
    bins = np.linspace(lo, hi, n_bins + 1)
    vol_at_bin = np.zeros(n_bins)
    for _, r in seg.iterrows():
        blo, bhi = r["low"], r["high"]
        if bhi <= blo:
            continue
        # 這根K棒的量,依它涵蓋的bin數平均分攤
        i0 = np.searchsorted(bins, blo, side="right") - 1
        i1 = np.searchsorted(bins, bhi, side="right") - 1
        i0, i1 = max(0, i0), min(n_bins - 1, i1)
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
        expand_lo = vol_at_bin[lo_i - 1] if lo_i > 0 else -1
        expand_hi = vol_at_bin[hi_i + 1] if hi_i < n_bins - 1 else -1
        if expand_hi >= expand_lo:
            hi_i += 1; acc += vol_at_bin[hi_i]
        else:
            lo_i -= 1; acc += vol_at_bin[lo_i]
    poc = (bins[poc_i] + bins[poc_i + 1]) / 2
    val = bins[lo_i]
    vah = bins[hi_i + 1]
    return dict(poc=poc, val=val, vah=vah)


def backtest_symbol(df):
    is_high, is_low = find_swings(df)
    swing_low_idx = np.where(is_low)[0]
    swing_high_idx = np.where(is_high)[0]
    trades = []
    last_low_i = None
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    swing_low_ptr = 0
    for i in range(len(df)):
        # 維護「最近一個擺動低點」指標(在i之前發生的)
        while swing_low_ptr < len(swing_low_idx) and swing_low_idx[swing_low_ptr] < i:
            last_low_i = swing_low_idx[swing_low_ptr]
            swing_low_ptr += 1
        if not is_high[i] or last_low_i is None:
            continue
        leg_lo_i, leg_hi_i = last_low_i, i
        if leg_hi_i - leg_lo_i < SWING_N * 2:
            continue
        leg_low = l[leg_lo_i]
        leg_high = h[leg_hi_i]
        if leg_high <= leg_low:
            continue
        fib50 = leg_high - 0.5 * (leg_high - leg_low)
        seg = df.iloc[leg_lo_i:leg_hi_i + 1]
        vp = volume_profile(seg)
        if vp is None:
            continue
        # 匯合條件:fib50 落在 VAL~POC 之間(容許一點誤差)
        band_lo = vp["val"] * (1 - FIB_TOL)
        band_hi = vp["poc"] * (1 + FIB_TOL)
        if not (band_lo <= fib50 <= band_hi):
            continue
        entry_zone = fib50
        sl = vp["val"] * (1 - SL_BUFFER)
        tp = leg_high
        if entry_zone <= sl:
            continue
        rr = (tp - entry_zone) / (entry_zone - sl)
        # 往後找:價格回踩到 entry_zone 附近、且該棒收盤守住(收在 entry_zone 之上一點)
        entered = False
        for j in range(leg_hi_i + 1, min(leg_hi_i + 200, len(df))):
            if l[j] <= entry_zone and c[j] >= sl:
                entry_i = j
                entered = True
                break
            if l[j] <= sl:  # 還沒進場就先破底,這個setup作廢
                break
        if not entered:
            continue
        # 判定結果:進場之後,看先碰到 sl 還是 tp
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
        real_r = rr if result == "win" else -1.0
        net_r = real_r - fee_r
        trades.append(dict(entry_i=entry_i, entry=entry_zone, sl=sl, tp=tp, rr=rr,
                           result=result, net_r=net_r, ts=df["ts"].iloc[entry_i]))
    return pd.DataFrame(trades)


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    all_trades = []
    for sym in SYMBOLS:
        print("抓 %s %s 歷史資料(%d年)..." % (sym, TF, YEARS))
        df = fetch_history(ex, sym, TF, YEARS)
        print("  共 %d 根K棒,跑匯合規則掃描中..." % len(df))
        tr = backtest_symbol(df)
        tr["symbol"] = sym
        print("  → 找到 %d 筆符合條件的歷史交易" % len(tr))
        all_trades.append(tr)
    trades = pd.concat(all_trades, ignore_index=True)
    trades.to_csv("confluence_backtest_trades.csv", index=False)

    print("\n=== Confluence(Structure+Fib50+FRVP) 歷史回測結果 ===")
    n = len(trades)
    if n < 5:
        print("樣本太少(n=%d),規則可能太嚴或資料不足。" % n); return
    win = (trades["result"] == "win").mean()
    net = trades["net_r"].mean()
    t, _ = stats.ttest_1samp(trades["net_r"], 0)
    fair = (1 / (1 + trades["rr"])).mean()
    z = (win - fair) / np.sqrt(fair * (1 - fair) / n)
    print("n=%d  勝率%.1f%%  平均R:R=%.2f  扣費後淨值%+.3fR/筆  t=%+.2f  z=%+.2f" % (
        n, win * 100, trades["rr"].mean(), net, t, z))
    print("公平勝率(R:R隱含)=%.1f%%  實際勝率=%.1f%%" % (fair * 100, win * 100))
    print("\n判讀:%s" % ("✅ 顯著正(大概率不是雜訊)" if (net > 0 and t > 1.64)
                       else ("🟡 正但不顯著" if net > 0 else "❌ 負(無edge)")))
    print("\n各幣別:")
    for s, seg in trades.groupby("symbol"):
        print("  %-10s n=%3d 勝率%.0f%% 淨R%+.3f" % (s, len(seg), (seg['result']=='win').mean()*100, seg['net_r'].mean()))


if __name__ == "__main__":
    main()
