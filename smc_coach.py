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
CHAN_LB = 70           # 通道取最近幾根(貼近當前趨勢段)


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


def channel(df, lb=CHAN_LB, project=20):
    """沿最近一段擺動結構畫通道：用擺動高/低點回歸定方向，平行包住價格，並向前虛線投影。
    回傳 (xs, up_line, dn_line, slope)。xs 含向前 project 根的投影。"""
    n0 = max(0, len(df) - lb)
    seg = df.iloc[n0:]
    x = np.arange(len(seg))
    hi, lo = swings(seg)
    # 優先用擺動點定斜率(更貼結構)；點太少才退回全段回歸
    pts_x = (hi + lo)
    if len(pts_x) >= 4:
        ph = [seg["high"].iat[i] for i in hi] + [seg["low"].iat[i] for i in lo]
        m = np.polyfit(pts_x, ph, 1)[0]
    else:
        m = np.polyfit(x, seg["close"].values, 1)[0]
    mid = m * x + (seg["close"].values - m * x).mean()
    up = (seg["high"].values - mid).max()
    dn = (seg["low"].values - mid).min()
    xs = np.arange(n0, len(df) + project)            # 含向前投影
    base = m * (xs - n0) + (seg["close"].values - m * x).mean()
    return xs, base + up, base + dn, m, len(seg)


def render(df, events, gaps, obs, extra):
    """只畫 K 線圖(結構/訂單區/缺口/通道)；右側面板交給網頁原生 HTML 渲染(字才不糊)。"""
    fig, ax = plt.subplots(figsize=(14, 7.6), facecolor="#0d0f14")
    ax.set_facecolor("#0d0f14")
    for i, r in df.iterrows():
        c = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=c, lw=0.6)
        ax.plot([i, i], [r["open"], r["close"]], color=c, lw=2.2)
    ax.plot(df.index, ema(df["close"], 50), color="#ffd54f", lw=1.2)

    # 通道 — A方案：只有趨勢盤(ER夠高)才畫；C方案：畫得淡、退居二線
    xs, up_line, dn_line, slope, seglen = extra["chan"]
    if extra.get("chan_on"):
        nreal = (xs < len(df)).sum()
        for line in (up_line, dn_line):
            ax.plot(xs[:nreal], line[:nreal], color="#4dd0e1", lw=0.8, alpha=0.40, ls=(0, (5, 4)))
            ax.plot(xs[nreal - 1:], line[nreal - 1:], color="#4dd0e1", lw=0.7, alpha=0.25, ls="--")
    else:
        ax.text(0.012, 0.97, f"震盪盤 · 通道休眠 (ER {extra.get('er', 0):.2f})",
                transform=ax.transAxes, color="#607d8b", fontsize=9, va="top")

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

    # 結構標記(減量,只留最近 7 個,降低雜訊)
    for i, ev, d in events[-7:]:
        y = df["high"].iat[i] if d == "↑" else df["low"].iat[i]
        col = "#ef5350" if ev == "CHoCH" else "#ffa726"
        ax.text(i, y, f"{ev}{d}", color=col, fontsize=7,
                va="bottom" if d == "↑" else "top", ha="center")

    ax.set_title(f"{SYMBOL}  {MAIN_TF}  ·  SMC 教練", color="#e0e0e0", fontsize=13)
    ax.tick_params(colors="#5B7494"); ax.grid(alpha=0.08)
    for s in ax.spines.values(): s.set_color("#2a2f3a")
    fig.tight_layout()
    return fig                                  # 純 K 線圖(面板用網頁 HTML 另畫)


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


def build_coach(ex=None, symbol=SYMBOL, main_tf=MAIN_TF, draw=True):
    """跑完整 SMC 教練分析，回傳 (fig, panel)。draw=False 時不畫圖(fig=None，供交易器省資源)。"""
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
    # 1H 通道（資訊用）
    _, _, h1_dn, h1_slope, _ = channel(h1, min(CHAN_LB, len(h1) - 1), project=0)
    chan_dir = "下降通道" if h1_slope < 0 else "上升通道"
    broke = "·跌破下軌" if (h1_slope < 0 and h1["close"].iat[-1] < h1_dn[-1]) else "·軌道內"
    # A 方案：效率比率閘門 — 只有「明確趨勢盤」才畫主圖通道，震盪盤休眠（不硬畫）
    _seg = df["close"].iloc[-CHAN_LB:].values
    er = abs(_seg[-1] - _seg[0]) / (np.abs(np.diff(_seg)).sum() or 1)
    chan_on = er >= 0.40
    extra = {
        "chan": channel(df), "chan_on": chan_on, "er": er,
        "progress": progress, "ready": done >= 6,
        "htf": htf, "sl_tp": f"{sl:,.1f} 上方 ／ 目標 {tp:,.1f}" if bias == "空" else f"{sl:,.1f} 下方 ／ 目標 {tp:,.1f}",
        "chan_txt": (chan_dir + broke) if chan_on else f"震盪盤·通道休眠 (ER {er:.2f})",
    }
    fig = render(df, events, gaps, obs, extra) if draw else None
    # 標籤誠實反映時框共振度(只顯示,不影響 bias/收集器):
    #   4/4 共振、3/4 偏向、2/4 平手分歧(此時「空」是 line201 的 >= 平手規則挑的,非真共振)
    _nal = list(dirs.values()).count(bias)
    _ntf = len(dirs)
    if _nal == _ntf:
        _hdr, _hc = f"{bias}向共振 {_nal}/{_ntf}｜多時框一致", "#1b5e20"
    elif _nal > _ntf - _nal:
        _hdr, _hc = f"偏{bias} {_nal}/{_ntf}｜主要時框同向", "#33691e"
    else:
        _hdr, _hc = f"時框分歧 {_nal}/{_ntf}｜非共振，觀望（bias 平手取{bias}）", "#5d4037"
    panel = {
        "rows": [
            ("SMC 教練", _hdr, _hc),
            ("方向", " ｜ ".join(f"{tf.upper()} {d}" for tf, d in dirs.items()), "#263238"),
            ("進場進度", progress, "#1b5e20" if done >= 6 else "#263238"),
            ("高週期區域", htf, "#4e342e"),
            ("停損／目標", extra["sl_tp"], "#263238"),
            ("1H 通道", extra["chan_txt"], "#5d4037"),
            ("持倉", "無持倉｜方向觀察", "#263238"),
            ("程式正在等待", f"{bias}方轉向確認流程", "#1b5e20"),
        ],
        "steps": steps,
        "summary": {"dirs": dirs, "bias": bias, "n_struct": len(events),
                    "n_fvg": len(gaps), "n_ob": len(obs), "chan": extra["chan_txt"],
                    # 數值版進場資訊(供自動交易器用)
                    "price": float(df["close"].iat[-1]), "sl": float(sl), "tp": float(tp),
                    "all_pass": all(ok for _, _, ok in steps), "n_pass": done,
                    "er": float(er), "n_align": list(dirs.values()).count(bias)},
    }
    return fig, panel


def main():
    print(f"抓 {SYMBOL} 多時框…")
    fig, panel = build_coach()
    fig.savefig("assets/smc_coach.png", dpi=170, facecolor="#0d0f14")
    import matplotlib.pyplot as _plt; _plt.close(fig)
    print("── 教練面板 ──")
    for label, val, _ in panel["rows"]:
        print(f"  {label}：{val}")
    for name, status, ok in panel["steps"]:
        print(f"  步驟 {name}  {'●' if ok else '○'} {status}")
    print("✓ 已輸出 assets/smc_coach.png(純K線圖；面板上方為文字版)")


if __name__ == "__main__":
    main()
