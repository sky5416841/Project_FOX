"""
資金費行情偵測器 (Funding Regime Detector)

唯一有「條件式真實 edge」的方向是資金費套利，但它的 edge 看行情：
平靜期幾乎沒得收，牛市狂熱期(多單瘋狂擁擠)才肥。

這支工具掃全市場資金費，判斷『現在是該部署、還是該觀望』，並給出
若現在部署的預估年化。平常它會說「觀望」，等到狂熱時才喊「部署」。

定位：把武器擦亮、放口袋的『時機雷達』 —— 不是現在賺錢，是等對的行情。
判讀門檻是啟發式(heuristic)，不是真理；數字僅供相對參考。
"""
import ccxt
import numpy as np

PERIODS_YEAR = 365 * 24 / 8          # 8h 一期 → 約 1095
MAJORS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT"]


def snapshot(ex):
    """全市場當前資金費快照。"""
    rates = ex.fetch_funding_rates()
    vals = []
    for sym, r in rates.items():
        fr = r.get("fundingRate")
        if fr is not None and sym.endswith(":USDT"):
            vals.append(fr * 100)        # 轉 %
    return np.array(vals)


def trend(ex, win_periods=63):
    """主流幣近 ~21 天的『市場平均資金費』走勢(看在升溫還是降溫)。"""
    series = []
    for s in MAJORS:
        try:
            h = ex.fetch_funding_rate_history(s, limit=win_periods)
            series.append([x["fundingRate"] * 100 for x in h if x.get("fundingRate") is not None])
        except Exception:
            pass
    if not series:
        return None
    m = min(len(s) for s in series)
    arr = np.array([s[-m:] for s in series])     # (幣, 期)
    market_avg = arr.mean(axis=0)                # 每期的市場平均
    half = len(market_avg) // 2
    early = market_avg[:half].mean() * PERIODS_YEAR
    late = market_avg[half:].mean() * PERIODS_YEAR
    return early, late


def verdict(ann_avg, breadth):
    if ann_avg > 30 and breadth > 75:
        return "🔥 強烈部署區（市場狂熱、多單擁擠）", "現在就是資金費套利該上場的時候"
    if ann_avg > 15:
        return "🟠 部署區（資金費偏高）", "值得部署，留意是否續熱"
    if ann_avg > 8:
        return "🟡 微溫 / 觀察區", "可小量試水，但別期待太高"
    return "😴 平靜 / 觀望區", "資金費太低，現在做不划算 —— 空手等狂熱"


def main():
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    v = snapshot(ex)
    ann = v * PERIODS_YEAR                       # 各幣年化%
    ann_avg = float(np.mean(ann))
    ann_med = float(np.median(ann))
    breadth = float((v > 0).mean() * 100)        # 多少比例的幣資金費為正
    n_hot = int((ann > 30).sum())                # 年化>30% 的「肥幣」數量

    print("=" * 60)
    print("           資金費行情偵測器  Funding Regime Radar")
    print("=" * 60)
    print(f"全市場 {len(v)} 個永續合約當前快照：")
    print(f"  · 市場平均年化資金費 : {ann_avg:+.1f}%   (中位數 {ann_med:+.1f}%)")
    print(f"  · 資金費為正的比例   : {breadth:.0f}%   (越高=越多單擁擠)")
    print(f"  · 年化>30% 的肥幣    : {n_hot} 個")

    t = trend(ex)
    if t:
        early, late = t
        arrow = "📈 升溫中" if late > early + 2 else "📉 降溫中" if late < early - 2 else "➡️ 持平"
        print(f"  · 近 3 週走勢        : 前段年化 {early:+.1f}% → 近段 {late:+.1f}%  {arrow}")

    head, note = verdict(ann_avg, breadth)
    print("-" * 60)
    print(f"  判定：{head}")
    print(f"        {note}")
    if ann_avg > 8:
        net = ann_avg * 0.6                       # 扣現實摩擦(基差/滑點/借貸)粗估打 6 折
        print(f"        若現在部署，預估『扣摩擦後』年化 ≈ {net:.0f}%（粗估，僅供相對參考）")
    print("=" * 60)
    print("提醒：門檻為啟發式；真要部署仍要處理對沖、爆倉保證金、借貸成本。")
    print("      平常它喊『觀望』很正常 —— 它的價值是在狂熱來時喊『部署』。")


if __name__ == "__main__":
    main()
