"""
score>=60 進場規則的「歷史回測」—— 不用等兩個月，幾分鐘給答案

把 engine_core 的多單進場邏輯 100% 搬到歷史資料上：
  Long 進場 = RSI<30 且 爆量(vol_surge)>150 且 趨勢沒太逆(trend_gap>=-3)
             且 共振評分 score>=60
全部用「截至第 t 根」的資料計算（★無未來函數），15m 線、RSI14、
vol_surge=當前量/前5均量、MA10/MA50 趨勢、上影線比例 —— 與引擎一致。

出場用固定持有 N 根（純測『進場品質』；引擎實際用 ATR 移動停利，但若進場本身
無前向 edge，再好的出場也救不了）。看扣費後淨期望 vs 隨機進場對照。
"""
import time
import ccxt
import numpy as np

# ── 與 engine_core 一致的常數/函式（複製，自包含不動引擎）──
RSI_LONG, VOL_MIN, MIN_SCORE, TREND_BLOCK = 30.0, 150.0, 60, 3.0
COST_PCT = 0.20
HORIZONS = [10, 20]
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT",
           "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "LTC/USDT", "TRX/USDT"]


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return float("nan")
    deltas = np.diff(closes)
    gains = np.clip(deltas, 0, None); losses = np.clip(-deltas, 0, None)
    ag = gains[:period].mean(); al = losses[:period].mean()
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def resonance_long(rsi, wick_pct, vol_surge):
    s = 0
    s += 40 if rsi < 15 else 30 if rsi < 20 else 20 if rsi < 25 else 10
    s += 35 if wick_pct >= 85 else 25 if wick_pct >= 75 else 15 if wick_pct >= 65 else 5 if wick_pct >= 60 else 0
    s += 25 if vol_surge >= 400 else 20 if vol_surge >= 300 else 15 if vol_surge >= 200 else 10 if vol_surge >= 150 else 0
    return min(s, 100)


def fetch_paged(ex, sym, pages=4, per=1000, tf="15m"):
    tf_ms = 15 * 60 * 1000
    now = ex.milliseconds()
    since = now - pages * per * tf_ms
    rows, seen = [], set()
    while since < now:
        chunk = ex.fetch_ohlcv(sym, tf, since=since, limit=per)
        if not chunk:
            break
        for c in chunk:
            if c[0] not in seen:
                seen.add(c[0]); rows.append(c)
        since = chunk[-1][0] + tf_ms
        if len(chunk) < per:
            break
    rows.sort(key=lambda x: x[0])
    return np.array(rows, dtype=float)


def signals_long(o):
    """回傳所有 score>=60 多單進場的 t 索引（無未來函數）。"""
    O, H, L, C, V = o[:, 1], o[:, 2], o[:, 3], o[:, 4], o[:, 5]
    out = []
    for t in range(60, len(o)):
        win = C[max(0, t - 99):t + 1]               # 與引擎一致：最近 100 根
        rsi = calc_rsi(win)
        if not (rsi < RSI_LONG):
            continue
        avg5 = V[t - 5:t].mean()
        vol_surge = V[t] / avg5 * 100 if avg5 > 0 else 0
        if not (vol_surge > VOL_MIN):
            continue
        ma_s = C[t - 9:t + 1].mean(); ma_l = C[t - 49:t + 1].mean()
        trend_gap = (ma_s - ma_l) / ma_l * 100 if ma_l > 0 else 0
        if trend_gap < -TREND_BLOCK:                 # 趨勢濾網擋逆勢
            continue
        tot = H[t] - L[t]
        wick = (H[t] - max(O[t], C[t])) / tot * 100 if tot > 0 else 0
        if resonance_long(rsi, wick, vol_surge) >= MIN_SCORE:
            out.append(t)
    return out


def stat(label, rets):
    n = len(rets)
    if n == 0:
        print(f"{label:<22} 無觸發"); return
    a = np.array(rets); m = a.mean(); se = a.std() / np.sqrt(n); t = m / se if se else 0
    wr = (a > 0).mean() * 100
    flag = "✅有edge" if t > 1.96 else ("❌負" if t < -1.96 else "⚪不顯著")
    print(f"{label:<22} 筆數={n:<4} 勝率={wr:4.0f}%  淨={m:+.2f}%/筆  95%CI[{m-1.96*se:+.2f},{m+1.96*se:+.2f}]  t={t:+.2f}  {flag}")


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    data = {}
    for s in SYMBOLS:
        try:
            data[s] = fetch_paged(ex, s)
        except Exception as e:
            print(f"  {s} 失敗：{e}")
    total_bars = sum(len(o) for o in data.values())
    print(f"=== score>=60 多單進場 歷史回測 | {len(data)}幣 15m 共{total_bars}根 | 成本{COST_PCT}% ===")
    print("（引擎進場邏輯 100% 重建、無未來函數）\n")

    for H in HORIZONS:
        sig, base = [], []
        nsig = 0
        for s, o in data.items():
            C = o[:, 4]
            ts = signals_long(o)
            nsig += len(ts)
            for t in ts:
                if t + H < len(o):
                    sig.append((C[t + H] - C[t]) / C[t] * 100 - COST_PCT)
            for t in range(60, len(o) - H):
                base.append((C[t + H] - C[t]) / C[t] * 100 - COST_PCT)
        print(f"── 持有 {H} 根 K（共偵測到 {nsig} 個 score>=60 進場）──")
        stat("score>=60 進場做多", sig)
        stat("對照:每根都做多", base)
        print()
    print("判讀：score>=60 進場要『顯著為正』且明顯贏過對照組，才值得花兩個月實盤前向驗證；")
    print("      若這裡就顯著為負/打平，代表沒必要等——直接知道沒 edge。")


if __name__ == "__main__":
    main()
