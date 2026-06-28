"""
smc_coach.py — SMC 教練面板 v2

多時框方向 + 結構(BOS/CHoCH) + 訂單區/FVG缺口 + 下降通道 + 7步驟進場流程 + 停損目標，
畫成一張「教練面板」圖(左：標註K線；右：狀態表)。輸出 assets/smc_coach.png。

★ 誠實聲明：SMC 已驗證扣費後無 edge(t=-12.27)。此為「看盤輔助/盤感教練/作品展示」，
  不是賺錢訊號。所有判定為啟發式，數字僅供相對參考。

用法：python smc_coach.py
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
TFS_DIR = ["1d", "4h", "1h", "15m"]
BARS    = 180
SWING_K = 2
CHAN_LB = 110          # 通道回歸取最近幾根


def make_ex():
    return ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})


def fetch(ex, tf, n):
    o = ex.fetch_ohlcv(SYMBOL, tf, limit=n)
    return pd.DataFrame(o, columns=["ts", "open", "high", "low", "close", "vol"])


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def tf_direction(df):
    e = ema(df["close"], 50)
    up = df["close"].iat[-1] > e.iat[-1]
    slope = e.iat[-1] - e.iat[-6]
    if up and slope > 0:   return "多"
    if not up and slope < 0: return "空"
    return "盤"


def swings(df, k=SWING_K):
    h, l = df["high"].values, df["low"].values
    hi, lo = [], []
    for i in range(k, len(df) - k):
        if h[i] == max(h[i - k:i + k + 1]): hi.append(i)
        if l[i] == min(l[i - k:i + k + 1]): lo.append(i)
    return hi, lo


def structure(df):
    hi, lo = swings(df)
    events, trend, last_h, last_l = [], None, None, None
    for i, t in sorted([(i, "H") for i in hi] + [(i, "L") for i in lo]):
        if t == "H":
            if last_h is not None and df["high"].iat[i] > df["high"].iat[last_h]:
                events.append((i, "BOS" if trend == "up" else "CHoCH", "↑")); trend = "up"
            last_h = i
        else:
            if last_l is not None and df["low"].iat[i] < df["low"].iat[last_l]:
                events.append((i, "BOS" if trend == "down" else "CHoCH", "↓")); trend = "down"
            last_l = i
    return events


def fvg(df):
    out = []
    for i in range(2, len(df)):
        h0, l0 = df["high"].iat[i - 2], df["low"].iat[i - 2]
        h2, l2 = df["high"].iat[i], df["low"].iat[i]
        if l2 > h0:   out.append((i, h0, l2, "bull"))
        elif h2 < l0: out.append((i, h2, l0, "bear"))
    return out[-2:]


def order_blocks(df, events, bias):
    """訂單區：結構破壞前最後一根反向 K 棒。回傳 [(i, lo, hi, label)]。"""
    obs = []
    want = "↓" if bias == "空" else "↑"
    for i, ev, d in [e for e in events if e[2] == want][-2:]:
        # 破壞前 10 根內，找最後一根反向(空方找紅前的綠、多方找綠前的紅)收斂 K
        rng = range(max(0, i - 10), i)
        cand = [j for j in rng if (df["close"].iat[j] >= df["open"].iat[j]) == (bias == "空")]
        if not cand:
            continue
        j = cand[-1]
        lo, hi = min(df["open"].iat[j], df["close"].iat[j]), max(df["open"].iat[j], df["close"].iat[j])
        obs.append((j, df["low"].iat[j] if bias == "空" else lo,
                    df["high"].iat[j] if bias == "空" else hi,
                    "空方訂單區" if bias == "空" else "多方訂單區"))
    return obs


def channel(df, lb=CHAN_LB):
    seg = df.iloc[-lb:]
    x = np.arange(len(seg))
    m, b = np.polyfit(x, seg["close"].values, 1)
    mid = m * x + b
    up = (seg["high"].values - mid).max()
    dn = (seg["low"].values - mid).min()
    xs = np.arange(len(df) - lb, len(df))
    return xs, mid + up, mid + dn, m


def render(df, dirs, events, gaps, obs, steps, bias, extra):
    fig = plt.figure(figsize=(16, 9), facecolor="#0d0f14")
    gs = fig.add_gridspec(1, 20)
    ax = fig.add_subplot(gs[0, :13]); axp = fig.add_subplot(gs[0, 13:])
    for a in (ax, axp): a.set_facecolor("#0d0f14")

    for i, r in df.iterrows():
        c = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=c, lw=0.6)
        ax.plot([i, i], [r["open"], r["close"]], color=c, lw=2.2)
    ax.plot(df.index, ema(df["close"], 50), color="#ffd54f", lw=1.2)

    # 下降通道
    xs, up_line, dn_line, slope = extra["chan"]
    ax.plot(xs, up_line, color="#26c6da", lw=1.1, alpha=0.8)
    ax.plot(xs, dn_line, color="#26c6da", lw=1.1, alpha=0.8)

    # 訂單區(延伸到右；標籤放框「下緣左側」，與缺口錯開)
    for j, lo, hi, label in obs:
        ax.add_patch(Rectangle((j, lo), len(df) - j, hi - lo,
                               facecolor="#6d4c41", alpha=0.30, edgecolor="#a1887f", lw=1.0))
        ax.text(j + 1, lo, label, color="#d7ccc8", fontsize=8, va="top", ha="left")

    # FVG 缺口(標籤放框「上緣左側」，與訂單區上下分開避免重疊)
    for i, lo, hi, kind in gaps:
        col = "#7e57c2" if kind == "bear" else "#5c6bc0"
        ax.add_patch(Rectangle((i - 2, lo), len(df) - i + 2, hi - lo,
                               facecolor=col, alpha=0.16, edgecolor=col, lw=0.7))
        ax.text(i - 2, hi, "空方缺口" if kind == "bear" else "多方缺口",
                color=col, fontsize=7, va="bottom", ha="left")

    # 結構標記
    for i, ev, d in events[-12:]:
        y = df["high"].iat[i] if d == "↑" else df["low"].iat[i]
        col = "#ef5350" if ev == "CHoCH" else "#ffa726"
        ax.text(i, y, f"{ev}{d}", color=col, fontsize=7,
                va="bottom" if d == "↑" else "top", ha="center")

    ax.set_title(f"{SYMBOL}  {MAIN_TF}  ·  SMC 教練", color="#e0e0e0", fontsize=13)
    ax.tick_params(colors="#5B7494"); ax.grid(alpha=0.08)
    for s in ax.spines.values(): s.set_color("#2a2f3a")

    # ── 右側面板 ──
    axp.axis("off")
    rows = [
        ("SMC 教練", f"自動：{bias}單｜同向{bias}方推進", "#1b5e20"),
        ("方向", " ｜ ".join(f"{tf.upper()} {d}" for tf, d in dirs.items()), "#263238"),
        ("進場進度", extra["progress"], "#1b5e20" if extra["ready"] else "#263238"),
        ("高週期區域", extra["htf"], "#4e342e"),
        ("停損／目標", extra["sl_tp"], "#263238"),
        ("1H 通道", extra["chan_txt"], "#5d4037"),
        ("持倉", "無持倉｜方向觀察", "#263238"),
        ("程式正在等待", f"{bias}方轉向確認流程", "#1b5e20"),
    ]
    y = 0.985
    for label, val, bg in rows:
        axp.add_patch(Rectangle((0.0, y - 0.05), 0.30, 0.048, transform=axp.transAxes,
                                facecolor="#37474f", edgecolor="none"))
        axp.add_patch(Rectangle((0.30, y - 0.05), 0.70, 0.048, transform=axp.transAxes,
                                facecolor=bg, edgecolor="none"))
        axp.text(0.02, y - 0.026, label, color="#cfd8dc", fontsize=8.3, va="center")
        axp.text(0.32, y - 0.026, val, color="#ffffff", fontsize=8.0, va="center")
        y -= 0.055
    y -= 0.015
    for name, status, ok in steps:
        col = "#26a69a" if ok else "#78909c"
        axp.add_patch(Rectangle((0.0, y - 0.046), 1.0, 0.044, transform=axp.transAxes,
                                facecolor="#1a1f28", edgecolor="#2a2f3a", lw=0.5))
        axp.text(0.02, y - 0.024, f"步驟 {name}", color="#b0bec5", fontsize=8, va="center")
        axp.text(0.42, y - 0.024, f"{'●' if ok else '○'} {status}", color=col, fontsize=8, va="center")
        y -= 0.05

    fig.tight_layout()
    return fig                                  # 回傳 Figure(供網頁 st.pyplot / CLI 存檔)


def seven_steps(df, bias, events, gaps):
    last = df.iloc[-1]
    recent = [e for e in events if e[0] >= len(df) - 40]
    has_choch = any(e[1] == "CHoCH" and (e[2] == "↓") == (bias == "空") for e in recent)
    has_bos = any(e[1] == "BOS" and (e[2] == "↓") == (bias == "空") for e in recent)
    swept = (df["high"].iloc[-20:].idxmax() >= len(df) - 6) if bias == "空" else \
            (df["low"].iloc[-20:].idxmin() >= len(df) - 6)
    in_zone = any(g[3] == ("bear" if bias == "空" else "bull") for g in gaps)
    react = (last["close"] < last["open"]) if bias == "空" else (last["close"] > last["open"])
    return [
        ("1 方向", f"多時框{bias}向推進", True),
        ("2 區域", "進入訂單區/FVG" if in_zone else "等待進入區域", in_zone),
        ("3 掃蕩", "掃過前高/前低" if swept else "尚未掃蕩", bool(swept)),
        ("4 轉向", "MSS/CHoCH 完成" if has_choch else "等待轉向", has_choch),
        ("5 延續", "BOS 完成" if has_bos else "等待延續", has_bos),
        ("6 回測", "回測新區" if in_zone and has_bos else "等待回測", in_zone and has_bos),
        ("7 反應K", "反應 K 完成" if react and has_bos else "等待反應 K", react and has_bos),
    ]


def build_coach(ex=None, symbol=SYMBOL, main_tf=MAIN_TF):
    """跑完整 SMC 教練分析，回傳 (fig, summary)。供網頁 st.pyplot 與 CLI 共用。"""
    ex = ex or make_ex()
    global SYMBOL, MAIN_TF
    SYMBOL, MAIN_TF = symbol, main_tf
    dir_dfs = {tf: fetch(ex, tf, 120) for tf in TFS_DIR}
    dirs = {tf: tf_direction(d) for tf, d in dir_dfs.items()}
    bias = "空" if list(dirs.values()).count("空") >= list(dirs.values()).count("多") else "多"
    df = fetch(ex, main_tf, BARS)
    events = structure(df)
    gaps = fvg(df)
    obs = order_blocks(df, events, bias)
    steps = seven_steps(df, bias, events, gaps)

    # 面板細節
    sw_hi = df["high"].iloc[-30:].max(); sw_lo = df["low"].iloc[-30:].min()
    if bias == "空":
        sl, tp = sw_hi * 1.001, sw_lo
    else:
        sl, tp = sw_lo * 0.999, sw_hi
    done = sum(1 for _, _, ok in steps if ok)
    zone = obs[-1] if obs else (gaps[-1] if gaps else None)
    if zone and len(zone) >= 3:
        zlo, zhi = (zone[1], zone[2])
        progress = (f"進場條件完成 {zlo:,.1f}～{zhi:,.1f}" if done >= 6 else f"等待 第{done+1}步({zlo:,.1f}～{zhi:,.1f})")
    else:
        progress = f"等待 第{done+1}步"
    # 高週期區域：1H 最近的反向擺動
    h1 = dir_dfs["1h"]; h1_hi, h1_lo = swings(h1)
    if bias == "空" and h1_hi:
        nearest = h1["high"].iat[h1_hi[-1]]
        htf = f"上方最近 1H 區域 @ {nearest:,.1f}"
    elif h1_lo:
        nearest = h1["low"].iat[h1_lo[-1]]
        htf = f"下方最近 1H 區域 @ {nearest:,.1f}"
    else:
        htf = "—"
    # 1H 通道
    _, _, h1_dn, h1_slope = channel(h1, min(CHAN_LB, len(h1) - 1))
    chan_dir = "下降通道" if h1_slope < 0 else "上升通道"
    broke = "·跌破下軌" if (h1_slope < 0 and h1["close"].iat[-1] < h1_dn[-1]) else "·軌道內"
    extra = {
        "chan": channel(df), "progress": progress, "ready": done >= 6,
        "htf": htf, "sl_tp": f"{sl:,.1f} 上方 ／ 目標 {tp:,.1f}" if bias == "空" else f"{sl:,.1f} 下方 ／ 目標 {tp:,.1f}",
        "chan_txt": chan_dir + broke,
    }
    fig = render(df, dirs, events, gaps, obs, steps, bias, extra)
    summary = {"dirs": dirs, "bias": bias, "n_struct": len(events),
               "n_fvg": len(gaps), "n_ob": len(obs),
               "progress": progress, "sl_tp": extra["sl_tp"], "chan": extra["chan_txt"]}
    return fig, summary


def main():
    print(f"抓 {SYMBOL} 多時框…")
    fig, s = build_coach()
    fig.savefig("assets/smc_coach.png", dpi=110, facecolor="#0d0f14")
    import matplotlib.pyplot as _plt; _plt.close(fig)
    print(f"  方向 {s['dirs']} → 偏{s['bias']}　結構{s['n_struct']}　FVG{s['n_fvg']}　訂單區{s['n_ob']}")
    print(f"  進場進度: {s['progress']} | SL/TP: {s['sl_tp']} | 1H: {s['chan']}")
    print("✓ 已輸出 assets/smc_coach.png")


if __name__ == "__main__":
    main()
