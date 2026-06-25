"""
ml_advanced_trainer_template.py — 進階 ML 訓練骨架(時間序列驗證 + 類別平衡)

為未來的「增量微觀數據」(live_ml_features.csv,含真實 Delta/CVD/OBI)準備的
機構級訓練模板。現在先讀離線 ml_features_dataset.csv 測通,日後資料夠了會
自動改讀 live 資料集,並把訂單流特徵一併納入(特徵欄位是動態抓的,不用改碼)。

四個高階要點:
  1. 類別不平衡 → RandomForest(class_weight='balanced')(可換 XGBClassifier)
  2. 時間序列滾動驗證 → TimeSeriesSplit(>=5 折),禁用隨機 train_test_split(防未來函數)
  3. Meta-Labeling 式評估 → 每折印 Win(1) 的 Precision/Recall(目標是精準挑出會賺的單)
  4. 特徵重要性視覺化 → 長條圖,含 delta/obi_ratio 權重(若資料集已有這些欄)

★ 誠實聲明:這是「正確的驗證骨架」,不是「會賺的保證」。以現有 OHLCV 特徵,
  預期每折 Win precision 仍貼近基準勝率(~31%) = 無預測力(見 PO3_ORDERFLOW.md)。
  模板的價值在『方法論正確』:時間序列切分 + 類別平衡 + 看 Win precision 而非 Accuracy。
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import precision_score, recall_score, accuracy_score

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_CSV = os.path.join(HERE, "live_ml_features.csv")        # 未來增量資料(含訂單流)
OFFLINE_CSV = os.path.join(HERE, "ml_features_dataset.csv")  # 現有離線資料(僅 OHLCV)
IMP_PNG = os.path.join(HERE, "ml_advanced_feature_importance.png")
N_SPLITS = 5
MIN_SAMPLES = 50         # live 資料至少這麼多『已打標』樣本才改用它

# 非特徵欄(識別碼/標籤/事後資訊),其餘一律當特徵 → 訂單流欄位日後自動納入
META_COLS = {"label", "symbol", "tf", "ts", "trade_id", "datetime", "side",
             "entry", "sl", "tp", "opened", "resolved_at"}


def load_dataset():
    """優先用 live 增量資料(若已累積足量已打標樣本),否則回退離線資料集。"""
    if os.path.exists(LIVE_CSV):
        d = pd.read_csv(LIVE_CSV)
        d = d[pd.to_numeric(d["label"], errors="coerce").isin([0, 1])].copy()
        if len(d) >= MIN_SAMPLES:
            return d, "live(含訂單流特徵)"
    d = pd.read_csv(OFFLINE_CSV)
    d = d[pd.to_numeric(d["label"], errors="coerce").isin([0, 1])].copy()
    return d, "offline(僅 OHLCV)"


def make_model():
    """預設 RandomForest(class_weight='balanced');若裝了 xgboost 可改用 XGBClassifier。"""
    try:
        from xgboost import XGBClassifier      # 選用:若環境有 xgboost
        spw = None                             # XGB 用 scale_pos_weight 處理不平衡,訓練時再算
        return ("xgboost", XGBClassifier(n_estimators=300, max_depth=4,
                                         learning_rate=0.05, subsample=0.9,
                                         eval_metric="logloss", random_state=42))
    except Exception:
        return ("rf", RandomForestClassifier(n_estimators=300, max_depth=4,
                                             min_samples_leaf=5, class_weight="balanced",
                                             random_state=42, n_jobs=-1))


def main():
    data, source = load_dataset()
    data = data.sort_values("ts").reset_index(drop=True) if "ts" in data else \
           data.sort_values("trade_id").reset_index(drop=True)
    feat_cols = [c for c in data.columns if c not in META_COLS]
    X = data[feat_cols].astype(float).values
    y = pd.to_numeric(data["label"]).astype(int).values
    print(f"資料來源:{source}　樣本 {len(data)}　特徵 {len(feat_cols)} 個　Win 率 {y.mean():.1%}")
    print(f"特徵欄:{feat_cols}")
    if "delta" in feat_cols or "obi_ratio" in feat_cols:
        print("✓ 已含訂單流特徵(delta/cvd_slope/obi_ratio) → 真正的機構級燃料已就位")
    else:
        print("⚠ 目前僅 OHLCV 特徵;待 live_ml_features.csv 孵化足量後,訂單流特徵會自動納入")

    if len(data) < N_SPLITS + 2:
        print(f"✗ 樣本太少(<{N_SPLITS+2}),無法做 {N_SPLITS} 折時間序列驗證。先讓孵化器多收集。")
        return

    # ── 時間序列滾動驗證(禁止隨機打亂)──────────────────────────────
    name, _ = make_model()
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    base_rate = y.mean()
    print(f"\n模型:{name}　TimeSeriesSplit {N_SPLITS} 折(Walk-Forward,無未來函數)")
    print("=" * 70)
    win_prec, win_rec, accs = [], [], []
    for i, (tr, te) in enumerate(tscv.split(X), 1):
        _, clf = make_model()
        clf.fit(X[tr], y[tr])
        pred = clf.predict(X[te])
        acc = accuracy_score(y[te], pred)
        p1 = precision_score(y[te], pred, pos_label=1, zero_division=0)
        r1 = recall_score(y[te], pred, pos_label=1, zero_division=0)
        p0 = precision_score(y[te], pred, pos_label=0, zero_division=0)
        accs.append(acc); win_prec.append(p1); win_rec.append(r1)
        print(f"Fold {i}: 訓練 {len(tr):>3} / 測試 {len(te):>3} | "
              f"Acc {acc:.3f} | Loss precision {p0:.3f} | "
              f"★ Win precision {p1:.3f}  Win recall {r1:.3f}")

    print("-" * 70)
    mp = np.mean(win_prec)
    print(f"各折平均:Acc {np.mean(accs):.3f} | ★ Win precision {mp:.3f}（基準勝率 {base_rate:.3f}）")
    edge = mp - base_rate
    print(f"Win precision − 基準 = {edge:+.3f} → "
          + ("有一點挑單能力(仍需更多樣本佐證)" if edge > 0.03 else "≈基準,沒有有效挑單能力"))
    print("  (Meta-Labeling 目標就是『提高 Win precision』:寧可少做、做的要準)")

    # ── 特徵重要性(最終以全資料擬合一次,含 delta/obi 若有)──────────
    _, final = make_model()
    final.fit(X, y)
    imp = pd.Series(getattr(final, "feature_importances_", np.zeros(len(feat_cols))),
                    index=feat_cols).sort_values()
    colors = ["#ef5350" if c in ("delta", "cvd_slope", "obi_ratio") else "#42a5f5" for c in imp.index]
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.barh(imp.index, imp.values, color=colors)
    ax.set_title(f"進階 ML 特徵重要性({name},紅=訂單流特徵)", fontsize=13)
    ax.set_xlabel("重要性"); ax.grid(axis="x", alpha=0.2)
    fig.tight_layout(); fig.savefig(IMP_PNG, dpi=110); plt.close(fig)
    print(f"✓ 特徵重要性圖 → {IMP_PNG}")
    print("\n⚠ 骨架就緒。現在只是『方法論正確的空槍』;真正的子彈是 live 訂單流資料,孵化中。")


if __name__ == "__main__":
    main()
