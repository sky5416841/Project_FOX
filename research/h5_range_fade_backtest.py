# -*- coding: utf-8 -*-
"""H5(預先登記於 HYPOTHESES.md,2026-11-01):盤整環境區間邊緣反轉歷史回測。
規則、門檻、樣本、報酬定義全部以 HYPOTHESES.md 為準,本檔不得調整。
- 環境:4h CNN cls=="range" 且 conf>=0.90(只用「已收完」的4h K棒,避免前瞻)。
- 區間:最近96根已收15m的 hi/lo;w>=3*ATR14(15m,TR簡單平均)才交易。
- 做多:綠棒、low<=lo+0.15w、close<mid → 進場=收盤,停損=近3根最低-0.5ATR,停利=mid。做空鏡像。
- 每幣同時一筆;往後最多200根15m;同根雙碰算停損;200根未碰→收盤平倉照算(不剔除)。
- 資料:現有快取,只用 ts < 2026-10-01 00:00 UTC(之後為留存樣本,本檔不碰)。
- 主檢驗:BTC+ETH合併淨R單尾t檢驗,t>3.0;最低樣本 n>=60。只跑一次。
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys, time
import numpy as np, pandas as pd
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setupc_backtest as sb
from cv_dataset_gen import WINDOW

CUTOFF_MS = int(pd.Timestamp("2026-10-01", tz="UTC").timestamp() * 1000)
T_THRESH, N_MIN = 3.0, 60
TAKER, MAKER = 0.0005, 0.0002
RANGE_N, EDGE, W_ATR, SL_ATR, HOLD = 96, 0.15, 3.0, 0.5, 200
M15, H4 = 15 * 60_000, 4 * 60 * 60_000


def run_symbol(sym):
    df15 = sb.fetch_full(None, sym, "15m", 12); df4h = sb.fetch_full(None, sym, "4h", 12)
    df15 = df15[df15["ts"] < CUTOFF_MS].reset_index(drop=True)
    df4h = df4h[df4h["ts"] < CUTOFF_MS].reset_index(drop=True)
    ts, o, h, l, c = (df15[k].to_numpy(float) for k in ("ts", "open", "high", "low", "close"))
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    ends4h = df4h["ts"].to_numpy(float) + H4
    cls_cache, trades, i, n_range = {}, [], RANGE_N, 0
    while i < len(df15) - HOLD:
        k = i - 1
        hi, lo = h[i - RANGE_N:i].max(), l[i - RANGE_N:i].min()
        w, mid = hi - lo, (hi + lo) / 2
        side = None
        if not np.isnan(atr[k]) and w >= W_ATR * atr[k]:
            if c[k] > o[k] and l[k] <= lo + EDGE * w and c[k] < mid: side = "long"
            elif c[k] < o[k] and h[k] >= hi - EDGE * w and c[k] > mid: side = "short"
        if side is None:
            i += 1; continue
        j = np.searchsorted(ends4h, ts[k] + M15, side="right") - 1   # 已收完的最後一根4h
        if j + 1 < WINDOW:
            i += 1; continue
        if j not in cls_cache:
            try:
                r4 = sb.classify_df(df4h.iloc[j - WINDOW + 1:j + 1].reset_index(drop=True), "4h")
                cls_cache[j] = (r4["cls"], r4["conf"])
            except Exception:
                cls_cache[j] = (None, 0.0)
        cl, cf = cls_cache[j]
        if not (cl == "range" and cf >= sb.CONF_MIN):
            i += 1; continue
        n_range += 1
        entry = c[k]
        if side == "long":
            sl = l[i - 3:i].min() - SL_ATR * atr[k]; tp = mid
            if not (sl < entry < tp): i += 1; continue
        else:
            sl = h[i - 3:i].max() + SL_ATR * atr[k]; tp = mid
            if not (tp < entry < sl): i += 1; continue
        risk = abs(entry - sl); exit_px, why, e = None, "timeout", i + HOLD - 1
        for q in range(i, i + HOLD):
            hit_sl = (l[q] <= sl) if side == "long" else (h[q] >= sl)
            hit_tp = (h[q] >= tp) if side == "long" else (l[q] <= tp)
            if hit_sl: exit_px, why, e = sl, "loss", q; break
            if hit_tp: exit_px, why, e = tp, "win", q; break
        if exit_px is None: exit_px = c[i + HOLD - 1]
        gross = ((exit_px - entry) if side == "long" else (entry - exit_px)) / risk
        stop_pct = risk / entry
        trades.append({"symbol": sym, "side": side, "ts": ts[k], "rr": abs(tp - entry) / risk, "result": why,
                       "gross_r": gross, "bars": e - i + 1,
                       "net_r": gross - 2 * TAKER / stop_pct, "net_r_maker": gross - 2 * MAKER / stop_pct})
        i = e + 1
    print("  %s: 通過區間條件且4h=range 的訊號%d個,成交%d筆" % (sym, n_range, len(trades)), flush=True)
    return pd.DataFrame(trades)


def main():
    t0 = time.time()
    tr = pd.concat([run_symbol(s) for s in sb.SYMBOLS], ignore_index=True)
    tr.to_csv("h5_range_fade_trades.csv", index=False)
    n = len(tr)
    print("\n=== H5 預先登記驗證結果(單次) 耗時%.0f秒 ===" % (time.time() - t0))
    if n < N_MIN:
        print("樣本不足:n=%d < %d → 依登記規則判『樣本不足,無法判定』,不放寬、不重跑。" % (n, N_MIN)); return
    t, _ = stats.ttest_1samp(tr["net_r"], 0, alternative="greater")
    wr = (tr.gross_r > 0).mean()
    be = 1 / (1 + tr.rr.mean())  # 以平均R:R估的兩平勝率(未含費)
    print("n=%d 勝率%.1f%% 平均R:R=%.2f 兩平勝率(未含費)%.1f%% 毛利%+.3fR 淨(吃單)%+.3fR t=%+.2f | 掛單淨%+.3fR" % (
        n, wr * 100, tr.rr.mean(), be * 100, tr.gross_r.mean(), tr.net_r.mean(), t, tr.net_r_maker.mean()))
    print("逾時%d筆(%.1f%%) 平均持有%.0f根15m" % ((tr.result == "timeout").sum(), (tr.result == "timeout").mean() * 100, tr.bars.mean()))
    for s, g in tr.groupby("symbol"):
        print("  %s n=%d 勝率%.1f%% 淨%+.3fR" % (s, len(g), (g.gross_r > 0).mean() * 100, g.net_r.mean()))
    for s, g in tr.groupby("side"):
        print("  %s n=%d 勝率%.1f%% 淨%+.3fR" % (s, len(g), (g.gross_r > 0).mean() * 100, g.net_r.mean()))
    print("判定:", "✅ 通過(t>%.1f 且淨>0)" % T_THRESH if (t > T_THRESH and tr.net_r.mean() > 0) else "❌ 不通過(依規則結案,不換參數救)")

if __name__ == "__main__":
    main()
