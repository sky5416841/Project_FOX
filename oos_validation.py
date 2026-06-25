"""
樣本外驗證 (Out-of-Sample) —— 「接刀進場 + 持有20根」是真的還是鬼影？

我們在『最近約 41 天的 15m』上找到它(t=2.37 顯著正)。但今天測了幾十個東西，
撞到一個顯著值很可能是多重比較的運氣。真章在『換沒看過的資料還在不在』。

兩個檢驗(全部原樣列出，不挑)：
  1. 時間切分：同一批 15m 歷史切兩半，比『較舊半段(樣本外)』vs『較新半段(原樣本)』
  2. 換時間框：5m / 30m / 1h 跑同一條規則，看是不是 15m 專屬鬼影

規則固定：RSI<30 + 爆量>150 + 趨勢沒太逆 → 進場；持有 20 根 → 出場(不停損)。無未來函數。
"""
import ccxt
import numpy as np
from score_backtest import calc_rsi, SYMBOLS, COST_PCT

RSI_LONG, VOL_MIN, TREND_BLOCK, HOLD = 30.0, 150.0, 3.0, 20
TF_MS = {"5m": 5*60*1000, "15m": 15*60*1000, "30m": 30*60*1000, "1h": 60*60*1000}


def fetch_tf(ex, sym, tf, pages=4, per=1000):
    ms = TF_MS[tf]; now = ex.milliseconds(); since = now - pages*per*ms
    rows, seen = [], set()
    while since < now:
        ch = ex.fetch_ohlcv(sym, tf, since=since, limit=per)
        if not ch:
            break
        for c in ch:
            if c[0] not in seen:
                seen.add(c[0]); rows.append(c)
        since = ch[-1][0] + ms
        if len(ch) < per:
            break
    rows.sort(key=lambda x: x[0])
    return np.array(rows, dtype=float)


def hold20_rets(o):
    C, V = o[:, 4], o[:, 5]
    rets = []
    for t in range(60, len(o) - HOLD):
        if not (calc_rsi(C[max(0, t-99):t+1]) < RSI_LONG):
            continue
        avg5 = V[t-5:t].mean()
        if not (avg5 > 0 and V[t]/avg5*100 > VOL_MIN):
            continue
        ma_s = C[t-9:t+1].mean(); ma_l = C[t-49:t+1].mean()
        if (ma_s - ma_l)/ma_l*100 < -TREND_BLOCK:
            continue
        rets.append((C[t+HOLD] - C[t])/C[t]*100 - COST_PCT)
    return rets


def stat(label, rets):
    a = np.array(rets); n = len(a)
    if n < 2:
        print(f"{label:<22} 樣本太少"); return
    m = a.mean(); se = a.std()/np.sqrt(n); t = m/se if se else 0
    wr = (a > 0).mean()*100
    flag = "✅正" if t > 1.96 else "❌負" if t < -1.96 else "⚪不顯著"
    print(f"{label:<22} n={n:<4} 勝率={wr:4.0f}%  淨={m:+.2f}%/筆  t={t:+.2f}  {flag}")


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})

    print("=== 樣本外驗證：接刀進場 + 持有20根(不停損) ===\n")
    print("【1. 時間切分(15m，~83天切兩半)】")
    older, newer = [], []
    for s in SYMBOLS:
        try:
            o = fetch_tf(ex, s, "15m", pages=8)
            mid = len(o)//2
            older += hold20_rets(o[:mid])      # 較舊半段 = 樣本外
            newer += hold20_rets(o[mid-60:])   # 較新半段 = 原樣本(留60暖機)
        except Exception as e:
            print(f"  {s} 失敗：{e}")
    stat("較新半段(原樣本)", newer)
    stat("較舊半段(樣本外)", older)

    print("\n【2. 換時間框(各~測同規則)】")
    for tf in ["5m", "30m", "1h"]:
        rets = []
        for s in SYMBOLS:
            try:
                rets += hold20_rets(fetch_tf(ex, s, tf))
            except Exception:
                pass
        stat(f"時間框 {tf}", rets)

    print("\n判讀：樣本外(較舊半段)+其他時間框 若也顯著正且方向一致 → 這東西可能是真的；")
    print("      若樣本外就垮掉/翻負 → 那 t=2.37 是資料探勘的鬼影，乾淨放下。")


if __name__ == "__main__":
    main()
