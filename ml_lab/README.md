# ml_lab — PO3 機器學習實驗室

把 PO3 掃針訊號做成監督式學習問題。**完整脈絡見 [../PO3_ORDERFLOW.md](../PO3_ORDERFLOW.md) 第四之二節。**

## 檔案
| 檔案 | 用途 |
|---|---|
| `ml_data_prep.py` | 考題產生器:跨市場抓歷史、找掃針、抽 OHLCV 特徵 + 2R/1R 標籤 → `ml_features_dataset.csv` |
| `ml_train.py` | RandomForest 時間序列切分訓練 + 基準對照 + 特徵重要性圖 + 存 `fox_ml_model.pkl` |
| `live_logger.py` | 即時特徵孵化器:由紙上交易員呼叫,每偵測到掃針就把 OHLCV+Delta/CVD/OBI 寫進 `live_ml_features.csv`,延遲打標 |

## ⚠️ 模型狀態:**孵化中,未上線**
- 在背景跑的是**資料蒐集**（`live_logger`），不是模型。
- `fox_ml_model.pkl` 是**靜止檔案**,沒有被 load、不參與任何開倉決策。
- 不上線的原因:時間序列驗證準確率 **0.45 < 基準 0.667**（OHLCV 特徵無預測力）。
  正路是繼續孵化「含真實訂單流」的新資料、數週後重訓。
- **沒有「71.4%」這個成績**;真實是 45%。單折高分屬 p-hacking,不採信。

> 統計引擎與過濾器在運作;ML 模型在「等資料」,不在「做決策」。

## 註
`ml_features_dataset.csv` / `live_ml_features.csv` / `fox_ml_model.pkl` 為產出物,已 gitignore（部分）或可重生。
