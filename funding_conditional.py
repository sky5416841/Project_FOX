"""
條件式資金費套利 —— 「只在資金費高時才進場」能不能把報酬拉到有意義？

上一支(funding_research.py)是「一直掛著」，平均年化淨僅 ~0.8%(被低費/負費期數拖累)。
這支只在「資金費高」時才部署對沖部位、其餘時間空手，看選擇性進場能否提升報酬。

★ 紀律：
  - 無未來函數：第 t 期是否進場，只看『過去 3 期(t-3..t-1)』的資金費平均當訊號
    (資金費有持續性，高了通常續高，所以這是合理的預測代理)。
  - 誠實面對「使用率」：進場時年化高很正常，真正要看的是
    『有多少時間真的在場上(capital utilization)』與『頻繁進出的手續費』。
  - 多門檻原樣列出，不挑好看的。

⚠️ 同樣理想化(忽略基差/爆倉/借貸/滑點)，且資料僅約 66 天平靜期 —— 牛市狂熱期數字會高很多。
"""
import ccxt
import numpy as np

SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT",
           "XRP/USDT:USDT", "DOGE/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT"]
LIMIT        = 1000
ROUNDTRIP_FEE = 0.10          # 每次進出場成本%(兩腿)
SIGNAL_WIN   = 3              # 用過去幾期當訊號
PERIODS_YEAR = 365 * 24 / 8
# 訊號門檻(每期資金費%)；年化參考 = ×1095
THRESHOLDS = [0.0, 0.005, 0.01, 0.02, 0.03]


def fetch_rates(ex, sym):
    hist = ex.fetch_funding_rate_history(sym, limit=LIMIT)
    return np.array([h["fundingRate"] * 100 for h in hist if h.get("fundingRate") is not None])  # 轉成 %


def simulate(rates, thr):
    """回傳 (在場期數, 進場次數, 在場期間收到的總資金費%)。無未來函數。"""
    deployed = collected = entries = 0
    prev_in = False
    for t in range(SIGNAL_WIN, len(rates)):
        signal = rates[t - SIGNAL_WIN:t].mean()      # 過去 3 期平均(已知)
        in_now = signal > thr
        if in_now:
            deployed += 1
            collected += rates[t]                     # 收當期資金費
            if not prev_in:
                entries += 1                          # 由空手→進場，記一次進出成本
        prev_in = in_now
    return deployed, entries, collected


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    data = {}
    for s in SYMBOLS:
        try:
            r = fetch_rates(ex, s)
            if len(r) > 20:
                data[s] = r
        except Exception as e:
            print(f"{s} 失敗：{e}")
    total_periods = sum(len(r) for r in data.values())
    print(f"=== 條件式資金費套利 | {len(data)}幣 共{total_periods}期(~66天) | 訊號=過去{SIGNAL_WIN}期均 ===")
    print(f"（無未來函數；進出成本{ROUNDTRIP_FEE}%/次；多門檻原樣列出）\n")
    print(f"{'門檻/期':>8}{'年化參考':>9}{'在場%':>8}{'進場次':>7}{'在場時年化淨':>13}{'總資本年化淨':>13}")

    for thr in THRESHOLDS:
        tot_dep = tot_ent = tot_col = tot_per = 0
        for r in data.values():
            d, e, c = simulate(r, thr)
            tot_dep += d; tot_ent += e; tot_col += c; tot_per += (len(r) - SIGNAL_WIN)
        if tot_dep == 0:
            print(f"{thr:>7.3f}%{thr*PERIODS_YEAR:>8.0f}%      —  從不進場"); continue
        net_col = tot_col - tot_ent * ROUNDTRIP_FEE          # 扣進出成本後的總收益%
        util = tot_dep / tot_per * 100                       # 使用率
        ann_when = tot_col / tot_dep * PERIODS_YEAR          # 在場期間年化(毛)
        ann_total = net_col / tot_per * PERIODS_YEAR         # 攤到全部資本+時間的年化(淨)
        print(f"{thr:>7.3f}%{thr*PERIODS_YEAR:>8.0f}%{util:>7.0f}%{tot_ent:>7}{ann_when:>12.1f}%{ann_total:>12.1f}%")

    print("\n判讀：")
    print("  ·『在場時年化』高 = 進對時機真的有錢收；但要對照『在場%』(其餘時間資本閒置)。")
    print("  ·『總資本年化淨』才是你真正賺的(已扣頻繁進出手續費)。")
    print("  · 門檻太高→很少進場、手續費吃掉；門檻太低→退化成『一直掛著』的 0.8%。")
    print("  · 資料是平靜期；牛市狂熱期資金費會數倍高，這策略才真正發威。")


if __name__ == "__main__":
    main()
