"""
資金費率套利(Funding Rate Arbitrage)可行性研究 —— 換對方向的第一步

策略(市場中性，不賭漲跌)：
  做空永續合約 + 買等量現貨對沖 → 價格漲跌互抵，純收「資金費」。
  幣圈資金費長期偏正(多單擁擠 → 多付給空)，所以「空合約」這邊長期是收錢的。

本檔抓各幣的歷史資金費，算「若一直維持對沖部位，年化能收多少」，扣掉一次性
進出場手續費，看淨報酬是正是負、有多少。

★ 與之前的差別：這不是「猜未來」，是「賺機制」。所以它有機會真的為正
  (這正是基差交易/資金費農場是真實存在策略的原因)。

⚠️ 誠實簡化(這只是第一步可行性，非可上線策略)：
  - 假設完美對沖、忽略基差風險、爆倉風險、現貨借貸成本、再平衡滑點。
  - 假設資金費照單全收(實際要扣交易所結算細節)。
  真要做，這些風險每一個都可能吃掉甚至超過這裡算出的報酬。
"""
import ccxt
import numpy as np
from datetime import datetime

SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT",
           "XRP/USDT:USDT", "DOGE/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT"]
LIMIT       = 1000      # 抓幾期資金費(每 8h 一期 → 1000 期 ≈ 333 天)
ROUNDTRIP_FEE = 0.10    # 一次性進出場成本%(兩腿開+平，估保守值)
PERIODS_PER_YEAR = 365 * 24 / 8   # 8h 一期 → 每年約 1095 期


def analyze(ex, sym):
    hist = ex.fetch_funding_rate_history(sym, limit=LIMIT)
    if not hist:
        return None
    rates = np.array([h["fundingRate"] for h in hist if h.get("fundingRate") is not None])
    ts = [h["timestamp"] for h in hist if h.get("timestamp")]
    if len(rates) < 10:
        return None
    days = (ts[-1] - ts[0]) / 1000 / 86400
    total = rates.sum()                      # 整段「空合約」收到的資金費(分數)
    ann_gross = total / days * 365 * 100     # 年化毛收益%
    pos_pct = (rates > 0).mean() * 100       # 多少比例的期數是「收錢」
    # 淨：扣一次性手續費(假設整段只進出一次)
    net_total = total * 100 - ROUNDTRIP_FEE
    ann_net = net_total / days * 365
    return dict(n=len(rates), days=days, ann_gross=ann_gross, ann_net=ann_net,
                pos_pct=pos_pct, avg_period=rates.mean() * 100)


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    print(f"=== 資金費率套利 可行性回測 | {len(SYMBOLS)}幣 | 每幣約{LIMIT}期(~333天) ===")
    print(f"（做空永續+現貨對沖、純收資金費；一次性成本估{ROUNDTRIP_FEE}%）\n")
    print(f"{'幣種':<8}{'天數':>6}{'收錢期%':>9}{'年化毛':>9}{'年化淨':>9}")
    anns = []
    for sym in SYMBOLS:
        try:
            r = analyze(ex, sym)
            if not r:
                print(f"{sym.split('/')[0]:<8} 無資料"); continue
            anns.append(r["ann_net"])
            print(f"{sym.split('/')[0]:<8}{r['days']:>6.0f}{r['pos_pct']:>8.0f}%{r['ann_gross']:>8.1f}%{r['ann_net']:>8.1f}%")
        except Exception as e:
            print(f"{sym.split('/')[0]:<8} 失敗：{e}")
    if anns:
        print("-" * 42)
        print(f"{'平均年化淨':<8}{'':>6}{'':>9}{'':>9}{np.mean(anns):>8.1f}%")
        print()
        avg = np.mean(anns)
        if avg > 5:
            print(f"判讀：平均年化淨 {avg:.1f}% → 機制上長期為正(這就是基差交易的真實 edge)。")
            print("      但這是『理想化』數字，真實要扣基差/爆倉/借貸/滑點，會明顯打折，")
            print("      且報酬不高、對資金與執行很敏感。是『真的方向』，不是『印鈔機』。")
        elif avg > 0:
            print(f"判讀：平均年化淨 {avg:.1f}% → 微正，扣掉現實摩擦後大概率所剩無幾。")
        else:
            print(f"判讀：平均年化淨 {avg:.1f}% → 連理想化都不正，這段期間資金費農場不划算。")


if __name__ == "__main__":
    main()
