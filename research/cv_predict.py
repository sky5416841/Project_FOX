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


def render_readable(df, path):
    """畫一張人看得懂的 K 線圖(有顏色/價格軸),給使用者對照『我看起來是不是也這樣』。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 3.4), dpi=110)
    for i, r in df.reset_index(drop=True).iterrows():
        col = "#26a69a" if r["close"] >= r["open"] else "#ef5350"
        ax.plot([i, i], [r["low"], r["high"]], color=col, linewidth=0.7)
        ax.plot([i, i], [r["open"], r["close"]], color=col, linewidth=2.6)
    ax.set_xticks([]); ax.grid(axis="y", alpha=0.15)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    fig.patch.set_facecolor("#0e1117"); ax.set_facecolor("#0e1117")
    ax.tick_params(colors="#8899a6", labelsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110, bbox_inches="tight", facecolor="#0e1117")
    plt.close(fig)


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
    chart = os.path.join(tempfile.gettempdir(), "cv_live_readable.png")
    render_readable(df, chart)
    return {"symbol": symbol, "tf": tf, "n": WINDOW, "probs": probs,
            "cls": cls, "conf": probs[cls], "label_tw": TW[cls], "setup": SETUP[cls],
            "chart": chart}


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
