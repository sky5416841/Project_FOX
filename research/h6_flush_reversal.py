# -*- coding: utf-8 -*-
"""H6(預先登記於 HYPOTHESES.md,2026-11-01):爆倉洗盤後的反彈做多歷史回測。
規則、門檻、樣本、報酬定義全部以 HYPOTHESES.md 為準,本檔不得調整。
- 訊號:24h(6根4h)跌幅 / 過去90天(540根)同指標標準差 = z <= -2.5。
- 進場:下一根開盤;停損=近6根最低-0.5*ATR14;持有6根收盤出場,不設停利。
- 資料:Binance現貨BTC/ETH 4h,只用 ts < 2026-10-01 00:00 UTC。主檢驗淨R單尾t>3.0,n>=60,只跑一次。
"""
import warnings; warnings.filterwarnings("ignore")
import os, time, json, urllib.request
import numpy as np, pandas as pd
from scipy import stats

CUTOFF_MS = int(pd.Timestamp("2026-10-01", tz="UTC").timestamp() * 1000)
START_MS = int(pd.Timestamp("2017-08-01", tz="UTC").timestamp() * 1000)
Z_THRESH, LOOK, SIGMA_N, HOLD, SL_ATR = -2.5, 6, 540, 6, 0.5
T_THRESH, N_MIN, TAKER, MAKER = 3.0, 60, 0.0005, 0.0002
HERE = os.path.dirname(os.path.abspath(__file__))


def fetch(sym):
    path = os.path.join(HERE, "_cache_h6_%s_4h.csv" % sym)
    if os.path.exists(path):
        return pd.read_csv(path)
    rows, t = [], START_MS
    while t < CUTOFF_MS:
        url = "https://api.binance.com/api/v3/klines?symbol=%s&interval=4h&startTime=%d&limit=1000" % (sym, t)
        k = json.loads(urllib.request.urlopen(url, timeout=30).read())
        if not k: break
        rows += [[x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4])] for x in k]
        t = k[-1][0] + 4 * 3600 * 1000
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).drop_duplicates("ts")
    df = df[df["ts"] < CUTOFF_MS].reset_index(drop=True)
    df.to_csv(path, index=False)
    return df


def run_symbol(sym):
    df = fetch(sym)
    ts, o, h, l, c = (df[k].to_numpy(float) for k in ("ts", "open", "high", "low", "close"))
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    ret = pd.Series(c).pct_change(LOOK)
    sigma = ret.rolling(SIGMA_N).std().shift(1).to_numpy()   # 只用到 k-1
    ret = ret.to_numpy()
    trades, k = [], max(SIGMA_N + LOOK + 1, 20)
    while k < len(df) - HOLD - 1:
        z = ret[k] / sigma[k] if sigma[k] and not np.isnan(sigma[k]) else np.nan
        if not (z <= Z_THRESH):
            k += 1; continue
        entry = o[k + 1]
        sl = l[k - LOOK + 1:k + 1].min() - SL_ATR * atr[k]
        if not (sl < entry):
            k += 1; continue
        exit_px, e = None, k + HOLD
        for q in range(k + 1, k + 1 + HOLD):
            if o[q] <= sl: exit_px, e = o[q], q; break
            if l[q] <= sl: exit_px, e = sl, q; break
        if exit_px is None: exit_px = c[k + HOLD]
        risk = entry - sl
        gross = (exit_px - entry) / risk
        stop_pct = risk / entry
        trades.append({"symbol": sym, "ts": ts[k], "z": z, "gross_r": gross, "stopped": exit_px <= sl + 1e-9,
                       "ret_pct": (exit_px / entry - 1) * 100,
                       "net_r": gross - 2 * TAKER / stop_pct, "net_r_maker": gross - 2 * MAKER / stop_pct})
        k = e + 1
    print("  %s: %d根4h K棒(%s ~ %s),成交%d筆" % (sym, len(df), pd.to_datetime(ts[0], unit="ms").date(),
          pd.to_datetime(ts[-1], unit="ms").date(), len(trades)), flush=True)
    return pd.DataFrame(trades)


def main():
    tr = pd.concat([run_symbol(s) for s in ("BTCUSDT", "ETHUSDT")], ignore_index=True)
    tr.to_csv(os.path.join(HERE, "h6_flush_trades.csv"), index=False)
    n = len(tr)
    print("\n=== H6 預先登記驗證結果(單次) ===")
    if n < N_MIN:
        print("樣本不足:n=%d < %d → 依登記規則判『樣本不足,無法判定』,不放寬、不重跑。" % (n, N_MIN)); return
    t, _ = stats.ttest_1samp(tr["net_r"], 0, alternative="greater")
    w, lo = tr[tr.gross_r > 0], tr[tr.gross_r <= 0]
    aw, al = w.gross_r.mean(), -lo.gross_r.mean()
    print("n=%d 勝率%.1f%% 平均賺%.2fR 平均賠%.2fR 兩平勝率(未含費)%.1f%% 毛利%+.3fR 淨(吃單)%+.3fR t=%+.2f | 掛單淨%+.3fR" % (
        n, len(w) / n * 100, aw, al, al / (aw + al) * 100, tr.gross_r.mean(), tr.net_r.mean(), t, tr.net_r_maker.mean()))
    print("觸停損%d筆(%.1f%%) 最大單筆虧損%.2fR 平均報酬%+.2f%%" % (tr.stopped.sum(), tr.stopped.mean() * 100, tr.net_r.min(), tr.ret_pct.mean()))
    yr = pd.to_datetime(tr.ts, unit="ms").dt.year
    for name, g in (("2021以前", tr[yr <= 2021]), ("2022起", tr[yr >= 2022])):
        print("  %s n=%d 勝率%.1f%% 淨%+.3fR" % (name, len(g), (g.gross_r > 0).mean() * 100 if len(g) else 0, g.net_r.mean() if len(g) else 0))
    for s, g in tr.groupby("symbol"):
        print("  %s n=%d 勝率%.1f%% 淨%+.3fR" % (s, len(g), (g.gross_r > 0).mean() * 100, g.net_r.mean()))
    print("判定:", "✅ 通過(t>%.1f 且淨>0)" % T_THRESH if (t > T_THRESH and tr.net_r.mean() > 0) else "❌ 不通過(依規則結案,不換參數救)")

if __name__ == "__main__":
    main()
