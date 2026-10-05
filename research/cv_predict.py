"""
cv_predict.py — 用訓練好的 CNN 判斷「現在盤面是 上升/下降/盤整」
================================================================
把 cv_model.pt(85% 那個)變成能實際用的工具:抓幣安即時 100 根 K 線 →
畫成跟訓練時一模一樣的圖 → 模型判類別 → 告訴你該用哪個 Setup。

★ 誠實定位:這是「環境偵測器」,不是漲跌預測。
  它分類「現在這張圖長得像什麼」(描述),幫你:
    · 選對 Setup(盤整→A支撐反彈、趨勢→C趨勢回調)
    · 別在震盪盤做趨勢單、別逆著環境交易(失敗複盤證明逆regime會賠)
  它『不會』告訴你接下來漲還是跌 —— 那個沒 edge,別拿它當買賣訊號。

用法:
  python research/cv_predict.py BTC/USDT
  python research/cv_predict.py ETH/USDT --tf 4h
"""
import argparse
import os
import sys
import tempfile

import ccxt
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from torchvision import datasets

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from cv_train import SmallCNN, TF          # noqa: E402
from cv_dataset_gen import render, WINDOW, TIMEFRAME  # noqa: E402

CLASSES = ["down", "range", "up"]          # ImageFolder 字母序(訓練時的順序)
TW = {"down": "📉 下降趨勢", "range": "🟰 盤整/震盪", "up": "📈 上升趨勢"}
SETUP = {
    "up":    "Setup C 趨勢回調（順勢做多；等回調到動態支撐）",
    "down":  "Setup C 趨勢回調（順勢做空；等反彈到動態壓力）",
    "range": "Setup A 支撐反彈（區間來回；到區間邊緣才進）",
}


_MODEL_CACHE = {}


def load_model(path=None):
    """載入模型(依路徑快取,避免每次判斷都重讀硬碟)。"""
    path = path or os.path.join(ROOT, "cv_model.pt")
    if path not in _MODEL_CACHE:
        m = SmallCNN(len(CLASSES))
        m.load_state_dict(torch.load(path, map_location="cpu"))
        m.eval()
        _MODEL_CACHE[path] = m
    return _MODEL_CACHE[path]


def _model_path_for_tf(tf):
    """驗證發現:cv_model.pt 是拿 1h 訓練的,套到 4h 準確率會從85%崩到53%(幾乎塌縮成猜range)。
    改用專門拿 4h 資料訓練的 cv_model_4h.pt(LOSO-CV 87%)。15m/1h 維持用原模型(驗證過84~85%,沒明顯掉)。"""
    if tf == "4h":
        p4h = os.path.join(ROOT, "cv_model_4h.pt")
        if os.path.exists(p4h):
            return p4h
    return os.path.join(ROOT, "cv_model.pt")


def fetch_last(symbol, tf, n):
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    raw = ex.fetch_ohlcv(symbol, tf, limit=n + 1)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])
    return df.iloc[:-1].tail(n).reset_index(drop=True)   # 丟未收完的那根


def _pfmt(x):
    """價格自適應格式:大幣0位、中價2位、低價(ADA/DOGE)多給幾位,不會顯示成0。"""
    ax = abs(x)
    if ax >= 100:
        return f"{x:,.0f}"
    if ax >= 1:
        return f"{x:,.2f}"
    if ax >= 0.01:
        return f"{x:.4f}"
    return f"{x:.6f}"


ER_TREND, ER_CHOP = 0.45, 0.30      # >0.45=趨勢可騎 / <0.30=絞肉別做


def _efficiency_ratio(d, n=30):
    """效率比率 = |淨移動| / Σ|每根變動|(0~1)。高=走得直(趨勢)、低=來回鋸(絞肉)。"""
    c = d["close"].tail(n).to_numpy(dtype=float)
    if len(c) < 3:
        return 0.0
    path = float(np.abs(np.diff(c)).sum())
    return float(abs(c[-1] - c[0]) / path) if path else 0.0


def _two_lights(cls, er):
    """把『方向(燈1)+ ER 品質(燈2)』合成一句可操作判定。回傳 dict。"""
    # 燈1:方向
    if cls == "up":
        l1 = "🟢 做多"
    elif cls == "down":
        l1 = "🟢 做空"
    else:
        l1 = "⚪ 無方向(震盪)"
    # 燈2:ER 品質
    if er >= ER_TREND:
        l2, l2ok = f"🟢 有趨勢可騎(ER {er:.2f})", True
    elif er <= ER_CHOP:
        l2, l2ok = f"🔴 絞肉盤(ER {er:.2f})", False
    else:
        l2, l2ok = f"🟡 中性(ER {er:.2f})", False
    # 綜合(只針對趨勢單 Setup C)
    if cls in ("up", "down"):
        if l2ok:
            verdict = "✅ 兩燈綠 → 可順勢做(回踩+確認再進)"
        else:
            verdict = "⏸ 方向對但無趨勢可騎 → 空手等(絞肉盤做趨勢單=送錢)"
    else:
        verdict = "震盪盤 → 打 Setup A 區間(非趨勢單);ER 低是正常的"
    return {"er": round(er, 3), "light1": l1, "light2": l2, "tradeable": l2ok, "verdict": verdict}


def _calc_levels(d, cls):
    """統一算 Setup 價位(圖與模擬倉共用,確保一致)。趨勢停損=最近20根擺動低/高點外側(結構停損,不被正常回踩掃出),至少留0.5ATR緩衝。"""
    last = float(d["close"].iloc[-1])
    atr = float((d["high"] - d["low"]).tail(14).mean()) or last * 0.01
    if cls in ("range", None):
        hi = float(d["high"].quantile(0.90)); lo = float(d["low"].quantile(0.10))
        entry, sl, tp, side = lo, float(d["low"].min()) * 0.999, hi, "long"
        extra = {"support": lo, "resistance": hi, "width_pct": (hi - lo) / last * 100}
    elif cls == "up":
        ema = float(d["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        swing_low = float(d["low"].tail(20).min())           # 最近擺動低點=結構支撐
        # 停損放支撐下方(正常回踩測支撐不會掃到你);至少留 0.5 ATR 緩衝避免太貼
        sl = min(swing_low * (1 - 0.0015), ema - 0.5 * atr)
        entry, tp, side = ema, float(d["high"].max()), "long"
        if tp <= entry:
            tp = entry + 2 * (entry - sl)
        extra = {"ema": ema, "swing": swing_low}
    else:  # down
        ema = float(d["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        swing_high = float(d["high"].tail(20).max())         # 最近擺動高點=結構壓力
        sl = max(swing_high * (1 + 0.0015), ema + 0.5 * atr) # 停損放壓力上方
        entry, tp, side = ema, float(d["low"].min()), "short"
        if tp >= entry:
            tp = entry - 2 * (sl - entry)
        extra = {"ema": ema, "swing": swing_high}
    rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
    return dict(side=side, entry=entry, sl=sl, tp=tp, rr=rr, last=last, **extra)


def render_readable(df, cls=None):
    """畫一張『交易者看得懂』的圖並回傳 PNG bytes:蠟燭 + EMA + 區間上下緣(支撐壓力)。
    幫使用者看懂為什麼是趨勢/震盪、以及該在哪裡進場(區間邊緣)。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from io import BytesIO
    plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    d = df.reset_index(drop=True)
    n = len(d)
    fig, ax = plt.subplots(figsize=(9.5, 4.2), dpi=110)

    # 蠟燭
    for i, r in d.iterrows():
        col = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=col, linewidth=0.7, zorder=3)
        ax.plot([i, i], [r["open"], r["close"]], color=col, linewidth=2.6, zorder=3)

    # EMA20(看趨勢還是圍著它上下磨=震盪)
    ema = d["close"].ewm(span=20, adjust=False).mean()
    ax.plot(range(n), ema, color="#ffb300", linewidth=1.4, alpha=0.9, label="EMA20", zorder=4)

    last = float(d["close"].iloc[-1])
    lv = _calc_levels(d, cls)                     # 圖與模擬倉共用同一套價位
    _range = cls in ("range", None)
    _entry_lbl = "做多進場／支撐" if _range else "回調進場（EMA）"
    _tp_lbl = "壓力" if _range else "目標"
    for y, txt, c, ls in [
        (lv["tp"],    f"停利／{_tp_lbl} {_pfmt(lv['tp'])}", "#ef9a9a", "-"),
        (lv["entry"], f"{_entry_lbl} {_pfmt(lv['entry'])}", "#66bb6a", "--"),
        (lv["sl"],    f"停損 {_pfmt(lv['sl'])}",            "#ef5350", ":"),
    ]:
        ax.axhline(y, color=c, linestyle=ls, linewidth=1.1, alpha=0.9, zorder=2)
        ax.text(n * 0.005, y, f" {txt}", color=c, fontsize=9, va="bottom", zorder=5)

    if _range:
        lo, hi, wpct = lv["support"], lv["resistance"], lv["width_pct"]
        ax.axhspan(lo, hi, color="#42a5f5", alpha=0.06, zorder=1)
        wide = wpct > 3.0
        warn = "！寬幅震盪·絞肉區" if wide else "窄幅震盪"
        pos = (last - lo) / (hi - lo) if (hi - lo) > 0 else 0.5
        if last < lo:
            state = f"🚫 跌破支撐 {_pfmt(lo)} → 破底風險，別進（等站回上方）"; info_color = "#ef5350"
        elif last <= lo * 1.004:
            state = "🟢 接近支撐 → 等撐住的綠K確認再進（別接刀）"; info_color = "#66bb6a"
        elif pos < 0.5:
            state = "⏳ 在區間中下 → 還沒到支撐，等回落，別追"; info_color = "#ffd54f" if wide else "#b0bec5"
        else:
            state = "⏳ 在區間上半 → 別追多（這裡偏目標區）"; info_color = "#ffd54f" if wide else "#b0bec5"
        info = f"區間寬度 {wpct:.1f}% | {warn} | Setup A R:R約{lv['rr']:.1f}\n{state}"
    else:
        up = (cls == "up")
        # 破位優先判斷:價格已穿過停損 → 趨勢轉弱/轉強,別再喊「可考慮進」(修落後樣板)
        broke = (last < lv["sl"]) if up else (last > lv["sl"])
        not_back = (last > lv["entry"]) if up else (last < lv["entry"])
        if broke:
            _turn = "趨勢轉弱" if up else "趨勢轉強"
            _reclaim = "等重新站上 EMA" if up else "等重新跌回 EMA"
            note = f"已跌破停損 {_pfmt(lv['sl'])} → 破位／{_turn}，別進（{_reclaim}再看）"
            info_color = "#ef5350"
        elif not_back:
            note = f"現價 {_pfmt(last)} 還沒回到 EMA → 等回調到 {_pfmt(lv['entry'])} 才進，別追"
            info_color = "#80cbc4"
        else:
            note = "回調到 EMA 附近（停損上方）→ 等順勢確認 K 再進，別接刀"
            info_color = "#66bb6a"
        info = f"趨勢盤（Setup C {'做多' if up else '做空'}）  R:R 約 {lv['rr']:.1f}\n{note}"

    # 燈2:ER 趨勢品質(趨勢單才在意;震盪盤 ER 低是正常,不加擾)。圖上不用 emoji(字體無彩色glyph→豆腐),顏色已由 info_color 表達
    _er = _efficiency_ratio(df)
    if cls in ("up", "down"):
        if _er >= ER_TREND:
            info += f"\n燈2｜ER {_er:.2f} 有趨勢可騎 → 可做"
        elif _er <= ER_CHOP:
            info += f"\n燈2｜ER {_er:.2f} 絞肉盤 → 空手(趨勢單會被磨死)"
            info_color = "#ef5350"
        else:
            info += f"\n燈2｜ER {_er:.2f} 中性 → 觀望"

    # 最新價
    ax.axhline(last, color="#eceff1", linewidth=0.6, alpha=0.4, zorder=2)
    ax.text(n - 1, last, f" {_pfmt(last)}", color="#eceff1", fontsize=9, va="center", zorder=5)

    ax.text(0.99, 0.02, info, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=10, color=info_color,
            bbox=dict(boxstyle="round", fc="#1a1f28", ec="#37474f", alpha=0.9), zorder=6)

    title = {"up": "上升趨勢", "down": "下降趨勢", "range": "盤整／震盪"}.get(cls, "")
    ax.set_title(f"CNN 判定：{title}" if title else "", color="#cfd8dc", fontsize=10, loc="left")
    ax.set_xticks([]); ax.set_xlim(-1, n); ax.grid(axis="y", alpha=0.12)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color("#37474f")
    fig.patch.set_facecolor("#0e1117"); ax.set_facecolor("#0e1117")
    ax.tick_params(colors="#8899a6", labelsize=8)
    # 不放圖例(金線=均線,一看就懂),免得左上圖例擋到停利/壓力標籤
    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor="#0e1117")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def classify(symbol, tf):
    """回傳判定資料(不印字),給網頁/其他程式呼叫。"""
    df = fetch_last(symbol, tf, WINDOW)
    tmp = os.path.join(tempfile.gettempdir(), "cv_live.png")
    render(df, tmp)
    x = TF(datasets.folder.default_loader(tmp)).unsqueeze(0)
    with torch.no_grad():
        prob = F.softmax(load_model(_model_path_for_tf(tf))(x), dim=1)[0]
    probs = {c: float(prob[i]) for i, c in enumerate(CLASSES)}
    cls = max(probs, key=probs.get)
    lights = _two_lights(cls, _efficiency_ratio(df))
    return {"symbol": symbol, "tf": tf, "n": WINDOW, "probs": probs,
            "cls": cls, "conf": probs[cls], "label_tw": TW[cls], "setup": SETUP[cls],
            "chart_bytes": render_readable(df, cls), "levels": _levels(df, cls),
            "lights": lights}


def _levels(df, cls):
    """回傳 Setup 可用價位 {side,entry,sl,tp,rr},給模擬倉一鍵帶入。用 _calc_levels(與圖同一套)。
    進場=Setup計畫點(震盪@支撐/趨勢@EMA);停損=區間下方 or EMA±1.5ATR;R:R 從計畫進場算。"""
    lv = _calc_levels(df.reset_index(drop=True), cls)
    return {"side": lv["side"], "entry": round(lv["entry"], 6), "sl": round(lv["sl"], 6),
            "tp": round(lv["tp"], 6), "rr": round(lv["rr"], 2), "last": round(lv["last"], 6)}


def predict(symbol, tf):
    r = classify(symbol, tf)
    prob = [r["probs"][c] for c in CLASSES]
    cls, conf = r["cls"], r["conf"]
    print("=" * 58)
    print(f"  盤面辨識 {symbol} {tf}（最近 {WINDOW} 根）")
    print("=" * 58)
    for i, c in enumerate(CLASSES):
        bar = "█" * int(prob[i] * 30)
        print(f"    {TW[c]:<12} {prob[i]*100:>5.1f}%  {bar}")
    print("-" * 58)
    print(f"  判定：{TW[cls]}   信心 {conf*100:.0f}%")
    print(f"  → 建議 setup：{SETUP[cls]}")
    if conf < 0.5:
        print("  ⚠ 信心偏低(三類接近)＝盤面不明確,這種時候最好『空手等』。")
    print("=" * 58)
    print("  ★ 這是環境偵測(選對招式用),不是漲跌預測。別拿它當買賣訊號。")
    return cls, conf


def main():
    ap = argparse.ArgumentParser(description="CNN 盤面型態辨識(環境偵測器)")
    ap.add_argument("symbol", nargs="?", default="BTC/USDT")
    ap.add_argument("--tf", default=TIMEFRAME, help=f"時框(預設 {TIMEFRAME}=訓練用的)")
    args = ap.parse_args()
    predict(args.symbol, args.tf)


if __name__ == "__main__":
    main()
