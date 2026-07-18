"""
策略搜尋實驗室 (Strategy Search Lab) —— 誠實版
================================================
目的:回答「能不能訓練/搜尋出一個好策略」。做法是廣撒一堆有經濟理由的
簡單策略、掃參數,但**用訓練/測試分段**讓過度擬合(overfitting)現形。

核心誠實機制:
  1. 資料切兩段:前 70% = 訓練段(找最佳參數)、後 30% = 樣本外測試段(驗證)。
  2. 在訓練段挑出每個策略族群「回測最好」的參數。
  3. 把那個「訓練冠軍」原封不動拿到它從沒看過的測試段跑。
  4. 你會親眼看到:訓練段超賺的,到測試段大多打回原形 → 那就是過擬合。
  5. 全程扣手續費、無未來函數(訊號用當根收盤決定、下一根才生效)。
  6. 多重檢定警告:掃越多組合越可能撞到「歷史剛好賺」的幻覺,故看的是
     『樣本外』表現,不是訓練段最高分。

★ 誠實聲明:這工具不是要「找到印鈔機」,是要嚴謹回答「找不找得到」。
  最可能的結論是:訓練段的好策略,樣本外會退化 → 沒有可靠 edge。
  若真有哪個活過樣本外(機率低),才值得進一步驗證(換市場/換時段再測)。

用法:
  python strategy_search.py                      # BTC/ETH 4h 預設
  python strategy_search.py --symbols BTC/USDT --tf 1h --bars 1500
  python strategy_search.py --short              # 允許做空(預設只做多/空手)
"""
import argparse
import itertools
import time

import ccxt
import numpy as np
import pandas as pd

FEE = 0.0004          # 單邊 taker 手續費 ~0.04%(永續)
BARS_PER_YEAR = {"5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "1d": 365}


# ----------------------------------------------------------------- 資料
def fetch(ex, symbol, tf, bars):
    """抓 OHLCV(必要時分頁),回傳含 close 的 DataFrame。丟掉最後一根未收完的。"""
    all_rows, since = [], None
    limit = 1000
    while len(all_rows) < bars:
        for attempt in range(3):
            try:
                chunk = ex.fetch_ohlcv(symbol, tf, since=since, limit=limit)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.0)
        if not chunk:
            break
        all_rows += chunk
        since = chunk[-1][0] + 1
        if len(chunk) < limit:
            break
        time.sleep(0.2)
    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "vol"])
    df = df.drop_duplicates("ts").iloc[:-1].reset_index(drop=True)
    return df.tail(bars).reset_index(drop=True)


# ----------------------------------------------------------------- 指標
def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50)


# ----------------------------------------------------------------- 策略族群
# 每個 gen 函式回傳「目標部位序列」pos(+1做多/-1做空/0空手),用當根收盤決定。
def gen_ma_cross(df, fast, slow):
    f, s = df["close"].rolling(fast).mean(), df["close"].rolling(slow).mean()
    return np.sign(f - s).fillna(0)

def gen_breakout(df, look):
    hi = df["close"].rolling(look).max()
    lo = df["close"].rolling(look).min()
    pos = pd.Series(0.0, index=df.index)
    pos[df["close"] >= hi] = 1
    pos[df["close"] <= lo] = -1
    return pos.replace(0, np.nan).ffill().fillna(0)

def gen_momentum(df, look):
    return np.sign(df["close"].pct_change(look)).fillna(0)

def gen_rsi_revert(df, n, lo, hi):
    r = rsi(df["close"], n)
    pos = pd.Series(np.nan, index=df.index)
    pos[r < lo] = 1            # 超賣做多
    pos[r > hi] = -1           # 超買做空
    pos[(r > 45) & (r < 55)] = 0   # 回到中性就平倉
    return pos.ffill().fillna(0)


FAMILIES = {
    "MA交叉":   (gen_ma_cross, [dict(fast=f, slow=s)
                 for f in (5, 10, 20, 50) for s in (20, 50, 100, 200) if f < s]),
    "突破":     (gen_breakout, [dict(look=l) for l in (10, 20, 30, 55, 100)]),
    "動量":     (gen_momentum, [dict(look=l) for l in (5, 10, 20, 40, 60)]),
    "RSI回歸":  (gen_rsi_revert, [dict(n=n, lo=lo, hi=100-lo)
                 for n in (7, 14, 21) for lo in (20, 25, 30)]),
}


# ----------------------------------------------------------------- 回測
def backtest(df, pos, tf, allow_short):
    """回傳 (sharpe, 累積報酬%, 交易次數)。無未來函數:pos 用當根收盤決定,
    下一根才生效(pos.shift(1));換倉手續費計在成交那根。"""
    pos = pos.copy()
    if not allow_short:
        pos = pos.clip(lower=0)
    ret = df["close"].pct_change().fillna(0)
    trades = pos.diff().abs().fillna(pos.abs())
    gross = pos.shift(1).fillna(0) * ret
    net = gross - trades.shift(1).fillna(0) * FEE
    n_tr = int((trades > 0).sum())
    if net.std() == 0 or len(net) < 2:
        return 0.0, 0.0, n_tr
    sharpe = net.mean() / net.std() * np.sqrt(BARS_PER_YEAR.get(tf, 8760))
    cum = ((1 + net).prod() - 1) * 100
    return float(sharpe), float(cum), n_tr


def run(symbol, tf, bars, allow_short):
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    df = fetch(ex, symbol, tf, bars)
    n = len(df)
    cut = int(n * 0.7)
    train, test = df.iloc[:cut].reset_index(drop=True), df.iloc[cut:].reset_index(drop=True)

    print("=" * 84)
    print(f"  策略搜尋 {symbol} {tf}   總 {n} 根   訓練 {len(train)} | 樣本外測試 {len(test)}"
          f"   {'(可做空)' if allow_short else '(只做多/空手)'}")
    print("=" * 84)
    # 基準:測試段買入持有
    bh = ((test['close'].iloc[-1] / test['close'].iloc[0]) - 1) * 100
    print(f"  基準(測試段買入持有) = {bh:+.1f}%\n")
    print(f"{'策略族群':<10}{'訓練冠軍參數':<26}{'訓練Sharpe':>10}{'訓練報酬%':>10}"
          f"{'→樣本外Sharpe':>14}{'樣本外報酬%':>12}{'測試交易':>8}")
    print("-" * 84)

    survivors = 0
    for fam, (gen, grid) in FAMILIES.items():
        # 訓練段:挑 Sharpe 最高的參數
        best, best_sh = None, -1e9
        for params in grid:
            sh, _, ntr = backtest(train, gen(train, **params), tf, allow_short)
            if ntr >= 3 and sh > best_sh:
                best_sh, best = sh, params
        if best is None:
            continue
        tr_sh, tr_ret, _ = backtest(train, gen(train, **best), tf, allow_short)
        te_sh, te_ret, te_ntr = backtest(test, gen(test, **best), tf, allow_short)
        if te_sh > 0 and te_ret > 0:
            survivors += 1
        pstr = ",".join(f"{k}={v}" for k, v in best.items())
        flag = "✅活" if (te_sh > 0 and te_ret > 0) else "❌退化"
        print(f"{fam:<10}{pstr:<26}{tr_sh:>10.2f}{tr_ret:>+10.1f}"
              f"{te_sh:>14.2f}{te_ret:>+12.1f}{te_ntr:>6}  {flag}")

    print("-" * 84)
    print(f"  訓練冠軍在樣本外仍為正的:{survivors}/{len(FAMILIES)} 個族群")
    print("=" * 84)
    print("★ 怎麼讀:")
    print("  · 訓練 Sharpe 高、樣本外 Sharpe 掉下來(甚至翻負)= 過度擬合,那個『好』是")
    print("    對歷史雜訊的曲線硬湊,不是真規律 → 上實盤會賠。")
    print("  · 掃了幾十組參數,就算有 1-2 個樣本外剛好也正,也要當心多重檢定(運氣)。")
    print("    真要信,需再換『別的市場/別的時段』重測仍正,才算數。")
    print("  · 若整排都❌退化 = 這批簡單策略在這市場沒有可靠 edge(最可能的誠實結論)。")


GEN_BY_NAME = {name: gen for name, (gen, _) in FAMILIES.items()}
ROBUST_BASKET = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
                 "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT",
                 "DOT/USDT", "TRX/USDT"]


def _parse_strat(spec):
    """把 '動量:look=5' 解析成 (族群名, {參數}). 參數值都當 int。"""
    fam, _, prm = spec.partition(":")
    fam = fam.strip()
    if fam not in GEN_BY_NAME:
        raise SystemExit(f"未知族群 '{fam}'，可用:{list(GEN_BY_NAME)}")
    params = {}
    for kv in prm.split(","):
        if "=" in kv:
            k, v = kv.split("=")
            params[k.strip()] = int(v)
    return fam, params


def robust(spec, tf, bars, allow_short, folds=4):
    """穩健性交叉驗證:把一個『固定策略』丟到整籃子市場 × 多個時段(fold),
    看它在多少比例的『市場×時段』仍為正。真 edge→到處活;運氣→約一半(擲銅板)。"""
    fam, params = _parse_strat(spec)
    gen = GEN_BY_NAME[fam]
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    print("=" * 84)
    print(f"  穩健性交叉驗證   策略={fam} {params}   {tf}   {len(ROBUST_BASKET)}市場 × {folds}時段")
    print(f"  真 edge → 幾乎到處為正;運氣/過擬合 → 命中率約 50%(擲銅板)")
    print("=" * 84)
    print(f"{'市場':<12}" + "".join(f"{'段'+str(i+1):>9}" for i in range(folds)) + f"{'正比例':>9}")
    print("-" * 84)
    cells_pos = cells_tot = 0
    for sym in ROBUST_BASKET:
        try:
            df = fetch(ex, sym, tf, bars)
        except Exception as e:
            print(f"{sym:<12} 抓資料失敗 {e}")
            continue
        m = len(df) // folds
        line, pos_here = f"{sym:<12}", 0
        for i in range(folds):
            seg = df.iloc[i*m:(i+1)*m].reset_index(drop=True)
            _, ret, _ = backtest(seg, gen(seg, **params), tf, allow_short)
            cells_tot += 1
            if ret > 0:
                cells_pos += 1; pos_here += 1
            line += f"{ret:>+8.1f}%" if abs(ret) < 1000 else f"{ret:>+8.0f}%"
        line += f"{pos_here/folds*100:>8.0f}%"
        print(line)
    print("-" * 84)
    rate = cells_pos / cells_tot * 100 if cells_tot else 0
    print(f"  總命中率(正報酬的市場×時段格子)= {cells_pos}/{cells_tot} = {rate:.0f}%")
    verdict = ("≈50% → 擲銅板,沒有 edge(那個『survivor』是運氣)" if 40 <= rate <= 60
               else "明顯 >60% → 值得再深究(但仍要換時框/加更長歷史確認)" if rate > 60
               else "明顯 <40% → 這策略在此市場群是負的")
    print(f"  判定:{verdict}")
    print("=" * 84)


def main():
    ap = argparse.ArgumentParser(description="策略搜尋實驗室(訓練/測試分段,防過擬合)")
    ap.add_argument("--symbols", nargs="*", default=["BTC/USDT", "ETH/USDT"])
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--bars", type=int, default=1500)
    ap.add_argument("--short", action="store_true", help="允許做空(預設只做多/空手)")
    ap.add_argument("--robust", default=None,
                    help="穩健性交叉驗證單一策略,如 --robust '動量:look=5'")
    ap.add_argument("--folds", type=int, default=4, help="穩健性:每市場切幾個時段")
    args = ap.parse_args()

    if args.robust:
        robust(args.robust, args.tf, args.bars, args.short, args.folds)
        return
    for sym in args.symbols:
        run(sym, args.tf, args.bars, args.short)
        print()


if __name__ == "__main__":
    main()
