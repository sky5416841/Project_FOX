"""
Project F.O.X. — AI 量化交易後台 (Streamlit Dashboard)
"""

import os
import json
import time
import ctypes
from datetime import datetime

import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv
from database import init_db, insert_trade, create_user, verify_user
from ai_copilot import ask_copilot
# ── 載入 .env（放在所有 st.* 呼叫之前）────────────────────────────────────────
load_dotenv()
init_db()   # 初始化 SQLite 持久化資料庫

# ── Page config (MUST be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Project F.O.X. — AI 量化後台",
    page_icon="🦊",
    layout="wide",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* ── Global ── */
  html, body, [data-testid="stAppViewContainer"] {
    background-color: #080C12;
    color: #E0E6F0;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
  }

  /* ── Top metric cards ── */
  .metric-card {
    background: linear-gradient(135deg, #0D1321 0%, #111827 100%);
    border: 1px solid #1E2D45;
    border-radius: 10px;
    padding: 1.1rem 1.4rem;
    position: relative;
    overflow: hidden;
  }
  .metric-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, #00C2FF, #00FF88);
  }
  .metric-label {
    font-size: 0.68rem;
    color: #5B7494;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 0.35rem;
  }
  .metric-value {
    font-size: 1.55rem;
    font-weight: 800;
    font-family: monospace;
    line-height: 1.15;
  }
  .metric-sub {
    font-size: 0.72rem;
    color: #5B7494;
    margin-top: 0.25rem;
  }
  .val-green  { color: #00FF88; }
  .val-red    { color: #FF4B4B; }
  .val-cyan   { color: #00C2FF; }
  .val-white  { color: #EAEAEA; }

  /* ── Section headers ── */
  .section-header {
    font-size: 0.72rem;
    font-weight: 700;
    color: #5B7494;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    border-left: 3px solid #00C2FF;
    padding-left: 0.55rem;
    margin-bottom: 0.6rem;
  }

  /* ── AI brain panel ── */
  .ai-panel {
    background: #0D1321;
    border: 1px solid #1E2D45;
    border-radius: 10px;
    padding: 1rem 1.2rem;
    height: 100%;
  }
  /* ── Alert banner ── */
  .alert-banner {
    background: linear-gradient(90deg, #7B0000, #FF0000, #7B0000);
    background-size: 200% 100%;
    animation: alertPulse 1s ease-in-out infinite;
    border-radius: 8px;
    padding: 1rem 1.4rem;
    font-size: 1.2rem;
    font-weight: 800;
    color: #FFF;
    text-align: center;
    margin-bottom: 0.8rem;
  }
  @keyframes alertPulse {
    0%   { background-position: 0%   50%; }
    50%  { background-position: 100% 50%; }
    100% { background-position: 0%   50%; }
  }

  /* ── Positions table ── */
  .pos-block {
    background: #0D1321;
    border: 1px solid #1E2D45;
    border-radius: 10px;
    padding: 1rem 1.2rem;
  }

  /* ── Sidebar ── */
  div[data-testid="stSidebar"] {
    background: #080C12;
    border-right: 1px solid #1E2D45;
  }

  footer { visibility: hidden; }

  /* ── Streamlit dataframe override ── */
  [data-testid="stDataFrame"] {
    border-radius: 8px;
    overflow: hidden;
  }

  /* ── Login wall ── */
  .login-card {
    background: linear-gradient(135deg, #0D1321 0%, #111827 100%);
    border: 1px solid #1E2D45;
    border-radius: 14px 14px 0 0;
    padding: 2.2rem 2.4rem 1.6rem;
    margin-bottom: -1px;
    text-align: center;
  }
  .login-title {
    font-size: 1.7rem;
    font-weight: 900;
    color: #00C2FF;
    letter-spacing: 0.05em;
    margin-bottom: 0.45rem;
  }
  .login-sub {
    font-size: 0.83rem;
    color: #5B7494;
  }
</style>
""", unsafe_allow_html=True)

_STATE_DIR = os.path.dirname(os.path.abspath(__file__))


def _user_state_path(user_id: int) -> str:
    return os.path.join(_STATE_DIR, f"fox_sandbox_state_{user_id}.json")


def save_state() -> None:
    """將虛擬沙盒狀態寫入使用者專屬 JSON 存檔（靜默執行）。"""
    try:
        _uid = st.session_state.get("user_id", 0)
        payload = {
            "virtual_balance":     st.session_state.virtual_balance,
            "virtual_positions":   st.session_state.virtual_positions,
            "virtual_history_log": st.session_state.get("virtual_history_log", []),
        }
        with open(_user_state_path(_uid), "w", encoding="utf-8") as _f:
            json.dump(payload, _f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── Session state init ────────────────────────────────────────────────────────
defaults = {
    "logged_in":           False,
    "user_id":             0,
    "username":            "",
    "_sandbox_loaded":     False,       # 登入後才從存檔載入一次
    "selected_symbol":     "BTC/USDT",
    "selected_timeframe":  "15m",
    "price_history":       [],
    "alerted":             set(),
    "alert_active":        False,
    "alert_msg":           "",
    "prev_price":          None,
    # ── 虛擬量化沙盒（預設值；登入後由 _sandbox_loaded 機制從存檔覆蓋）─────
    "virtual_balance":     100_000.0,
    "virtual_positions":   [],
    "virtual_history_log": [],
    "agent_log":           [],
    "copilot_history":     [],
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

HISTORY_WINDOW_SEC = 60
MAX_CHART_POINTS   = 180   # 15 minutes @ 5s intervals

# ── Cached exchange（公開，僅供價格抓取）────────────────────────────────────
@st.cache_resource
def get_exchange() -> ccxt.binance:
    return ccxt.binance({
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    })

# ── Cached exchange（認證，供帳戶 / 持倉查詢）────────────────────────────────
@st.cache_resource
def get_auth_exchange() -> ccxt.binance | None:
    api_key    = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    if not api_key or not api_secret:
        return None
    return ccxt.binance({
        "apiKey":  api_key,
        "secret":  api_secret,
        "options": {
            "defaultType":            "future",
            "adjustForTimeDifference": True,   # 自動同步伺服器時間，修正 -1021 時間戳偏移
        },
        "enableRateLimit": True,
    })

# ── 帳戶餘額 ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=10, show_spinner=False)
def fetch_account_balance() -> dict | None:
    """回傳 {'total': float, 'free': float}，失敗回傳 None。"""
    ex = get_auth_exchange()
    if ex is None:
        return None
    balance = ex.fetch_balance()
    usdt = balance.get("USDT", {})
    return {
        "total": float(usdt.get("total", 0.0) or 0.0),
        "free":  float(usdt.get("free",  0.0) or 0.0),
    }

# ── 持倉 ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=10, show_spinner=False)
def fetch_real_positions() -> pd.DataFrame | None:
    """回傳持倉 DataFrame，失敗回傳 None；無倉位回傳空 DataFrame。"""
    ex = get_auth_exchange()
    if ex is None:
        return None
    raw    = ex.fetch_positions()
    active = [p for p in raw if (p.get("contracts") or 0) != 0]
    if not active:
        return pd.DataFrame()
    rows = []
    for p in active:
        side = "多" if p.get("side") == "long" else "空"
        upnl = p.get("unrealizedPnl", 0.0) or 0.0
        rows.append({
            "Symbol":     p.get("symbol", ""),
            "Side":       side,
            "Entry":      p.get("entryPrice", 0.0),
            "Mark":       p.get("markPrice",  0.0),
            "Qty":        p.get("contracts",  0.0),
            "UPNL":       round(float(upnl), 4),
        })
    return pd.DataFrame(rows)

# ── OHLCV K 線資料（依幣種 + 時間級別快取）──────────────────────────────────
_TF_TTL = {
    "1m": 30, "5m": 60, "15m": 120,
    "30m": 180, "1h": 300, "4h": 600, "1d": 1800,
}

@st.cache_data(ttl=120, show_spinner=False)
def fetch_ohlcv_data(symbol: str, timeframe: str, limit: int = 100) -> tuple[pd.DataFrame, str | None]:
    """取得 OHLCV K 線，回傳 (DataFrame, error_msg)。cache TTL 由呼叫端控制失效。"""
    try:
        ex  = get_exchange()
        raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not raw:
            return pd.DataFrame(), f"取不到 {symbol} {timeframe} K 線資料"
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df, None
    except ccxt.RateLimitExceeded as e:
        return pd.DataFrame(), f"Binance Rate Limit 觸發：{e}"
    except ccxt.NetworkError as e:
        return pd.DataFrame(), f"網路連線錯誤：{e}"
    except ccxt.ExchangeError as e:
        return pd.DataFrame(), f"交易所錯誤：{e}"
    except Exception as e:
        return pd.DataFrame(), f"K 線取得失敗：{e}"


# ── Beep ──────────────────────────────────────────────────────────────────────
def beep() -> None:
    try:
        ctypes.windll.kernel32.Beep(1200, 300)
        ctypes.windll.kernel32.Beep(900,  500)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 天眼掃描器：工具函式 + 資料層
# ─────────────────────────────────────────────────────────────────────────────

def _calc_rsi(closes: list, period: int = 14) -> float:
    """Wilder Smoothing RSI，純 Python，無外部 ta 套件。"""
    if len(closes) < period + 1:
        return float("nan")
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d,  0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_g  = sum(gains[:period])  / period
    avg_l  = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i])  / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def _calc_cci(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """
    Commodity Channel Index (CCI)，純 Python 實作，無外部 ta 套件。
    公式：CCI = (TP - SMA_TP) / (0.015 × MeanDev)
    TP (Typical Price) = (High + Low + Close) / 3
    """
    if len(closes) < period:
        return float("nan")
    tps      = [(highs[i] + lows[i] + closes[i]) / 3.0
                for i in range(len(closes) - period, len(closes))]
    tp_mean  = sum(tps) / period
    mean_dev = sum(abs(tp - tp_mean) for tp in tps) / period
    if mean_dev == 0:
        return 0.0
    return round((tps[-1] - tp_mean) / (0.015 * mean_dev), 1)


def _calc_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """
    Average True Range (ATR)，Wilder Smoothing，純 Python，無外部 ta 套件。
    TR = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
    ATR = Wilder MA of TR over `period` bars
    """
    if len(closes) < period + 1:
        return float("nan")
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)
    # Wilder smoothing：第一個值取簡單平均，之後逐步平滑
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return round(atr, 8)


# ── 天眼掃描器：終極黑名單（非加密資產完全排除）────────────────────────────
_SCANNER_BLACKLIST: frozenset[str] = frozenset({
    # 主流大型幣
    "BTC", "ETH",
    # 貴金屬 / 大宗商品（含期貨代碼）
    "XAU", "XAG", "GOLD", "SILVER",
    "CL",                              # WTI 原油期貨
    "OIL", "BRENT", "BRENTOIL",
    "GAS", "NG",                       # 天然氣
    "COPPER", "HG",
    # 穩定幣（各類型）
    "USDC", "USDT", "BUSD", "TUSD", "USDP", "DAI",
    "FDUSD", "PYUSD", "USDE", "FRAX", "LUSD", "GUSD",
    # 法幣錨定 / 匯率衍生品
    "EUR", "GBP", "AUD", "JPY", "BRL",
    "BIDR", "IDRT", "NGN", "RUB", "TRY",
    # 黃金錨定代幣
    "PAXG",
})

# 字元格式白名單規則（純加密代幣應符合的模式）
import re as _re
_CRYPTO_NAME_RE = _re.compile(r"^[A-Z0-9]{2,12}$")   # 2–12 位大寫字母或數字
_CRYPTO_NAME_BLOCKLIST_PATTERNS = ("XAU", "XAG", "USD", "EUR", "GBP", "CL")

_RADAR_PHASE1_VOL_MIN = 10_000_000   # 第一段：24h 成交額下限 (USDT)，過濾殭屍幣
_RADAR_TOP_N          = 30           # 第二段：精準火控標的數量


def _is_clean_crypto(base: str) -> bool:
    """True iff base 看起來像純加密代幣，非金融商品/穩定幣/法幣。"""
    if base in _SCANNER_BLACKLIST:
        return False
    if not _CRYPTO_NAME_RE.match(base):
        return False
    # 字串包含已知非加密前綴的一律排除
    for pat in _CRYPTO_NAME_BLOCKLIST_PATTERNS:
        if pat in base:
            return False
    return True


@st.cache_data(ttl=20, show_spinner=False)
def fetch_scanner_data() -> tuple[pd.DataFrame, str | None]:
    """
    兩段式廣域雷達：全市場覆蓋 → 精準火控。
    第一段：fetch_tickers() 一次性廣域掃描，按絕對漲跌幅排出 Top 30 高波動活躍榜。
    第二段：逐一 fetch_ohlcv() 計算 RSI / Vol Surge，0.1s 限速防封鎖。
    快取 20 秒。回傳 (DataFrame, error_message)。
    """
    try:
        ex = get_exchange()

        # ════════════════════════════════════════════════════════════════════
        # 第一段：廣域掃描 — fetch_tickers() 全市場一次性拉取
        # ════════════════════════════════════════════════════════════════════
        tickers = ex.fetch_tickers()

        candidates = []
        for sym, t in tickers.items():
            # ① 只要 USDT 永續合約
            if not sym.endswith(":USDT"):
                continue
            # ② contractType 必須是 PERPETUAL
            contract_type = (t.get("info") or {}).get("contractType", "PERPETUAL")
            if contract_type and contract_type != "PERPETUAL":
                continue
            sym_base = sym.split("/")[0]
            # ③ 終極純加密過濾（黑名單 + 穩定幣 + 法幣 + 商品）
            if not _is_clean_crypto(sym_base):
                continue
            last = float(t.get("last") or 0)
            qv   = float(t.get("quoteVolume") or 0)
            pct  = t.get("percentage")       # 24h 漲跌幅 (%)，可能為 None
            if last <= 0 or qv <= 0 or pct is None:
                continue
            # ④ 活躍度篩選：24h 成交額 > 1,000 萬 U（過濾殭屍幣）
            if qv < _RADAR_PHASE1_VOL_MIN:
                continue
            candidates.append((sym, sym_base, last, qv, float(pct)))

        # 按 24h 漲跌幅『絕對值』降序 → 波動最劇烈的在最前
        candidates.sort(key=lambda x: abs(x[4]), reverse=True)
        fire_control = candidates[:_RADAR_TOP_N]

        # ════════════════════════════════════════════════════════════════════
        # 第二段：精準火控 — 逐一 fetch_ohlcv，0.1s 限速防封鎖
        # ════════════════════════════════════════════════════════════════════
        rows = []
        for sym, sym_base, last_price, _, pct24h in fire_control:
            time.sleep(0.1)       # 🛡️ 防封鎖裝甲：每次 K 線請求間隔 100ms

            try:
                ohlcv = ex.fetch_ohlcv(sym, timeframe="15m", limit=20)

                # 資料完整性驗證
                if len(ohlcv) < 15:
                    continue
                highs   = [c[2] for c in ohlcv]
                lows    = [c[3] for c in ohlcv]
                closes  = [c[4] for c in ohlcv]
                volumes = [c[5] for c in ohlcv]
                if sum(volumes) <= 0 or max(closes) == min(closes):
                    continue

                rsi       = _calc_rsi(closes)
                cci       = _calc_cci(highs, lows, closes)
                atr       = _calc_atr(highs, lows, closes)
                avg_prev5 = sum(volumes[-6:-1]) / 5
                vol_surge = round((volumes[-1] / avg_prev5) * 100, 1) if avg_prev5 > 0 else None

                if vol_surge is None or pd.isna(rsi) or pd.isna(cci):
                    continue

                # ── 資金費率 (Funding Rate) 獲取 ─────────────────────────────
                funding_rate = 0.0
                try:
                    fr_data = ex.fetch_funding_rate(sym)
                    funding_rate = float(fr_data.get("fundingRate", 0.0) or 0.0)
                except Exception as _fr_ex:
                    # API 報錯或不支援時預設為 0，不中斷掃描
                    funding_rate = 0.0

                # 取倒數第二根（最後一根已完整收盤的 K 線）供 Protocol Delta 分析
                _lc       = ohlcv[-2]
                price_str = f"{last_price:,.2f}" if last_price >= 10 else f"{last_price:,.4f}"
                rows.append({
                    "Symbol":            sym_base,
                    "24h 漲跌幅 (%)":    round(pct24h, 2),
                    "價格 (USDT)":       price_str,
                    "RSI 15m":           rsi,
                    "CCI 14":            cci,
                    "Vol Surge (%)":     vol_surge,
                    "atr":               atr if not pd.isna(atr) else 0.0,  # ATR(14)，供動態停利使用
                    "funding_rate":      funding_rate,  # 最新資金費率（原始小數，>0 代表多頭擁擠）
                    # ── 最後已收盤 K 線結構（供協議 Delta 刺客邏輯使用）──────
                    "ohlc_o":            float(_lc[1]),   # Open
                    "ohlc_h":            float(_lc[2]),   # High
                    "ohlc_l":            float(_lc[3]),   # Low
                    "ohlc_c":            float(_lc[4]),   # Close
                })

            except (ccxt.NetworkError, ccxt.RequestTimeout) as _ex:
                print(f"[FOX][WARN] fetch_scanner {sym}: 網路閃斷 → {_ex}")
                continue          # 網路閃斷：跳過此幣，繼續掃下一個
            except Exception as _ex:
                print(f"[FOX][WARN] fetch_scanner {sym}: 未知異常 → {_ex}")
                continue          # 任何解析錯誤：跳過此幣，不中斷掃描

        return (pd.DataFrame(rows) if rows else pd.DataFrame()), None

    except ccxt.RateLimitExceeded as exc:
        return pd.DataFrame(), f"Binance Rate Limit 觸發：{exc}"
    except ccxt.NetworkError as exc:
        return pd.DataFrame(), f"網路連線錯誤：{exc}"
    except ccxt.ExchangeError as exc:
        return pd.DataFrame(), f"交易所錯誤：{exc}"
    except Exception as exc:
        return pd.DataFrame(), f"掃描失敗（未知錯誤）：{exc}"

# ─────────────────────────────────────────────────────────────────────────────
# 虛擬狙擊引擎：常數 + 決策函式
# ─────────────────────────────────────────────────────────────────────────────
SNIPER_MARGIN_DEFAULT  = 1_000.0   # 單筆保證金預設值 (USDT)，實際由 sidebar 控制
SNIPER_RSI_LONG        = 30.0      # RSI 低於此值 → 恐慌超賣 → 做多
SNIPER_CCI_SHORT       = 250.0     # CCI 高於此值 → 極度超買 → 配合 is_pin_bar 做空
SNIPER_VOL_MIN         = 150.0     # Vol Surge 須超過此值 (%) 才觸發做多
AGENT_LOG_MAX          = 50        # 日誌最多保留幾條
TRAILING_STOP_PCT      = 0.05      # 移動停利回撤比例：5%（從最高水位回撤觸發平倉）
OPEN_FEE_RATE          = 0.0005    # 開倉手續費率 (0.05%)
CLOSE_FEE_RATE         = 0.0005    # 平倉手續費率 (0.05%)
SLIPPAGE_PCT           = 0.001     # 滑價模擬 0.1%
LIQUIDATION_THRESHOLD  = 0.95      # 浮虧達保證金 95% → 爆倉
# ── 協議 Delta：K 線型態刺客常數 ─────────────────────────────────────────
DELTA_WICK_RATIO      = 0.60       # 上影線須佔 K 線總長 60% 以上
DELTA_TOTAL_MIN_PCT   = 0.02       # K 線總長須 > 收盤價 2%（確保有效波動）


def _parse_price(price_str: str) -> float | None:
    """將資料層格式化過的價格字串解析回 float，失敗回傳 None。"""
    try:
        return float(str(price_str).replace(",", "").strip())
    except Exception:
        return None


def _calc_resonance_score(
    rsi: float,
    cci: float,
    wick_pct: float,
    vol_surge: float,
    side: str,
) -> int:
    """
    AI 共振評分 (0–100)：量化本次開倉信號的綜合強度。
    Component 1 (0–40) — 動能極端度：做多用 RSI 低點；做空用 CCI 高點。
    Component 2 (0–35) — 影線長度：上影線佔比越高越極端。
    Component 3 (0–25) — 量能爆發：Vol Surge 越誇張越高分。
    """
    score = 0

    # ── Component 1：動能極端度 ─────────────────────────────────────────────
    if side == "Long":
        if rsi < 15:       score += 40
        elif rsi < 20:     score += 30
        elif rsi < 25:     score += 20
        else:              score += 10   # < 30，基準分
    else:  # Short
        cci_abs = abs(cci)
        if cci_abs > 400:   score += 40
        elif cci_abs > 300: score += 30
        elif cci_abs > 250: score += 20
        else:               score += 10

    # ── Component 2：影線長度 ───────────────────────────────────────────────
    if wick_pct >= 85:       score += 35
    elif wick_pct >= 75:     score += 25
    elif wick_pct >= 65:     score += 15
    elif wick_pct >= 60:     score += 5

    # ── Component 3：量能爆發 ───────────────────────────────────────────────
    if vol_surge >= 400:     score += 25
    elif vol_surge >= 300:   score += 20
    elif vol_surge >= 200:   score += 15
    elif vol_surge >= 150:   score += 10

    return min(score, 100)


def _run_sniper(scan_df: pd.DataFrame) -> None:
    """
    Auto-Sniper Engine：掃描 scan_df，依條件對虛擬帳戶自動開倉。
    含槓桿、滑價模擬與開倉手續費。直接操作 st.session_state，不觸碰真實帳戶。
    """
    if scan_df is None or scan_df.empty:
        return

    # ── 讀取戰術控制台參數（由 sidebar 控制）─────────────────────────────────
    leverage      = int(st.session_state.get("sniper_leverage",     10))
    margin        = float(st.session_state.get("sniper_margin",     SNIPER_MARGIN_DEFAULT))
    max_pos       = int(st.session_state.get("sniper_max_pos",      5))
    cooldown_min  = int(st.session_state.get("sniper_loss_cooldown", 120))

    # 僅統計「Open」倉位，已平倉的不佔用 symbol slot（允許重新入場）
    open_pos = [p for p in st.session_state.virtual_positions
                if p.get("status", "Open") == "Open"]
    existing = {p["symbol"] for p in open_pos}

    for _, row in scan_df.iterrows():
        # ── 最大持倉數上限 ────────────────────────────────────────────────────
        if len(existing) >= max_pos:
            break

        symbol       = row.get("Symbol", "")
        rsi          = row.get("RSI 15m")
        cci          = row.get("CCI 14")
        vol_surge    = row.get("Vol Surge (%)")
        atr_val      = float(row.get("atr", 0.0) or 0.0)   # ATR(14)，0 代表資料不足
        price_val    = _parse_price(row.get("價格 (USDT)", ""))
        funding_rate = float(row.get("funding_rate", 0.0) or 0.0)

        if not symbol or price_val is None or price_val <= 0:
            continue
        if pd.isna(rsi) or pd.isna(vol_surge) or pd.isna(cci):
            continue
        if symbol in existing:
            continue

        rsi = float(rsi)
        cci = float(cci)

        # ── 升級一：同幣種虧損冷卻檢查 ──────────────────────────────────────
        if cooldown_min > 0:
            _settled_for_sym = []
            for _sp in st.session_state.virtual_positions:
                if _sp.get("symbol") == symbol and _sp.get("status") == "Closed":
                    _settled_for_sym.append(_sp)
            for _sp in st.session_state.get("virtual_history_log", []):
                if _sp.get("symbol") == symbol:
                    _settled_for_sym.append(_sp)

            if _settled_for_sym:
                _now_dt = datetime.now()
                _min_elapsed_sec = float("inf")
                _last_pnl_for_cooldown = None
                for _sp in _settled_for_sym:
                    _cat   = str(_sp.get("closed_at", ""))
                    _tpart = _cat.split()[-1]
                    try:
                        _cdt = datetime.strptime(_tpart, "%H:%M:%S").replace(
                            year=_now_dt.year, month=_now_dt.month, day=_now_dt.day)
                        _elapsed_sec = (_now_dt - _cdt).total_seconds()
                        if _elapsed_sec < 0:
                            _elapsed_sec += 86400  # 跨日修正
                        if _elapsed_sec < _min_elapsed_sec:
                            _min_elapsed_sec = _elapsed_sec
                            _last_pnl_for_cooldown = float(_sp.get("realized_pnl", 0.0))
                    except Exception:
                        continue

                if (_last_pnl_for_cooldown is not None
                        and _last_pnl_for_cooldown < 0
                        and _min_elapsed_sec / 60 < cooldown_min):
                    _remain = cooldown_min - _min_elapsed_sec / 60
                    print(f"[冷卻中] {symbol} 該幣種剛經歷虧損停損，暫不開倉"
                          f"（距上次虧損 {_min_elapsed_sec/60:.0f} 分鐘，"
                          f"冷卻剩餘 {_remain:.0f} 分鐘）")
                    continue

        # ── 協議 Delta：長上影線 (is_pin_bar) 計算 ───────────────────────────
        _o = float(row.get("ohlc_o", 0))
        _h = float(row.get("ohlc_h", 0))
        _l = float(row.get("ohlc_l", 0))
        _c = float(row.get("ohlc_c", 0))
        is_pin_bar  = False
        _wick_pct   = 0.0
        if _h > _l and _c > 0:
            _upper_wick = _h - max(_o, _c)
            _total_len  = _h - _l
            _wick_pct   = round(_upper_wick / _total_len * 100, 1)
            if (_upper_wick > _total_len * DELTA_WICK_RATIO
                    and _total_len > _c * DELTA_TOTAL_MIN_PCT):
                is_pin_bar = True

        # ── 決策判斷 ─────────────────────────────────────────────────────────
        # Long  : RSI 超賣 (<30) + 爆量 (Vol Surge >150%)
        # Short : CCI 極度超買 (>250) + 長上影線 (is_pin_bar) + 資金費率 > 0
        #         → 協議 Delta 刺客，資金費率過濾確保多頭擁擠才做空
        side = None

        if rsi < SNIPER_RSI_LONG and vol_surge > SNIPER_VOL_MIN:
            side = "Long"
        elif cci > SNIPER_CCI_SHORT and is_pin_bar and funding_rate > 0:
            side = "Short"

        if side is None:
            continue

        # ── 升級三：AI 共振評分 ───────────────────────────────────────────────
        score = _calc_resonance_score(rsi, cci, _wick_pct, float(vol_surge), side)

        # ── 動態保證金配置（依共振評分決定倉位大小）─────────────────────────
        if score >= 80:
            dynamic_margin = margin * 1.5     # 絕對送分題：放大 1.5 倍
        elif score >= 60:
            dynamic_margin = margin           # 標準機會：正常倉位
        else:
            dynamic_margin = margin * 0.5     # 勉強及格：縮小試水溫

        # ── 風控硬頂：單筆保證金不得超過帳戶總資金的 10% ────────────────────
        # 防止極端插針造成爆倉穿倉，從開倉源頭限制最大損失敞口
        _balance_cap   = st.session_state.virtual_balance * 0.10
        dynamic_margin = min(dynamic_margin, _balance_cap)
        if dynamic_margin < 1.0:      # 餘額過低，跳過本次開倉
            continue

        # ── 滑價模擬：做多吃漲、做空吃跌 ────────────────────────────────────
        entry_slipped = (price_val * (1.0 + SLIPPAGE_PCT) if side == "Long"
                         else price_val * (1.0 - SLIPPAGE_PCT))

        # ── 計算名目價值與開倉手續費（使用動態保證金）───────────────────────
        nominal  = dynamic_margin * leverage
        qty      = nominal / entry_slipped
        open_fee = nominal * OPEN_FEE_RATE

        # ── 資金是否足夠（動態保證金 + 預估開倉手續費）──────────────────────
        if st.session_state.virtual_balance < dynamic_margin + open_fee:
            continue

        # ── 執行虛擬開倉 ─────────────────────────────────────────────────────
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state.virtual_positions.append({
            "symbol":       symbol,
            "side":         side,
            "entry_price":  entry_slipped,
            "qty":          qty,
            "mark_price":   price_val,
            "margin":       dynamic_margin,
            "leverage":     leverage,
            "nominal":      nominal,
            "opened_at":    ts,
            "hwm":          entry_slipped,
            "atr":          atr_val,       # 開倉當下的 ATR(14)，驅動動態移動停利
            "score":        score,         # AI 共振評分 (0–100)
            "funding_rate": funding_rate,  # 開倉當下資金費率
            "status":       "Open",
        })
        st.session_state.virtual_balance -= (dynamic_margin + open_fee)
        existing.add(symbol)

        # ── 寫入決策日誌 ──────────────────────────────────────────────────────
        _score_tag = ("🔥 絕殺" if score >= 80 else "✅ 標準" if score >= 60 else "🔬 試水")
        if side == "Short":
            log_entry = (
                f"[{ts}]　🟣 [協議 Delta 觸發] **{symbol}** "
                f"CCI 極度超買 ({cci:.0f} > {SNIPER_CCI_SHORT:.0f}) "
                f"且出現 {_wick_pct:.0f}% 長上影線，資金費率 {funding_rate*100:+.4f}%，"
                f"主力出貨確認，執行刺客狙擊！"
                f"　共振評分 {score}分 {_score_tag} | {leverage}x"
                f" | 保證金 ${dynamic_margin:,.0f} | 名目 ${nominal:,.0f} USDT"
                f"　@ {entry_slipped:,.4f}　Qty {qty:.4f}　手續費 -${open_fee:,.2f}"
            )
        else:
            log_entry = (
                f"[{ts}]　🔫 偵測到 **{symbol}** "
                f"恐慌超賣 (RSI {rsi:.1f} < {SNIPER_RSI_LONG}, 爆量 {vol_surge:.0f}%)。"
                f"已自動市價做多 {leverage}x"
                f" | 共振評分 {score}分 {_score_tag}"
                f" | 保證金 ${dynamic_margin:,.0f} | 名目 ${nominal:,.0f} USDT"
                f"　@ {entry_slipped:,.4f}（含滑價）　Qty {qty:.4f}　手續費 -${open_fee:,.2f}"
            )
        st.session_state.agent_log.insert(0, log_entry)

        # ── 開倉後立刻存檔 ────────────────────────────────────────────────────
        save_state()

    # 日誌長度上限
    st.session_state.agent_log = st.session_state.agent_log[:AGENT_LOG_MAX]


def _update_mark_prices(scan_df: pd.DataFrame) -> None:
    """
    批次更新所有 Open 倉位的標記價格。
    策略（三段式，降低 API 成本）：
      1. 先從 scan_df（已快取）建立 price_map — 零 API 成本
      2. 對 scan_df 未覆蓋的持倉，用 fetch_tickers(symbols) 一次性補齊
      3. 仍取不到價格的倉位設 price_error=True，UI 顯示 ⚠️ 警告
    symbol 格式：虛擬倉位存 base（如 "SOL"），API 用 "SOL/USDT:USDT"。
    """
    # ── Step 1：從 scan_df 免費建立 price_map (base → float) ─────────────────
    price_map: dict[str, float] = {}
    if scan_df is not None and not scan_df.empty:
        try:
            for _, row in scan_df.iterrows():
                p = _parse_price(row.get("價格 (USDT)", ""))
                if p is not None and p > 0:
                    price_map[row.get("Symbol", "")] = p
        except Exception as _ex:
            print(f"[FOX][WARN] price_map 建立失敗 → {_ex}")

    # ── Step 2：找出 Open 倉位中 scan_df 未覆蓋的 symbol ────────────────────
    open_positions = [vp for vp in st.session_state.virtual_positions
                      if vp.get("status", "Open") == "Open"]
    missing_bases  = [vp["symbol"] for vp in open_positions
                      if vp.get("symbol") and vp["symbol"] not in price_map]

    if missing_bases:
        # base → "BASE/USDT:USDT"（Binance 永續合約格式）
        missing_full = [f"{b}/USDT:USDT" for b in missing_bases]
        try:
            ex       = get_exchange()
            tickers  = ex.fetch_tickers(missing_full)   # 一次性批次請求
            for sym_full, t in tickers.items():
                base = sym_full.split("/")[0]
                last = t.get("last") or t.get("close") or 0.0
                if last and float(last) > 0:
                    price_map[base] = float(last)
        except (ccxt.NetworkError, ccxt.RequestTimeout) as _ex:
            print(f"[FOX][WARN] fetch_tickers 網路閃斷 → {_ex}")
        except ccxt.ExchangeError as _ex:
            print(f"[FOX][WARN] fetch_tickers 交易所錯誤 → {_ex}")
        except Exception as _ex:
            print(f"[FOX][WARN] fetch_tickers 未知錯誤 → {_ex}")

    # ── Step 3：逐筆更新，取不到價格則標記 price_error ──────────────────────
    for vp in open_positions:
        try:
            base     = vp.get("symbol", "")
            new_mark = price_map.get(base)
            if new_mark and new_mark > 0:
                vp["mark_price"]  = new_mark
                vp["price_error"] = False      # 清除舊警告
            else:
                vp["price_error"] = True       # ⚠️ 讓 UI 顯示警告
                print(f"[FOX][WARN] {base} 無法取得最新報價，標記 ⚠️")
        except Exception as _ex:
            vp["price_error"] = True
            print(f"[FOX][WARN] _update_mark_prices {vp.get('symbol', '?')} → {_ex}")


def _run_trailing_stop() -> None:
    """
    移動停利 (Trailing Stop) + 爆倉 (Liquidation) 結算引擎。
    對每筆 Open 倉位依序執行：
      1. 更新最高水位 (HWM)
      2. 爆倉檢查（優先）：浮虧 ≥ 保證金 × 95% → 強制爆倉清算
      3. 移動停利：回撤 ≥ TRAILING_STOP_PCT → 觸發平倉
      4. 結算：歸還保證金 + PNL，扣除平倉手續費
    """
    ts = datetime.now().strftime("%H:%M:%S")
    for vp in st.session_state.virtual_positions:
        try:
            if vp.get("status", "Open") != "Open":
                continue

            mark   = float(vp.get("mark_price", vp["entry_price"]))
            entry  = float(vp["entry_price"])
            qty    = float(vp["qty"])
            side   = vp["side"]
            hwm    = float(vp.get("hwm", entry))
            margin = float(vp.get("margin", SNIPER_MARGIN_DEFAULT))
            atr    = float(vp.get("atr", 0.0) or 0.0)

            # ── 動態追蹤距離：2 × ATR（絕對價格距離）────────────────────────
            # 舊倉位 atr == 0 時降級回固定 5% 保護，確保不報錯
            trail_dist = (2.0 * atr) if atr > 0 else (entry * TRAILING_STOP_PCT)

            # ── 計算當前浮動盈虧 ──────────────────────────────────────────
            upnl = (mark - entry) * qty if side == "Long" else (entry - mark) * qty

            # ── Step 1：更新最高水位 (HWM / LWM) ───────────────────────────
            if side == "Long":
                if mark > hwm:
                    vp["hwm"] = mark
                    hwm = mark
            else:  # Short：hwm 實際上是「最低水位 (LWM)」，追蹤最低點
                if mark < hwm:
                    vp["hwm"] = mark
                    hwm = mark

            # ── Step 2：爆倉檢查（優先於移動停利）──────────────────────────
            liquidated = upnl <= -(margin * LIQUIDATION_THRESHOLD)

            # ── Step 3：ATR 動態移動停利檢查 ───────────────────────────────
            # Long  觸發線：HWM - (2 × ATR)
            # Short 觸發線：LWM + (2 × ATR)
            trailing_triggered = False
            if not liquidated:
                if side == "Long"  and mark <= hwm - trail_dist:
                    trailing_triggered = True
                elif side == "Short" and mark >= hwm + trail_dist:
                    trailing_triggered = True

            if not liquidated and not trailing_triggered:
                continue

            # ── Step 4：執行結算（含平倉手續費）────────────────────────────
            pnl           = round(upnl, 4)
            close_nominal = qty * mark
            close_fee     = round(close_nominal * CLOSE_FEE_RATE, 4)

            # 歸還：保證金 + PNL - 平倉手續費（爆倉時 PNL ≈ -margin*0.95，幾乎歸零）
            returned = margin + pnl - close_fee
            st.session_state.virtual_balance += max(returned, 0.0)  # 不允許負數歸還

            # 標記平倉
            vp["status"]        = "Closed"
            vp["closed_at"]     = ts
            vp["closed_price"]  = mark
            vp["realized_pnl"]  = pnl
            vp["close_fee"]     = close_fee

            # ── Step 5：寫入日誌 ──────────────────────────────────────────
            if liquidated:
                st.session_state.agent_log.insert(0,
                    f"[{ts}]　💥 [爆倉] **{vp['symbol']}** "
                    f"浮虧 ${pnl:,.2f} 觸及保證金 {LIQUIDATION_THRESHOLD*100:.0f}% 下限，強制清算！"
                    f"　平倉手續費 -${close_fee:,.2f}"
                )
            else:
                # PNL ≥ 0 → 真正停利；PNL < 0 → ATR 防守線出場但虧損，標記為停損
                _close_label = "移動停利" if pnl >= 0 else "動態停損"
                icon  = "🟢" if pnl >= 0 else "🔴"
                sign  = "+" if pnl >= 0 else ""
                atr_label = (f"ATR×2={trail_dist:,.4f}" if atr > 0
                             else f"固定5%={trail_dist:,.4f}")
                st.session_state.agent_log.insert(0,
                    f"[{ts}]　{icon} [{_close_label}] **{vp['symbol']}** "
                    f"ATR 防守線觸發（{atr_label}），最終結算：{sign}{pnl:,.2f} USDT"
                    f"　平倉手續費 -${close_fee:,.2f}"
                )

            # ── 平倉後立刻存檔 ────────────────────────────────────────────
            save_state()

            # ── 寫入 SQLite 持久化交易紀錄（精確區分停利/停損）────────────
            if liquidated:
                _exit_reason = "爆倉"
            elif pnl >= 0:
                _exit_reason = "移動停利"
            else:
                _exit_reason = "動態停損"
            insert_trade(
                timestamp   = ts,
                symbol      = vp["symbol"],
                side        = vp.get("side", ""),
                entry_price = float(vp.get("entry_price", 0)),
                exit_price  = float(mark),
                pnl         = float(pnl),
                score       = int(vp.get("score") or 0),
                exit_reason = _exit_reason,
                user_id     = st.session_state.get("user_id", 0),
            )

        except Exception as _ex:
            print(f"[FOX][WARN] _run_trailing_stop: {vp.get('symbol', '?')} → {_ex}")
            continue                   # 單筆異常，跳下一筆，不中斷引擎

    st.session_state.agent_log = st.session_state.agent_log[:AGENT_LOG_MAX]


# ── 登入攔截牆 (Multi-user SaaS) ─────────────────────────────────────────────
if not st.session_state.get("logged_in", False):
    _, _lc, _ = st.columns([1, 1.6, 1])
    with _lc:
        st.markdown(
            '<div class="login-card">'
            '<div class="login-title">🦊 Project F.O.X.</div>'
            '<div class="login-sub">AI 量化交易後台 &nbsp;·&nbsp; 指揮官驗證系統</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        _tab_login, _tab_register = st.tabs(["🔐 登入", "📝 建立帳號"])

        with _tab_login:
            with st.form("fox_login"):
                _username = st.text_input("帳號", placeholder="輸入指揮官帳號")
                _password = st.text_input("密碼", placeholder="輸入存取密碼",
                                          type="password")
                _login_btn = st.form_submit_button(
                    "🔐 登入指揮中心", type="primary", use_container_width=True
                )
            if _login_btn:
                _uid = verify_user(_username, _password)
                if _uid is not None:
                    st.session_state.logged_in  = True
                    st.session_state.user_id    = _uid
                    st.session_state.username   = _username.strip()
                    st.rerun()
                else:
                    st.error("❌ 帳號或密碼錯誤，請重試。")

        with _tab_register:
            with st.form("fox_register"):
                _reg_user  = st.text_input("新帳號", placeholder="3–20 個字元")
                _reg_pass  = st.text_input("密碼",   placeholder="至少 6 個字元",
                                           type="password")
                _reg_pass2 = st.text_input("確認密碼", placeholder="再輸入一次密碼",
                                           type="password")
                _reg_btn = st.form_submit_button(
                    "📝 建立帳號", type="primary", use_container_width=True
                )
            if _reg_btn:
                if _reg_pass != _reg_pass2:
                    st.error("❌ 兩次密碼不一致，請重新確認。")
                else:
                    _ok, _msg = create_user(_reg_user, _reg_pass)
                    if _ok:
                        st.success(f"✅ {_msg}")
                    else:
                        st.error(f"❌ {_msg}")
    st.stop()

# ── 登入後：按需載入使用者專屬沙盒狀態（每個 browser session 只執行一次）──────
if not st.session_state._sandbox_loaded:
    _sf = _user_state_path(st.session_state.user_id)
    if os.path.exists(_sf):
        try:
            with open(_sf, "r", encoding="utf-8") as _f:
                _saved = json.load(_f)
            st.session_state.virtual_balance     = float(_saved.get("virtual_balance",     100_000.0))
            st.session_state.virtual_positions   = list(_saved.get("virtual_positions",     []))
            st.session_state.virtual_history_log = list(_saved.get("virtual_history_log",   []))
        except Exception:
            pass
    st.session_state._sandbox_loaded = True

# ── 幣種別警報線設定表（min / max / default / step / format）────────────────
_FLOOR_CFG: dict = {
    "BTC/USDT": {"min": 50_000, "max": 120_000, "value": 70_500, "step": 100,  "fmt": "%d"},
    "ETH/USDT": {"min": 500,    "max": 10_000,  "value": 2_000,  "step": 50,   "fmt": "%d"},
    "ADA/USDT": {"min": 0.10,   "max": 5.0,     "value": 0.50,   "step": 0.05, "fmt": "%.2f"},
}

# ═════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🦊 F.O.X. 參數設定")
    _uname = st.session_state.get("username", "")
    if _uname:
        st.markdown(
            f"<div style='font-size:0.72rem;color:#5B7494;margin-top:-0.5rem'>"
            f"👤 指揮官：<span style='color:#00C2FF'>{_uname}</span></div>",
            unsafe_allow_html=True,
        )
    st.divider()

    st.selectbox(
        "🎯 交易標的 (Symbol)",
        ["BTC/USDT", "ETH/USDT", "ADA/USDT"],
        key="selected_symbol",
    )
    st.divider()

    _fcfg = _FLOOR_CFG.get(st.session_state.selected_symbol, _FLOOR_CFG["BTC/USDT"])
    st.slider("🔻 跌破警報線 (USDT)",
              min_value=_fcfg["min"], max_value=_fcfg["max"], value=_fcfg["value"],
              step=_fcfg["step"], format=_fcfg["fmt"], key="price_floor")
    st.slider("⚡ 急跌警報 % (1 分鐘)",
              min_value=0.1, max_value=5.0, value=0.5,
              step=0.1, format="%.1f%%", key="drop_pct")

    st.divider()
    st.markdown("### 🎛️ 虛擬沙盒戰術控制台")
    st.slider("⚡ 槓桿倍數 (Leverage)",
              min_value=1, max_value=50, value=10,
              step=1, format="%dx", key="sniper_leverage")
    st.number_input("💰 單筆保證金 (USDT)",
                    min_value=100.0, max_value=50_000.0,
                    value=1_000.0, step=100.0, key="sniper_margin")
    st.slider("📂 最大同時持倉數",
              min_value=1, max_value=10, value=5,
              step=1, key="sniper_max_pos")
    st.number_input("⏳ 同幣種虧損冷卻時間 (分鐘)",
                    min_value=0, max_value=1440,
                    value=120, step=10, key="sniper_loss_cooldown")

    st.divider()
    st.caption("⚡ 各區塊獨立刷新：5s / 10s / 15s")
    st.caption("📡 資料來源：Binance Futures")

    if st.button("🔕 解除警報", width="stretch"):
        st.session_state.alert_active = False
        st.session_state.alert_msg    = ""
        st.session_state.alerted.clear()

    st.divider()
    with st.expander("⚠️ 危險操作區", expanded=False):
        st.markdown(
            "<div style='font-size:0.75rem;color:#FF4B4B;margin-bottom:0.5rem'>"
            "此操作將清空所有持倉與歷史紀錄，並將餘額重置為 100,000 USDT，<b>無法撤銷</b>。</div>",
            unsafe_allow_html=True,
        )
        if st.button("🔄 重置沙盒 (清空持倉與歷史)", width="stretch", type="primary"):
            st.session_state.virtual_positions   = []
            st.session_state.virtual_history_log = []
            st.session_state.virtual_balance     = 100_000.0
            save_state()
            st.rerun()

    st.divider()
    if st.button("🚪 登出指揮中心", width="stretch"):
        for _k in ("logged_in", "user_id", "username", "_sandbox_loaded",
                   "virtual_balance", "virtual_positions", "virtual_history_log",
                   "agent_log", "copilot_history", "price_history",
                   "alert_active", "alert_msg", "prev_price"):
            st.session_state.pop(_k, None)
        st.rerun()

# ── 幣種切換偵測：清空走勢快取 + 重置警報 + 讓 slider 回到新幣種預設值 ───────
if st.session_state.get("_price_sym") != st.session_state.selected_symbol:
    st.session_state.price_history = []
    st.session_state.prev_price    = None
    st.session_state.alert_active  = False
    st.session_state.alert_msg     = ""
    st.session_state.alerted       = set()
    st.session_state.pop("price_floor", None)   # 刪除舊 key → slider 以新幣種預設值渲染
    st.session_state["_price_sym"] = st.session_state.selected_symbol

# ═════════════════════════════════════════════════════════════════════════════
# HEADER
# ═════════════════════════════════════════════════════════════════════════════
st.markdown(
    f"### 🦊 Project F.O.X. &nbsp;&nbsp;"
    f"<span style='font-size:0.9rem;color:#5B7494;font-weight:400'>"
    f"AI 量化交易後台 · {st.session_state.selected_symbol} 永續合約 · Binance Futures</span>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='font-size:0.78rem;color:#3D7A94;margin-top:-0.4rem;margin-bottom:0.4rem'>"
    f"📅 當前終端機時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>",
    unsafe_allow_html=True,
)

# ═════════════════════════════════════════════════════════════════════════════
# FRAGMENTS — 各區塊獨立更新，互不干擾，零全頁重繪
# ═════════════════════════════════════════════════════════════════════════════

# ── Fragment 1：沙盒引擎 (15s) ─────────────────────────────────────────────
@st.fragment(run_every=15)
def frag_sandbox() -> None:
    # ── 引擎心跳：任何網路或解析異常都不中斷 UI 刷新 ──────────────────────
    try:
        _sdf, _ = fetch_scanner_data()
        _run_sniper(_sdf)
        _update_mark_prices(_sdf)
        _run_trailing_stop()       # 移動停利結算引擎
    except Exception as _ex:
        print(f"[FOX][WARN] frag_sandbox 引擎心跳異常（已攔截，UI 繼續）→ {_ex}")

    st.markdown(
        "#### 🎮 F.O.X. 虛擬量化沙盒 &nbsp;"
        "<span style='font-size:0.78rem;color:#B8860B;font-weight:500;"
        "background:rgba(255,180,0,0.12);border:1px solid rgba(255,180,0,0.3);"
        "border-radius:4px;padding:0.15rem 0.5rem'>PAPER TRADING · ATR 動態停利</span>",
        unsafe_allow_html=True,
    )
    # 只計算 Open 持倉的浮動盈虧
    _open_pos = [p for p in st.session_state.virtual_positions
                 if p.get("status", "Open") == "Open"]
    _vu = 0.0
    for _vp in _open_pos:
        _e = float(_vp.get("entry_price", 0)); _q = float(_vp.get("qty", 0))
        _m = float(_vp.get("mark_price", _e))
        _vu += (_m - _e) * _q if _vp.get("side") == "Long" else (_e - _m) * _q
    _vt = st.session_state.virtual_balance + _vu
    _closed_cnt = len(st.session_state.virtual_positions) - len(_open_pos)
    _c1, _c2, _c3, _c4 = st.columns(4)
    _c1.metric("💰 虛擬總資產 (USDT)", f"${_vt:,.2f}",
               f"{_vu:+,.2f}" if _open_pos else None)
    _c2.metric("📊 浮動盈虧 (USDT)", f"${_vu:+,.2f}",
               f"{(_vu / st.session_state.virtual_balance * 100):+.2f}%"
               if st.session_state.virtual_balance else None)
    _c3.metric("📂 當前持倉 (Open)", len(_open_pos))
    _c4.metric("🏁 已結算 (Closed)", _closed_cnt)


# ── Fragment 2：即時報價 + 警報 + 真實資產卡 (5s) ──────────────────────────
@st.fragment(run_every=5)
def frag_ticker() -> None:
    _sym = st.session_state.selected_symbol
    _sym_base = _sym.split("/")[0]
    _price = None; _fetch_err = None
    try:
        _t     = get_exchange().fetch_ticker(_sym)
        _price = float(_t["last"]); _now = datetime.now()
        st.session_state.price_history.append(
            {"time": _now.strftime("%H:%M:%S"), "ts": _now.timestamp(), "price": _price})
        if len(st.session_state.price_history) > MAX_CHART_POINTS:
            st.session_state.price_history = st.session_state.price_history[-MAX_CHART_POINTS:]
    except Exception as exc:
        _fetch_err = str(exc)

    _pf  = st.session_state.get("price_floor", 70_500)
    _dpt = st.session_state.get("drop_pct", 0.5)
    if _price is not None:
        _h = st.session_state.price_history; _nt = _h[-1]["ts"]
        if _price < _pf:
            if "floor" not in st.session_state.alerted:
                st.session_state.alert_active = True
                st.session_state.alert_msg    = f"價格跌破 {_pf:,.0f} USDT！現價：{_price:,.2f}"
                st.session_state.alerted.add("floor"); beep()
        else:
            st.session_state.alerted.discard("floor")
        _win = [e for e in _h if e["ts"] >= _nt - HISTORY_WINDOW_SEC]
        if len(_win) > 1:
            _op = _win[0]["price"]
            if _op > 0:
                _dp = (_op - _price) / _op * 100
                if _dp >= _dpt:
                    _k = f"drop_{int(_nt // HISTORY_WINDOW_SEC)}"
                    if _k not in st.session_state.alerted:
                        st.session_state.alert_active = True
                        st.session_state.alert_msg    = f"1 分鐘急跌 {_dp:.2f}%！{_op:,.2f} → {_price:,.2f}"
                        st.session_state.alerted.add(_k); beep()

    if st.session_state.alert_active:
        st.markdown(f'<div class="alert-banner">🚨 F.O.X. 警報：市場異動！ — '
                    f'{st.session_state.alert_msg}</div>', unsafe_allow_html=True)

    _prev  = st.session_state.get("prev_price")
    _delta = (_price - _prev) if (_price and _prev) else 0.0
    _pdsp  = _price or 0.0
    if _price: st.session_state.prev_price = _price

    _aerr = _equity = _free = _plen = None
    try:
        _b = fetch_account_balance()
        if _b is None: _aerr = "未設定 API 金鑰（.env 缺少 API_KEY / API_SECRET）"
        else: _equity = _b["total"]; _free = _b["free"]
    except ccxt.AuthenticationError as e: _aerr = f"API 驗證失敗：{e}"
    except ccxt.NetworkError       as e: _aerr = f"網路錯誤：{e}"
    except Exception               as e: _aerr = f"帳戶餘額查詢失敗：{e}"

    _pdf = None
    try:
        _pdf = fetch_real_positions()
        if _pdf is not None: _plen = 0 if _pdf.empty else len(_pdf)
    except Exception: pass

    if _aerr: st.error(f"❌ {_aerr}")

    _m1, _m2, _m3, _m4 = st.columns(4)
    with _m1:
        _es = f"${_equity:>12,.2f}" if _equity is not None else "— 無法取得 —"
        st.markdown(f'<div class="metric-card"><div class="metric-label">Total Equity</div>'
                    f'<div class="metric-value {"val-cyan" if _equity else "val-red"}">{_es}</div>'
                    f'<div class="metric-sub">帳戶總資產 (USDT)</div></div>', unsafe_allow_html=True)
    with _m2:
        _fs = f"${_free:>12,.2f}" if _free is not None else "— 無法取得 —"
        st.markdown(f'<div class="metric-card"><div class="metric-label">Available Balance</div>'
                    f'<div class="metric-value {"val-white" if _free else "val-red"}">{_fs}</div>'
                    f'<div class="metric-sub">可用保證金 (USDT)</div></div>', unsafe_allow_html=True)
    with _m3:
        if _pdf is not None and not _pdf.empty and "UPNL" in _pdf.columns:
            _tu = _pdf["UPNL"].sum()
            _pc = "val-green" if _tu >= 0 else "val-red"
            _ps = f"{'+' if _tu >= 0 else ''}{_tu:,.2f}"
        else: _pc, _ps = "val-white", "—"
        _ba = "▲" if _delta >= 0 else "▼"; _bc = "val-green" if _delta >= 0 else "val-red"
        st.markdown(f'<div class="metric-card"><div class="metric-label">Unrealized P&amp;L</div>'
                    f'<div class="metric-value {_pc}">{_ps}</div>'
                    f'<div class="metric-sub">{_sym_base} <span class="{_bc}">{_ba} {abs(_delta):,.2f}</span>'
                    f'</div></div>', unsafe_allow_html=True)
    with _m4:
        _cnt = _plen if _plen is not None else "—"
        _cc  = "val-cyan" if _plen is not None else "val-white"
        st.markdown(f'<div class="metric-card"><div class="metric-label">Positions</div>'
                    f'<div class="metric-value {_cc}">{_cnt}</div>'
                    f'<div class="metric-sub">{_sym_base} 現價：<span class="val-white">${_pdsp:,.2f}'
                    f'</span></div></div>', unsafe_allow_html=True)
    if _fetch_err: st.warning(f"⚠️ 無法取得 {_sym_base} 現價：{_fetch_err}")


# ── Fragment 3：互動式 K 線圖 (60s 自動刷新，幣種/時間級別切換即時更新) ─────
_TF_OPTIONS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

@st.fragment(run_every=60)
def frag_chart() -> None:
    _chart_sym = st.session_state.selected_symbol

    # ── 時間級別選擇器 ────────────────────────────────────────────────────────
    _tf = st.radio(
        "時間級別",
        _TF_OPTIONS,
        index=_TF_OPTIONS.index(st.session_state.get("selected_timeframe", "15m")),
        horizontal=True,
        key="selected_timeframe",
        label_visibility="collapsed",
    )

    st.markdown(
        f'<div class="section-header">📈 {_chart_sym} K 線圖 &nbsp;·&nbsp; {_tf}</div>',
        unsafe_allow_html=True,
    )

    # ── 抓取 OHLCV（快取，依幣種+時間級別區分）──────────────────────────────
    _df, _err = fetch_ohlcv_data(_chart_sym, _tf, limit=100)

    if _err:
        st.warning(f"⚠️ {_err}")
        return
    if _df.empty:
        st.info("📡 K 線資料載入中…")
        return

    # ── 繪製互動 K 線圖 ───────────────────────────────────────────────────────
    _fig = go.Figure(go.Candlestick(
        x=_df["timestamp"],
        open=_df["open"],
        high=_df["high"],
        low=_df["low"],
        close=_df["close"],
        increasing_line_color="#00FF88",
        increasing_fillcolor="rgba(0,255,136,0.75)",
        decreasing_line_color="#FF4B4B",
        decreasing_fillcolor="rgba(255,75,75,0.75)",
        name=_chart_sym,
        hovertext=[
            f"O: {o:,.4f}  H: {h:,.4f}  L: {l:,.4f}  C: {c:,.4f}"
            for o, h, l, c in zip(_df["open"], _df["high"], _df["low"], _df["close"])
        ],
        hoverinfo="x+text",
    ))
    _fig.update_layout(
        height=360,
        template="plotly_dark",
        paper_bgcolor="#080C12",
        plot_bgcolor="#080C12",
        font=dict(color="#5B7494", family="monospace"),
        margin=dict(l=4, r=4, t=8, b=4),
        showlegend=False,
        xaxis_rangeslider_visible=False,
        xaxis=dict(
            showgrid=False, zeroline=False,
            tickfont=dict(size=10, color="#3D5070"),
        ),
        yaxis=dict(
            showgrid=True, gridcolor="rgba(30,45,69,0.5)",
            zeroline=False,
            tickformat=",.4f", tickprefix="$",
            tickfont=dict(size=10, color="#3D5070"),
            side="right",
        ),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#0D1321", bordercolor="#1E2D45",
            font=dict(color="#E0E6F0", size=12, family="monospace"),
        ),
    )
    st.plotly_chart(_fig, width="stretch", config={
        "displayModeBar": True,
        "modeBarButtonsToRemove": ["autoScale2d", "lasso2d", "select2d", "toImage"],
        "scrollZoom": True,
    })
    st.caption(
        f"資料來源：Binance Futures · {_chart_sym} · {_tf} · "
        f"最後 K 線：{_df['timestamp'].iloc[-1].strftime('%H:%M')} · "
        f"拖曳平移 / 滾輪縮放"
    )


# ── Fragment 4：全網廣域雷達 (20s) ─────────────────────────────────────────
@st.fragment(run_every=20)
def frag_scanner() -> None:
    with st.expander("🌐 全網廣域雷達　(Top 30 高波動活躍榜 · 兩段式掃描 · RSI + CCI 14)", expanded=True):
        _sdf, _serr = fetch_scanner_data()
        if _serr:
            st.error(f"❌ 廣域雷達錯誤：{_serr}")
        elif _sdf.empty:
            st.info("📡 兩段式掃描中，請稍候…（首次載入約需 10–20 秒）")
        else:
            # 只取 UI 顯示欄位（去除引擎專用 ohlc_* 欄位）
            _display_cols = ["Symbol", "24h 漲跌幅 (%)", "價格 (USDT)",
                             "RSI 15m", "CCI 14", "Vol Surge (%)"]
            _disp = _sdf[[c for c in _display_cols if c in _sdf.columns]]

            def _pc(v):
                if not isinstance(v, (int, float)) or pd.isna(v): return ""
                return ("color: #00FF88; font-weight: bold" if v > 0 else
                        "color: #FF4B4B; font-weight: bold" if v < 0 else "")
            def _rc(v):
                if not isinstance(v, (int, float)) or pd.isna(v): return ""
                return ("color: #FF6B35; font-weight: bold" if v > 70 else
                        "color: #00C2FF; font-weight: bold" if v < 30 else "")
            def _cc(v):
                # CCI > 250 → 協議 Delta 警戒（紫色）；CCI < -100 → 超賣（藍色）
                if not isinstance(v, (int, float)) or pd.isna(v): return ""
                return ("color: #9B59B6; font-weight: bold" if v > 250 else
                        "color: #00C2FF; font-weight: bold" if v < -100 else "")
            def _vc(v):
                if not isinstance(v, (int, float)) or pd.isna(v): return ""
                return "color: #FF6B35; font-weight: bold" if v > 200 else ""

            _fmt = {"24h 漲跌幅 (%)": "{:+.2f}%",
                    "RSI 15m":        "{:.1f}",
                    "CCI 14":         "{:.0f}",
                    "Vol Surge (%)":  "{:.1f}%"}
            _styled = (_disp.style
                       .map(_pc, subset=["24h 漲跌幅 (%)"])
                       .map(_rc, subset=["RSI 15m"])
                       .map(_cc, subset=["CCI 14"])
                       .map(_vc, subset=["Vol Surge (%)"])
                       .format({k: v for k, v in _fmt.items() if k in _disp.columns}))
            st.dataframe(_styled, width="stretch", hide_index=True)
            st.caption(f"掃描時間：{datetime.now().strftime('%H:%M:%S')} · 快取 20 秒 · "
                       "RSI < 30 🔵 超賣　"
                       "CCI > 250 🟣 協議 Delta 警戒　CCI < -100 🔵 超賣　"
                       "Vol Surge > 200% 🟠 爆量")


# ── Fragment 5：F.O.X. 風控大腦 + AI 決策日誌 (10s) ───────────────────────
@st.fragment(run_every=10)
def frag_brain() -> None:
    st.markdown('<div class="section-header">🧠 F.O.X. 風控大腦</div>', unsafe_allow_html=True)
    try: _pdf = fetch_real_positions()
    except Exception: _pdf = None
    if _pdf is None or _pdf.empty:
        _bc, _bb, _bg = "#00FF88", "rgba(0,255,136,0.30)", "rgba(0,255,136,0.06)"
        _bm, _bd = "🟢 系統狀態：空倉觀望中，尋找狙擊點位。", "無開放倉位 · 資金安全待機"
    else:
        _tu = _pdf["UPNL"].sum(); _pcnt = len(_pdf)
        _bd = f"{_pcnt} 筆倉位 · 總未實現盈虧：{'+' if _tu >= 0 else ''}{_tu:,.4f} USDT"
        if _tu > 0:
            _bc, _bb, _bg = "#00C2FF", "rgba(0,194,255,0.30)", "rgba(0,194,255,0.06)"
            _bm = "🔵 系統狀態：持倉獲利中，請注意防守停利點。"
        else:
            _bc, _bb, _bg = "#FF4B4B", "rgba(255,75,75,0.40)", "rgba(255,75,75,0.08)"
            _bm = "🔴 警告：持倉浮虧中！請確認是否觸發停損機制！"
    st.markdown(
        f'<div style="background:{_bg};border:1px solid {_bb};border-radius:10px;padding:1.4rem 1.6rem">'
        f'<div style="font-size:1.05rem;font-weight:800;color:{_bc};line-height:1.6">{_bm}</div>'
        f'<div style="font-size:0.74rem;color:#5B7494;margin-top:0.6rem">{_bd}</div>'
        f'<div style="font-size:0.66rem;color:#3D5070;margin-top:0.3rem">'
        f'資料時間：{datetime.now().strftime("%H:%M:%S")}</div></div>', unsafe_allow_html=True)

    st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
    st.markdown('<div class="section-header">🤖 虛擬 Agent 決策日誌</div>', unsafe_allow_html=True)
    _log = st.session_state.get("agent_log", [])
    if not _log:
        st.markdown('<div style="background:rgba(255,180,0,0.05);border:1px solid rgba(255,180,0,0.2);'
                    'border-radius:8px;padding:0.9rem 1.1rem;font-size:0.78rem;color:#7A6A3A">'
                    '🟡 狙擊引擎待機中，等待符合條件的 RSI / 爆量信號…</div>', unsafe_allow_html=True)
    else:
        for _e in _log[:12]:
            _dc = ("#FF8C00" if "手動平倉" in _e else
                   "#9B59B6" if "協議 Delta" in _e else
                   "#00C2FF" if "做多" in _e else
                   "#FF4B4B" if "做空" in _e else "#5B7494")
            st.markdown(f'<div style="border-left:3px solid {_dc};padding:0.35rem 0.7rem;'
                        f'margin-bottom:0.3rem;font-size:0.72rem;color:#8BA5C5;line-height:1.55">'
                        f'{_e}</div>', unsafe_allow_html=True)


# ── Fragment 6：真實持倉表 (10s) ───────────────────────────────────────────
@st.fragment(run_every=10)
def frag_real_positions() -> None:
    st.markdown('<div class="section-header">📋 當前持倉 (Current Positions)</div>', unsafe_allow_html=True)
    _pdf = _perr = None
    try: _pdf = fetch_real_positions()
    except Exception as e: _perr = str(e)
    if _perr:                      st.error(f"❌ {_perr}")
    elif _pdf is None:             st.warning("⚠️ 未設定 API 金鑰，無法顯示持倉。")
    elif _pdf.empty:               st.info("目前沒有任何開放倉位。")
    else:
        _ddf = _pdf.copy()
        _ddf["Entry"] = _ddf["Entry"].map(lambda x: f"{x:,.4f}")
        _ddf["Mark"]  = _ddf["Mark"].map(lambda x: f"{x:,.4f}")
        _ddf["UPNL"]  = _ddf["UPNL"].map(lambda v: f"{'+' if v >= 0 else ''}{v:,.4f}")
        st.dataframe(_ddf, width="stretch", hide_index=True, column_config={
            "Symbol": st.column_config.TextColumn("Symbol",            width="medium"),
            "Side":   st.column_config.TextColumn("方向",              width="small"),
            "Entry":  st.column_config.TextColumn("開倉均價",          width="medium"),
            "Mark":   st.column_config.TextColumn("標記價格",          width="medium"),
            "Qty":    st.column_config.NumberColumn("倉位數量",        width="small", format="%.4f"),
            "UPNL":   st.column_config.TextColumn("未實現盈虧 (USDT)", width="medium"),
        })


# ── Fragment 7：虛擬持倉表（Open + Closed 分開顯示） (5s) ─────────────────
@st.fragment(run_every=5)
def frag_virtual_positions() -> None:
    st.markdown(
        "#### 🎮 虛擬持倉紀錄 &nbsp;"
        "<span style='font-size:0.78rem;color:#B8860B;font-weight:500;"
        "background:rgba(255,180,0,0.12);border:1px solid rgba(255,180,0,0.3);"
        "border-radius:4px;padding:0.15rem 0.5rem'>Virtual Positions Log</span>",
        unsafe_allow_html=True,
    )

    _all = st.session_state.virtual_positions
    _open   = [p for p in _all if p.get("status", "Open") == "Open"]
    _closed = [p for p in _all if p.get("status", "Open") == "Closed"]

    # ── 當前持倉 (Open) ────────────────────────────────────────────────────
    st.markdown('<div class="section-header">📂 當前持倉 (Open)</div>', unsafe_allow_html=True)
    _OPEN_COLS = ["Symbol", "方向", "共振評分", "開倉均價", "數量", "標記價格",
                  "最高水位 (HWM)", "觸發線", "資金費率 (%)", "浮動盈虧 (USDT)"]
    if not _open:
        st.dataframe(pd.DataFrame(columns=_OPEN_COLS), width="stretch", hide_index=True)
    else:
        _rows = []
        for _vp in _open:
            _e      = float(_vp.get("entry_price", 0))
            _q      = float(_vp.get("qty", 0))
            _m      = float(_vp.get("mark_price", _e))
            _s      = _vp.get("side", "Long")
            _h      = float(_vp.get("hwm", _e))
            _atr    = float(_vp.get("atr", 0.0) or 0.0)
            _perr   = bool(_vp.get("price_error", False))
            _score  = _vp.get("score", "—")
            _fr     = float(_vp.get("funding_rate", 0.0) or 0.0)
            _p      = (_m - _e) * _q if _s == "Long" else (_e - _m) * _q
            # 觸發線：ATR 動態距離 (2×ATR)；舊倉位 ATR=0 時降級回固定 5%
            _tdist = (2.0 * _atr) if _atr > 0 else (_h * TRAILING_STOP_PCT)
            _trig  = _h - _tdist if _s == "Long" else _h + _tdist
            # 欄位尾綴：顯示是 ATR 動態還是固定 5%（方便識別舊倉位）
            _trig_label = f"{_trig:,.4f} ({'ATR×2' if _atr > 0 else '固定5%'})"
            # 共振評分標籤
            if isinstance(_score, int):
                _score_label = (f"{_score} 🔥" if _score >= 80
                                else f"{_score} ✅" if _score >= 60
                                else f"{_score} 🔬")
            else:
                _score_label = "—"
            # ── 報價異常時在標記價格與盈虧欄顯示 ⚠️ ──────────────────────
            _mark_label = f"⚠️ {_m:,.4f}" if _perr else f"{_m:,.4f}"
            _pnl_label  = f"⚠️ API Error" if _perr else f"{_p:+,.4f}"
            _rows.append({
                "Symbol":          _vp.get("symbol", ""),
                "方向":            _s,
                "共振評分":        _score_label,
                "開倉均價":        f"{_e:,.4f}",
                "數量":            _q,
                "標記價格":        _mark_label,
                "最高水位 (HWM)":  f"{_h:,.4f}",
                "觸發線":          _trig_label,
                "資金費率 (%)":    f"{_fr*100:+.4f}%",
                "浮動盈虧 (USDT)": _pnl_label,
            })
        st.dataframe(pd.DataFrame(_rows), width="stretch", hide_index=True, column_config={
            "Symbol":          st.column_config.TextColumn("Symbol",          width="small"),
            "方向":            st.column_config.TextColumn("方向",            width="small"),
            "共振評分":        st.column_config.TextColumn("共振評分",        width="small"),
            "開倉均價":        st.column_config.TextColumn("開倉均價",        width="medium"),
            "數量":            st.column_config.NumberColumn("數量",          width="small", format="%.4f"),
            "標記價格":        st.column_config.TextColumn("標記價格",        width="medium"),
            "最高水位 (HWM)":  st.column_config.TextColumn("最高水位 (HWM)", width="medium"),
            "觸發線":          st.column_config.TextColumn("觸發線",          width="medium"),
            "資金費率 (%)":    st.column_config.TextColumn("資金費率 (%)",    width="small"),
            "浮動盈虧 (USDT)": st.column_config.TextColumn("浮動盈虧 (USDT)", width="medium"),
        })

    # ── 歷史績效 (Closed) — 合併 AI 自動平倉 + 手動覆蓋平倉 ───────────────
    st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
    st.markdown('<div class="section-header">🏁 歷史績效 (Closed)</div>', unsafe_allow_html=True)
    # ── 📥 CSV 匯出按鈕 ───────────────────────────────────────────────────
    _export_log = st.session_state.get("virtual_history_log", [])
    _export_ai  = [p for p in st.session_state.virtual_positions
                   if p.get("status", "Open") == "Closed"]
    _export_all = _export_ai + _export_log
    if _export_all:
        _export_df  = pd.DataFrame([{
            "Symbol":            p.get("symbol", ""),
            "方向":              p.get("side", ""),
            "開倉均價":          p.get("entry_price", ""),
            "平倉價格":          p.get("closed_price", ""),
            "數量":              p.get("qty", ""),
            "已實現盈虧 (USDT)": p.get("realized_pnl", ""),
            "開倉時間":          p.get("opened_at", ""),
            "平倉時間":          p.get("closed_at", ""),
        } for p in reversed(_export_all)])
        _export_filename = f"fox_trading_log_{datetime.now().strftime('%Y%m%d')}.csv"
        st.download_button(
            label="📥 匯出交易日誌 (CSV)",
            data=_export_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name=_export_filename,
            mime="text/csv",
            width="content",
        )
    _CLOSED_COLS = ["Symbol", "方向", "開倉均價", "平倉價格", "數量", "已實現盈虧 (USDT)", "開倉時間", "平倉時間"]
    # 合併：AI 自動平倉（virtual_positions Closed）+ 手動覆蓋（virtual_history_log）
    _manual_closed = st.session_state.get("virtual_history_log", [])
    _all_closed    = _closed + _manual_closed
    if not _all_closed:
        st.dataframe(pd.DataFrame(columns=_CLOSED_COLS), width="stretch", hide_index=True)
    else:
        _crows = []
        for _vp in reversed(_all_closed):
            _e    = float(_vp.get("entry_price", 0))
            _cp   = float(_vp.get("closed_price", _e))
            _q    = float(_vp.get("qty", 0))
            _rpnl = float(_vp.get("realized_pnl", 0))
            _crows.append({
                "Symbol":            _vp.get("symbol", ""),
                "方向":              _vp.get("side", ""),
                "開倉均價":          f"{_e:,.4f}",
                "平倉價格":          f"{_cp:,.4f}",
                "數量":              _q,
                "已實現盈虧 (USDT)": f"{_rpnl:+,.4f}",
                "開倉時間":          _vp.get("opened_at", "—"),
                "平倉時間":          _vp.get("closed_at", "—"),  # 手動平倉含 🛑 標記
            })
        st.dataframe(pd.DataFrame(_crows), width="stretch", hide_index=True, column_config={
            "Symbol":            st.column_config.TextColumn("Symbol",              width="small"),
            "方向":              st.column_config.TextColumn("方向",                width="small"),
            "開倉均價":          st.column_config.TextColumn("開倉均價",            width="medium"),
            "平倉價格":          st.column_config.TextColumn("平倉價格",            width="medium"),
            "數量":              st.column_config.NumberColumn("數量",              width="small", format="%.4f"),
            "已實現盈虧 (USDT)": st.column_config.TextColumn("已實現盈虧 (USDT)",  width="medium"),
            "開倉時間":          st.column_config.TextColumn("開倉時間",            width="small"),
            "平倉時間":          st.column_config.TextColumn("平倉時間",            width="small"),
        })

# ── Fragment 8：CFO 戰情分析室（資金統計 + 淨值曲線）(15s) ─────────────────
@st.fragment(run_every=15)
def frag_cfo_room() -> None:
    st.markdown(
        "#### 📊 CFO 戰情分析室 &nbsp;"
        "<span style='font-size:0.78rem;color:#00C2FF;font-weight:500;"
        "background:rgba(0,194,255,0.10);border:1px solid rgba(0,194,255,0.3);"
        "border-radius:4px;padding:0.15rem 0.5rem'>Performance Analytics</span>",
        unsafe_allow_html=True,
    )

    # ── 合併所有已結算紀錄（AI 自動平倉 + 手動覆蓋平倉）──────────────────
    _ai_closed     = [p for p in st.session_state.virtual_positions
                      if p.get("status", "Open") == "Closed"]
    _manual_closed = st.session_state.get("virtual_history_log", [])
    _all_closed    = _ai_closed + _manual_closed

    if not _all_closed:
        st.markdown(
            '<div style="background:rgba(0,194,255,0.05);border:1px solid rgba(0,194,255,0.2);'
            'border-radius:8px;padding:1.1rem 1.3rem;font-size:0.82rem;color:#3D7A94">'
            '📊 戰情室等待數據累積中...</div>',
            unsafe_allow_html=True,
        )
        return

    # ── 統計四大指標 ──────────────────────────────────────────────────────
    _total        = len(_all_closed)
    _pnls         = [float(p.get("realized_pnl", 0.0)) for p in _all_closed]
    _wins         = sum(1 for x in _pnls if x > 0)
    _losses       = sum(1 for x in _pnls if x < 0)
    _win_rate     = (_wins / _total * 100) if _total > 0 else 0.0
    _net_pnl      = sum(_pnls)
    _gross_profit = sum(x for x in _pnls if x > 0)
    _gross_loss   = sum(abs(x) for x in _pnls if x < 0)

    if _gross_loss > 0:
        _pf_val = _gross_profit / _gross_loss
        _pf_str = f"{_pf_val:.2f}"
        _pf_delta = f"獲利 ${_gross_profit:,.2f} / 虧損 ${_gross_loss:,.2f}"
    else:
        _pf_str   = "∞" if _gross_profit > 0 else "N/A"
        _pf_delta = "無虧損紀錄" if _gross_profit > 0 else None

    _mc1, _mc2, _mc3, _mc4 = st.columns(4)
    _mc1.metric(
        "📋 總交易筆數",
        _total,
        f"{_wins} 勝 / {_losses} 敗",
    )
    _mc2.metric(
        "🎯 勝率 (Win Rate)",
        f"{_win_rate:.1f}%",
        f"{_wins} 勝 / {_total} 筆",
    )
    _mc3.metric(
        "💰 總淨利 (Net PnL)",
        f"${_net_pnl:+,.2f}",
        f"{(_net_pnl / 100_000.0 * 100):+.2f}% 報酬率",
    )
    _mc4.metric(
        "⚖️ 盈虧比 (Profit Factor)",
        _pf_str,
        _pf_delta,
    )

    # ── 資金淨值曲線 (Equity Curve) ──────────────────────────────────────
    st.markdown("<div style='margin-top:0.9rem'></div>", unsafe_allow_html=True)
    st.markdown('<div class="section-header">📈 資金淨值曲線 (Equity Curve)</div>',
                unsafe_allow_html=True)

    # 依 closed_at 時間字串排序（手動平倉格式「🛑 [手動平倉] HH:MM:SS」→ 取末段時間）
    def _sort_key(p: dict) -> str:
        s = str(p.get("closed_at", ""))
        return s.split()[-1] if s else "00:00:00"

    _sorted_closed = sorted(_all_closed, key=_sort_key)

    _INITIAL = 100_000.0
    _equity  = _INITIAL
    _curve_rows = [{"交易筆次": 0, "資金淨值 (USDT)": _equity}]
    for _i, _p in enumerate(_sorted_closed, start=1):
        _equity += float(_p.get("realized_pnl", 0.0))
        _curve_rows.append({"交易筆次": _i, "資金淨值 (USDT)": round(_equity, 4)})

    _curve_df = pd.DataFrame(_curve_rows).set_index("交易筆次")
    st.line_chart(_curve_df, width="stretch", height=220)
    st.caption(
        f"基準：初始資金 $100,000 USDT · 共 {_total} 筆已結算 · "
        f"最新淨值 ${_equity:,.2f} · 更新：{datetime.now().strftime('%H:%M:%S')}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# RENDER — 三分頁版面架構
# 各 Fragment 的 run_every 計時器在伺服器端獨立運行，
# 無論使用者停在哪個 Tab，session_state 寫入與資料更新均持續進行。
# ═════════════════════════════════════════════════════════════════════════════
_tab1, _tab2, _tab3, _tab4 = st.tabs(["🌐 戰術指揮大廳", "📡 天眼雷達", "💼 持倉與結算", "🤖 AI 副駕"])

# ── Tab 1：戰術指揮大廳 ────────────────────────────────────────────────────
with _tab1:
    frag_sandbox()          # 虛擬沙盒資產卡 + 引擎心跳 (15s)
    st.divider()
    frag_ticker()           # 真實資產卡 + 警報 (5s)
    st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
    _col_left, _col_right = st.columns([6, 4])
    with _col_left:
        frag_chart()        # BTC 即時走勢圖 (5s)
    with _col_right:
        frag_brain()        # 風控大腦 + AI 決策日誌 (10s)

# ── Tab 2：天眼雷達 ────────────────────────────────────────────────────────
with _tab2:
    frag_scanner()          # 全網廣域雷達 Top 30 (20s)

# ── Tab 3：持倉與結算 ──────────────────────────────────────────────────────
with _tab3:
    frag_cfo_room()          # CFO 戰情分析室：統計指標 + 淨值曲線 (15s)
    st.divider()
    frag_real_positions()    # 真實持倉表 (10s)
    st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
    frag_virtual_positions() # 虛擬持倉紀錄 Open + Closed (5s)

    # ── 緊急手動覆蓋平倉（直接渲染，不入 fragment，避免 run_every 重置 selectbox）
    st.markdown("<div style='margin-top:1rem'></div>", unsafe_allow_html=True)
    with st.expander("🔴 緊急手動平倉 (Manual Override)", expanded=False):
        _mo_open = [p for p in st.session_state.virtual_positions
                    if p.get("status", "Open") == "Open"]
        if not _mo_open:
            st.info("✅ 目前沒有任何 Open 倉位，無需手動介入。")
        else:
            st.markdown(
                "<div style='font-size:0.78rem;color:#FF8C00;margin-bottom:0.6rem'>"
                "⚠️ 此操作將以最後已知標記價格強制平倉，無法撤銷。</div>",
                unsafe_allow_html=True,
            )
            _mo_symbols = [p["symbol"] for p in _mo_open]
            _mo_col1, _mo_col2 = st.columns([3, 1])
            with _mo_col1:
                _mo_sel = st.selectbox(
                    "選擇強制結算幣種", _mo_symbols, key="manual_override_sym"
                )
            with _mo_col2:
                st.markdown("<div style='margin-top:1.75rem'></div>", unsafe_allow_html=True)
                _mo_fire = st.button(
                    "⚡ 執行強制結算", type="primary", width="stretch"
                )

            if _mo_fire and _mo_sel:
                _mo_target = next(
                    (p for p in _mo_open if p["symbol"] == _mo_sel), None
                )
                if _mo_target:
                    _mo_ts  = datetime.now().strftime("%H:%M:%S")
                    _mo_e   = float(_mo_target.get("entry_price", 0))
                    _mo_m   = float(_mo_target.get("mark_price", _mo_e))  # 最後已知標記價
                    _mo_q   = float(_mo_target.get("qty", 0))
                    _mo_s   = _mo_target.get("side", "Long")
                    _mo_mg  = float(_mo_target.get("margin", SNIPER_MARGIN_DEFAULT))

                    # ── 結算損益 ──────────────────────────────────────────────
                    _mo_pnl = ((_mo_m - _mo_e) * _mo_q if _mo_s == "Long"
                               else (_mo_e - _mo_m) * _mo_q)
                    _mo_pnl = round(_mo_pnl, 4)

                    # ── 平倉手續費 ────────────────────────────────────────────
                    _mo_fee = round(_mo_q * _mo_m * CLOSE_FEE_RATE, 4)

                    # ── 歸還帳戶（保證金 + PNL - 手續費）────────────────────
                    _mo_returned = _mo_mg + _mo_pnl - _mo_fee
                    st.session_state.virtual_balance += max(_mo_returned, 0.0)

                    # ── 移入 virtual_history_log（🛑 標記）────────────────────
                    st.session_state.virtual_history_log.insert(0, {
                        **_mo_target,
                        "status":       "Closed",
                        "closed_at":    f"🛑 [手動平倉] {_mo_ts}",
                        "closed_price": _mo_m,
                        "realized_pnl": _mo_pnl,
                        "close_fee":    _mo_fee,
                    })

                    # ── 寫入 SQLite 持久化交易紀錄 ────────────────────────────
                    insert_trade(
                        timestamp   = _mo_ts,
                        symbol      = _mo_sel,
                        side        = _mo_s,
                        entry_price = float(_mo_e),
                        exit_price  = float(_mo_m),
                        pnl         = float(_mo_pnl),
                        score       = int(_mo_target.get("score") or 0),
                        exit_reason = "手動平倉",
                        user_id     = st.session_state.get("user_id", 0),
                    )

                    # ── 從 virtual_positions 刪除 Open 倉位 ───────────────────
                    st.session_state.virtual_positions = [
                        p for p in st.session_state.virtual_positions
                        if not (p["symbol"] == _mo_sel
                                and p.get("status", "Open") == "Open")
                    ]

                    # ── AI 決策日誌 ───────────────────────────────────────────
                    _mo_sign = "+" if _mo_pnl >= 0 else ""
                    st.session_state.agent_log.insert(0,
                        f"[{_mo_ts}]　🛑 [手動平倉] **{_mo_sel}** "
                        f"指揮官手動覆蓋強制結算，PNL：{_mo_sign}{_mo_pnl:,.2f} USDT"
                        f"　平倉手續費 -${_mo_fee:,.2f}"
                    )
                    st.session_state.agent_log = \
                        st.session_state.agent_log[:AGENT_LOG_MAX]

                    # ── 存檔 + 全頁刷新 ───────────────────────────────────────
                    save_state()
                    st.rerun()

# ── Tab 4：AI 量化副駕 ─────────────────────────────────────────────────────
with _tab4:
    st.markdown(
        "#### 🤖 AI 量化副駕 &nbsp;"
        "<span style='font-size:0.78rem;color:#5B7494;font-weight:400'>"
        "代號「狐影」· Text-to-SQL · Powered by Gemini</span>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div style='font-size:0.78rem;color:#5B7494;margin-bottom:0.8rem'>"
        "直接用中文詢問交易紀錄，副駕自動轉成 SQL 查詢並回傳專業戰報分析。"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── 快捷問題按鈕 ──────────────────────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.72rem;color:#5B7494;margin-bottom:0.4rem'>"
        "💡 快捷詢問</div>",
        unsafe_allow_html=True,
    )
    _preset_questions = [
        "幫我統計整體勝率和總盈虧",
        "哪個幣種的總盈虧最高？",
        "列出所有虧損超過 100 USDT 的交易",
        "共振評分高於 80 的交易，勝率和平均盈虧如何？",
        "各平倉原因（停利/爆倉/手動）的次數與總盈虧統計",
    ]
    _preset_cols = st.columns(len(_preset_questions))
    _preset_clicked: str | None = None
    for _ci, (_col_p, _q) in enumerate(zip(_preset_cols, _preset_questions)):
        with _col_p:
            if st.button(_q, key=f"preset_{_ci}", width="stretch"):
                _preset_clicked = _q

    st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)

    # ── 顯示歷史對話 ──────────────────────────────────────────────────────
    _chat_container = st.container()
    with _chat_container:
        for _msg in st.session_state.copilot_history:
            with st.chat_message(_msg["role"],
                                 avatar="🦊" if _msg["role"] == "assistant" else "👤"):
                st.markdown(_msg["content"])

    # ── 輸入框（快捷按鈕或手動輸入）─────────────────────────────────────
    _user_input = st.chat_input("詢問你的交易數據，例如：幫我查最近勝率最高的幣種…")

    # 快捷按鈕優先；若同一幀兩者都有，以快捷為主
    _question = _preset_clicked or _user_input

    if _question:
        # 把使用者問題加入對話紀錄
        st.session_state.copilot_history.append({
            "role":    "user",
            "content": _question,
        })

        # 顯示使用者訊息
        with _chat_container:
            with st.chat_message("user", avatar="👤"):
                st.markdown(_question)

        # 呼叫 AI 副駕（帶 spinner 等待提示）
        with _chat_container:
            with st.chat_message("assistant", avatar="🦊"):
                with st.spinner("狐影正在分析資料庫…"):
                    _answer = ask_copilot(
                        _question,
                        user_id=st.session_state.get("user_id", 0),
                    )
                st.markdown(_answer)

        # 把 AI 回應加入對話紀錄
        st.session_state.copilot_history.append({
            "role":    "assistant",
            "content": _answer,
        })

    # ── 清除對話按鈕 ──────────────────────────────────────────────────────
    if st.session_state.copilot_history:
        if st.button("🗑️ 清除對話紀錄", key="copilot_clear"):
            st.session_state.copilot_history = []
            st.rerun()

# ── FOOTER（靜態，不參與刷新）─────────────────────────────────────────────
st.markdown("---")
st.caption("Project F.O.X. © 2026 · 僅供參考，非投資建議 · 各區塊獨立刷新（5s / 10s / 20s）")
