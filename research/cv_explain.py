"""
C 階段第三步：Grad-CAM 可解釋性 —— 讓 CNN「指出它在圖上看哪裡」

訓練一個模型後，用 Grad-CAM 產生熱力圖：紅色=模型判斷時最關注的區域。
目的有二：
  1. 作品深度：從「我訓練了分類器」升級到「我能解釋模型學到什麼」。
  2. 誠實檢查：若熱點落在趨勢結構(蠟燭走勢)上 → 模型學對了；
     若落在邊角/空白/某種假影 → 模型在作弊，結果不可信。

依賴 cv_train.py 的 SmallCNN 與前處理。
"""
import os
import numpy as np
import torch
import torch.nn.functional as F
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
from torchvision import datasets
from torch.utils.data import DataLoader

from cv_train import SmallCNN, TF, DATA_DIR, IMG, BATCH, EPOCHS, LR

torch.manual_seed(42); np.random.seed(42)


def train_full(ds, classes):
    """用全部資料快速訓一個模型（這支重點是解釋，不是評估泛化）。"""
    counts = np.bincount([y for _, y in ds.samples], minlength=len(classes))
    counts = np.where(counts == 0, 1, counts)
    w = torch.tensor(counts.sum() / (len(counts) * counts), dtype=torch.float32)
    loader = DataLoader(ds, BATCH, shuffle=True)
    model = SmallCNN(len(classes))
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = torch.nn.CrossEntropyLoss(weight=w)
    for _ in range(EPOCHS):
        model.train()
        for x, y in loader:
            opt.zero_grad(); lossf(model(x), y).backward(); opt.step()
    return model


def grad_cam(model, x, target_layer):
    """對單張輸入 x(1,1,H,W) 算 Grad-CAM 熱力圖，回傳 (預測類別, 熱力圖 HxW)。"""
    acts, grads = {}, {}
    h1 = target_layer.register_forward_hook(lambda m, i, o: acts.__setitem__("v", o.detach()))
    h2 = target_layer.register_full_backward_hook(lambda m, gi, go: grads.__setitem__("v", go[0].detach()))

    logits = model(x)
    cls = logits.argmax(1).item()
    model.zero_grad()
    logits[0, cls].backward()

    a = acts["v"][0]                       # (C,h,w) 特徵圖
    g = grads["v"][0]                      # (C,h,w) 梯度
    weights = g.mean(dim=(1, 2))           # 每個 channel 的重要度
    cam = F.relu((weights[:, None, None] * a).sum(0))   # 加權後取正
    cam = cam / (cam.max() + 1e-8)
    cam = F.interpolate(cam[None, None], size=(IMG, IMG), mode="bilinear", align_corners=False)[0, 0]
    h1.remove(); h2.remove()
    return cls, cam.numpy()


def main():
    ds = datasets.ImageFolder(DATA_DIR, transform=TF)
    classes = ds.classes
    print(f"訓練模型中（{EPOCHS} epochs）...")
    model = train_full(ds, classes)
    model.eval()
    target_layer = model.net[6]            # 最後一層卷積 Conv2d(32,64)

    # 每類別挑一張代表圖做 Grad-CAM
    picks = {}
    for path, y in ds.samples:
        if classes[y] not in picks:
            picks[classes[y]] = path
        if len(picks) == len(classes):
            break

    fig, axes = plt.subplots(2, len(classes), figsize=(4 * len(classes), 7))
    for col, cls_name in enumerate(classes):
        x = TF(datasets.folder.default_loader(picks[cls_name])).unsqueeze(0)
        pred, cam = grad_cam(model, x, target_layer)
        img = x[0, 0].numpy()
        axes[0, col].imshow(img, cmap="gray"); axes[0, col].axis("off")
        axes[0, col].set_title(f"真實:{cls_name}  預測:{classes[pred]}", fontsize=11)
        axes[1, col].imshow(img, cmap="gray")
        axes[1, col].imshow(cam, cmap="jet", alpha=0.5)
        axes[1, col].axis("off")
        axes[1, col].set_title("Grad-CAM 關注區", fontsize=11)
    fig.suptitle("CNN 在 K 線圖上看哪裡做判斷 (Grad-CAM)", fontsize=13)
    fig.tight_layout()
    fig.savefig("cv_gradcam.png", dpi=110)
    print("✓ 已輸出 cv_gradcam.png")


if __name__ == "__main__":
    main()
