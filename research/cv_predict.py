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


def load_model(path=None):
    path = path or os.path.join(ROOT, "cv_model.pt")
    m = SmallCNN(len(CLASSES))
    m.load_state_dict(torch.load(path, map_location="cpu"))
    m.eval()
    return m


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

    last = d["close"].iloc[-1]
    is_range = (cls == "range" or cls is None)

    if is_range:
        # ── 震盪盤 → Setup A:區間上下緣 + 做多進場/停損/停利 + R:R ──
        hi = d["high"].quantile(0.90)
        lo = d["low"].quantile(0.10)
        width_pct = (hi - lo) / last * 100
        wide = width_pct > 3.0                  # 寬幅=危險絞肉
        ax.axhspan(lo, hi, color="#42a5f5", alpha=0.06, zorder=1)
        stop = d["low"].min() * 0.999
        rr = (hi - lo) / (lo - stop) if (lo - stop) > 0 else 0
        for y, txt, c, ls in [
            (hi,   f"停利／壓力 {_pfmt(hi)}",       "#ef9a9a", "-"),
            (lo,   f"做多進場／支撐 {_pfmt(lo)}",    "#66bb6a", "--"),
            (stop, f"停損 {_pfmt(stop)}",           "#ef5350", ":"),
        ]:
            ax.axhline(y, color=c, linestyle=ls, linewidth=1.1, alpha=0.9, zorder=2)
            ax.text(n * 0.005, y, f" {txt}", color=c, fontsize=8, va="bottom", zorder=5)
        warn = "！寬幅震盪·兩邊巴掌·絞肉區" if wide else "窄幅震盪"
        info = f"區間寬度 {width_pct:.1f}%  |  {warn}\nSetup A 做多  R:R 約 {rr:.1f}（碰下緣才進，別追中間）"
        info_color = "#ffd54f" if wide else "#b0bec5"
    else:
        # ── 趨勢盤 → Setup C:以 EMA 為動態支撐/壓力,等回調順勢進(不畫震盪盤價位) ──
        _dir = "做多" if cls == "up" else "做空"
        ax.text(n * 0.005, ema.iloc[-1], " EMA＝動態支撐/壓力（回調到這才進）",
                color="#ffb300", fontsize=8, va="bottom", zorder=5)
        info = (f"趨勢盤（Setup C {_dir}）\n順勢，等回調到 EMA 金線再進，別在半山腰追中間")
        info_color = "#80cbc4"

    # 最新價
    ax.axhline(last, color="#eceff1", linewidth=0.6, alpha=0.4, zorder=2)
    ax.text(n - 1, last, f" {_pfmt(last)}", color="#eceff1", fontsize=8, va="center", zorder=5)

    ax.text(0.99, 0.02, info, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=8, color=info_color,
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
    ax.legend(loc="upper left", fontsize=8, facecolor="#1a1f28", edgecolor="none", labelcolor="#cfd8dc")
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
        prob = F.softmax(load_model()(x), dim=1)[0]
    probs = {c: float(prob[i]) for i, c in enumerate(CLASSES)}
    cls = max(probs, key=probs.get)
    return {"symbol": symbol, "tf": tf, "n": WINDOW, "probs": probs,
            "cls": cls, "conf": probs[cls], "label_tw": TW[cls], "setup": SETUP[cls],
            "chart_bytes": render_readable(df, cls)}


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
