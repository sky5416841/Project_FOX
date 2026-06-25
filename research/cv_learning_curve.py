"""
C 階段：學習曲線 —— 看 CNN「一個 epoch 一個 epoch 學會」的過程

訓練時記錄每個 epoch 的訓練/驗證準確率與 loss，畫成兩張曲線圖。
- 準確率曲線往上爬 = 模型在學會辨識盤面。
- ★同時畫「訓練線」與「驗證線」：若兩條分岔很大(訓練高、驗證停)＝模型在
  死背訓練資料(過擬合)，這是誠實檢查模型有沒有真的學到「通用型態」的關鍵。

為避免視窗重疊洩漏：驗證集用一個沒參與訓練的商品(預設 XRP)。
"""
import numpy as np
import torch
import torch.nn as nn
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
from torchvision import datasets
from torch.utils.data import DataLoader, Subset

from cv_train import SmallCNN, TF, DATA_DIR, IMG, BATCH, LR, symbol_of

torch.manual_seed(42); np.random.seed(42)

EPOCHS   = 25
HELD_OUT = "XRP"      # 驗證商品（沒參與訓練）


def acc_loss(model, loader, lossf):
    model.eval()
    correct = total = 0
    loss_sum = 0.0
    with torch.no_grad():
        for x, y in loader:
            out = model(x)
            loss_sum += lossf(out, y).item() * len(x)
            correct += (out.argmax(1) == y).sum().item()
            total += len(x)
    return correct / total * 100, loss_sum / total


def main():
    ds = datasets.ImageFolder(DATA_DIR, transform=TF)
    classes = ds.classes
    tr_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) != HELD_OUT]
    va_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) == HELD_OUT]
    counts = np.bincount([ds.samples[i][1] for i in tr_idx], minlength=len(classes))
    counts = np.where(counts == 0, 1, counts)
    w = torch.tensor(counts.sum() / (len(counts) * counts), dtype=torch.float32)
    va_counts = np.bincount([ds.samples[i][1] for i in va_idx], minlength=len(classes))
    baseline = va_counts.max() / va_counts.sum() * 100

    tr = DataLoader(Subset(ds, tr_idx), BATCH, shuffle=True)
    va = DataLoader(Subset(ds, va_idx), BATCH)
    model = SmallCNN(len(classes))
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = nn.CrossEntropyLoss(weight=w)

    hist = {"tr_acc": [], "va_acc": [], "tr_loss": [], "va_loss": []}
    print(f"訓練 {len(tr_idx)} 張、驗證(沒看過的{HELD_OUT}) {len(va_idx)} 張；基準線 {baseline:.0f}%\n")
    for ep in range(1, EPOCHS + 1):
        model.train()
        for x, y in tr:
            opt.zero_grad(); lossf(model(x), y).backward(); opt.step()
        ta, tl = acc_loss(model, tr, lossf)
        va_, vl = acc_loss(model, va, lossf)
        hist["tr_acc"].append(ta); hist["va_acc"].append(va_)
        hist["tr_loss"].append(tl); hist["va_loss"].append(vl)
        print(f"epoch {ep:>2}  訓練準確率 {ta:5.1f}%  驗證準確率 {va_:5.1f}%  (loss {tl:.3f}/{vl:.3f})")

    # ── 畫學習曲線 ────────────────────────────────────────────────────────
    xs = range(1, EPOCHS + 1)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    ax1.plot(xs, hist["tr_acc"], "-o", ms=3, color="#1976d2", label="訓練準確率")
    ax1.plot(xs, hist["va_acc"], "-o", ms=3, color="#d32f2f", label="驗證準確率(沒看過的幣)")
    ax1.axhline(baseline, ls="--", color="#999", label=f"基準線(無腦猜) {baseline:.0f}%")
    ax1.set_title("準確率 — 看模型一個 epoch 一個 epoch 學會"); ax1.set_xlabel("epoch")
    ax1.set_ylabel("準確率 %"); ax1.legend(fontsize=9); ax1.grid(alpha=0.2)

    ax2.plot(xs, hist["tr_loss"], "-o", ms=3, color="#1976d2", label="訓練 loss")
    ax2.plot(xs, hist["va_loss"], "-o", ms=3, color="#d32f2f", label="驗證 loss")
    ax2.set_title("損失(loss) — 越低代表預測越準"); ax2.set_xlabel("epoch")
    ax2.set_ylabel("loss"); ax2.legend(fontsize=9); ax2.grid(alpha=0.2)

    fig.suptitle("CNN 學習曲線：訓練 vs 驗證（兩線分岔=過擬合）", fontsize=13)
    fig.tight_layout()
    fig.savefig("cv_learning_curve.png", dpi=110)
    print("\n✓ 已輸出 cv_learning_curve.png")


if __name__ == "__main__":
    main()
