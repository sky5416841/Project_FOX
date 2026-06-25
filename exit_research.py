"""
出場規則研究 —— 進場固定(接刀)，只變出場，看中性進場能否被好出場救起來

門檻掃描發現：最寬的接刀進場(RSI<30+爆量>150)固定持有10根 ≈ +0.08%/筆(t=1.85,
差一點點顯著正)；但真實引擎用移動停利卻 -34%。→ 線索在『出場』。

本檔固定進場、測一整組出場規則(固定持有 + 各種 TP/SL 盈虧比)，全部原樣列出。
判讀：要『一整類出場都正』才可信；某單一設定剛好跳正 = 多重比較的雜訊。

★ 無未來函數：進場只用截至 t 的資料；TP/SL 逐根往後走、同根都中時保守算 SL 先到。
"""
import ccxt
import numpy as np
from score_backtest import calc_rsi, fetch_paged, SYMBOLS, COST_PCT

RSI_LONG, VOL_MIN, TREND_BLOCK = 30.0, 150.0, 3.0
MAXBARS = 48                      # TP/SL 最長等 48 根(12h)沒中就以收盤出
FIXED_HOLDS = [5, 10, 20, 30]
TPSL = [(2, 1), (3, 1.5), (4, 2), (1, 1), (2, 2), (3, 3)]   # (停利%, 停損%)


def entries_of(o):
    """回傳接刀進場的 t 索引(無未來函數，無評分門檻=最寬)。"""
    C, V = o[:, 4], o[:, 5]
    idx = []
    for t in range(60, len(o) - MAXBARS - 1):
        win = C[max(0, t - 99):t + 1]
        if not (calc_rsi(win) < RSI_LONG):
            continue
        avg5 = V[t - 5:t].mean()
        if not (avg5 > 0 and V[t] / avg5 * 100 > VOL_MIN):
            continue
        ma_s = C[t - 9:t + 1].mean(); ma_l = C[t - 49:t + 1].mean()
        if (ma_s - ma_l) / ma_l * 100 < -TREND_BLOCK:
            continue
        idx.append(t)
    return idx


def ret_fixed(o, t, n):
    C = o[:, 4]
    return (C[t + n] - C[t]) / C[t] * 100 - COST_PCT


def ret_tpsl(o, t, tp, sl):
    H, L, C = o[:, 2], o[:, 3], o[:, 4]
    entry = C[t]
    tp_px = entry * (1 + tp / 100); sl_px = entry * (1 - sl / 100)
    for k in range(t + 1, min(t + 1 + MAXBARS, len(o))):
        if L[k] <= sl_px:                       # 同根都中→保守算停損先到
            return -sl - COST_PCT
        if H[k] >= tp_px:
            return tp - COST_PCT
    return (C[min(t + MAXBARS, len(o) - 1)] - entry) / entry * 100 - COST_PCT


def stat(label, rets):
    a = np.array(rets); n = len(a)
    if n < 2:
        print(f"{label:<16} 樣本太少"); return
    m = a.mean(); se = a.std() / np.sqrt(n); t = m / se if se else 0
    wr = (a > 0).mean() * 100
    flag = "✅正" if t > 1.96 else "❌負" if t < -1.96 else "⚪不顯著"
    print(f"{label:<16} n={n:<4} 勝率={wr:4.0f}%  淨={m:+.2f}%/筆  t={t:+.2f}  {flag}")


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    data = {}
    for s in SYMBOLS:
        try:
            o = fetch_paged(ex, s)
            data[s] = (o, entries_of(o))
        except Exception as e:
            print(f"  {s} 失敗：{e}")
    ne = sum(len(e) for _, e in data.values())
    print(f"=== 出場規則研究 | {len(data)}幣 15m | 接刀進場 {ne} 筆 | 成本{COST_PCT}% ===\n")

    print("── 固定持有 N 根 ──")
    for n in FIXED_HOLDS:
        rets = [ret_fixed(o, t, n) for o, es in data.values() for t in es if t + n < len(o)]
        stat(f"持有{n}根", rets)

    print("\n── 停利/停損 (盈虧比) ──")
    for tp, sl in TPSL:
        rets = [ret_tpsl(o, t, tp, sl) for o, es in data.values() for t in es]
        stat(f"TP+{tp}%/SL-{sl}%", rets)

    print("\n判讀：要『一整類出場都正且顯著』才算數；只有單一設定跳正 = 雜訊。")


if __name__ == "__main__":
    main()
