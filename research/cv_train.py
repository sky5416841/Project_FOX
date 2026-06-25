"""
C 階段第二步：訓練 CNN 辨識 K 線盤面型態（上升/下降/盤整）

★ 嚴謹評估：留一商品交叉驗證 (Leave-One-Symbol-Out Cross-Validation)
  輪流拿 4 個幣訓練、留 1 個「完全沒看過的幣」驗證，跑 5 輪取平均。

  為什麼這樣切？因為 cv_dataset_gen 用滑動視窗(步長8、視窗100)切圖，
  相鄰圖重疊 92%、幾乎相同。若用「隨機」切訓練/驗證，near-duplicate
  會同時落在兩邊 → 資料洩漏(leakage) → 驗證分數灌水。
  按商品切則訓練與驗證集完全不同商品、零重疊，分數才可信。

延續本專案紀律：看每類別準確率/混淆矩陣、與「無腦猜多數類」基準比。
"""
import os
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms

torch.manual_seed(42); np.random.seed(42)

DATA_DIR = "data_cv"
IMG, BATCH, EPOCHS, LR = 64, 32, 15, 1e-3

TF = transforms.Compose([
    transforms.Grayscale(1),
    transforms.Resize((IMG, IMG)),
    transforms.ToTensor(),
    transforms.Normalize([0.5], [0.5]),
])


class SmallCNN(nn.Module):
    def __init__(self, n_cls):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Flatten(),
            nn.Linear(64 * 8 * 8, 128), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(128, n_cls),
        )

    def forward(self, x):
        return self.net(x)


def symbol_of(path):
    return os.path.basename(path).split("_")[0]


def train_one_fold(ds, tr_idx, va_idx, classes):
    counts = np.bincount([ds.samples[i][1] for i in tr_idx], minlength=len(classes))
    counts = np.where(counts == 0, 1, counts)            # 防除以 0
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


def main():
    ds = datasets.ImageFolder(DATA_DIR, transform=TF)
    classes = ds.classes
    syms = sorted({symbol_of(p) for p, _ in ds.samples})
    counts = np.bincount([y for _, y in ds.samples], minlength=len(classes))
    majority = counts.max() / counts.sum() * 100
    print(f"類別 {classes}｜商品 {syms}｜全資料 {dict(zip(classes, counts.tolist()))}")
    print(f"基準線（無腦猜多數類）= {majority:.0f}%")
    print("留一商品交叉驗證（每折驗證集都是沒看過的商品）：\n")

    total_cm = np.zeros((len(classes), len(classes)), dtype=int)
    fold_acc = []
    for held in syms:
        tr_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) != held]
        va_idx = [i for i, (p, _) in enumerate(ds.samples) if symbol_of(p) == held]
        cm = train_one_fold(ds, tr_idx, va_idx, classes)
        acc = cm.trace() / cm.sum() * 100
        fold_acc.append(acc)
        total_cm += cm
        print(f"  留 {held:<4} 驗證 → 準確率 {acc:.0f}%  ({cm.trace()}/{cm.sum()})")

    print(f"\n=== 5 折平均準確率 {np.mean(fold_acc):.0f}%  (±{np.std(fold_acc):.0f}%)  vs 基準 {majority:.0f}% ===")
    print("\n彙總混淆矩陣（列=真實, 欄=預測）：")
    print("        " + "  ".join(f"{c:>6}" for c in classes))
    for i, c in enumerate(classes):
        print(f"{c:>6} " + "  ".join(f"{total_cm[i][j]:>6}" for j in range(len(classes))))
    print("\n每類別準確率(recall)：")
    for i, c in enumerate(classes):
        s = total_cm[i].sum()
        print(f"  {c:<6} {(total_cm[i][i]/s*100 if s else 0):.0f}%  ({total_cm[i][i]}/{s})")
    ov = np.mean(fold_acc)
    print("\n判讀：" + ("✅ 明顯贏過基準，CNN 真的學到盤面型態（且是沒看過的商品）"
                       if ov > majority + 5 else "⚠ 沒明顯贏過基準，需更多資料/調整"))


if __name__ == "__main__":
    main()
