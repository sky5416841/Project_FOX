"""
PO3 自適應結構特徵提取引擎 (Adaptive, Zero-Hardcoded-Threshold)

把前面三課(盤整框 / 操縱掃針 / 前向追蹤)的死板門檻全部拿掉,換成
「統計學相對位置」—— 讓同一套邏輯不靠人為塞答案,就能跨 BTC/ETH/任何時框
自己定義『當下』的盤整、異常掃針與擴張路徑。

零硬編碼的三個自適應雷達:
  1. 自適應盤整(Adaptive Box):震幅 rng 的『滾動分位數』(過去 QUANTILE_LB 根的
     BOX_Q 分位)。當前 rng < 該分位 → 相對於近期自己,現在算窄幅盤整。
     大波動/小波動市場都能各自定義,不寫死 0.95%。
  2. 統計學掃針(Statistical Sweep):影線長度的『滾動分位數』(WICK_Q 分位)。
     只有當這根針的影線長過近期 85% 的普通 K 棒,才算異常強力 Rejection,
     不寫死 0.6×ATR。
  3. 通用管線:run_po3_pipeline(df, symbol, tf) 純靠相對統計運作,
     傳什麼市場/時框進去都能自動圈框、鎖假突破、跑完 MFE/MAE。

★ 誠實聲明:自適應 ≠ edge。分位數(BOX_Q/WICK_Q)與回看窗仍是參數選擇,
  本引擎價值是「通用特徵工程 + 跨市場泛化」這個作品,不是賺錢
  (ROP 類訊號扣費後負期望已被 n=2041 t=-12.27 釘死,見 QUANT_RESEARCH.md)。
"""
import os
import ccxt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# === 自適應參數(全是「相對統計」的設定,不是寫死的價格門檻) ===
WINDOW      = 30      # 量震幅用的滑動窗(根)
QUANTILE_LB = 200     # 滾動分位數的回看長度(根)
BOX_Q       = 0.25    # 震幅低於近期 25% 分位 → 盤整成立
WICK_Q      = 0.85    # 影線長過近期 85% 分位 → 異常掃針
MIN_BARS    = 8       # 盤整至少持續幾根
HUNT_BARS   = 16      # 框後幾根內找掃針
PIERCE_MIN  = 0.0004  # 影線至少刺破框緣的相對比例(極小,僅排除貼邊雜訊)
SL_BUFFER   = 0.0003  # 停損放掃針極值外的微小緩衝
MAX_HOLD    = 60      # 前向追蹤上限(根)


# ---------------------------------------------------------------- 資料
def fetch(symbol, tf, limit=500):
    """抓 + 凍結(每個 市場/時框 一份快取,標本才可重現)。"""
    cache = f"po3_data_{symbol.replace('/', '')}_{tf}.csv"
    if os.path.exists(cache):
        return pd.read_csv(cache)
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    raw = ex.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])
    df.to_csv(cache, index=False)
    return df


def resample_tf(df, rule):
    """把細時框 OHLCV 重採樣成更高時框(供無監督多時框掃描用)。"""
    d = df.copy()
    d["dt"] = pd.to_datetime(d["ts"], unit="ms")
    d = d.set_index("dt")
    out = d.resample(rule).agg({"ts": "first", "open": "first", "high": "max",
                                "low": "min", "close": "last", "vol": "sum"}).dropna()
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- 自適應偵測
def _adaptive_boxes(df):
    """雷達1:用震幅的滾動分位數自己定義盤整,合併成方塊。"""
    rmax = df["high"].rolling(WINDOW).max()
    rmin = df["low"].rolling(WINDOW).min()
    rng  = (rmax - rmin) / rmin
    thr  = rng.rolling(QUANTILE_LB, min_periods=WINDOW * 2).quantile(BOX_Q)
    a = (rng < thr).fillna(False).values

    boxes, i = [], 0
    while i < len(df):
        if a[i]:
            j = i
            while j < len(df) and a[j]:
                j += 1
            if j - i >= MIN_BARS:
                seg = df.iloc[i:j]
                boxes.append((i, j - 1, float(seg["high"].max()), float(seg["low"].min())))
            i = j
        else:
            i += 1
    return boxes


def _wick_thresholds(df):
    """雷達2:上/下影線長度的滾動分位數 → 動態『異常掃針』門檻。"""
    up_wick = df["high"] - df[["open", "close"]].max(axis=1)
    dn_wick = df[["open", "close"]].min(axis=1) - df["low"]
    up_thr = up_wick.rolling(QUANTILE_LB, min_periods=WINDOW * 2).quantile(WICK_Q)
    dn_thr = dn_wick.rolling(QUANTILE_LB, min_periods=WINDOW * 2).quantile(WICK_Q)
    return up_wick.values, dn_wick.values, up_thr.values, dn_thr.values


def _find_manipulations(df, boxes):
    up_wick, dn_wick, up_thr, dn_thr = _wick_thresholds(df)
    events = []
    for (i0, i1, bh, bl) in boxes:
        lo, hi = i1 + 1, min(i1 + HUNT_BARS, len(df) - 1)
        for k in range(lo, hi + 1):
            o, h, l, c = (df["open"].iat[k], df["high"].iat[k],
                          df["low"].iat[k], df["close"].iat[k])
            if not (np.isfinite(up_thr[k]) and np.isfinite(dn_thr[k])):
                continue
            # 上緣誘多掃針:刺破 box_high + 收回框內 + 上影線屬統計異常
            if h > bh * (1 + PIERCE_MIN) and c < bh and up_wick[k] >= up_thr[k]:
                events.append(dict(box=(i0, i1, bh, bl), k=k, side="bear",
                                   level=bh, wick=up_wick[k], thr=up_thr[k],
                                   price_h=h, price_l=l)); break
            # 下緣誘空掃針:刺破 box_low + 收回框內 + 下影線屬統計異常
            if l < bl * (1 - PIERCE_MIN) and c > bl and dn_wick[k] >= dn_thr[k]:
                events.append(dict(box=(i0, i1, bh, bl), k=k, side="bull",
                                   level=bl, wick=dn_wick[k], thr=dn_thr[k],
                                   price_h=h, price_l=l)); break
    return events


def _track_forward(df, e):
    """雷達3:訊號後前向追蹤,算實現 R 與 MFE/MAE。"""
    i0, i1, bh, bl = e["box"]
    k = e["k"]
    if k + 1 >= len(df):
        return None
    entry = float(df["open"].iat[k + 1])
    if e["side"] == "bear":
        sl, tp, direction = e["price_h"] * (1 + SL_BUFFER), bl, -1
        risk = sl - entry
    else:
        sl, tp, direction = e["price_l"] * (1 - SL_BUFFER), bh, +1
        risk = entry - sl
    if risk <= 0:
        return None

    outcome, exit_price, exit_k = "OPEN", None, None
    mfe = mae = 0.0
    end = min(k + 1 + MAX_HOLD, len(df))
    for j in range(k + 1, end):
        h, l = float(df["high"].iat[j]), float(df["low"].iat[j])
        fav = (h - entry) if direction > 0 else (entry - l)
        adv = (entry - l) if direction > 0 else (h - entry)
        mfe, mae = max(mfe, fav), max(mae, adv)
        if direction < 0:
            hit_sl, hit_tp = h >= sl, l <= tp
        else:
            hit_sl, hit_tp = l <= sl, h >= tp
        if hit_sl:                       # 同根雙觸保守當輸
            outcome, exit_price, exit_k = "LOSS", sl, j; break
        if hit_tp:
            outcome, exit_price, exit_k = "WIN", tp, j; break
    if outcome == "OPEN":
        exit_k = end - 1
        exit_price = float(df["close"].iat[exit_k])

    realized_r = direction * (exit_price - entry) / risk
    return dict(entry=entry, sl=sl, tp=tp, entry_k=k + 1, exit_k=exit_k,
                exit_price=exit_price, outcome=outcome, realized_r=realized_r,
                rr_target=abs(tp - entry) / risk, mfe_r=mfe / risk, mae_r=mae / risk)


# ---------------------------------------------------------------- 通用管線
def run_po3_pipeline(df, symbol="?", tf="?") -> pd.DataFrame:
    """
    通用 PO3 三步管線:任何市場/時框的 OHLCV 進來 → 自動圈框、鎖假突破、
    跑完前向追蹤。純靠統計相對位置,零硬編碼價格門檻。
    回傳每個訊號一列的 DataFrame。
    """
    df = df.reset_index(drop=True).copy()
    boxes = _adaptive_boxes(df)
    events = _find_manipulations(df, boxes)
    rows = []
    for e in events:
        t = _track_forward(df, e)
        if t is None:
            continue
        rows.append({
            "symbol": symbol, "tf": tf, "signal_k": e["k"],
            "side": "看跌(空)" if e["side"] == "bear" else "看漲(多)",
            "box_low": round(e["box"][3], 1), "box_high": round(e["box"][2], 1),
            "wick": round(e["wick"], 1), "wick_thr": round(e["thr"], 1),
            "entry": round(t["entry"], 1), "sl": round(t["sl"], 1), "tp": round(t["tp"], 1),
            "outcome": t["outcome"], "rr_target": round(t["rr_target"], 2),
            "realized_r": round(t["realized_r"], 2),
            "mfe_r": round(t["mfe_r"], 2), "mae_r": round(t["mae_r"], 2),
            "entry_k": t["entry_k"], "exit_k": t["exit_k"], "exit_price": round(t["exit_price"], 1),
        })
    cols = ["symbol", "tf", "signal_k", "side", "box_low", "box_high", "wick", "wick_thr",
            "entry", "sl", "tp", "outcome", "rr_target", "realized_r", "mfe_r", "mae_r",
            "entry_k", "exit_k", "exit_price"]
    return pd.DataFrame(rows, columns=cols), boxes, events


# ---------------------------------------------------------------- 畫圖(監工用)
def plot(df, boxes, events, symbol, tf, fname):
    fig, ax = plt.subplots(figsize=(16, 8))
    for i, r in df.iterrows():
        col = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=col, lw=0.6)
        ax.plot([i, i], [r["open"], r["close"]], color=col, lw=2.0)
    for x0, x1, bh, bl in boxes:
        ax.add_patch(Rectangle((x0, bl), x1 - x0, bh - bl,
                               facecolor="#42a5f5", alpha=0.15, edgecolor="#1565c0", lw=1.0))
        ax.hlines([bh, bl], x0, x1, color="#1565c0", lw=0.7, ls=":")
    for e in events:
        k = e["k"]
        t = _track_forward(df, e)
        if e["side"] == "bear":
            ax.scatter([k], [e["price_h"]], marker="v", s=70, color="#d32f2f", zorder=5)
        else:
            ax.scatter([k], [e["price_l"]], marker="^", s=70, color="#2e7d32", zorder=5)
        if t is None:
            continue
        pc = "#2e7d32" if t["outcome"] == "WIN" else ("#d32f2f" if t["outcome"] == "LOSS" else "#9e9e9e")
        ax.plot([t["entry_k"], t["exit_k"]], [t["entry"], t["exit_price"]], color=pc, lw=1.6, ls="--", zorder=6)
        ax.scatter([t["entry_k"]], [t["entry"]], marker="o", s=45, facecolor="white", edgecolor="#212121", lw=1.2, zorder=7)
        ax.scatter([t["exit_k"]], [t["exit_price"]], marker="X", s=80, color=pc, zorder=7)
        ax.annotate(f"{t['realized_r']:+.2f}R", xy=(t["exit_k"], t["exit_price"]),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=pc, fontweight="bold")
    ax.add_patch(Rectangle((0, 0), 0, 0, facecolor="#42a5f5", alpha=0.4, label="自適應盤整框"))
    ax.scatter([], [], marker="v", color="#d32f2f", label="統計異常掃針→看跌")
    ax.scatter([], [], marker="^", color="#2e7d32", label="統計異常掃針→看漲")
    ax.set_title(f"{symbol} {tf} — PO3 自適應引擎(零硬編碼,分位數自定義結構)", fontsize=13)
    ax.set_xlabel("K 線序號"); ax.set_ylabel("價格"); ax.legend(loc="best", fontsize=9); ax.grid(alpha=0.12)
    fig.tight_layout(); fig.savefig(fname, dpi=110); plt.close(fig)


# ---------------------------------------------------------------- 多市場示範
def main():
    targets = [("BTC/USDT", "5m"), ("ETH/USDT", "5m"), ("BTC/USDT", "15m")]
    all_sig = []
    for symbol, tf in targets:
        df = fetch(symbol, tf)
        res, boxes, events = run_po3_pipeline(df, symbol, tf)
        fname = f"po3_engine_{symbol.replace('/', '')}_{tf}.png"
        plot(df, boxes, events, symbol, tf, fname)
        print(f"\n■ {symbol} {tf}  ({len(df)}根) → 框 {len(boxes)} 個,訊號 {len(res)} 個 → {fname}")
        if len(res):
            print(res[["signal_k", "side", "outcome", "rr_target", "realized_r", "mfe_r", "mae_r"]].to_string(index=False))
            all_sig.append(res)

    print("\n" + "=" * 64)
    if all_sig:
        agg = pd.concat(all_sig, ignore_index=True)
        r = agg["realized_r"]
        wins = int((r > 0).sum())
        print(f"跨市場合計 n={len(agg)}  勝 {wins}/{len(agg)}  總計 {r.sum():+.2f}R  平均 {r.mean():+.2f}R/筆")
        agg.to_csv("po3_engine_signals.csv", index=False)
        print("✓ 全訊號表 → po3_engine_signals.csv")
    print("⚠ 自適應 ≠ edge:分位數/回看窗仍是參數,n 仍小。作品價值=跨市場通用特徵工程,非賺錢。")


if __name__ == "__main__":
    main()
