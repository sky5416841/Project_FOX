# -*- coding: utf-8 -*-
"""信心值校準檢查:模型說「95%確定」的時候,是不是真的比說「55%確定」的時候更準?

用留一商品交叉驗證(每折都是模型沒看過的商品,誠實無外洩),
把每筆驗證樣本按信心值分箱,比較每箱的實際準確率。
理想情況:信心值越高,那箱的準確率也應該越高(校準良好)。
若不同信心箱準確率差不多 → 這個百分比只是心理安慰,沒有實質參考價值。

分別驗證:1h(新資料) 與 4h(新資料) 兩個模型。
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset
from torchvision import datasets

from cv_train import SmallCNN, TF, BATCH, EPOCHS, LR, symbol_of

torch.manual_seed(42); np.random.seed(42)

BINS = [(0.0, 0.6), (0.6, 0.75), (0.75, 0.9), (0.9, 1.01)]


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
    confs, corrects = [], []
    with torch.no_grad():
        for x, y in va:
            prob = F.softmax(model(x), dim=1)
            conf, pred = prob.max(1)
            confs.extend(conf.numpy().tolist())
            corrects.extend((pred == y).numpy().tolist())
    return confs, corrects


def run(data_dir, label):
    ds = datasets.ImageFolder(data_dir, transform=TF)
    classes = ds.classes
    syms = sorted({symbol_of(p) for p, _ in ds.samples})
    all_conf, all_correct = [], []
    for held in syms:
        tr_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) != held]
        va_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) == held]
        c, k = train_one_fold(ds, tr_idx, va_idx, classes)
        all_conf.extend(c); all_correct.extend(k)
    all_conf = np.array(all_conf); all_correct = np.array(all_correct)
    print(f"\n=== {label} 信心值校準(n={len(all_conf)}) ===")
    print(f"整體準確率: {all_correct.mean()*100:.0f}%  平均信心值: {all_conf.mean()*100:.0f}%")
    print(f"{'信心區間':<14}{'樣本數':>8}{'實際準確率':>12}{'與信心的落差':>14}")
    for lo, hi in BINS:
        mask = (all_conf >= lo) & (all_conf < hi)
        n = mask.sum()
        if n == 0:
            print(f"  {lo*100:.0f}~{hi*100:.0f}%      (無樣本)"); continue
        acc = all_correct[mask].mean() * 100
        mid_conf = all_conf[mask].mean() * 100
        gap = acc - mid_conf
        print(f"  {lo*100:.0f}~{min(hi,1.0)*100:.0f}%{'':<6}{n:>6}{acc:>11.0f}%{gap:>+13.0f}%")


if __name__ == "__main__":
    run("data_cv", "1h(最新資料)")
    run("data_cv_4h", "4h(最新資料)")
