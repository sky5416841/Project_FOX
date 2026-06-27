"""
correlation_check.py — 相關性陷阱偵測器（你以為分散，其實是一注）

新手常開好幾個幣的倉，以為「分散風險」。但加密貨幣大多高度正相關——
BTC 一跌全部跟著跌。開 5 個 0.9 相關的倉 ≈ 把同一注下 5 倍，不是分散。

這支算主流幣報酬的相關矩陣，給出：
  · 平均兩兩相關係數（越接近 1 = 越像「同一注」）
  · 「有效獨立注數」估計（高相關 → 遠少於你開的倉數）

⚠ 相關性會隨行情變（恐慌時全部衝向 1）；這是相對參考，非投資建議。
"""
import ccxt
import numpy as np

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
           "XRP/USDT", "DOGE/USDT", "ADA/USDT", "LINK/USDT"]
TF, WINDOW = "1h", 168          # 近 7 天的小時報酬


def scan_correlation(ex=None):
    """回傳 (names, C, avg, eff)，供 CLI 與網頁共用（不印字）。"""
    ex = ex or ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    rets = {}
    for s in SYMBOLS:
        try:
            o = ex.fetch_ohlcv(s, TF, limit=WINDOW + 1)
            c = np.array([x[4] for x in o])
            rets[s.split("/")[0]] = np.diff(np.log(c))
        except Exception:
            continue
    names = list(rets)
    m = min(len(v) for v in rets.values())
    R = np.array([rets[n][-m:] for n in names])
    C = np.corrcoef(R)
    iu = np.triu_indices(len(names), 1)
    avg = float(C[iu].mean())
    N = len(names)
    eff = N / (1 + (N - 1) * avg)
    return names, C, round(avg, 2), round(eff, 1)


def main():
    names, C, avg, eff = scan_correlation()
    print(f"主流幣報酬相關矩陣（{TF}，近 {WINDOW//24} 天）")
    print("=" * (8 + 6 * len(names)))
    print("        " + "".join(f"{n[:5]:>6}" for n in names))
    for i, n in enumerate(names):
        row = "".join(f"{C[i,j]:>6.2f}" for j in range(len(names)))
        print(f"  {n[:6]:<6}{row}")
    print("-" * (8 + 6 * len(names)))

    N = len(names)
    print(f"  平均兩兩相關係數 : {avg:.2f}")
    print(f"  你開了 {N} 個倉，但『有效獨立注數』≈ {eff:.1f}")
    print("=" * (8 + 6 * len(names)))
    if avg > 0.7:
        print(f"  🔴 高度同向：開 {N} 個幣 ≈ 下同一注 {N/eff:.1f} 倍。BTC 一倒全倒。")
    elif avg > 0.4:
        print("  🟡 中度相關：有點分散，但別自以為很安全。")
    else:
        print("  🟢 相對分散：彼此較獨立。")
    print("  教訓：真正的分散看『相關性』，不是『開幾個倉』。")
    print("  全押加密貨幣 = 一個宏觀風險(美元/流動性)壓著全部 → 高相關難避免。")


if __name__ == "__main__":
    main()
