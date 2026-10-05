# -*- coding: utf-8 -*-
"""PO3 訊號跨時框回測:15m vs 30m vs 1h,比扣費後淨利。純唯讀、不動任何帳本。"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, ccxt
import po3_engine as pe

COINS = ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT","DOGE/USDT",
         "ADA/USDT","AVAX/USDT","LINK/USDT","LTC/USDT","DOT/USDT","TRX/USDT"]
TFS = ["15m","30m","1h"]
FEE = 0.0005
ex = ccxt.binance({"options":{"defaultType":"future"},"enableRateLimit":True})

TF_MS = {"15m":15*60000, "30m":30*60000, "1h":60*60000}
def fetch(sym, tf, total=6000):
    """分頁往回抓 total 根 OHLCV。"""
    step = TF_MS[tf]; per = 1500
    until = ex.milliseconds()
    chunks = []
    while len(chunks)*per < total:
        since = until - per*step
        o = ex.fetch_ohlcv(sym, tf, since=since, limit=per)
        if not o: break
        chunks.append(o)
        until = o[0][0] - step
        if len(o) < per: break
    rows = sorted({c[0]: c for ch in chunks for c in ch}.values())
    df = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"])
    return df

def stat(x):
    x = np.asarray(x, float); m=x.mean(); s=x.std(ddof=1); n=len(x)
    t = m/(s/np.sqrt(n)) if s and n>1 else 0
    return m, t, n

allrows = {tf: [] for tf in TFS}
for sym in COINS:
    for tf in TFS:
        try:
            df = fetch(sym, tf)
            res, _, _ = pe.run_po3_pipeline(df, sym, tf)
            if len(res)==0: continue
            res = res[res["outcome"]!="OPEN"].copy()   # 只算已結算
            for _,r in res.iterrows():
                sd = abs(r["entry"]-r["sl"])/r["entry"]
                if sd<=1e-9: continue
                feeR = 2*FEE/sd
                allrows[tf].append((r["realized_r"], r["realized_r"]-feeR, feeR,
                                    r["outcome"]=="WIN", sd, r["rr_target"]))
        except Exception as e:
            print("skip", sym, tf, e)

print("時框 |  n  | 勝率 | 平均R:R | 平均停損% | 平均費R | 毛利EV(t) | 淨利EV(t)")
print("-"*84)
for tf in TFS:
    a = allrows[tf]
    if not a: print(f"{tf}: 無資料"); continue
    gR=[x[0] for x in a]; nR=[x[1] for x in a]; feeR=[x[2] for x in a]
    win=np.mean([x[3] for x in a]); sd=np.mean([x[4] for x in a]); rr=np.mean([x[5] for x in a])
    gm,gt,n = stat(gR); nm,nt,_ = stat(nR)
    print(f"{tf:>4} | {n:4d} | {win*100:4.0f}% | {rr:6.2f} | {sd*100:7.2f}% | "
          f"{np.mean(feeR):6.3f} | {gm:+.3f}({gt:+.1f}) | {nm:+.3f}({nt:+.1f})")

# 手續費敏感度(用 1h)
print("\n1h 手續費敏感度(單邊費率 → 淨EV):")
a = allrows.get("1h", [])
if a:
    for f in [0.0005,0.0002,0.0001,0.0]:
        nr = np.mean([g - 2*f/sd for (g,_,_,_,sd,_) in a])
        print(f"  {f*100:.2f}% → {nr:+.3f}R")
