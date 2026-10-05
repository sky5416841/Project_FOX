# -*- coding: utf-8 -*-
"""H2(見 HYPOTHESES.md):日線 Donchian 20/10 僅做多的趨勢跟隨,是否具有擇時能力。
規則已事前固定,不做參數掃描;三項條件全成立才算「值得追蹤」。
"""
import warnings; warnings.filterwarnings("ignore")
import os, time
import numpy as np
import pandas as pd
import ccxt
from scipy import stats

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
ENTRY_N, EXIT_N = 20, 10
COST_PER_SIDE = 0.0010
T_PRIMARY, T_REPLICATE = 2.0, 1.64
DD_RATIO_MAX = 0.6
CACHE_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_daily(ex, sym):
    cache = os.path.join(CACHE_DIR, "_cache_h2_%s_1d.csv" % sym.split("/")[0])
    if os.path.exists(cache):
        return pd.read_csv(cache)
    since = ex.parse8601("2017-08-01T00:00:00Z")
    rows = []
    while True:
        for a in range(4):
            try:
                b = ex.fetch_ohlcv(sym, "1d", since=since, limit=1000); break
            except Exception:
                if a == 3: raise
                time.sleep(2 * (a + 1))
        if not b: break
        rows += b
        since = b[-1][0] + 86400000
        if len(b) < 1000: break
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df = df.drop_duplicates("ts").iloc[:-1].reset_index(drop=True)   # 丟未收完的最後一根
    df.to_csv(cache, index=False)
    return df


def states(df):
    hi, lo, cl = df["high"].values, df["low"].values, df["close"].values
    st = np.zeros(len(df), dtype=int)
    cur = 0
    for t in range(len(df)):
        if t >= ENTRY_N and cl[t] > hi[t - ENTRY_N:t].max():
            cur = 1
        elif t >= EXIT_N and cl[t] < lo[t - EXIT_N:t].min():
            cur = 0
        st[t] = cur
    return st


def metrics(ret_series):
    r = pd.Series(ret_series).dropna()
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    yrs = len(r) / 365.0
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    sharpe = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan
    return cagr, sharpe, dd


def run(sym, df):
    st = states(df)
    r_next = df["close"].pct_change().shift(-1).values        # r_{t+1},以t日狀態持有
    valid = ~np.isnan(r_next)
    long_r = r_next[valid & (st == 1)]
    flat_r = r_next[valid & (st == 0)]
    t, p2 = stats.ttest_ind(long_r, flat_r, equal_var=False)
    # 淨績效:狀態切換當天(t+1報酬)扣每邊成本
    prev = np.concatenate([[0], st[:-1]])
    flips = (st != prev).astype(float)
    strat = st * np.where(valid, r_next, 0.0) - flips * COST_PER_SIDE
    strat = np.where(valid, strat, np.nan)
    bh = np.where(valid, r_next, np.nan)
    c_s, sh_s, dd_s = metrics(strat)
    c_b, sh_b, dd_b = metrics(bh)
    # 交易統計(描述用)
    trades, entry_px = [], None
    cl = df["close"].values
    for i in range(1, len(st)):
        if st[i] == 1 and st[i - 1] == 0:
            entry_px = cl[i]
        if st[i] == 0 and st[i - 1] == 1 and entry_px:
            trades.append(cl[i] / entry_px - 1 - 2 * COST_PER_SIDE)
            entry_px = None
    tr = np.array(trades)
    years = pd.to_datetime(df["ts"], unit="ms").dt.year.values
    yearly = []
    for y in sorted(set(years)):
        m = (years == y) & valid
        s_y = np.prod(1 + np.nan_to_num(strat[m])) - 1
        b_y = np.prod(1 + r_next[m]) - 1
        yearly.append((y, s_y, b_y, (st[m] == 1).mean()))
    return dict(sym=sym, n_long=len(long_r), n_flat=len(flat_r),
                mean_long=long_r.mean(), mean_flat=flat_r.mean(), t=t,
                cagr_s=c_s, sh_s=sh_s, dd_s=dd_s, cagr_b=c_b, sh_b=sh_b, dd_b=dd_b,
                trades=tr, yearly=yearly, start=pd.to_datetime(df["ts"].iloc[0], unit="ms").date(),
                end=pd.to_datetime(df["ts"].iloc[-1], unit="ms").date())


def main():
    ex = ccxt.binance({"enableRateLimit": True})
    res = {}
    for s in SYMBOLS:
        res[s] = run(s, fetch_daily(ex, s))
    for s, r in res.items():
        print("=== %s  %s ~ %s ===" % (s, r["start"], r["end"]))
        print("擇時檢驗:LONG日 n=%d 平均日報酬%+.4f%% | FLAT日 n=%d 平均%+.4f%% | Welch t=%+.2f" % (
            r["n_long"], r["mean_long"] * 100, r["n_flat"], r["mean_flat"] * 100, r["t"]))
        print("淨績效  策略: CAGR%+.1f%% 夏普%.2f 最大回撤%.1f%%  |  買進持有: CAGR%+.1f%% 夏普%.2f 最大回撤%.1f%%" % (
            r["cagr_s"] * 100, r["sh_s"], r["dd_s"] * 100, r["cagr_b"] * 100, r["sh_b"], r["dd_b"] * 100))
        tr = r["trades"]
        if len(tr):
            print("交易(描述用):%d 筆, 勝率%.0f%%, 平均每筆淨%+.1f%%" % (len(tr), (tr > 0).mean() * 100, tr.mean() * 100))
        print("逐年(策略淨 / 買進持有 / 持倉比例):")
        for y, sy, by, inm in r["yearly"]:
            print("  %d  %+7.1f%%  %+7.1f%%  持倉%.0f%%" % (y, sy * 100, by * 100, inm * 100))
        print()
    b, e = res["BTC/USDT"], res["ETH/USDT"]
    c1 = b["t"] > T_PRIMARY
    c2 = e["t"] > T_REPLICATE
    c3 = (abs(b["dd_s"]) <= DD_RATIO_MAX * abs(b["dd_b"])) and (b["sh_s"] >= b["sh_b"])
    print("=== H2 判定(預先登記) ===")
    print("① BTC 擇時 t>%.1f: %s (t=%+.2f)" % (T_PRIMARY, "✅" if c1 else "❌", b["t"]))
    print("② ETH 重複驗證 t>%.2f: %s (t=%+.2f)" % (T_REPLICATE, "✅" if c2 else "❌", e["t"]))
    print("③ 風險閘門(回撤≤0.6倍買進持有 且 夏普≥買進持有): %s (回撤比 %.2f, 夏普 %.2f vs %.2f)" % (
        "✅" if c3 else "❌", abs(b["dd_s"]) / abs(b["dd_b"]), b["sh_s"], b["sh_b"]))
    print("結論:%s" % ("值得追蹤(三項全成立)" if (c1 and c2 and c3) else "不被支持(未全數成立),依規則結案,不換參數救"))


if __name__ == "__main__":
    main()
