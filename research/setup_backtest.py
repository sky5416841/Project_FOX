"""
setup_backtest.py — 「支撐反彈(震盪盤)」Setup A 實際勝率 vs 公平勝率
==================================================================
回答一個關鍵問題:CNN判震盪 + 支撐反彈 + 確認K + R:R≥1.5 這個「有技術含量」
的 setup,實際勝率是**高於**R:R隱含的公平勝率(=有edge),還是**只是公平銅板**(=沒edge)?

方法(無未來函數、逐根前向追蹤):
  · 震盪定義 = 視窗100根迴歸斜率 ±0.05%/根(與 cv_dataset_gen 標 range 完全一致)
  · 支撐/壓力 = 視窗 low 0.10 / high 0.90 分位數
  · 進場 = 碰到支撐 + 綠K收在支撐上(確認);停損=視窗最低下方;停利=壓力;R:R≥1.5才取
  · 公平勝率 = 風險距離/(風險+報酬)（隨機漫步下先碰哪邊的機率）
  · 扣費 = 來回 2×0.05% 折算成 R

若「實際勝率 ≈ 或 < 公平勝率」→ 公平銅板、無edge、技術含量只給風控不給預測。
"""
import ccxt
import numpy as np
import pandas as pd

EX = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
FEE = 0.0005
W = 100
COINS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT",
         "ADA/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT", "DOT/USDT", "TRX/USDT"]
TFS = ["1h", "4h"]


def fetch(sym, tf, bars=6000):
    rows, since = [], None
    while len(rows) < bars:
        try:
            c = EX.fetch_ohlcv(sym, tf, since=since, limit=1000)
        except Exception:
            break
        if not c:
            break
        rows += c; since = c[-1][0] + 1
        if len(c) < 1000:
            break
    d = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"]).drop_duplicates("ts")
    return d.reset_index(drop=True)


def scan(d):
    """回傳這段資料上所有 Setup A 訊號的結果 list of dict。"""
    n = len(d); out = []; i = W
    hi_a = d["h"].values; lo_a = d["l"].values
    while i < n - 1:
        seg = d.iloc[i - W:i]
        y = seg["c"].values
        slope = np.polyfit(np.arange(W), y, 1)[0] / y.mean() * 100
        if abs(slope) > 0.05:                       # 非震盪→跳
            i += 1; continue
        sup = seg["l"].quantile(0.10); res = seg["h"].quantile(0.90)
        lo_min = seg["l"].min()
        bar = d.iloc[i]
        if not (bar["l"] <= sup and bar["c"] > sup and bar["c"] > bar["o"]):
            i += 1; continue
        entry = bar["c"]; sl = lo_min * 0.999; tp = res
        if entry <= sl:
            i += 1; continue
        rr = (tp - entry) / (entry - sl)
        if rr < 1.5:
            i += 1; continue
        outcome = None
        for j in range(i + 1, n):
            if lo_a[j] <= sl: outcome = 0; break
            if hi_a[j] >= tp: outcome = 1; break
        if outcome is None:
            i += 1; continue
        fair = (entry - sl) / ((entry - sl) + (tp - entry))
        fee_R = 2 * FEE / ((entry - sl) / entry)     # 來回費折成 R
        r_gross = rr if outcome == 1 else -1
        out.append({"win": outcome, "rr": rr, "fair": fair,
                    "r_gross": r_gross, "r_net": r_gross - fee_R})
        i += W // 4                                  # 避免過度重疊
    return out


def main():
    all_t = []
    print(f"掃描 {len(COINS)} 幣 × {TFS} … (無未來函數、逐根前向追蹤)\n")
    for tf in TFS:
        for sym in COINS:
            try:
                d = fetch(sym, tf)
                if len(d) < W + 50:
                    continue
                res = scan(d)
                for r in res:
                    r["mkt"] = f"{sym.split('/')[0]}·{tf}"
                all_t += res
            except Exception as e:
                print(f"  {sym} {tf} 失敗 {e}")
    t = pd.DataFrame(all_t)
    if t.empty:
        print("無訊號"); return
    n = len(t)
    aw = t["win"].mean() * 100
    fw = t["fair"].mean() * 100
    rr = t["rr"].mean()
    ev_g = t["r_gross"].mean()
    ev_n = t["r_net"].mean()
    # 二項檢定:實際勝率有沒有「顯著高於」公平勝率
    from math import sqrt
    p0 = t["fair"].mean()
    se = sqrt(p0 * (1 - p0) / n)
    z = (t["win"].mean() - p0) / se if se > 0 else 0

    print("=" * 66)
    print(f"  Setup A(支撐反彈) 回測   總 {n} 筆")
    print("=" * 66)
    print(f"  實際勝率     : {aw:.1f}%")
    print(f"  公平勝率(R:R隱含): {fw:.1f}%   平均 R:R {rr:.2f}")
    print(f"  實際 − 公平   : {aw - fw:+.1f} 個百分點   (z = {z:+.2f})")
    print(f"  每筆 EV 毛(未扣費): {ev_g:+.3f}R")
    print(f"  每筆 EV 淨(扣費) : {ev_n:+.3f}R")
    print("-" * 66)
    if z > 1.64 and ev_n > 0:
        print("  判定：實際顯著 > 公平 且扣費後為正 → 疑似有 edge，值得換市場/時段再驗！")
    else:
        print("  判定：實際勝率沒有顯著高於公平值 → 公平銅板、無 edge。")
        print("        『技術含量』給的是風控與一致性,不是預測力。你贏是丟到正面。")
    print("=" * 66)


if __name__ == "__main__":
    main()
