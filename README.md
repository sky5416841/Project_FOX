# 🦊 Project F.O.X.
### Financial Operations eXecutor — AI 量化交易後台系統

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.35+-FF4B4B?logo=streamlit&logoColor=white)
![CCXT](https://img.shields.io/badge/CCXT-4.3+-2D2D2D?logo=bitcoin&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-5.0+-3F4F75?logo=plotly&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-2.0_Flash-4285F4?logo=google&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

**一套以容錯優先、資料驅動為核心設計哲學的微型 SaaS 量化交易研究平台**

</div>

---

## 一、系統定位與設計哲學

Project F.O.X. 並非單純的價格提醒工具，而是一套**具備完整交易生命週期管理能力**的量化研究平台，並已演化為支援多使用者的 SaaS 雛型。

系統從零開始自行實作所有核心金融指標（RSI、CCI、ATR），刻意不引入任何第三方技術分析套件，以確保：

- **計算邏輯完全可審計**：每一行指標程式碼均可溯源至金融教科書公式
- **零隱性依賴風險**：外部套件版本更新不會導致交易訊號漂移
- **多使用者資料隔離**：PBKDF2-HMAC-SHA256 密碼加密，每位指揮官擁有獨立沙盒與交易紀錄
- **AI 副駕輔助決策**：自然語言 → SQL 查詢，由 Gemini 2.0 Flash 驅動，帶 user_id 過濾確保數據不外洩

系統架構遵循**防禦性程式設計（Defensive Programming）**原則，任何單點 API 失敗、網路閃斷、資料解析異常，均不得中斷主引擎循環或破壞 UI 渲染狀態。

---

## 二、專案架構

```
Project_FOX/
│
├── dashboard.py          # 主控制台（Streamlit，~1,950 行）
│   ├── Cookie 保持登入（HMAC-SHA256 簽名，7 天有效）
│   ├── 登入攔截牆（Login Wall）— 未登入完全封鎖主面板
│   ├── 多幣種雷達選擇器（BTC / ETH / ADA，可即時切換）
│   ├── 完全體 K 線圖（Candlestick + MA7/MA25 + 成交量副圖）
│   ├── 兩段式廣域掃描雷達（天眼）
│   ├── Auto-Sniper 狙擊引擎
│   ├── ATR 動態移動停利引擎
│   ├── AI 共振評分系統
│   ├── CFO 戰情分析室（淨值曲線）
│   └── Fragment 獨立刷新架構（5s / 10s / 15s / 60s）
│
├── database.py           # SQLite 多使用者持久化層
│   ├── _hash_password()  — PBKDF2-HMAC-SHA256，260,000 次迭代
│   ├── _verify_password()— constant-time 比對，防時序攻擊
│   ├── init_db()         — 冪等建表 + 舊資料庫自動遷移
│   ├── create_user()     — 帳號建立，含格式驗證與唯一性保護
│   ├── verify_user()     — 帳密驗證，成功回傳 user_id
│   └── insert_trade()    — 帶 user_id 外鍵的交易寫入接口
│
├── ai_copilot.py         # AI 量化副駕（代號：狐影）
│   ├── ask_copilot()     — Text-to-SQL 主函式，含 user_id 過濾注入
│   ├── _call_gemini()    — Gemini 2.0 Flash API 封裝
│   ├── _is_safe_sql()    — 安全閘：只允許 SELECT，拒絕任何 DDL/DML
│   └── _execute_sql()    — SQLite 執行 + 自動重試（帶 Gemini 錯誤修正）
│
├── main.py               # CLI 警報雷達（零 Streamlit 依賴，純 stdlib）
│   └── 滾動視窗急跌偵測 + Windows Kernel32 Beep 警報
│
├── fox_sandbox_state_{user_id}.json  # 使用者專屬虛擬倉位存檔
├── fox_trading.db                    # SQLite（users + trade_history）
├── .env                              # API 金鑰（不納入版控）
└── requirements.txt
```

---

## 三、技術棧

| 層次 | 技術 | 用途 |
|------|------|------|
| **前端 / UI** | Streamlit 1.35+ | 響應式交易後台，自定義深色主題 CSS |
| **資料視覺化** | Plotly 5.0+ + make_subplots | K 線圖、MA7/MA25 均線、成交量副圖 |
| **交易所接口** | CCXT 4.3+ | Binance USDⓈ-M 永續合約 REST API |
| **資料處理** | Pandas 2.0+ | OHLCV DataFrame、MA 計算、掃描器管線 |
| **持久化** | SQLite3（stdlib）| users 資料表 + trade_history，多使用者隔離 |
| **身份驗證** | hashlib + hmac（stdlib）| PBKDF2-HMAC-SHA256 密碼加密，constant-time 比對 |
| **持久登入** | streamlit-cookies-controller | HMAC-SHA256 簽名 Cookie，7 天免登入，降級防呆 |
| **AI 副駕** | Google Gemini 2.0 Flash | Text-to-SQL + 繁體中文戰報生成 |
| **狀態管理** | JSON + Session State | 使用者專屬沙盒，跨重整恢復 |
| **配置管理** | python-dotenv | API 金鑰環境隔離 |
| **指標計算** | 純 Python（自實作）| RSI / CCI / ATR，無外部 ta 套件 |

---

## 四、核心引擎詳解

### 4.1 多使用者 SaaS 架構

```
Cookie 保持登入（Persistent Login）
  ├─ Token 格式：{user_id}:{username}:{expiry_unix}:{hmac_sha256_sig}
  ├─ 有效期：7 天（max_age=604800），登出時立即清除
  ├─ 簽名金鑰：COOKIE_SECRET 環境變數；未設定時自動從現有金鑰派生
  ├─ 降級防呆：套件未安裝時靜默降級，使用者仍可手動登入
  └─ 刷新流程：F5 → 讀 Cookie → HMAC 驗簽 + expiry 驗證 → 還原 session

登入攔截牆 (Login Wall)
  └─ st.session_state.logged_in == False → st.stop() 封鎖全部主面板
  └─ 登入成功 → session_state 記錄 user_id / username → 寫入 Cookie

使用者資料隔離
  ├─ 沙盒存檔：fox_sandbox_state_{user_id}.json（各使用者獨立）
  ├─ 交易紀錄：trade_history WHERE user_id = {uid}（SQL 層隔離）
  └─ AI 副駕：ask_copilot() 動態注入 WHERE user_id，禁止跨帳查詢

密碼安全
  └─ PBKDF2-HMAC-SHA256，salt=16 bytes urandom，260,000 次迭代
  └─ hmac.compare_digest() 常數時間比對，防時序攻擊
```

---

### 4.2 兩段式廣域雷達（Two-Phase Radar）

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

### 4.3 完全體 K 線看盤終端

```
資料層：fetch_ohlcv(symbol, timeframe, limit=100)
  └─ 幣種：側邊欄選擇（BTC / ETH / ADA，可擴充）
  └─ 時間級別：1m / 5m / 15m / 30m / 1h / 4h / 1d（預設 15m）
  └─ 快取：@st.cache_data ttl=120s，依 symbol+timeframe 獨立分區

圖表層（雙層子圖，make_subplots）
  ├─ Row 1（80%）：Candlestick K 線（漲綠跌紅）
  │              + MA7（金黃 #FFD700，rolling 7，min_periods=1）
  │              + MA25（紫色 #9B59B6，rolling 25，min_periods=1）
  └─ Row 2（20%）：成交量 Bar（收盤≥開盤→半透明綠，否則紅）

互動：shared_xaxes=True / scrollZoom=True / rangeslider_visible=False
```

---

### 4.4 AI 量化副駕「狐影」（Text-to-SQL）

```
流程：自然語言問題 → Gemini 2.0 Flash → SQL → SQLite → 繁體中文戰報

安全閘控：
  ① _is_safe_sql()：只允許 SELECT，拒絕 INSERT/UPDATE/DELETE/DROP
  ② user_id 動態注入：prompt 強制要求所有 SQL 帶 WHERE user_id = {uid}
  ③ 自動重試：執行失敗時帶錯誤訊息讓 Gemini 修正 SQL，最多重試一次

人格設定（代號：狐影）：
  情報官口吻，核心結論先行，數據細節展開，禁止企業客服語句
```

---

### 4.5 金融指標自實作（Zero-Dependency Indicators）

所有指標均為**純 Python 手工實作**，採用與 TradingView 相同的 Wilder Smoothing 方法：

#### RSI(14) — Relative Strength Index
```python
avg_g = (avg_g * (period - 1) + gains[i]) / period
avg_l = (avg_l * (period - 1) + losses[i]) / period
RSI = 100 - (100 / (1 + avg_g / avg_l))
```

#### CCI(14) — Commodity Channel Index
```python
TP = (High + Low + Close) / 3
CCI = (TP - SMA_TP) / (0.015 × MeanDev)
```

#### ATR(14) — Average True Range
```python
TR = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
ATR = Wilder MA(TR, 14)
```

---

### 4.6 協議 Delta 刺客引擎（Protocol Delta Assassin）

做空信號需同時滿足**三重條件閘控**：

```
觸發條件（三者 AND）：
  ① CCI(14) > 250        — 極度超買，賣壓蓄積臨界點
  ② 長上影線 (Pin Bar)   — 上影線佔 K 線全幅 ≥ 60%，主力出貨確認
  ③ funding_rate > 0     — 資金費率為正，驗證多頭擁擠
```

---

### 4.7 AI 共振評分系統（Resonance Score, 0–100）

```
Component 1 — 動能極端度（0–40 分）
  做多：RSI < 15 → 40 | < 20 → 30 | < 25 → 20
  做空：|CCI| > 400 → 40 | > 300 → 30 | > 250 → 20

Component 2 — 影線長度（0–35 分）
  上影線佔比 ≥ 85% → 35 | ≥ 75% → 25 | ≥ 65% → 15

Component 3 — 量能爆發（0–25 分）
  Vol Surge ≥ 400% → 25 | ≥ 300% → 20 | ≥ 200% → 15
```

| 共振評分 | 倉位倍率 | 標籤 |
|----------|----------|------|
| ≥ 80 分 | × 1.5 | 🔥 絕殺 |
| 60–79 分 | × 1.0 | ✅ 標準 |
| < 60 分 | × 0.5 | 🔬 試水 |

---

### 4.8 ATR 動態移動停利裝甲

```
追蹤距離 = 2 × ATR(14)
做多觸發線 = HWM（最高水位）- 追蹤距離
做空觸發線 = LWM（最低水位）+ 追蹤距離

降級保護：ATR = 0 時自動降級為固定 5%，確保舊倉位不報錯
```

---

## 五、系統級防護架構

### 5.1 API 容錯攔截（三層異常分類）

| 異常類型 | 處理策略 |
|----------|----------|
| `ccxt.NetworkError` | 跳過單幣，繼續掃描，印出 WARN |
| `ccxt.RequestTimeout` | 同上 |
| `ccxt.RateLimitExceeded` | 掃描器層級回傳錯誤訊息，UI 顯示警告 |
| 任意 `Exception` | 單筆平倉結算失敗不影響其他倉位 |

### 5.2 引擎心跳保護

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
Step 3：仍取不到的倉位 → price_error=True，UI 顯示 ⚠️ 警告
```

### 5.4 雙層持久化架構

| 層次 | 技術 | 資料範疇 | 觸發時機 |
|------|------|----------|----------|
| **即時快照層** | JSON（使用者專屬）| 虛擬餘額 / 所有持倉狀態 | 每次平倉後立即寫入 |
| **歷史歸檔層** | SQLite `trade_history` | 完整交易紀錄（含共振評分）| 每次平倉（含手動覆蓋）寫入 |
| **身份認證層** | SQLite `users` | 帳號 + 密碼雜湊 | 註冊時寫入，登入時驗證 |
| **瀏覽器 Cookie** | HMAC-SHA256 Token | 登入狀態（user_id / username）| 登入時寫入，登出時清除 |

---

## 六、UI 架構

系統採用 **Streamlit Fragment 獨立刷新**架構，各功能區塊互不干擾，杜絕全頁重繪導致的閃爍與效能損耗：

```
Fragment 1 — 沙盒引擎心跳    (run_every=15s)  核心交易邏輯
Fragment 2 — 多幣種即時報價  (run_every=5s)   價格監控 + 警報（BTC/ETH/ADA）
Fragment 3 — 完全體 K 線圖   (run_every=60s)  Candlestick + MA7/MA25 + 成交量
Fragment 4 — 全市場掃描器    (run_every=20s)  天眼雷達結果表格
Fragment 5 — 風控大腦 + 日誌 (run_every=10s)  倉位狀態 + AI 決策日誌
Fragment 6 — 真實持倉        (run_every=10s)  Binance 帳戶持倉
Fragment 7 — 虛擬沙盒倉位    (run_every=5s)   模擬持倉 + 績效
Fragment 8 — CFO 資金曲線    (run_every=15s)  淨值走勢 + 統計指標
```

**UI 功能清單**：
- 深色量化終端主題（`#080C12` 背景，JetBrains Mono 字型）
- 登入 / 註冊介面，指揮官帳號顯示於側邊欄
- 多幣種選擇器（BTC / ETH / ADA），切換時自動重置走勢快取與警報
- K 線圖時間級別選擇（1m / 5m / 15m / 30m / 1h / 4h / 1d）
- 持倉表格顯示共振評分、資金費率、⚠️ 報價異常標記
- 📥 一鍵匯出 CSV 交易日誌（UTF-8 BOM，相容 Excel）
- 危險操作區：沙盒重置按鈕（需展開確認，防誤觸）
- 手動覆蓋（Manual Override）緊急平倉介面
- 🤖 AI 副駕「狐影」Tab：快捷問題按鈕 + 自由中文問答

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

### 配置 API 金鑰

```env
# .env（複製此範本，填入後儲存）

# Binance API（選用：不填仍可使用公開報價與虛擬沙盒）
API_KEY=your_binance_api_key
API_SECRET=your_binance_api_secret

# Gemini API（選用：不填則 AI 副駕「狐影」離線）
# 免費申請：https://aistudio.google.com/apikey
GEMINI_API_KEY=your_gemini_api_key

# Cookie 簽名金鑰（選用：不填時自動從上方金鑰派生，生產環境建議自行設定）
# COOKIE_SECRET=your_random_secret_string

# 管理員帳號預設密碼（選用：首次啟動時自動建立 admin 帳號）
# ADMIN_DEFAULT_PWD=your_admin_password
```

### 啟動主控台

```bash
streamlit run dashboard.py
```

瀏覽器開啟 `http://localhost:8501`，點選「📝 建立帳號」註冊後即可登入。

### 啟動 CLI 警報雷達（選用）

```bash
python main.py
```

---

## 八、資料庫結構

```sql
-- 使用者帳號表
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL   -- PBKDF2-HMAC-SHA256 格式
);

-- 交易歷史表
CREATE TABLE trade_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL DEFAULT 0,  -- 外鍵，對應 users.id
    timestamp    TEXT    NOT NULL,             -- 平倉時間 (HH:MM:SS)
    symbol       TEXT    NOT NULL,             -- 交易標的 (e.g., SOL)
    side         TEXT    NOT NULL,             -- Long / Short
    entry_price  REAL    NOT NULL,             -- 開倉均價（含滑價）
    exit_price   REAL    NOT NULL,             -- 平倉價格
    pnl          REAL    NOT NULL,             -- 已實現盈虧 (USDT)
    score        INTEGER NOT NULL,             -- AI 共振評分 (0–100)
    exit_reason  TEXT    NOT NULL              -- 移動停利 / 爆倉 / 手動平倉
);
```

> 舊資料庫自動遷移：`init_db()` 啟動時檢查 `trade_history` 是否缺少 `user_id` 欄位，若缺少則自動 `ALTER TABLE` 補齊，無需手動操作。

---

## 九、模組依賴

```
requirements.txt
├── ccxt>=4.3.0                          # 交易所統一接口，支援 100+ 交易所
├── streamlit>=1.35.0                    # 即時 Web UI 框架
├── pandas>=2.0.0                        # 高效能 DataFrame 管線
├── plotly>=5.0.0                        # 互動式金融圖表（含 make_subplots）
├── python-dotenv>=1.0.0                 # 環境變數安全管理
├── google-genai>=1.0.0                  # Gemini API（AI 副駕「狐影」）
└── streamlit-cookies-controller>=0.4.0  # 瀏覽器 Cookie 管理（保持登入）
```

**標準庫使用**（零額外安裝成本）：
`sqlite3` / `hashlib` / `hmac` / `secrets` / `json` / `os` / `re` / `time` / `datetime` / `ctypes` / `collections.deque`

---

## 十、系統架構圖

```
┌─────────────────────────────────────────────────────────────────┐
│                     Project F.O.X. 架構                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    REST API    ┌──────────────────────────┐   │
│  │ Binance      │ ◄──────────── │  CCXT 接口層              │   │
│  │ Futures API  │               │  (Rate Limit 保護)        │   │
│  └──────────────┘               └─────────────┬────────────┘   │
│                                               │                │
│  ┌──────────────┐                ┌────────────▼────────────┐   │
│  │ Gemini 2.0   │ ◄────────────  │   兩段式廣域雷達 +       │   │
│  │ Flash API    │  Text-to-SQL   │   fetch_ohlcv K 線      │   │
│  └──────┬───────┘                └─────────────┬────────────┘   │
│         │ 戰報生成                              │               │
│         ▼                       ┌─────────────▼────────────┐   │
│  ┌──────────────┐               │    Auto-Sniper 狙擊引擎   │   │
│  │ AI 副駕「狐影」│               │  RSI/CCI/ATR/共振評分    │   │
│  │ (ai_copilot) │               └──────────┬───────────────┘   │
│  └──────────────┘                          │                   │
│                                            │                   │
│          ┌─────────────────────────────────┼─────────────┐     │
│          ▼                                 ▼             ▼     │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ ATR 動態     │  │  Session State   │  │    SQLite DB     │  │
│  │ 移動停利引擎  │  │  (虛擬帳戶狀態)  │  │  users 帳號表    │  │
│  └──────────────┘  └────────┬─────────┘  │  trade_history   │  │
│                             │            └──────────────────┘  │
│                    ┌────────▼──────────┐                       │
│                    │ JSON 快照存檔      │                       │
│                    │ (per-user, 跨重啟) │                       │
│                    └───────────────────┘                       │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │          多使用者登入攔截牆 + Cookie 保持登入              │  │
│  │  PBKDF2-SHA256 加密 · HMAC Cookie 7天 · user_id 隔離     │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              Streamlit Fragment UI（8 個獨立刷新區塊）       │  │
│  │    完全體 K 線（Candlestick + MA7/MA25 + 成交量副圖）       │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 十一、開發紀錄

| 版本 | 里程碑 |
|------|--------|
| Phase 1 | CLI 警報雷達，BTC/USDT 價格監控 + 急跌偵測 |
| Phase 2 | Streamlit Dashboard，全市場天眼掃描器，RSI 動能偵測 |
| Phase 3 | CCI 刺客引擎、ATR 動態停利、CFO 資金曲線戰情室 |
| Phase 4 | SQLite 持久化、資金費率整合、AI 共振評分、動態倉位、虧損冷卻 |
| Phase 5 | AI 副駕「狐影」(Text-to-SQL · Gemini 2.0 Flash)，狐影人格注入 |
| Phase 6 | 多帳號 SaaS 架構：users 資料表、PBKDF2 密碼加密、登入防護牆、per-user 沙盒隔離 |
| Phase 7 | 多幣種雷達（BTC/ETH/ADA 即時切換）、幣種同步警報線與走勢快取 |
| Phase 8 | K 線圖完全體升級：Candlestick + MA7/MA25 雙均線 + 成交量副圖（make_subplots）|
| Phase 9 | Cookie 保持登入：HMAC-SHA256 簽名 Token、7 天有效期、自動還原 session、登出清除 |

---

## 免責聲明

本系統為**量化研究與教育用途**，虛擬沙盒中的所有交易均為模擬，不涉及真實資金操作。使用者若將本系統連接真實 Binance 帳戶查看持倉，須自行承擔相關責任。加密貨幣市場具有極高風險，本系統之任何信號輸出均不構成投資建議。

---

<div align="center">

**Project F.O.X.** — Built with precision. Designed for resilience.

</div>
