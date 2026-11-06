# -*- coding: utf-8 -*-
"""H4(預先登記於 HYPOTHESES.md,2026-10-19):Setup C 空頭鏡像歷史回測。
規則、門檻、樣本、報酬定義全部以 HYPOTHESES.md 為準,本檔不得調整。
- 4h CNN cls=="down" 且 conf>=0.90;15m 用 _calc_levels(…,'down');
- 觸發:最近收盤為紅棒、high>=entry*0.998、close<sl;往後最多500根15m先到sl輸/先到tp贏;都沒到剔除。
- 資料:現有快取,只用 ts < 2026-10-01 00:00 UTC(之後為留存樣本,本檔不碰)。
- 主檢驗:BTC+ETH合併淨R單尾t檢驗,t>2.5;最低樣本 n>=60。只跑一次。
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys, time
import numpy as np, pandas as pd
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setupc_backtest as sb
from cv_dataset_gen import WINDOW

CUTOFF_MS = int(pd.Timestamp("2026-10-01", tz="UTC").timestamp() * 1000)
T_THRESH, N_MIN = 2.5, 60
TAKER, MAKER = 0.0005, 0.0002


def run_symbol(sym):
    df15 = sb.fetch_full(None, sym, "15m", 12); df4h = sb.fetch_full(None, sym, "4h", 12)
    df15 = df15[df15["ts"] < CUTOFF_MS].reset_index(drop=True)
    df4h = df4h[df4h["ts"] < CUTOFF_MS].reset_index(drop=True)
    trades, checked = [], 0
    for i in range(WINDOW, len(df15) - WINDOW, sb.STRIDE_15M):
        win15 = df15.iloc[i - WINDOW:i].reset_index(drop=True)
        ts_now = df15["ts"].iloc[i - 1]
        idx4h = df4h[df4h["ts"] <= ts_now].index
        if len(idx4h) < WINDOW: continue
        j = idx4h[-1]
        win4h = df4h.iloc[max(0, j - WINDOW + 1):j + 1].reset_index(drop=True)
        if len(win4h) < WINDOW: continue
        try:
            r4 = sb.classify_df(win4h, "4h"); r15 = sb.classify_df(win15, "15m")
        except Exception:
            continue
        checked += 1
        if not (r4["cls"] == "down" and r4["conf"] >= sb.CONF_MIN): continue
        import cv_predict as cp
        L = cp._levels(win15, "down")
        entry, sl, tp = L["entry"], L["sl"], L["tp"]
        if sl <= entry or tp >= entry: continue
        last = win15.iloc[-1]
        if not (last["close"] < last["open"] and last["high"] >= entry * 0.998 and last["close"] < sl): continue
        result = None
        for _, r in df15.iloc[i:i + 500].iterrows():
            if r["high"] >= sl: result = "loss"; break
            if r["low"] <= tp: result = "win"; break
        if result is None: continue
        stop_pct = (sl - entry) / entry
        rr = (entry - tp) / (sl - entry)
        gross = rr if result == "win" else -1.0
        trades.append({"symbol": sym, "ts": ts_now, "rr": rr, "result": result, "gross_r": gross,
                       "net_r": gross - 2 * TAKER / stop_pct, "net_r_maker": gross - 2 * MAKER / stop_pct})
    print("  %s: 檢查%d個時間點,觸發%d筆" % (sym, checked, len(trades)), flush=True)
    return pd.DataFrame(trades)


def main():
    t0 = time.time()
    tr = pd.concat([run_symbol(s) for s in sb.SYMBOLS], ignore_index=True)
    tr.to_csv("h4_short_backtest_trades.csv", index=False)
    n = len(tr)
    print("\n=== H4 預先登記驗證結果(單次) 耗時%.0f秒 ===" % (time.time() - t0))
    if n < N_MIN:
        print("樣本不足:n=%d < %d → 依登記規則判『樣本不足,無法判定』,不放寬、不重跑。" % (n, N_MIN)); return
    t, _ = stats.ttest_1samp(tr["net_r"], 0, alternative="greater")
    print("n=%d 勝率%.1f%% 平均R:R=%.2f 毛利%+.3fR 淨(吃單)%+.3fR t=%+.2f | 掛單淨%+.3fR" % (
        n, (tr.result == "win").mean() * 100, tr.rr.mean(), tr.gross_r.mean(), tr.net_r.mean(), t, tr.net_r_maker.mean()))
    for s, g in tr.groupby("symbol"):
        print("  %s n=%d 勝率%.1f%% 淨%+.3fR" % (s, len(g), (g.result == "win").mean() * 100, g.net_r.mean()))
    print("判定:", "✅ 通過(t>%.1f 且淨>0)" % T_THRESH if (t > T_THRESH and tr.net_r.mean() > 0) else "❌ 不通過(依規則結案,不換參數救)")

if __name__ == "__main__":
    main()
