# -*- coding: utf-8 -*-
"""4h 版早停:先找最佳停止epoch(用留一幣的驗證曲線),再用該epoch數重跑完整LOSO-CV確認準確率。
若準確率跟現行15epoch版打平或更好、且更穩(不再死背訓練集),就用這個epoch數重練正式版取代 cv_model_4h.pt。
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets

from cv_train import SmallCNN, TF, BATCH, LR, symbol_of

torch.manual_seed(42); np.random.seed(42)
DATA_DIR = os.path.join("data_cv_4h")
MAX_EPOCHS = 25
HELD_OUT = "XRP"


def acc(model, loader):
    model.eval(); c = t = 0
    with torch.no_grad():
        for x, y in loader:
            c += (model(x).argmax(1) == y).sum().item(); t += len(x)
    return c / t * 100


def find_best_epoch():
    ds = datasets.ImageFolder(DATA_DIR, transform=TF)
    classes = ds.classes
    tr_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) != HELD_OUT]
    va_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) == HELD_OUT]
    counts = np.bincount([ds.samples[i][1] for i in tr_idx], minlength=len(classes))
    counts = np.where(counts == 0, 1, counts)
    w = torch.tensor(counts.sum() / (len(counts) * counts), dtype=torch.float32)
    tr = DataLoader(Subset(ds, tr_idx), BATCH, shuffle=True)
    va = DataLoader(Subset(ds, va_idx), BATCH)
    model = SmallCNN(len(classes))
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = nn.CrossEntropyLoss(weight=w)
    best_ep, best_va = 0, -1
    print(f"=== 找4h最佳停止點(留{HELD_OUT}驗證) ===")
    for ep in range(1, MAX_EPOCHS + 1):
        model.train()
        for x, y in tr:
            opt.zero_grad(); lossf(model(x), y).backward(); opt.step()
        ta, va_ = acc(model, tr), acc(model, va)
        print(f"epoch {ep:>2}  訓練{ta:5.1f}%  驗證{va_:5.1f}%")
        if va_ > best_va:
            best_va, best_ep = va_, ep
    print(f"\n最佳停止點: epoch {best_ep} (驗證準確率 {best_va:.0f}%)")
    return best_ep


def train_one_fold(ds, tr_idx, va_idx, classes, epochs):
    counts = np.bincount([ds.samples[i][1] for i in tr_idx], minlength=len(classes))
    counts = np.where(counts == 0, 1, counts)
    weights = torch.tensor(counts.sum() / (len(counts) * counts), dtype=torch.float32)
    tr = DataLoader(Subset(ds, tr_idx), BATCH, shuffle=True)
    va = DataLoader(Subset(ds, va_idx), BATCH)
    model = SmallCNN(len(classes))
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = nn.CrossEntropyLoss(weight=weights)
    for _ in range(epochs):
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


def rerun_cv(epochs):
    ds = datasets.ImageFolder(DATA_DIR, transform=TF)
    classes = ds.classes
    syms = sorted({symbol_of(p) for p, _ in ds.samples})
    total_cm = np.zeros((3, 3), dtype=int)
    fold_acc = []
    print(f"\n=== 用 epoch={epochs} 重跑完整 LOSO-CV(對照現行15epoch版87%) ===")
    for held in syms:
        tr_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) != held]
        va_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) == held]
        cm = train_one_fold(ds, tr_idx, va_idx, classes, epochs)
        a = cm.trace() / cm.sum() * 100
        fold_acc.append(a); total_cm += cm
        print(f"  留 {held:<4} → {a:.0f}%")
    print(f"\n=== epoch={epochs} 版 5折平均 {np.mean(fold_acc):.0f}% (±{np.std(fold_acc):.0f}%) ===")
    for i, c in enumerate(classes):
        s = total_cm[i].sum()
        print(f"  {c:<6} {(total_cm[i][i]/s*100 if s else 0):.0f}%")
    return np.mean(fold_acc), total_cm


if __name__ == "__main__":
    best_ep = find_best_epoch()
    rerun_cv(best_ep)
