"""
純 ROP 看空訊號回測(基準)—— 沒有多時框/POI 過濾的 ROP，到底有多慘？

進場：偵測到看空 ROP(前根長上影線>=50% + 當根陰線破前根實體)→ 收盤做空。
風控(用策略自己的結構)：
  · 停損 SL = 前根長上影線最高點(High) + buffer
  · 停利 TP = 進場價 − 2R（R = 進場到停損的距離，對齊影片 2.27R 風格）
無未來函數；同根都中時保守算停損先到；超過 MAXBARS 沒中以收盤出。扣來回成本。

★ 這是「沒過濾」的基準。預期慘(ROP 在上漲段也亂響→盲空)。下一步加 POI/多時框過濾再比。
"""
import ccxt
import numpy as np

SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
           "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT"]
TF, PAGES, PER = "1m", 5, 1000     # ~5000 根 1m ≈ 3.5 天/幣
MAXBARS, COST_PCT, BUFFER = 60, 0.10, 0.0005


def fetch(ex, sym):
    ms = 60 * 1000; now = ex.milliseconds(); since = now - PAGES * PER * ms
    rows, seen = [], set()
    while since < now:
        ch = ex.fetch_ohlcv(sym, TF, since=since, limit=PER)
        if not ch:
            break
        for c in ch:
            if c[0] not in seen:
                seen.add(c[0]); rows.append(c)
        since = ch[-1][0] + ms
        if len(ch) < PER:
            break
    rows.sort(key=lambda x: x[0])
    return np.array(rows, dtype=float)


def rop_short_returns(o):
    O, H, L, C = o[:, 1], o[:, 2], o[:, 3], o[:, 4]
    rets = []
    for t in range(1, len(o) - MAXBARS - 1):
        rng = H[t-1] - L[t-1]
        if rng <= 0:
            continue
        upper = H[t-1] - max(O[t-1], C[t-1])
        if upper / rng < 0.50:                       # 條件B：長上影線
            continue
        if not (C[t] < O[t] and C[t] < min(O[t-1], C[t-1])):   # 條件C：陰線破前根實體
            continue
        entry = C[t]; sl = H[t-1] * (1 + BUFFER)     # 停損在長影線高點上方
        risk = sl - entry
        if risk <= 0:
            continue
        tp = entry - 2 * risk                        # 停利 2R(做空往下)
        ret = None
        for k in range(t + 1, t + 1 + MAXBARS):
            if H[k] >= sl:                           # 先碰停損(保守)
                ret = -(risk / entry) * 100 - COST_PCT; break
            if L[k] <= tp:
                ret = (2 * risk / entry) * 100 - COST_PCT; break
        if ret is None:
            ret = (entry - C[t + MAXBARS]) / entry * 100 - COST_PCT
        rets.append(ret)
    return rets


def stat(label, rets):
    n = len(rets)
    if n < 2:
        print(f"{label:<16} 樣本太少({n})"); return
    a = np.array(rets); m = a.mean(); se = a.std() / np.sqrt(n); t = m / se if se else 0
    wr = (a > 0).mean() * 100
    flag = "✅正" if t > 1.96 else "❌負" if t < -1.96 else "⚪不顯著"
    print(f"{label:<16} n={n:<4} 勝率={wr:4.0f}%  淨={m:+.2f}%/筆  t={t:+.2f}  {flag}")


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    allr, bars = [], 0
    print(f"=== 純 ROP 看空回測(無過濾) | {TF} | SL=影線高/TP=2R | 成本{COST_PCT}% ===\n")
    for s in SYMBOLS:
        try:
            o = fetch(ex, s); bars += len(o)
            r = rop_short_returns(o); allr += r
            stat(s.split("/")[0], r)
        except Exception as e:
            print(f"  {s} 失敗：{e}")
    print("-" * 50)
    print(f"共掃 {bars} 根 1m")
    stat("★ 全部彙總", allr)
    print("\n判讀：純 ROP(無 POI/多時框過濾)預期為負/不顯著 —— 這是基準。")
    print("      下一步加上多時框 bias + POI 過濾，看能不能把它救起來。")


if __name__ == "__main__":
    main()
