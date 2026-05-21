# 🦊 Project F.O.X.
### Financial Operations eXecutor — AI 量化交易後台系統

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.35+-FF4B4B?logo=streamlit&logoColor=white)
![CCXT](https://img.shields.io/badge/CCXT-4.3+-2D2D2D?logo=bitcoin&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-5.0+-3F4F75?logo=plotly&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

**一套以容錯優先、資料驅動為核心設計哲學的微型財資自動化系統**

</div>

---

## 一、系統定位與設計哲學

Project F.O.X. 並非單純的價格提醒工具，而是一套**具備完整交易生命週期管理能力**的量化研究平台。

系統從零開始自行實作所有核心金融指標（RSI、CCI、ATR），刻意不引入任何第三方技術分析套件，以確保：

- **計算邏輯完全可審計**：每一行指標程式碼均可溯源至金融教科書公式
- **零隱性依賴風險**：外部套件版本更新不會導致交易訊號漂移
- **最小化運行環境**：五個核心依賴即可完整部署（`ccxt`, `streamlit`, `pandas`, `plotly`, `python-dotenv`）

系統架構遵循**防禦性程式設計（Defensive Programming）**原則，任何單點 API 失敗、網路閃斷、資料解析異常，均不得中斷主引擎循環或破壞 UI 渲染狀態。

---

## 二、專案架構

```
Project_FOX/
│
├── dashboard.py          # 主控制台（Streamlit，~1,600 行）
│   ├── 兩段式廣域掃描雷達
│   ├── Auto-Sniper 狙擊引擎
│   ├── ATR 動態移動停利引擎
│   ├── AI 共振評分系統
│   ├── 標記價格三段式更新
│   └── Fragment 獨立刷新架構（5s / 10s / 15s）
│
├── database.py           # SQLite 持久化交易紀錄層
│   ├── init_db()         — 冪等建表，安全支援多次冷啟動
│   └── insert_trade()    — 帶完整型別驗證的交易寫入接口
│
├── main.py               # CLI 警報雷達（零 Streamlit 依賴，純 stdlib）
│   └── 滾動視窗急跌偵測 + Windows Kernel32 Beep 警報
│
├── fox_sandbox_state.json  # JSON 虛擬倉位狀態存檔（跨重啟持久化）
├── fox_trading.db          # SQLite 交易歷史資料庫（結算後自動寫入）
├── .env                    # Binance API 金鑰（不納入版控）
└── requirements.txt
```

---

## 三、技術棧

| 層次 | 技術 | 用途 |
|------|------|------|
| **前端 / UI** | Streamlit 1.35+ | 響應式交易後台，自定義深色主題 CSS |
| **資料視覺化** | Plotly 5.0+ | BTC 即時報價折線圖，K 線結構標記 |
| **交易所接口** | CCXT 4.3+ | Binance USDⓈ-M 永續合約 REST API |
| **資料處理** | Pandas 2.0+ | 掃描器 DataFrame 管線，樣式化表格渲染 |
| **持久化** | SQLite3（stdlib）| 交易歷史資料庫，零外部依賴 |
| **狀態管理** | JSON + Session State | 虛擬倉位跨重整恢復，Streamlit 記憶體共享 |
| **配置管理** | python-dotenv | API 金鑰環境隔離 |
| **指標計算** | 純 Python（自實作）| RSI / CCI / ATR，無外部 ta 套件 |

---

## 四、核心引擎詳解

### 4.1 兩段式廣域雷達（Two-Phase Radar）

```
Phase 1：廣域掃描
  └─ fetch_tickers() — 全市場一次性拉取，按 |24h 漲跌幅| 降序排列
  └─ 過濾條件：USDT 永續合約 / 24h 成交額 > $10M / 純加密代幣正則驗證
  └─ 黑名單剔除：穩定幣 / 法幣衍生品 / 貴金屬 / 大宗商品期貨

Phase 2：精準火控（Top 30）
  └─ fetch_ohlcv(15m, limit=20) — 逐一取 K 線，0.1s 限速防 API 封鎖
  └─ 計算 RSI(14) / CCI(14) / ATR(14) / Vol Surge
  └─ fetch_funding_rate() — 即時資金費率（多頭擁擠指標）
  └─ 快取 TTL = 20 秒，以 @st.cache_data 避免重複 API 呼叫
```

**設計亮點**：Phase 1 僅消耗 1 次 API 配額完成全市場掃描；Phase 2 以漸進式限速保護 Rate Limit，兩段式設計在吞吐量與精準度之間取得最優平衡。

---

### 4.2 金融指標自實作（Zero-Dependency Indicators）

所有指標均為**純 Python 手工實作**，採用與 TradingView 相同的 Wilder Smoothing 方法：

#### RSI(14) — Relative Strength Index
```python
# Wilder Smoothing：使用指數加權移動平均，非簡單 SMA
avg_g = (avg_g * (period - 1) + gains[i]) / period
avg_l = (avg_l * (period - 1) + losses[i]) / period
RSI = 100 - (100 / (1 + avg_g / avg_l))
```

#### CCI(14) — Commodity Channel Index
```python
TP = (High + Low + Close) / 3          # Typical Price
CCI = (TP - SMA_TP) / (0.015 × MeanDev)
```

#### ATR(14) — Average True Range
```python
TR = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
ATR = Wilder MA(TR, 14)                # 同 RSI 平滑機制
```

---

### 4.3 協議 Delta 刺客引擎（Protocol Delta Assassin）

做空信號需同時滿足**三重條件閘控**，防止低品質訊號誤觸：

```
觸發條件（三者 AND）：
  ① CCI(14) > 250        — 極度超買，賣壓蓄積臨界點
  ② 長上影線 (Pin Bar)   — 上影線佔 K 線全幅 ≥ 60%，主力出貨確認
  ③ funding_rate > 0     — 資金費率為正，驗證多頭擁擠（空方須支付多方）
```

**設計邏輯**：純 CCI 超買容易在趨勢行情中誤判，Pin Bar 確認「本根 K 線」有機構出貨行為，資金費率則從市場結構層面確認整體多頭持倉過重。三重閘控將假訊號率壓縮至最低。

---

### 4.4 AI 共振評分系統（Resonance Score, 0–100）

每筆開倉前，系統對信號進行多維度量化評分，驅動動態倉位配置：

```
Component 1 — 動能極端度（0–40 分）
  做多：RSI < 15 → 40 分 | < 20 → 30 分 | < 25 → 20 分
  做空：|CCI| > 400 → 40 分 | > 300 → 30 分 | > 250 → 20 分

Component 2 — 影線長度（0–35 分）
  上影線佔比 ≥ 85% → 35 分 | ≥ 75% → 25 分 | ≥ 65% → 15 分

Component 3 — 量能爆發（0–25 分）
  Vol Surge ≥ 400% → 25 分 | ≥ 300% → 20 分 | ≥ 200% → 15 分
```

評分結果直接驅動倉位規模：

| 共振評分 | 倉位倍率 | 標籤 |
|----------|----------|------|
| ≥ 80 分 | × 1.5 | 🔥 絕殺 |
| 60–79 分 | × 1.0 | ✅ 標準 |
| < 60 分 | × 0.5 | 🔬 試水 |

---

### 4.5 ATR 動態移動停利裝甲

傳統固定百分比停利在高波動行情中容易被提前震盪出場，本系統改採 **ATR 自適應動態追蹤距離**：

```
追蹤距離 = 2 × ATR(14)                  # 動態適應波動率
做多觸發線 = HWM（最高水位）- 追蹤距離
做空觸發線 = LWM（最低水位）+ 追蹤距離

降級保護：若開倉當下 ATR = 0（資料不足），
          自動降級為固定 5% 保護，確保舊倉位不報錯
```

**HWM（High Water Mark）機制**：系統持續追蹤倉位有利方向的最極值，僅在價格回拉超過 2×ATR 時觸發結算，最大化趨勢行情的盈利捕捉。

---

### 4.6 同幣種虧損冷卻機制

防止系統在單一幣種連續虧損中過度加倉：

```
觸發條件：同幣種最近一筆平倉為虧損 AND 距今 < 冷卻時間（預設 120 分鐘）
效果：冷卻期間，對該幣種的新開倉請求一律跳過
可配置：Sidebar 滑桿，0–1440 分鐘（0 = 關閉冷卻）
```

---

## 五、系統級防護架構

### 5.1 API 容錯攔截（三層異常分類）

```python
try:
    ohlcv = ex.fetch_ohlcv(sym, ...)
    # ... 正常業務邏輯 ...
except (ccxt.NetworkError, ccxt.RequestTimeout) as _ex:
    # 網路閃斷：僅記錄警告，跳過此幣，繼續掃下一個
    continue
except Exception as _ex:
    # 未知異常：同上，不中斷整體掃描週期
    continue
```

| 異常類型 | 處理策略 |
|----------|----------|
| `ccxt.NetworkError` | 跳過單幣，繼續掃描，印出 WARN |
| `ccxt.RequestTimeout` | 同上 |
| `ccxt.ExchangeError` | 批次請求失敗時靜默降級 |
| 任意 `Exception` | 單筆平倉結算失敗不影響其他倉位 |

### 5.2 引擎心跳保護

`frag_sandbox`（15 秒觸發的核心引擎 Fragment）整個執行體包裹於頂層 `try/except`，確保任何非預期錯誤不會導致 Streamlit UI 凍結或白畫面：

```python
@st.fragment(run_every=15)
def frag_sandbox() -> None:
    try:
        _sdf, _ = fetch_scanner_data()
        _run_sniper(_sdf)
        _update_mark_prices(_sdf)
        _run_trailing_stop()
    except Exception as _ex:
        print(f"[FOX][WARN] 引擎心跳異常（已攔截，UI 繼續）→ {_ex}")
```

### 5.3 標記價格三段式更新策略

```
Step 1：從 scan_df 快取建立 price_map → 零 API 成本
Step 2：持倉中 scan_df 未覆蓋的 symbol → fetch_tickers(symbols) 批次補齊
Step 3：仍取不到的倉位 → 設 price_error=True，UI 顯示 ⚠️ 警告
```

此設計確保在 API 成本最小化的前提下，所有活躍倉位的標記價格均能被及時更新，且資料缺失時不會用舊值誤算盈虧，而是明確標記異常狀態。

---

### 5.4 雙層持久化架構

| 層次 | 技術 | 資料範疇 | 觸發時機 |
|------|------|----------|----------|
| **即時快照層** | JSON (`fox_sandbox_state.json`) | 虛擬帳戶餘額 / 所有持倉狀態 | 每次平倉後立即寫入 |
| **歷史歸檔層** | SQLite (`fox_trading.db`) | 完整交易紀錄（含共振評分）| 每次平倉（含手動覆蓋）寫入 |

JSON 層保證系統重啟後能以毫秒級速度恢復完整沙盒狀態；SQLite 層則提供結構化查詢能力，支援後續績效分析、策略回測比較與合規稽核。

---

## 六、UI 架構

系統採用 **Streamlit Fragment 獨立刷新**架構，各功能區塊互不干擾，杜絕全頁重繪導致的閃爍與效能損耗：

```
Fragment 1 — 沙盒引擎心跳    (run_every=15s)  核心交易邏輯
Fragment 2 — BTC 即時報價    (run_every=5s)   價格監控 + 警報
Fragment 3 — K 線圖          (run_every=10s)  Plotly 即時圖表
Fragment 4 — 全市場掃描器    (run_every=20s)  天眼雷達結果表格
Fragment 5 — 真實倉位        (run_every=10s)  Binance 帳戶持倉
Fragment 6 — 虛擬沙盒倉位    (run_every=5s)   模擬持倉 + 績效
Fragment 7 — CFO 資金曲線    (run_every=30s)  淨值走勢折線圖
```

**UI 功能清單**：
- 深色量化終端主題（`#080C12` 背景，JetBrains Mono 字型）
- 持倉表格顯示共振評分、資金費率、⚠️ 報價異常標記
- 📥 一鍵匯出 CSV 交易日誌（UTF-8 BOM，相容 Excel）
- 危險操作區：沙盒重置按鈕（需展開確認，防誤觸）
- 手動覆蓋（Manual Override）緊急平倉介面

---

## 七、快速啟動

### 環境需求
- Python 3.11+
- Windows 10+（警報音效使用 `kernel32.Beep`；其餘平台可正常運行，警報音靜默降級）

### 安裝

```bash
git clone https://github.com/sky5416841/Project_FOX.git
cd Project_FOX

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

### 配置 API 金鑰（選用）

```bash
# 複製範本並填入 Binance API 金鑰
# 若不配置，系統以公開 API 運行，真實帳戶欄位顯示「未連線」
cp .env.example .env
```

```env
API_KEY=your_binance_api_key
API_SECRET=your_binance_api_secret
```

### 啟動主控台

```bash
streamlit run dashboard.py
```

瀏覽器開啟 `http://localhost:8501`

### 啟動 CLI 警報雷達（選用）

```bash
python main.py
```

---

## 八、資料庫結構

```sql
CREATE TABLE trade_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,   -- 平倉時間 (HH:MM:SS)
    symbol       TEXT    NOT NULL,   -- 交易標的 (e.g., SOL)
    side         TEXT    NOT NULL,   -- Long / Short
    entry_price  REAL    NOT NULL,   -- 開倉均價（含滑價）
    exit_price   REAL    NOT NULL,   -- 平倉價格
    pnl          REAL    NOT NULL,   -- 已實現盈虧 (USDT)
    score        INTEGER NOT NULL,   -- AI 共振評分 (0–100)
    exit_reason  TEXT    NOT NULL    -- 移動停利 / 爆倉 / 手動平倉
);
```

---

## 九、模組依賴

```
requirements.txt
├── ccxt>=4.3.0          # 交易所統一接口，支援 100+ 交易所
├── streamlit>=1.35.0    # 即時 Web UI 框架
├── pandas>=2.0.0        # 高效能 DataFrame 管線
├── plotly>=5.0.0        # 互動式金融圖表
└── python-dotenv>=1.0.0 # 環境變數安全管理
```

**標準庫使用**（零額外安裝成本）：
`sqlite3` / `json` / `os` / `re` / `time` / `datetime` / `ctypes` / `collections.deque`

---

## 十、系統架構圖

```
┌─────────────────────────────────────────────────────────────┐
│                    Project F.O.X. 架構                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐    REST API    ┌─────────────────────┐   │
│  │ Binance      │ ◄──────────── │  CCXT 接口層         │   │
│  │ Futures API  │               │  (Rate Limit 保護)   │   │
│  └──────────────┘               └──────────┬──────────┘   │
│                                            │               │
│                              ┌─────────────▼─────────────┐ │
│                              │     兩段式廣域雷達           │ │
│                              │  Phase 1: fetch_tickers()  │ │
│                              │  Phase 2: fetch_ohlcv()    │ │
│                              └─────────────┬─────────────┘ │
│                                            │               │
│                    ┌───────────────────────▼─────────────┐ │
│                    │        Auto-Sniper 狙擊引擎           │ │
│                    │  RSI / CCI / ATR / Vol / Funding    │ │
│                    │  共振評分 (0–100) → 動態保證金配置   │ │
│                    └──────────┬──────────────────────────┘ │
│                               │                            │
│          ┌────────────────────┼────────────────────┐       │
│          ▼                    ▼                    ▼       │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐ │
│  │ ATR 動態     │  │  Session State   │  │  SQLite DB   │ │
│  │ 移動停利引擎  │  │  (虛擬帳戶狀態) │  │  交易歷史    │ │
│  └──────────────┘  └────────┬─────────┘  └──────────────┘ │
│                             │                              │
│                    ┌────────▼─────────┐                    │
│                    │   JSON 快照存檔   │                    │
│                    │  (跨重啟恢復)    │                    │
│                    └──────────────────┘                    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Streamlit Fragment UI                   │   │
│  │   7 個獨立刷新區塊，互不干擾，零全頁重繪             │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 十一、開發紀錄

| 版本 | 里程碑 |
|------|--------|
| Phase 1 | CLI 警報雷達，BTC/USDT 價格監控 + 急跌偵測 |
| Phase 2 | Streamlit Dashboard，全市場天眼掃描器，RSI 動能偵測 |
| Phase 3 | CCI 刺客引擎、ATR 動態停利、CFO 資金曲線戰情室 |
| Phase 4 | SQLite 持久化、資金費率整合、AI 共振評分、動態倉位、虧損冷卻 |

---

## 免責聲明

本系統為**量化研究與教育用途**，虛擬沙盒中的所有交易均為模擬，不涉及真實資金操作。使用者若將本系統連接真實 Binance 帳戶查看持倉，須自行承擔相關責任。加密貨幣市場具有極高風險，本系統之任何信號輸出均不構成投資建議。

---

<div align="center">

**Project F.O.X.** — Built with precision. Designed for resilience.

</div>
