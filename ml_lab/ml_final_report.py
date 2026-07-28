"""
ml_final_report.py — PO3 500 筆收尾:完整資料科學流水線(不只 pass/fail)
======================================================================
目的:用親手收集的 500 筆(含訂單流 Delta/OBI/CVD)跑一個誠實、完整的監督式
學習專案,產出可放作品集的產物 —— 不是只印一句「失敗」。

流程(無未來洩漏):
  1. 時間序切分 70% 訓練 / 30% 樣本外(不洗牌)
  2. 對照組:多數類基準 + 整體 base rate
  3. 模型:RandomForest + Logistic(標準化)
  4. 樣本外指標:Acc / Win-precision / Recall / ROC-AUC vs 基準
  5. 產物 PNG:特徵重要度、校準曲線、學習曲線、混淆矩陣
  6. 誠實判讀:模型有沒有『真的比亂猜/基準好』

結論預期:失敗(失敗複盤已證特徵零分辨力)。但你得到的是完整流水線+對資料的理解。
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import (accuracy_score, precision_score, recall_score,
                             roc_auc_score, confusion_matrix)
from sklearn.calibration import calibration_curve
from sklearn.model_selection import learning_curve

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "live_ml_features.csv")
FEATS = ["side_bear", "box_range_pct", "box_len", "bars_since_box", "wick_atr",
         "pierce_atr", "body_atr", "atr_pct", "vol_surge", "ret_5", "ret_20",
         "delta", "cvd_slope", "obi_ratio"]


def main():
    df = pd.read_csv(DATA)
    df = df[df["label"].isin([0, 1])].sort_values("datetime").reset_index(drop=True)
    for c in FEATS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=FEATS)
    X, y = df[FEATS].values, df["label"].astype(int).values
    n = len(df); cut = int(n * 0.7)
    Xtr, Xte, ytr, yte = X[:cut], X[cut:], y[:cut], y[cut:]

    base_rate = y.mean()
    majority = 0 if (1 - ytr.mean()) >= ytr.mean() else 1   # 多數類(多半=輸=0)
    maj_acc = accuracy_score(yte, np.full_like(yte, majority))

    print("=" * 70)
    print(f"  PO3 收尾 ML 報告  總 {n} 筆(含訂單流)  整體 Win {base_rate:.1%}")
    print(f"  訓練 {len(ytr)} / 樣本外 {len(yte)}   時間序切分(無未來洩漏)")
    print("=" * 70)
    print(f"  對照組：多數類(全猜{'輸' if majority==0 else '贏'}) 樣本外 Acc = {maj_acc:.1%}")
    print(f"          若隨機挑單，Win precision ≈ base rate {base_rate:.1%}")
    print("-" * 70)

    rf = RandomForestClassifier(n_estimators=300, max_depth=5,
                                min_samples_leaf=8, random_state=42, class_weight="balanced")
    rf.fit(Xtr, ytr)
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced"))
    lr.fit(Xtr, ytr)

    results = {}
    for name, m in [("RandomForest", rf), ("Logistic", lr)]:
        p = m.predict(Xte)
        proba = m.predict_proba(Xte)[:, 1]
        acc = accuracy_score(yte, p)
        prec = precision_score(yte, p, pos_label=1, zero_division=0)
        rec = recall_score(yte, p, pos_label=1, zero_division=0)
        try:
            auc = roc_auc_score(yte, proba)
        except ValueError:
            auc = float("nan")
        results[name] = (acc, prec, rec, auc, proba, p)
        print(f"  {name:<13} Acc {acc:.1%}  Win-precision {prec:.1%}  Recall {rec:.1%}  ROC-AUC {auc:.3f}")

    print("-" * 70)
    best_prec = max(results["RandomForest"][1], results["Logistic"][1])
    best_auc = max(r[3] for r in results.values() if not np.isnan(r[3]))
    print("  誠實判讀：")
    print(f"    · Win-precision {best_prec:.1%} vs 隨機基準 {base_rate:.1%} → "
          f"{'比亂猜好' if best_prec > base_rate + 0.03 else '沒有比亂猜好（挑不出賺錢子集）'}")
    print(f"    · ROC-AUC 最佳 {best_auc:.3f} → "
          f"{'>0.55 有一點分辨力' if best_auc > 0.55 else '≈0.5 幾乎無分辨力（贏輸分不開）'}")

    # ── 產物圖 ─────────────────────────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))

    # 1 特徵重要度
    imp = pd.Series(rf.feature_importances_, index=FEATS).sort_values()
    axes[0, 0].barh(imp.index, imp.values, color="#42a5f5")
    axes[0, 0].set_title("① 特徵重要度 (RandomForest)")
    axes[0, 0].tick_params(labelsize=8)

    # 2 校準曲線
    proba_rf = results["RandomForest"][4]
    try:
        frac, mean_pred = calibration_curve(yte, proba_rf, n_bins=5, strategy="quantile")
        axes[0, 1].plot(mean_pred, frac, "o-", color="#ef5350", label="RF")
        axes[0, 1].plot([0, 1], [0, 1], "--", color="#999", label="完美校準")
        axes[0, 1].set_title("② 校準曲線 (預測機率 vs 實際勝率)")
        axes[0, 1].set_xlabel("預測 Win 機率"); axes[0, 1].set_ylabel("實際 Win 比例")
        axes[0, 1].legend(fontsize=8)
    except Exception as e:
        axes[0, 1].text(0.5, 0.5, f"校準無法繪製\n{e}", ha="center")

    # 3 學習曲線
    try:
        ts, tr_s, te_s = learning_curve(
            RandomForestClassifier(n_estimators=200, max_depth=5, min_samples_leaf=8,
                                   random_state=42, class_weight="balanced"),
            X, y, cv=4, train_sizes=np.linspace(0.2, 1.0, 6), scoring="roc_auc")
        axes[1, 0].plot(ts, tr_s.mean(1), "o-", color="#66bb6a", label="訓練")
        axes[1, 0].plot(ts, te_s.mean(1), "o-", color="#ef5350", label="交叉驗證")
        axes[1, 0].axhline(0.5, ls="--", color="#999", label="亂猜 (AUC 0.5)")
        axes[1, 0].set_title("③ 學習曲線 (加資料會變好嗎?)")
        axes[1, 0].set_xlabel("訓練樣本數"); axes[1, 0].set_ylabel("ROC-AUC")
        axes[1, 0].legend(fontsize=8)
    except Exception as e:
        axes[1, 0].text(0.5, 0.5, f"學習曲線無法繪製\n{e}", ha="center")

    # 4 混淆矩陣(RF 樣本外)
    cm = confusion_matrix(yte, results["RandomForest"][5])
    axes[1, 1].imshow(cm, cmap="Blues")
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            axes[1, 1].text(j, i, cm[i, j], ha="center", va="center", fontsize=14)
    axes[1, 1].set_title("④ 混淆矩陣 (RF 樣本外)")
    axes[1, 1].set_xticks([0, 1]); axes[1, 1].set_xticklabels(["預測輸", "預測贏"])
    axes[1, 1].set_yticks([0, 1]); axes[1, 1].set_yticklabels(["實際輸", "實際贏"])

    plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.tight_layout()
    out = os.path.join(HERE, "ml_final_report.png")
    plt.savefig(out, dpi=110, bbox_inches="tight")
    print("-" * 70)
    print(f"  ✓ 四合一報告圖已存：{out}")
    print("=" * 70)


if __name__ == "__main__":
    main()
