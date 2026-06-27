"""
exit_style_lab.py — 出場方式實驗室：固定停利 vs 移動停利

用「同一批進場」比較兩種出場，看『讓利潤奔跑』如何改變損益形狀：
  A. 固定停利：+TP_R 就跑（封頂大贏）
  B. 移動停利：停損跟著最高價爬，反轉才出（讓大魚跑完）

進場用簡單突破(N根新高=順勢)，long-only。重點不是進場有沒有 edge，
是看『出場』怎麼塑造 勝率 / 平均贏 / 平均賠 / 尾部大贏。

⚠ 教學示範。進場很笨(突破)，所以期望值不一定正；看的是兩種出場的『形狀差異』。
"""
import ccxt
import numpy as np
import pandas as pd

SYMBOL, TF, BARS = "BTC/USDT", "4h", 2000
LOOKBACK = 20            # 突破前 N 根高點 = 進場
ATR_LEN  = 14
SL_ATR   = 1.5           # 初始停損 = 進場 - SL_ATR×ATR（= 1R）
TP_R     = 2.0           # 固定停利倍數(A 用)
TRAIL_ATR = 2.5          # 移動停損 = 最高價 - TRAIL_ATR×ATR（B 用）
MAX_HOLD = 120


def fetch():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    since = ex.milliseconds() - BARS * ex.parse_timeframe(TF) * 1000
    out, cur = [], since
    while len(out) < BARS:
        c = ex.fetch_ohlcv(SYMBOL, TF, since=cur, limit=1000)
        if not c: break
        out += c; cur = c[-1][0] + ex.parse_timeframe(TF)*1000
        if len(c) < 1000: break
    df = pd.DataFrame(out, columns=["ts","open","high","low","close","vol"]).drop_duplicates("ts")
    return df.reset_index(drop=True)


def atr(df, n=ATR_LEN):
    h,l,c = df["high"],df["low"],df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()


def run(df, mode):
    """回傳每筆交易的 R 倍數 list。mode = 'fixed' or 'trail'。"""
    df = df.copy(); df["atr"]=atr(df)
    hh = df["high"].rolling(LOOKBACK).max()
    Rs, i = [], LOOKBACK+ATR_LEN
    n = len(df)
    while i < n-1:
        # 進場：收盤突破前 LOOKBACK 根高點
        if not (df["close"].iat[i] > hh.iat[i-1]) or not np.isfinite(df["atr"].iat[i]):
            i += 1; continue
        entry = df["open"].iat[i+1]
        a = df["atr"].iat[i]
        risk = SL_ATR*a
        if risk<=0: i+=1; continue
        stop = entry - risk
        tp = entry + TP_R*risk
        hwm = entry
        exit_R = None
        j = i+1
        while j < min(i+1+MAX_HOLD, n):
            hi,lo = df["high"].iat[j], df["low"].iat[j]
            if mode=="trail":
                hwm = max(hwm, hi)
                stop = max(stop, hwm - TRAIL_ATR*a)   # 移動停損只往上抬
            if lo <= stop:                             # 先檢查停損(保守)
                exit_R = (stop-entry)/risk; break
            if mode=="fixed" and hi >= tp:
                exit_R = (tp-entry)/risk; break
            j += 1
        if exit_R is None:                             # 逾時以收盤平
            exit_R = (df["close"].iat[min(j,n-1)]-entry)/risk
        Rs.append(exit_R)
        i = j+1                                        # 平倉後才找下一筆
    return np.array(Rs)


def report(name, Rs):
    if len(Rs)==0: print(f"{name}: 無交易"); return
    win = Rs[Rs>0]; loss = Rs[Rs<=0]
    wr = len(win)/len(Rs)
    aw = win.mean() if len(win) else 0
    al = loss.mean() if len(loss) else 0
    payoff = (aw/abs(al)) if len(loss) and al!=0 else float('inf')
    print(f"  {name}")
    print(f"    交易數 {len(Rs)}　勝率 {wr:.0%}")
    print(f"    平均贏 {aw:+.2f}R　平均賠 {al:+.2f}R　賠率 {payoff:.2f}:1")
    print(f"    每筆期望 {Rs.mean():+.3f}R　總計 {Rs.sum():+.1f}R　最大單筆贏 {Rs.max():+.1f}R")


def main():
    print(f"抓 {SYMBOL} {TF} {BARS} 根…")
    df = fetch()
    print(f"（同一批突破進場，只換出場方式）\n")
    print("="*60)
    report("A. 固定停利 +2R（封頂）", run(df,"fixed"))
    print("-"*60)
    report("B. 移動停利（讓利潤奔跑）", run(df,"trail"))
    print("="*60)
    print("  看點：B 通常『勝率更低、平均賠差不多、但平均贏與最大單筆贏更大』")
    print("  → 這就是『大贏小賠』的形狀：靠少數大魚，不靠勝率。")


if __name__ == "__main__":
    main()
