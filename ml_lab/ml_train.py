"""
ml_train.py — 隨機森林分類器 + 特徵重要性評估

讀 ml_features_dataset.csv → 時間序列切分(不洗牌,避免未來洩漏) → 訓練 RandomForest
→ 印測試集 Accuracy / Classification Report → 畫特徵重要性長條圖 → 存 fox_ml_model.pkl。

★ 誠實讀法(務必看 Accuracy 的陷阱):
  標籤不平衡(Win≈31%)。一個「全猜 Loss」的笨模型 Accuracy 就有 ~69%。
  所以**單看 Accuracy 會被騙**。真正要看的是:
    · 模型 Accuracy 有沒有贏過「多數類基準」(majority baseline)?
    · Win 類的 precision/recall —— 模型能不能真的挑出會贏的單?
  本專案預期:樣本外幾乎贏不過基準(訊號本就負期望)。能誠實呈現這件事,
  比一個假裝高準確率的模型更有價值(呼應 QUANT_RESEARCH.md)。
"""
import os
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ml_features_dataset.csv")
MODEL_OUT = os.path.join(HERE, "fox_ml_model.pkl")
IMP_PNG = os.path.join(HERE, "ml_feature_importance.png")
TEST_FRAC = 0.30         # 時間序列尾段當測試集
META_COLS = ["label", "symbol", "tf", "ts"]


def main():
    df = pd.read_csv(DATA).dropna().sort_values("ts").reset_index(drop=True)
    feat_cols = [c for c in df.columns if c not in META_COLS]
    X, y = df[feat_cols].values, df["label"].values
    print(f"資料集 {len(df)} 筆,特徵 {len(feat_cols)} 個,整體 Win 率 {y.mean():.1%}")

    # ── 時間序列切分:前 70% 訓練、後 30% 測試(絕不洗牌)──────────────
    cut = int(len(df) * (1 - TEST_FRAC))
    Xtr, Xte, ytr, yte = X[:cut], X[cut:], y[:cut], y[cut:]
    print(f"訓練 {len(Xtr)} 筆 / 測試 {len(Xte)} 筆(時間序列切分)\n")

    clf = RandomForestClassifier(n_estimators=300, max_depth=4, min_samples_leaf=5,
                                 class_weight="balanced", random_state=42, n_jobs=-1)
    clf.fit(Xtr, ytr)
    pred = clf.predict(Xte)

    # ── 基準對照:全猜多數類 ─────────────────────────────────────────
    majority = int(round(ytr.mean()))           # 訓練集多數類
    base_acc = accuracy_score(yte, np.full_like(yte, majority))
    acc = accuracy_score(yte, pred)

    print("=" * 56)
    print(f"多數類基準 Accuracy : {base_acc:.3f}  (全猜 {'Win' if majority else 'Loss'})")
    print(f"模型     Accuracy   : {acc:.3f}")
    verdict = "✓ 險勝基準" if acc > base_acc + 1e-9 else ("= 打平基準" if abs(acc-base_acc) < 1e-9 else "✗ 還輸基準")
    print(f"判讀                : {verdict}")
    print("-" * 56)
    print("Classification Report(重點看 Win=1 的 precision/recall):")
    print(classification_report(yte, pred, target_names=["Loss(0)", "Win(1)"],
                                zero_division=0))

    # ── 特徵重要性長條圖 ─────────────────────────────────────────────
    imp = pd.Series(clf.feature_importances_, index=feat_cols).sort_values()
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.barh(imp.index, imp.values, color="#42a5f5")
    ax.set_title("PO3 ML 特徵重要性(Random Forest)", fontsize=13)
    ax.set_xlabel("重要性"); ax.grid(axis="x", alpha=0.2)
    fig.tight_layout(); fig.savefig(IMP_PNG, dpi=110); plt.close(fig)
    print(f"✓ 特徵重要性圖 → {IMP_PNG}")
    print("  最有貢獻的前 3 特徵:", ", ".join(imp.sort_values(ascending=False).index[:3]))

    joblib.dump({"model": clf, "features": feat_cols}, MODEL_OUT)
    print(f"✓ 模型已存 → {MODEL_OUT}")
    print("\n⚠ 別只看 Accuracy:標籤不平衡下它會虛高。若模型贏不過基準、Win 類 recall 低,")
    print("  就是『OHLCV 特徵預測不了勝負』的誠實證據 —— 與全站負期望結論一致。")


if __name__ == "__main__":
    main()
