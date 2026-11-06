# -*- coding: utf-8 -*-
"""H7(登記於 HYPOTHESES.md,2026-11-01):爆倉洗盤後不設停損買現貨、持有24h 的前瞻驗證。
未滿 N_TARGET 筆只印筆數、不印任何績效;滿了驗一次寫 H7_result.json 並鎖定。規則不得調整。"""
import json, os, time, urllib.request
import numpy as np, pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__)); RESULT = os.path.join(HERE, "H7_result.json")
FWD_MS = int(pd.Timestamp("2026-10-01", tz="UTC").timestamp() * 1000)
N_TARGET, T_THRESH, FEE = 40, 2.0, 0.0020
Z, LOOK, SIGMA_N, HOLD = -2.5, 6, 540, 6


def fetch(sym):
    t, rows = int(pd.Timestamp("2026-04-01", tz="UTC").timestamp() * 1000), []
    while True:
        k = json.loads(urllib.request.urlopen(
            "https://api.binance.com/api/v3/klines?symbol=%s&interval=4h&startTime=%d&limit=1000" % (sym, t), timeout=30).read())
        if not k: break
        rows += [[x[0], float(x[1]), float(x[4])] for x in k]
        if len(k) < 1000: break
        t = k[-1][0] + 1; time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["ts", "open", "close"]).drop_duplicates("ts").reset_index(drop=True)
    return df.iloc[:-1]  # 丟掉未收完的最後一根


def trades(sym):
    df = fetch(sym); ts, o, c = (df[x].to_numpy(float) for x in ("ts", "open", "close"))
    ret = pd.Series(c).pct_change(LOOK); sig = ret.rolling(SIGMA_N).std().shift(1).to_numpy(); r = ret.to_numpy()
    done, pend, k = [], 0, SIGMA_N + LOOK
    while k < len(df) - 1:
        if ts[k] >= FWD_MS and sig[k] > 0 and r[k] / sig[k] <= Z:
            if k + HOLD < len(df):
                done.append((sym, ts[k], c[k + HOLD] / o[k + 1] - 1 - FEE)); k += HOLD + 1; continue
            pend += 1; break
        k += 1
    return done, pend


def main():
    if os.path.exists(RESULT):
        print("H7 已驗證過並鎖定:", open(RESULT, encoding="utf-8").read()); return
    done, pend = [], 0
    for s in ("BTCUSDT", "ETHUSDT"):
        d, p = trades(s); done += d; pend += p
    done.sort(key=lambda x: x[1]); n = len(done)
    print("H7 前瞻樣本:已結束 %d / %d 筆(未結束 %d 筆);滿筆數前不顯示績效。" % (n, N_TARGET, pend))
    if n < N_TARGET: return
    x = np.array([d[2] for d in done[:N_TARGET]]); t, _ = stats.ttest_1samp(x, 0, alternative="greater")
    res = {"n": N_TARGET, "mean_net_pct": round(x.mean() * 100, 3), "t": round(float(t), 3), "win_rate": round(float((x > 0).mean()), 3),
           "worst_pct": round(x.min() * 100, 2), "passed": bool(t > T_THRESH and x.mean() > 0)}
    json.dump(res, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=1); print("驗證結果(單次、已鎖定):", res)

if __name__ == "__main__":
    main()
