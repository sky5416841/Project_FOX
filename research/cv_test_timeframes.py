# -*- coding: utf-8 -*-
"""驗證缺口填補:cv_model.pt 是拿 1h K 線訓練出來的(85% LOSO-CV),
但 cv_predict.py 實際拿去判斷的是 4h 和 15m —— 這兩個時框從沒被驗證過。

本程式:
  1. 用同一套標註規則(斜率),分別對 4h、15m 產生獨立的標籤資料集
  2. 直接載入正式上線的 cv_model.pt(不重新訓練),測它在這兩個時框上的準確率
  3. 跟原本 1h 的 85% 對照,回答「模型能不能跨時框使用」這個問題

★ 只讀取 cv_model.pt,不做任何訓練或覆寫 —— 對正式模型零風險。
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import torch
from PIL import Image
import ccxt
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from cv_train import SmallCNN, TF, IMG

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cv_model.pt")
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
WINDOW, STRIDE, BARS = 100, 8, 1000
SLOPE_UP, SLOPE_DN = 0.05, -0.05
CLASSES = ["down", "range", "up"]   # 需與訓練時的 ImageFolder 字母序一致


def fetch(symbol, tf):
    ex = ccxt.binance({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=BARS)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def label_window(seg):
    y = seg["close"].values
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    slope_pct = slope / y.mean() * 100
    if slope_pct > SLOPE_UP: return "up"
    if slope_pct < SLOPE_DN: return "down"
    return "range"


def render_to_array(seg):
    fig, ax = plt.subplots(figsize=(128 / 100, 128 / 100), dpi=100)
    for _, r in seg.iterrows():
        c = "#000000" if r["close"] >= r["open"] else "#bbbbbb"
        ax.plot([_, _], [r["low"], r["high"]], color=c, linewidth=0.5)
        ax.plot([_, _], [r["open"], r["close"]], color=c, linewidth=1.6)
    ax.axis("off"); ax.margins(0.01)
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())
    plt.close(fig)
    return Image.fromarray(buf).convert("RGB")


def load_model():
    m = SmallCNN(len(CLASSES))
    m.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    m.eval()
    return m


def eval_timeframe(model, tf):
    total_cm = np.zeros((3, 3), dtype=int)
    per_symbol = {}
    for sym in SYMBOLS:
        try:
            df = fetch(sym, tf)
        except Exception as e:
            print(f"  {sym} {tf} 抓取失敗:{e}"); continue
        correct = n = 0
        for start in range(0, len(df) - WINDOW, STRIDE):
            seg = df.iloc[start:start + WINDOW].reset_index(drop=True)
            true_cls = label_window(seg)
            img = render_to_array(seg)
            x = TF(img).unsqueeze(0)
            with torch.no_grad():
                pred = CLASSES[model(x).argmax(1).item()]
            ti, pi = CLASSES.index(true_cls), CLASSES.index(pred)
            total_cm[ti][pi] += 1
            correct += (pred == true_cls); n += 1
        per_symbol[sym] = correct / n * 100 if n else 0
        print(f"  {tf} {sym}: {per_symbol[sym]:.0f}% ({correct}/{n})")
    overall = total_cm.trace() / total_cm.sum() * 100
    majority = total_cm.sum(axis=1).max() / total_cm.sum() * 100
    print(f"\n[{tf}] 整體準確率 {overall:.0f}%  vs 基準(瞎猜多數類) {majority:.0f}%")
    print("混淆矩陣(列=真實,欄=預測) down/range/up:")
    for i, c in enumerate(CLASSES):
        print(f"  {c:>6} " + "  ".join(f"{total_cm[i][j]:>4}" for j in range(3)))
    return overall, majority


def main():
    print("=== 跨時框驗證:正式模型(1h訓練, 85%)套用到 4h / 15m 準不準 ===\n")
    model = load_model()
    results = {}
    for tf in ["4h", "15m"]:
        print(f"--- 時框 {tf} ---")
        acc, base = eval_timeframe(model, tf)
        results[tf] = acc
        print()
    print("=== 總結 ===")
    print(f"  1h(原始訓練/驗證)  : 85% (±3~6%)  ← 文件記載/剛重跑過")
    for tf, acc in results.items():
        gap = acc - 85
        print(f"  {tf}(套用,未重訓)   : {acc:.0f}%  差距 {gap:+.0f}%")


if __name__ == "__main__":
    main()
