# -*- coding: utf-8 -*-
"""驗證:如果直接用 4h K 線訓練(而非拿 1h 訓練的模型硬套到 4h),準確率能不能回到 85% 這個水準。

流程與 cv_dataset_gen.py + cv_train.py 完全相同,只把時框換成 4h,
輸出到獨立資料夾 data_cv_4h/,不動 data_cv/ 或正式的 cv_model.pt。
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import ccxt
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms

from cv_train import SmallCNN, IMG, BATCH, EPOCHS, LR

torch.manual_seed(42); np.random.seed(42)

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
TIMEFRAME = "4h"
BARS, WINDOW, STRIDE = 1000, 100, 8
SLOPE_UP, SLOPE_DN = 0.05, -0.05
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_cv_4h")

TF = transforms.Compose([
    transforms.Grayscale(1),
    transforms.Resize((IMG, IMG)),
    transforms.ToTensor(),
    transforms.Normalize([0.5], [0.5]),
])


def fetch(symbol):
    ex = ccxt.binance({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=BARS)
    return pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])


def label_window(seg):
    y = seg["close"].values
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    slope_pct = slope / y.mean() * 100
    if slope_pct > SLOPE_UP: return "up"
    if slope_pct < SLOPE_DN: return "down"
    return "range"


def render(seg, path):
    fig, ax = plt.subplots(figsize=(128 / 100, 128 / 100), dpi=100)
    for i, (_, r) in enumerate(seg.iterrows()):
        c = "#000000" if r["close"] >= r["open"] else "#bbbbbb"
        ax.plot([i, i], [r["low"], r["high"]], color=c, linewidth=0.5)
        ax.plot([i, i], [r["open"], r["close"]], color=c, linewidth=1.6)
    ax.axis("off"); ax.margins(0.01)
    fig.savefig(path, dpi=100, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


def gen_dataset():
    for cls in ["up", "down", "range"]:
        os.makedirs(os.path.join(OUT_DIR, cls), exist_ok=True)
    counts = {"up": 0, "down": 0, "range": 0}
    for sym in SYMBOLS:
        df = fetch(sym)
        tag = sym.split("/")[0]
        made = 0
        for start in range(0, len(df) - WINDOW, STRIDE):
            seg = df.iloc[start:start + WINDOW].reset_index(drop=True)
            cls = label_window(seg)
            render(seg, os.path.join(OUT_DIR, cls, f"{tag}_{start:04d}.png"))
            counts[cls] += 1; made += 1
        print(f"  {sym}: 產生 {made} 張")
    total = sum(counts.values())
    print(f"共 {total} 張 → {OUT_DIR}/")
    for cls, n in counts.items():
        print(f"   {cls:<6} {n:>4} 張 ({n/total*100:.0f}%)")


def symbol_of(path):
    return os.path.basename(path).split("_")[0]


def train_one_fold(ds, tr_idx, va_idx, classes):
    counts = np.bincount([ds.samples[i][1] for i in tr_idx], minlength=len(classes))
    counts = np.where(counts == 0, 1, counts)
    weights = torch.tensor(counts.sum() / (len(counts) * counts), dtype=torch.float32)
    tr = DataLoader(Subset(ds, tr_idx), BATCH, shuffle=True)
    va = DataLoader(Subset(ds, va_idx), BATCH)
    model = SmallCNN(len(classes))
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = nn.CrossEntropyLoss(weight=weights)
    for _ in range(EPOCHS):
        model.train()
        for x, y in tr:
            opt.zero_grad(); lossf(model(x), y).backward(); opt.step()
    model.eval()
    cm = np.zeros((len(classes), len(classes)), dtype=int)
    with torch.no_grad():
        for x, y in va:
            pred = model(x).argmax(1)
            for t, p in zip(y.numpy(), pred.numpy()):
                cm[t][p] += 1
    return cm


def run_cv():
    ds = datasets.ImageFolder(OUT_DIR, transform=TF)
    classes = ds.classes
    syms = sorted({symbol_of(p) for p, _ in ds.samples})
    counts = np.bincount([y for _, y in ds.samples], minlength=len(classes))
    majority = counts.max() / counts.sum() * 100
    print(f"\n類別 {classes}｜商品 {syms}｜全資料 {dict(zip(classes, counts.tolist()))}")
    print(f"基準線 = {majority:.0f}%")
    total_cm = np.zeros((len(classes), len(classes)), dtype=int)
    fold_acc = []
    for held in syms:
        tr_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) != held]
        va_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) == held]
        cm = train_one_fold(ds, tr_idx, va_idx, classes)
        acc = cm.trace() / cm.sum() * 100
        fold_acc.append(acc); total_cm += cm
        print(f"  留 {held:<4} 驗證 → {acc:.0f}%  ({cm.trace()}/{cm.sum()})")
    print(f"\n=== 4h 專用模型 5折平均準確率 {np.mean(fold_acc):.0f}% (±{np.std(fold_acc):.0f}%) vs 基準 {majority:.0f}% ===")
    print("混淆矩陣(列=真實,欄=預測):")
    for i, c in enumerate(classes):
        print(f"  {c:>6} " + "  ".join(f"{total_cm[i][j]:>5}" for j in range(len(classes))))
    print("\n每類別準確率:")
    for i, c in enumerate(classes):
        s = total_cm[i].sum()
        print(f"  {c:<6} {(total_cm[i][i]/s*100 if s else 0):.0f}%")


if __name__ == "__main__":
    print("=== 產生 4h 專用資料集 ===")
    gen_dataset()
    print("\n=== 用 4h 資料重新訓練+驗證(對照原本套用時的 53%) ===")
    run_cv()
