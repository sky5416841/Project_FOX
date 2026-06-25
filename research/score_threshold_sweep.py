"""
評分門檻掃描 —— 驗證「是不是門檻調太嚴? 放寬會不會更好?」

使用者觀察：score>=60 在 48000 根裡只觸發 ~24 次，太稀有不實用。
合理假設：放寬門檻 → 觸發更多 → 是否更好？

本檔在同一批歷史資料上，從寬(score>=0)到嚴(score>=70)各門檻都測一次，
看『觸發次數 vs 扣費後淨期望』的取捨關係。用數據回答，不靠嘴。
"""
import ccxt
import numpy as np
from score_backtest import calc_rsi, resonance_long, fetch_paged, SYMBOLS, COST_PCT

RSI_LONG, VOL_MIN, TREND_BLOCK = 30.0, 150.0, 3.0
HORIZON = 10
THRESHOLDS = [0, 20, 40, 50, 60, 70]


def all_long_entries(o):
    """回傳每根的 (score, 未來HORIZON根淨報酬%)，只在『RSI<30+爆量>150+趨勢沒太逆』時。"""
    O, H, L, C, V = o[:, 1], o[:, 2], o[:, 3], o[:, 4], o[:, 5]
    out = []
    for t in range(60, len(o) - HORIZON):
        win = C[max(0, t - 99):t + 1]
        rsi = calc_rsi(win)
        if not (rsi < RSI_LONG):
            continue
        avg5 = V[t - 5:t].mean()
        vol_surge = V[t] / avg5 * 100 if avg5 > 0 else 0
        if not (vol_surge > VOL_MIN):
            continue
        ma_s = C[t - 9:t + 1].mean(); ma_l = C[t - 49:t + 1].mean()
        if (ma_s - ma_l) / ma_l * 100 < -TREND_BLOCK:
            continue
        tot = H[t] - L[t]
        wick = (H[t] - max(O[t], C[t])) / tot * 100 if tot > 0 else 0
        score = resonance_long(rsi, wick, vol_surge)
        ret = (C[t + HORIZON] - C[t]) / C[t] * 100 - COST_PCT
        out.append((score, ret))
    return out


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    entries = []
    bars = 0
    for s in SYMBOLS:
        try:
            o = fetch_paged(ex, s)
            bars += len(o)
            entries += all_long_entries(o)
        except Exception as e:
            print(f"  {s} 失敗：{e}")
    print(f"=== 評分門檻掃描 | {len(SYMBOLS)}幣 15m 共{bars}根 | 持有{HORIZON}根 成本{COST_PCT}% ===\n")
    print(f"{'門檻':>8}{'觸發次數':>10}{'淨期望%/筆':>12}{'t值':>8}{'判定':>10}")
    arr = np.array(entries)  # (n,2): score, ret
    for thr in THRESHOLDS:
        sub = arr[arr[:, 0] >= thr][:, 1]
        n = len(sub)
        if n < 2:
            print(f"{thr:>7}+{n:>10}      樣本太少"); continue
        m = sub.mean(); se = sub.std() / np.sqrt(n); t = m / se if se else 0
        flag = "✅正" if t > 1.96 else "❌負" if t < -1.96 else "⚪不顯著"
        print(f"{thr:>7}+{n:>10}{m:>+11.2f}{t:>+8.2f}{flag:>10}")
    print("\n判讀：看『放寬門檻(往上)觸發變多，但淨期望有沒有變正』。")
    print("      若每個門檻都負/不顯著 → 不是門檻問題，是這個訊號本身沒 edge，調門檻救不了。")


if __name__ == "__main__":
    main()
