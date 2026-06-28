"""
smc_coach.py — SMC 教練面板 v1

把多時框方向 + 結構(BOS/CHoCH)+ 訂單區/FVG 缺口 + 7 步驟進場流程，
畫成一張「教練面板」圖（左：標註的 K 線圖；右：狀態表）。輸出 assets/smc_coach.png。

★ 誠實聲明：SMC 已驗證扣費後無 edge(t=-12.27)。這是「視覺化盤感教練/看盤輔助」
  與作品展示用，不是賺錢訊號。判定為啟發式，數字僅供相對參考。

用法：python smc_coach.py   （可改最上面 SYMBOL / MAIN_TF）
"""
import ccxt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

SYMBOL  = "BTC/USDT"
MAIN_TF = "15m"
TFS_DIR = ["1d", "4h", "1h", "15m"]   # 多時框方向（高→低）
BARS    = 180
SWING_K = 2                            # 擺動點：左右各 K 根比較


def make_ex():
    return ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})


def fetch(ex, tf, n):
    o = ex.fetch_ohlcv(SYMBOL, tf, limit=n)
    return pd.DataFrame(o, columns=["ts", "open", "high", "low", "close", "vol"])


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def tf_direction(df):
    """單一時框方向：收盤 vs EMA50 + EMA 斜率。回傳 '多'/'空'/'盤'。"""
    e = ema(df["close"], 50)
    up = df["close"].iat[-1] > e.iat[-1]
    slope = e.iat[-1] - e.iat[-6]
    if up and slope > 0:   return "多"
    if not up and slope < 0: return "空"
    return "盤"


def swings(df, k=SWING_K):
    """碎形擺動高/低點。回傳 (highs_idx, lows_idx)。"""
    h, l = df["high"].values, df["low"].values
    hi, lo = [], []
    for i in range(k, len(df) - k):
        if h[i] == max(h[i - k:i + k + 1]): hi.append(i)
        if l[i] == min(l[i - k:i + k + 1]): lo.append(i)
    return hi, lo


def structure(df):
    """從擺動點推 BOS / CHoCH 標記。回傳 [(idx, 'BOS'/'CHoCH', '↑'/'↓')]。"""
    hi, lo = swings(df)
    events, trend = [], None
    pts = sorted([(i, "H") for i in hi] + [(i, "L") for i in lo])
    last_h = last_l = None
    for i, t in pts:
        if t == "H":
            if last_h is not None and df["high"].iat[i] > df["high"].iat[last_h]:
                ev = "BOS" if trend == "up" else "CHoCH"
                events.append((i, ev, "↑")); trend = "up"
            last_h = i
        else:
            if last_l is not None and df["low"].iat[i] < df["low"].iat[last_l]:
                ev = "BOS" if trend == "down" else "CHoCH"
                events.append((i, ev, "↓")); trend = "down"
            last_l = i
    return events, trend


def fvg(df):
    """3 根 K 棒的失衡缺口。回傳 [(i, low, high, 'bear'/'bull')]（取最近幾個）。"""
    out = []
    for i in range(2, len(df)):
        h0, l0 = df["high"].iat[i - 2], df["low"].iat[i - 2]
        h2, l2 = df["high"].iat[i], df["low"].iat[i]
        if l2 > h0:   out.append((i, h0, l2, "bull"))   # 上方留口（多方缺口）
        elif h2 < l0: out.append((i, h2, l0, "bear"))   # 下方留口（空方缺口）
    return out[-4:]


def seven_steps(df, bias, events):
    """評估 7 步驟進場流程的進度（啟發式）。回傳 [(名稱, 狀態字, 通過?)]。"""
    last = df.iloc[-1]
    recent_ev = [e for e in events if e[0] >= len(df) - 40]
    has_choch = any(e[1] == "CHoCH" and ((e[2] == "↓") == (bias == "空")) for e in recent_ev)
    has_bos = any(e[1] == "BOS" and ((e[2] == "↓") == (bias == "空")) for e in recent_ev)
    swept = (df["high"].iloc[-20:].max() == df["high"].iloc[-20:-1].max()) if bias == "空" else \
            (df["low"].iloc[-20:].min() == df["low"].iloc[-20:-1].min())
    in_zone = any(g[3] == ("bear" if bias == "空" else "bull") for g in fvg(df))
    react = (last["close"] < last["open"]) if bias == "空" else (last["close"] > last["open"])
    steps = [
        ("1 方向", f"多時框{bias}向推進", True),
        ("2 區域", "進入訂單區/FVG" if in_zone else "等待進入區域", in_zone),
        ("3 掃蕩", "掃過前高/前低" if swept else "尚未掃蕩", bool(swept)),
        ("4 轉向", "MSS/CHoCH 完成" if has_choch else "等待轉向", has_choch),
        ("5 延續", "BOS 完成" if has_bos else "等待延續", has_bos),
        ("6 回測", "回測新區" if in_zone and has_bos else "等待回測", in_zone and has_bos),
        ("7 反應K", "反應 K 完成" if react and has_bos else "等待反應 K", react and has_bos),
    ]
    return steps


def render(df, dirs, events, gaps, steps, bias):
    fig = plt.figure(figsize=(16, 9), facecolor="#0d0f14")
    gs = fig.add_gridspec(1, 20)
    ax = fig.add_subplot(gs[0, :13]); axp = fig.add_subplot(gs[0, 13:])
    for a in (ax, axp): a.set_facecolor("#0d0f14")

    # K 線
    for i, r in df.iterrows():
        c = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=c, lw=0.6)
        ax.plot([i, i], [r["open"], r["close"]], color=c, lw=2.2)
    ax.plot(df.index, ema(df["close"], 50), color="#ffd54f", lw=1.2)

    # FVG 缺口 + 訂單區
    for i, lo, hi, kind in gaps:
        col = "#7e57c2" if kind == "bear" else "#5c6bc0"
        ax.add_patch(Rectangle((i - 2, lo), len(df) - i + 2, hi - lo,
                               facecolor=col, alpha=0.18, edgecolor=col, lw=0.8))
        ax.text(len(df) - 1, (lo + hi) / 2, "空方缺口" if kind == "bear" else "多方缺口",
                color=col, fontsize=8, va="center", ha="right")

    # 結構標記
    for i, ev, d in events[-12:]:
        y = df["high"].iat[i] if d == "↑" else df["low"].iat[i]
        col = "#ef5350" if ev == "CHoCH" else "#ffa726"
        ax.text(i, y, f"{ev}{d}", color=col, fontsize=7,
                va="bottom" if d == "↑" else "top", ha="center")

    ax.set_title(f"{SYMBOL}  {MAIN_TF}  ·  SMC 教練", color="#e0e0e0", fontsize=13)
    ax.tick_params(colors="#5B7494"); ax.grid(alpha=0.08)
    for s in ax.spines.values(): s.set_color("#2a2f3a")

    # ── 右側狀態面板 ──
    axp.axis("off")
    rows = [("SMC 教練", f"自動：{bias}單｜同向{bias}方推進", "#1b5e20")]
    rows.append(("方向", " ｜ ".join(f"{tf.upper()} {d}" for tf, d in dirs.items()), "#263238"))
    last = df["close"].iat[-1]
    rows.append(("現價", f"{last:,.1f}", "#263238"))
    rows.append(("整體偏向", f"{bias}方（看高週期方向）", "#4e342e"))
    rows.append(("持倉", "無持倉｜方向觀察", "#263238"))
    rows.append(("程式正在等待", f"{bias}方轉向確認流程", "#1b5e20"))
    y = 0.97
    for label, val, bg in rows:
        axp.add_patch(Rectangle((0.0, y - 0.052), 0.32, 0.05, transform=axp.transAxes,
                                facecolor="#37474f", edgecolor="none"))
        axp.add_patch(Rectangle((0.32, y - 0.052), 0.68, 0.05, transform=axp.transAxes,
                                facecolor=bg, edgecolor="none"))
        axp.text(0.02, y - 0.027, label, color="#cfd8dc", fontsize=8.5, va="center")
        axp.text(0.34, y - 0.027, val, color="#ffffff", fontsize=8.5, va="center")
        y -= 0.058
    y -= 0.02
    for name, status, ok in steps:
        col = "#26a69a" if ok else "#78909c"
        mark = "●" if ok else "○"
        axp.add_patch(Rectangle((0.0, y - 0.05), 1.0, 0.048, transform=axp.transAxes,
                                facecolor="#1a1f28", edgecolor="#2a2f3a", lw=0.5))
        axp.text(0.02, y - 0.026, f"步驟 {name}", color="#b0bec5", fontsize=8, va="center")
        axp.text(0.42, y - 0.026, f"{mark} {status}", color=col, fontsize=8, va="center")
        y -= 0.055

    fig.tight_layout()
    out = "assets/smc_coach.png"
    fig.savefig(out, dpi=110, facecolor="#0d0f14"); plt.close(fig)
    return out


def main():
    ex = make_ex()
    print(f"抓 {SYMBOL} 多時框…")
    dirs = {tf: tf_direction(fetch(ex, tf, 120)) for tf in TFS_DIR}
    bias = "空" if list(dirs.values()).count("空") >= list(dirs.values()).count("多") else "多"
    df = fetch(ex, MAIN_TF, BARS)
    events, _ = structure(df)
    gaps = fvg(df)
    steps = seven_steps(df, bias, events)
    out = render(df, dirs, events, gaps, steps, bias)
    print(f"  多時框方向: {dirs}  → 整體偏{bias}")
    print(f"  結構事件: {len(events)} 個  FVG: {len(gaps)} 個")
    print("  7 步驟:", " | ".join(f"{n}{'✓' if ok else '…'}" for n, _, ok in steps))
    print(f"✓ 已輸出 {out}")


if __name__ == "__main__":
    main()
