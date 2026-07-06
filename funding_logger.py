"""
資金費歷史紀錄 + Carry 回測 (Funding Logger & Carry Backtest)
============================================================
想法1 實作：把資金費從「當下快照」升級成「有紀錄的實測」。

唯一站得住的結構性 edge = delta-neutral carry：
  現貨買一顆 + 永續空一顆 → 方向對沖(不賭漲跌)，純賺永續『資金費』。
  資金費為正時，空方收錢；牛市多單擁擠時費率最肥。

跟 ML 收集器一樣被動、不下單、不碰任何常駐 daemon。三個模式：
  backfill  — 抓每個標的的歷史資金費(公開可回溯~上百天) → funding_history.csv
  log       — 附加最新一期快照(給每 8h 被動累積用，冪等去重)
  carry     — 用歷史資料回測「持續做空永續收資金費」扣費後的實現年化

★ 誠實聲明：
  · 這是唯一非「猜方向」的 edge，但不是白吃午餐 —— 對沖仍有基差風險、
    現貨買入/借貸成本、爆倉保證金管理、資金費可能翻負。
  · carry 回測 = 毛資金費加總 − 估計交易費，真實執行摩擦(滑點/強平/
    費率突變)會再吃掉一部分。數字是「量出來的相對參考」，不是保證。
  · fundingRate 是每期(8h)費率；年化 ≈ rate × (365×24/8) = rate × 1095。

用法：
  python funding_logger.py backfill                # 抓預設一籃子歷史(每幣~上百天)
  python funding_logger.py backfill --symbols BTC/USDT ETH/USDT --limit 1000
  python funding_logger.py log                     # 附加最新一期(排程每 8h 跑)
  python funding_logger.py carry                   # 回測整籃子的 carry 年化
  python funding_logger.py carry --symbol BTC/USDT # 單一標的細看
"""
import argparse
import os
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

HIST_CSV     = "funding_history.csv"
PERIODS_YEAR = 365 * 24 / 8          # 8h 一期 → ~1095 期/年

# 預設一籃子(流動性佳的永續，ccxt 統一符號 BASE/USDT:USDT)
DEFAULT_BASKET = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT",
    "DOT/USDT", "TRX/USDT",
]

# 交易費估計(單邊)：永續 taker 0.04%、現貨 taker 0.10%。
# 一次完整 carry = 開(現貨買+永續空) + 平(現貨賣+永續平) = 兩腿各進出一次。
FEE_ROUNDTRIP_PCT = (0.04 + 0.10) * 2 / 100      # ≈ 0.28% 一次性(非年化)


# ----------------------------------------------------------------- 交易所
def make_exchange():
    return ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})


def _perp(sym: str) -> str:
    """把 BTC/USDT 正規化成 ccxt 永續符號 BTC/USDT:USDT。"""
    return sym if sym.endswith(":USDT") else f"{sym}:USDT"


def _retry(fn, *a, retries=2, **kw):
    for i in range(retries + 1):
        try:
            return fn(*a, **kw)
        except Exception:
            if i >= retries:
                raise
            time.sleep(1.0)


# ----------------------------------------------------------------- 存取
def _load_hist() -> pd.DataFrame:
    if os.path.exists(HIST_CSV):
        return pd.read_csv(HIST_CSV)
    return pd.DataFrame(columns=["symbol", "ts", "datetime", "funding_rate"])


def _merge(hist: pd.DataFrame, rows: list) -> pd.DataFrame:
    """把新抓的列併進歷史(避開空 df concat 的 FutureWarning)。"""
    if not rows:
        return hist
    new = pd.DataFrame(rows)
    return new if hist.empty else pd.concat([hist, new], ignore_index=True)


def _save_hist(df: pd.DataFrame) -> None:
    """去重(symbol+ts)、排序後原子寫入，硬 kill 也不會留半截檔。"""
    df = (df.drop_duplicates(subset=["symbol", "ts"])
            .sort_values(["symbol", "ts"])
            .reset_index(drop=True))
    tmp = HIST_CSV + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, HIST_CSV)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)\
                   .astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------- 模式
def backfill(ex, symbols, limit=1000):
    """抓每個標的的歷史資金費(公開)，合併進 funding_history.csv。"""
    hist = _load_hist()
    rows = []
    for sym in symbols:
        p = _perp(sym)
        try:
            h = _retry(ex.fetch_funding_rate_history, p, limit=limit)
        except Exception as e:
            print(f"  [WARN] {p} 抓歷史失敗 → {e}")
            continue
        for x in h:
            fr = x.get("fundingRate")
            ts = x.get("timestamp")
            if fr is None or ts is None:
                continue
            rows.append({"symbol": p, "ts": int(ts),
                         "datetime": _iso(int(ts)), "funding_rate": float(fr)})
        span = f"{_iso(h[0]['timestamp'])[:10]}→{_iso(h[-1]['timestamp'])[:10]}" if h else "無資料"
        print(f"  {p:<16} {len(h):>4} 期  ({span})")
    before = len(hist)
    _save_hist(_merge(hist, rows))
    after = len(_load_hist())
    print(f"\n✓ funding_history.csv：{before} → {after} 筆 (新增 {after - before})")


def log_latest(ex, symbols):
    """附加每個標的『最新一期』資金費(排程每 8h 跑用，去重冪等)。"""
    hist = _load_hist()
    rows = []
    for sym in symbols:
        p = _perp(sym)
        try:
            r = _retry(ex.fetch_funding_rate, p)
        except Exception as e:
            print(f"  [WARN] {p} → {e}")
            continue
        fr = r.get("fundingRate")
        ts = r.get("fundingTimestamp") or r.get("timestamp")
        if fr is None or ts is None:
            continue
        rows.append({"symbol": p, "ts": int(ts),
                     "datetime": _iso(int(ts)), "funding_rate": float(fr)})
    before = len(hist)
    _save_hist(_merge(hist, rows))
    added = len(_load_hist()) - before
    print(f"[{_iso(int(time.time()*1000))}] log：掃 {len(rows)} 標的，新增 {added} 筆(其餘已存在)")


def carry(symbol=None):
    """回測『持續做空永續收資金費』扣費後的實現年化。"""
    hist = _load_hist()
    if hist.empty:
        print("尚無資料，請先 backfill。")
        return
    syms = [_perp(symbol)] if symbol else sorted(hist["symbol"].unique())

    print("=" * 74)
    print("   Delta-Neutral Carry 回測 (做空永續收資金費，扣費後實現年化)")
    print("=" * 74)
    print(f"{'標的':<16}{'期數':>6}{'涵蓋天':>7}{'正費率%':>8}"
          f"{'毛年化':>9}{'費用拖累':>9}{'淨年化':>9}")
    print("-" * 74)

    rows_out = []
    for p in syms:
        d = hist[hist["symbol"] == p].sort_values("ts")
        n = len(d)
        if n < 2:
            continue
        rate = d["funding_rate"]                       # 每期(8h)費率(小數)
        span_days = (d["ts"].iloc[-1] - d["ts"].iloc[0]) / 1000 / 86400
        span_years = span_days / 365 if span_days > 0 else 1e-9
        gross_ret = rate.sum()                         # 期間毛累積報酬(收正付負)
        gross_ann = gross_ret / span_years * 100       # 毛年化 %
        fee_drag = FEE_ROUNDTRIP_PCT / span_years * 100  # 一次性費用攤到年化 %
        net_ann = gross_ann - fee_drag
        pos_pct = (rate > 0).mean() * 100              # 費率為正的比例
        rows_out.append((p, n, span_days, pos_pct, gross_ann, fee_drag, net_ann))
        print(f"{p:<16}{n:>6}{span_days:>7.0f}{pos_pct:>8.0f}"
              f"{gross_ann:>+9.1f}{fee_drag:>9.2f}{net_ann:>+9.1f}")

    print("-" * 74)
    if rows_out and not symbol:
        avg_net = sum(r[6] for r in rows_out) / len(rows_out)
        best = max(rows_out, key=lambda r: r[6])
        print(f"整籃子平均淨年化 ≈ {avg_net:+.1f}%   最肥：{best[0]} {best[6]:+.1f}%")
    print("=" * 74)
    print("★ 誠實提醒：")
    print("  · 這是『整段期間一直持有』的被動 carry，未擇時。真部署會挑費率肥的")
    print("    時段進、平靜期空手 → 實際擇時後的年化通常高於這個『全程持有』均值。")
    print("  · 毛年化只算資金費，未計基差變動/現貨買入成本/借貸/強平風險。")
    print("  · 費率會翻負(空方要付錢)；正費率比例越高的標的，carry 越穩。")


def timing(symbol=None, thr_ann=10.0, lag=1):
    """擇時回測:只在『前一期觀察到的』費率年化 > 門檻時才持有那期收費率，其餘空手。
    檢驗核心論點『要擇時才有意義』—— 擇時後扣費年化真的比全程持有好嗎？
    用 lag 期前的費率當訊號(避免偷看未來:你觀察到上期費率才決定下期進不進)。"""
    hist = _load_hist()
    if hist.empty:
        print("尚無資料，請先 backfill。")
        return
    syms = [_perp(symbol)] if symbol else sorted(hist["symbol"].unique())
    thr = thr_ann / 100 / PERIODS_YEAR                 # 年化門檻 → 每期費率門檻

    print("=" * 82)
    print(f"   擇時 Carry 回測 (只在前{lag}期費率年化 > {thr_ann:.0f}% 時持有；訊號無偷看未來)")
    print("=" * 82)
    print(f"{'標的':<16}{'在場%':>7}{'持有年化':>10}{'擇時淨年化':>11}"
          f"{'全程淨年化':>11}{'贏過持有':>10}")
    print("-" * 82)

    rows_out = []
    for p in syms:
        d = hist[hist["symbol"] == p].sort_values("ts").reset_index(drop=True)
        n = len(d)
        if n < lag + 2:
            continue
        rate = d["funding_rate"].to_numpy()
        signal = rate[:-lag] > thr                      # 前 lag 期費率 > 門檻
        held = rate[lag:][signal]                       # 訊號成立時，收當期費率
        span_days = (d["ts"].iloc[-1] - d["ts"].iloc[0]) / 1000 / 86400
        span_years = span_days / 365 if span_days > 0 else 1e-9
        deploy_pct = signal.mean() * 100                # 在場時間比例
        # 每次進出算一次費用；連續在場算一段(用訊號由關→開的次數估進場次數)
        entries = int(((~signal[:-1]) & signal[1:]).sum()) + int(signal[0])
        fee_total = entries * FEE_ROUNDTRIP_PCT
        timed_net_ann = (held.sum() - fee_total) / span_years * 100
        held_ann = (held.sum() / span_years * 100) if len(held) else 0.0
        hold_all_net = (rate[lag:].sum() - FEE_ROUNDTRIP_PCT) / span_years * 100
        edge = timed_net_ann - hold_all_net
        rows_out.append((p, deploy_pct, held_ann, timed_net_ann, hold_all_net, edge))
        print(f"{p:<16}{deploy_pct:>7.0f}{held_ann:>+10.1f}{timed_net_ann:>+11.1f}"
              f"{hold_all_net:>+11.1f}{edge:>+10.1f}")

    print("-" * 82)
    if rows_out:
        avg_timed = sum(r[3] for r in rows_out) / len(rows_out)
        avg_hold = sum(r[4] for r in rows_out) / len(rows_out)
        n_beat = sum(1 for r in rows_out if r[5] > 0)
        print(f"整籃子平均：擇時淨年化 {avg_timed:+.1f}%  vs  全程 {avg_hold:+.1f}%  "
              f"→ 擇時贏過持有的標的 {n_beat}/{len(rows_out)}")
    print("=" * 82)
    print("★ 誠實提醒：")
    print(f"  · 訊號用 lag={lag} 期前費率,無偷看未來;門檻是啟發式,可 --threshold 調。")
    print("  · 若『擇時』沒明顯贏過『全程持有』,代表這段期間費率沒有可利用的持續性,")
    print("    edge 只在真正狂熱期出現 → 印證 funding_regime『平常空手、狂熱才部署』。")
    print("  · 仍未計基差變動/現貨成本/借貸/強平;在場%越低代表大多時間該空手等。")


def main():
    ap = argparse.ArgumentParser(description="資金費歷史紀錄 + Carry 回測")
    ap.add_argument("mode", choices=["backfill", "log", "carry", "timing"])
    ap.add_argument("--symbols", nargs="*", default=None, help="標的清單(預設一籃子)")
    ap.add_argument("--symbol", default=None, help="carry/timing 模式:只看單一標的")
    ap.add_argument("--limit", type=int, default=1000, help="backfill 每幣抓幾期(上限交易所定)")
    ap.add_argument("--threshold", type=float, default=10.0, help="timing:進場的年化費率門檻%")
    ap.add_argument("--lag", type=int, default=1, help="timing:用幾期前的費率當訊號(避免偷看未來)")
    args = ap.parse_args()

    if args.mode == "carry":
        carry(args.symbol)
        return
    if args.mode == "timing":
        timing(args.symbol, args.threshold, args.lag)
        return

    ex = make_exchange()
    symbols = args.symbols or DEFAULT_BASKET
    if args.mode == "backfill":
        print(f"[backfill] {len(symbols)} 標的，每幣抓 ≤{args.limit} 期歷史資金費…\n")
        backfill(ex, symbols, args.limit)
    else:
        log_latest(ex, symbols)


if __name__ == "__main__":
    main()
