# -*- coding: utf-8 -*-
"""進場品質診斷(描述性,非假說檢定):H6 爆倉洗盤進場 vs 隨機4h進場,比較進場後24h內的最大浮虧/浮盈(以ATR為單位)。"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h6_flush_reversal as h6

def feats(sym):
    df = h6.fetch(sym)
    ts, o, h, l, c = (df[k].to_numpy(float) for k in ("ts", "open", "high", "low", "close"))
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    ret = pd.Series(c).pct_change(6)
    sig = ret.rolling(540).std().shift(1).to_numpy(); ret = ret.to_numpy()
    allr, flush, k = [], [], 560
    N = len(df) - 8
    def rec(k):
        e = o[k + 1]; a = atr[k]
        lo = l[k + 1:k + 7].min(); hi = h[k + 1:k + 7].max()
        return dict(sym=sym, k=k, mae=(e - lo) / a, mfe=(hi - e) / a, ret=c[k + 6] / e - 1, atr_pct=a / e,
                    brk=lo < l[k - 5:k + 1].min(), gap=(e - l[k - 5:k + 1].min()) / a,
                    path=[(l[q], h[q]) for q in range(k + 1, k + 7)], e=e, a=a, cl=c[k + 6])
    for kk in range(560, N):
        if not np.isnan(atr[kk]): allr.append(rec(kk))
    while k < N:
        if sig[k] and not np.isnan(sig[k]) and ret[k] / sig[k] <= -2.5:
            flush.append(rec(k)); k += 7
        else: k += 1
    return pd.DataFrame(allr), pd.DataFrame(flush)

A, F = zip(*[feats(s) for s in ("BTCUSDT", "ETHUSDT")])
A, F = pd.concat(A, ignore_index=True), pd.concat(F, ignore_index=True)
print("樣本: 爆倉進場 %d 筆;對照(所有4h進場) %d 筆\n" % (len(F), len(A)))
q = [.25, .5, .75, .9]
for name, d in (("爆倉洗盤進場", F), ("對照:隨機4h進場", A)):
    print("【%s】進場後24h" % name)
    print("  最大浮虧(ATR) 分位25/50/75/90: " + " / ".join("%.2f" % d.mae.quantile(x) for x in q))
    print("  最大浮盈(ATR) 分位25/50/75/90: " + " / ".join("%.2f" % d.mfe.quantile(x) for x in q))
    print("  24h報酬 平均%+.2f%% 中位%+.2f%%  | 同期ATR占價格 %.2f%%" % (d.ret.mean() * 100, d.ret.median() * 100, d.atr_pct.mean() * 100))
    if name.startswith("爆倉"):
        print("  進場後24h內再破這輪洗盤最低點(接刀): %.1f%%" % (d.brk.mean() * 100))
        print("  進場價離洗盤最低點的距離(ATR) 中位: %.2f" % d.gap.median())
    print()
print("【停損放多遠】進場後固定停損 m×ATR、持有24h(扣0.10%來回費)")
print("  m    | 爆倉進場: 被掃%  平均毛R  平均淨R | 隨機進場: 被掃%  平均毛R  平均淨R")
for m in (0.5, 1.0, 1.5, 2.0, 3.0):
    out = []
    for d in (F, A):
        stop = (d.mae >= m)
        # 被掃以停損價出場(-1R);否則24h收盤
        g = np.where(stop, -1.0, (d.cl - d.e) / (m * d.a))
        n = g - 0.001 / (m * d.a / d.e)
        out += [stop.mean() * 100, g.mean(), n.mean()]
    print("  %.1f  |   %5.1f%%  %+.3f  %+.3f   |   %5.1f%%  %+.3f  %+.3f" % ((m,) + tuple(out)))
