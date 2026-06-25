"""
SMC 決定性測試:多時框 bias + POI 過濾,能不能把純 ROP(-0.07%, t=-12)救起來？

SMC 核心主張:edge 不在形態,在「只在對的 context(大時框看空 + 回到 POI)才扣扳機」。
本檔逐層加過濾、原樣比較,一槍定生死:
  ① 純 ROP(基準)
  ② ROP + 15m 看空 bias(15m 收盤 < 15m SMA50)
  ③ ROP + 15m bias + POI(現價回升到近期 15m 轉折高/壓力 0.3% 內)

進場做空、SL=長影線高、TP=2R、無未來函數(1m 訊號只用『已收盤的 15m』context)、扣費。
"""
import ccxt
import numpy as np

SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
           "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT"]
P1M, P15 = 5, 1
MAXBARS, COST, BUFFER = 60, 0.10, 0.0005
SMA_LEN, PIVOT_K, POI_NEAR, POI_LB = 50, 5, 0.003, 20
MS1, MS15 = 60_000, 15 * 60_000


def fetch(ex, sym, tf, pages, ms):
    now = ex.milliseconds(); since = now - pages * 1000 * ms
    rows, seen = [], set()
    while since < now:
        ch = ex.fetch_ohlcv(sym, tf, since=since, limit=1000)
        if not ch:
            break
        for c in ch:
            if c[0] not in seen:
                seen.add(c[0]); rows.append(c)
        since = ch[-1][0] + ms
        if len(ch) < 1000:
            break
    rows.sort(key=lambda x: x[0])
    return np.array(rows, dtype=float)


def ctx15(o15):
    ts, H, C = o15[:, 0], o15[:, 2], o15[:, 4]
    sma = np.array([C[max(0, i-SMA_LEN+1):i+1].mean() for i in range(len(C))])
    bias = C < sma                                   # 看空 bias
    highs = [i for i in range(PIVOT_K, len(o15)-PIVOT_K) if H[i] == H[i-PIVOT_K:i+PIVOT_K+1].max()]
    return ts, bias, highs, H


def sim_short(o, t):
    """2R 做空,回傳淨報酬%(無未來函數)。"""
    O, H, L, C = o[:, 1], o[:, 2], o[:, 3], o[:, 4]
    entry = C[t]; sl = H[t-1] * (1 + BUFFER); risk = sl - entry
    if risk <= 0:
        return None
    tp = entry - 2 * risk
    for k in range(t+1, t+1+MAXBARS):
        if H[k] >= sl:
            return -(risk/entry)*100 - COST
        if L[k] <= tp:
            return (2*risk/entry)*100 - COST
    return (entry - C[t+MAXBARS])/entry*100 - COST


def stat(label, rets):
    n = len(rets)
    if n < 2:
        print(f"{label:<22} 樣本太少({n})"); return
    a = np.array(rets); m = a.mean(); se = a.std()/np.sqrt(n); t = m/se if se else 0
    wr = (a > 0).mean()*100
    flag = "✅正" if t > 1.96 else "❌負" if t < -1.96 else "⚪不顯著"
    print(f"{label:<22} n={n:<4} 勝率={wr:4.0f}%  淨={m:+.3f}%/筆  t={t:+.2f}  {flag}")


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    raw, bias_f, poi_f = [], [], []
    for s in SYMBOLS:
        try:
            o1 = fetch(ex, s, "1m", P1M, MS1)
            o15 = fetch(ex, s, "15m", P15, MS15)
            ts15, bias15, highs15, H15 = ctx15(o15)
            O, H, L, C, T = o1[:,1], o1[:,2], o1[:,3], o1[:,4], o1[:,0]
            for t in range(1, len(o1)-MAXBARS-1):
                rng = H[t-1]-L[t-1]
                if rng <= 0:
                    continue
                if (H[t-1]-max(O[t-1], C[t-1]))/rng < 0.50:
                    continue
                if not (C[t] < O[t] and C[t] < min(O[t-1], C[t-1])):
                    continue
                r = sim_short(o1, t)
                if r is None:
                    continue
                raw.append(r)
                # 對齊「已收盤的 15m」(無未來函數)
                j = int(np.searchsorted(ts15, T[t], "right")) - 2
                if j < 0:
                    continue
                if not bias15[j]:
                    continue
                bias_f.append(r)
                conf = [hi for hi in highs15 if hi+PIVOT_K <= j and hi >= j-POI_LB]
                if any(C[t] >= H15[hi]*(1-POI_NEAR) for hi in conf):
                    poi_f.append(r)
        except Exception as e:
            print(f"  {s} 失敗：{e}")

    print("=== SMC 決定性測試:逐層過濾(6幣 1m,SL=影線高/TP=2R,扣費) ===\n")
    stat("① 純 ROP(基準)", raw)
    stat("② +15m 看空bias", bias_f)
    stat("③ +bias+POI過濾", poi_f)
    print("\n判讀:過濾後若『顯著轉正』→ SMC 的 context 真的是 edge;")
    print("      若還是負/不顯著(只是筆數變少)→ context 也是雜訊,SMC 主張不成立。")


if __name__ == "__main__":
    main()
