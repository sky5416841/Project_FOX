"""
Project F.O.X. — AI 量化交易後台 (Streamlit Dashboard)
"""

import time
import ctypes
from datetime import datetime

import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go

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
  .decision-chip {
    display: inline-block;
    background: rgba(0,194,255,0.12);
    border: 1px solid rgba(0,194,255,0.35);
    border-radius: 20px;
    padding: 0.18rem 0.75rem;
    font-size: 0.72rem;
    color: #00C2FF;
    margin: 0.2rem 0.2rem 0.2rem 0;
  }
  .decision-chip.bull {
    background: rgba(0,255,136,0.10);
    border-color: rgba(0,255,136,0.35);
    color: #00FF88;
  }
  .decision-chip.bear {
    background: rgba(255,75,75,0.10);
    border-color: rgba(255,75,75,0.35);
    color: #FF4B4B;
  }
  .cot-text {
    font-size: 0.82rem;
    color: #8BA5C5;
    line-height: 1.7;
    white-space: pre-wrap;
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
</style>
""", unsafe_allow_html=True)

# ── Session state init ────────────────────────────────────────────────────────
defaults = {
    "price_history": [],
    "alerted":       set(),
    "alert_active":  False,
    "alert_msg":     "",
    "prev_price":    None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

HISTORY_WINDOW_SEC = 60
MAX_CHART_POINTS   = 180   # 6 minutes @ 2s intervals

# ── Cached exchange ───────────────────────────────────────────────────────────
@st.cache_resource
def get_exchange() -> ccxt.binance:
    return ccxt.binance({
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    })

# ── Beep ──────────────────────────────────────────────────────────────────────
def beep() -> None:
    try:
        ctypes.windll.kernel32.Beep(1200, 300)
        time.sleep(0.15)
        ctypes.windll.kernel32.Beep(900, 500)
    except Exception:
        pass

# ═════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🦊 F.O.X. 參數設定")
    st.divider()

    price_floor = st.slider(
        "🔻 跌破警報線 (USDT)",
        min_value=50_000,
        max_value=120_000,
        value=70_500,
        step=100,
        format="%d",
    )
    drop_pct = st.slider(
        "⚡ 急跌警報 % (1 分鐘)",
        min_value=0.1,
        max_value=5.0,
        value=0.5,
        step=0.1,
        format="%.1f%%",
    )

    st.divider()
    st.caption("🔄 每 2 秒自動刷新")
    st.caption("📡 資料來源：Binance Futures")

    if st.button("🔕 解除警報", use_container_width=True):
        st.session_state.alert_active = False
        st.session_state.alert_msg    = ""
        st.session_state.alerted.clear()

# ═════════════════════════════════════════════════════════════════════════════
# HEADER
# ═════════════════════════════════════════════════════════════════════════════
st.markdown(
    "### 🦊 Project F.O.X. &nbsp;&nbsp;"
    "<span style='font-size:0.9rem;color:#5B7494;font-weight:400'>"
    "AI 量化交易後台 · BTC/USDT 永續合約 · Binance Futures</span>",
    unsafe_allow_html=True,
)

# ═════════════════════════════════════════════════════════════════════════════
# FETCH PRICE
# ═════════════════════════════════════════════════════════════════════════════
price       = None
fetch_error = None
try:
    ticker = get_exchange().fetch_ticker("BTC/USDT")
    price  = float(ticker["last"])
    now    = datetime.now()

    st.session_state.price_history.append({
        "time":     now.strftime("%H:%M:%S"),
        "ts":       now.timestamp(),
        "BTC/USDT": price,
    })
    if len(st.session_state.price_history) > MAX_CHART_POINTS:
        st.session_state.price_history = st.session_state.price_history[-MAX_CHART_POINTS:]

except Exception as exc:
    fetch_error = str(exc)

# ═════════════════════════════════════════════════════════════════════════════
# BACKGROUND ALERT LOGIC  (保留原有跌破警報邏輯)
# ═════════════════════════════════════════════════════════════════════════════
if price is not None:
    history = st.session_state.price_history
    now_ts  = history[-1]["ts"]

    # 1. Price-floor alert
    if price < price_floor:
        if "floor" not in st.session_state.alerted:
            st.session_state.alert_active = True
            st.session_state.alert_msg    = f"價格跌破 {price_floor:,.0f} USDT！現價：{price:,.2f}"
            st.session_state.alerted.add("floor")
            beep()
    else:
        st.session_state.alerted.discard("floor")

    # 2. 1-minute rapid-drop alert
    window_entries = [e for e in history if e["ts"] >= now_ts - HISTORY_WINDOW_SEC]
    if len(window_entries) > 1:
        oldest_price = window_entries[0]["BTC/USDT"]
        if oldest_price > 0:
            drop = (oldest_price - price) / oldest_price * 100
            if drop >= drop_pct:
                key = f"drop_{int(now_ts // HISTORY_WINDOW_SEC)}"
                if key not in st.session_state.alerted:
                    st.session_state.alert_active = True
                    st.session_state.alert_msg    = (
                        f"1 分鐘急跌 {drop:.2f}%！"
                        f"{oldest_price:,.2f} → {price:,.2f}"
                    )
                    st.session_state.alerted.add(key)
                    beep()

# ═════════════════════════════════════════════════════════════════════════════
# ALERT BANNER
# ═════════════════════════════════════════════════════════════════════════════
if st.session_state.alert_active:
    st.markdown(
        f'<div class="alert-banner">'
        f'🚨 F.O.X. 警報：市場異動！ — {st.session_state.alert_msg}'
        f'</div>',
        unsafe_allow_html=True,
    )

# ═════════════════════════════════════════════════════════════════════════════
# TOP METRICS  ── 四個數據卡
# ═════════════════════════════════════════════════════════════════════════════
# 模擬數據（後續可替換為交易所 API 真實資產）
MOCK_EQUITY    = 128_450.00
MOCK_BALANCE   = 43_210.00
MOCK_PNL       = +2_318.75
MOCK_POSITIONS = 3

prev  = st.session_state.prev_price
delta = (price - prev) if (price is not None and prev is not None) else 0.0
price_display = price if price is not None else 0.0

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.markdown(
        '<div class="metric-card">'
        '<div class="metric-label">Total Equity</div>'
        f'<div class="metric-value val-cyan">${MOCK_EQUITY:>12,.2f}</div>'
        '<div class="metric-sub">帳戶總資產 (USDT)</div>'
        '</div>',
        unsafe_allow_html=True,
    )

with c2:
    st.markdown(
        '<div class="metric-card">'
        '<div class="metric-label">Available Balance</div>'
        f'<div class="metric-value val-white">${MOCK_BALANCE:>12,.2f}</div>'
        '<div class="metric-sub">可用餘額 (USDT)</div>'
        '</div>',
        unsafe_allow_html=True,
    )

with c3:
    pnl_cls  = "val-green" if MOCK_PNL >= 0 else "val-red"
    pnl_sign = "+" if MOCK_PNL >= 0 else ""
    btc_arrow = "▲" if delta >= 0 else "▼"
    btc_cls   = "val-green" if delta >= 0 else "val-red"
    st.markdown(
        '<div class="metric-card">'
        '<div class="metric-label">Today\'s P&amp;L</div>'
        f'<div class="metric-value {pnl_cls}">{pnl_sign}{MOCK_PNL:,.2f}</div>'
        f'<div class="metric-sub">BTC <span class="{btc_cls}">{btc_arrow} {abs(delta):,.2f}</span></div>'
        '</div>',
        unsafe_allow_html=True,
    )

with c4:
    st.markdown(
        '<div class="metric-card">'
        '<div class="metric-label">Positions</div>'
        f'<div class="metric-value val-cyan">{MOCK_POSITIONS}</div>'
        f'<div class="metric-sub">BTC 現價：<span class="val-white">${price_display:,.2f}</span></div>'
        '</div>',
        unsafe_allow_html=True,
    )

if price is not None:
    st.session_state.prev_price = price

st.markdown("<div style='margin-top:1rem'></div>", unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# MIDDLE SECTION  ── 左 60% 圖表 ／ 右 40% AI Brain
# ═════════════════════════════════════════════════════════════════════════════
col_chart, col_ai = st.columns([6, 4])

# ── 左欄：BTC/USDT 即時動態折線圖 ────────────────────────────────────────────
with col_chart:
    st.markdown('<div class="section-header">📈 BTC/USDT 即時走勢</div>', unsafe_allow_html=True)

    if len(st.session_state.price_history) > 1:
        df     = pd.DataFrame(st.session_state.price_history)
        prices = df["BTC/USDT"]

        # ── 動態 Y 軸縮放：緊密跟隨歷史最高/最低價，呈現心電圖效果 ────────────
        p_min = prices.min()
        p_max = prices.max()
        span  = max(p_max - p_min, p_max * 0.0005)  # 最小 0.05% 防止完全持平
        buf   = span * 0.15
        y_lo  = p_min - buf
        y_hi  = p_max + buf

        fig = go.Figure()

        # ── 底層 Glow 效果：較寬、極低透明度的同色線，製造螢光暈染感 ──────────
        fig.add_trace(go.Scatter(
            x=df["time"],
            y=prices,
            mode="lines",
            line=dict(color="rgba(0,255,136,0.18)", width=8),
            hoverinfo="skip",
            showlegend=False,
        ))

        # ── 主線 + 半透明漸層面積填充 ─────────────────────────────────────────
        fig.add_trace(go.Scatter(
            x=df["time"],
            y=prices,
            mode="lines",
            line=dict(color="#00FF88", width=2),
            fill="tozeroy",
            fillcolor="rgba(0,255,136,0.10)",
            hovertemplate="<b>%{x}</b><br><span style='color:#00FF88'>$%{y:,.2f}</span><extra></extra>",
            name="BTC/USDT",
        ))

        _axis_common = dict(
            showgrid=False,       # 關閉網格線
            zeroline=False,       # 關閉零軸線
            showline=False,       # 關閉軸邊框線
            showticklabels=True,
        )

        fig.update_layout(
            height=360,
            margin=dict(l=4, r=4, t=8, b=4),
            paper_bgcolor="#080C12",   # 與頁面背景完全一致
            plot_bgcolor="#080C12",
            font=dict(color="#5B7494", family="monospace"),
            showlegend=False,
            xaxis=dict(
                **_axis_common,
                tickfont=dict(size=10, color="#3D5070"),
            ),
            yaxis=dict(
                **_axis_common,
                range=[y_lo, y_hi],   # 動態縮放，不鎖死
                tickformat=",.0f",
                tickprefix="$",
                tickfont=dict(size=10, color="#3D5070"),
                side="right",         # Y 軸標籤移到右側，更像彭博終端機
            ),
            hovermode="x unified",
            hoverlabel=dict(
                bgcolor="#0D1321",
                bordercolor="#1E2D45",
                font=dict(color="#E0E6F0", size=12, family="monospace"),
            ),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("📡 資料累積中…請稍候幾秒")

# ── 右欄：AI Brain / 近期決策 ────────────────────────────────────────────────
with col_ai:
    st.markdown('<div class="section-header">🧠 近期決策 (Recent Decisions)</div>', unsafe_allow_html=True)

    # 信號籌碼列
    st.markdown(
        '<div class="metric-card" style="margin-bottom:0.8rem">'
        '<div class="metric-label">最新信號</div>'
        '<div style="margin-top:0.4rem">'
        '<span class="decision-chip bull">多頭趨勢</span>'
        '<span class="decision-chip bull">15m 支撐完好</span>'
        '<span class="decision-chip">RSI 62</span>'
        '<span class="decision-chip">MA 趨勢向上</span>'
        '<span class="decision-chip bear">成交量縮減</span>'
        '</div>'
        '<div class="metric-sub" style="margin-top:0.6rem">更新：'
        + datetime.now().strftime("%H:%M:%S") +
        ' · 信心指數 <span class="val-green">82%</span></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # AI Chain of Thought — 直接 Markdown 排版，無折疊框
    st.markdown(
        '<div class="cot-text">'
        '<span style="color:#5B7494;font-size:0.68rem;text-transform:uppercase;'
        'letter-spacing:0.1em">🧠 AI 思維鏈 (Chain of Thought)</span><br><br>'

        '<span style="color:#00C2FF;font-size:0.72rem">▸ 市場結構</span><br>'
        '多頭趨勢極強，日線 EMA20 / EMA50 呈金叉排列。<br><br>'

        '<span style="color:#00C2FF;font-size:0.72rem">▸ 15m K線結構</span><br>'
        '回測後出現強勢吞噬型態，支撐結構完好，無破位跡象。<br><br>'

        '<span style="color:#00C2FF;font-size:0.72rem">▸ 成交量</span><br>'
        '回測量縮、突破放量確認，主力資金仍在多方。<br><br>'

        '<span style="color:#00FF88;font-size:0.72rem">▸ 決策建議</span><br>'
        '⚡ 建議持有多單，止損設於 71,500。<br>'
        '若收破 72,000 則降倉至 50%。<br><br>'

        '<span style="color:#FF4B4B;font-size:0.72rem">▸ 風險因素</span><br>'
        '· 宏觀：Fed 利率決議 2 日後公布<br>'
        '· 技術：RSI 接近超買區間（&gt;70 注意）<br>'
        '· 鏈上：大戶 BTC 轉入交易所增加'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown("<div style='margin-top:0.6rem'></div>", unsafe_allow_html=True)

    # 操作紀錄
    st.markdown('<div class="metric-label" style="margin-top:0.8rem;margin-bottom:0.4rem">最近操作紀錄</div>', unsafe_allow_html=True)
    ops = [
        ("09:32:14", "OPEN  LONG",  "+0.50 BTC @ 73,200", "val-green"),
        ("08:15:07", "CLOSE SHORT", "−0.25 BTC @ 72,800", "val-red"),
        ("07:01:44", "OPEN  LONG",  "+0.25 BTC @ 71,950", "val-green"),
    ]
    for ts, action, detail, cls in ops:
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;'
            f'padding:0.28rem 0;border-bottom:1px solid #1E2D45;font-size:0.75rem">'
            f'<span style="color:#5B7494">{ts}</span>'
            f'<span class="{cls}" style="font-weight:700">{action}</span>'
            f'<span style="color:#8BA5C5">{detail}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

st.markdown("<div style='margin-top:1rem'></div>", unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# BOTTOM TABLE  ── 當前持倉
# ═════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="section-header">📋 當前持倉 (Current Positions)</div>', unsafe_allow_html=True)

# 模擬持倉數據
mock_positions = pd.DataFrame([
    {
        "Symbol":  "BTCUSDT",
        "Side":    "LONG",
        "Entry":   73_200.00,
        "Mark":    price_display,
        "Qty":     0.50,
        "UPNL":    round((price_display - 73_200.00) * 0.50, 2),
    },
    {
        "Symbol":  "ETHUSDT",
        "Side":    "LONG",
        "Entry":   2_450.00,
        "Mark":    2_518.30,
        "Qty":     3.20,
        "UPNL":    round((2_518.30 - 2_450.00) * 3.20, 2),
    },
    {
        "Symbol":  "SOLUSDT",
        "Side":    "SHORT",
        "Entry":   168.50,
        "Mark":    162.40,
        "Qty":     15.0,
        "UPNL":    round((168.50 - 162.40) * 15.0, 2),
    },
])

# 格式化欄位
def fmt_upnl(v):
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.2f}"

mock_positions["Entry"] = mock_positions["Entry"].map(lambda x: f"{x:,.2f}")
mock_positions["Mark"]  = mock_positions["Mark"].map(lambda x: f"{x:,.2f}")
mock_positions["UPNL"]  = mock_positions["UPNL"].map(fmt_upnl)

st.dataframe(
    mock_positions,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Symbol": st.column_config.TextColumn("Symbol",  width="small"),
        "Side":   st.column_config.TextColumn("Side",    width="small"),
        "Entry":  st.column_config.TextColumn("Entry",   width="medium"),
        "Mark":   st.column_config.TextColumn("Mark",    width="medium"),
        "Qty":    st.column_config.NumberColumn("Qty",   width="small", format="%.4f"),
        "UPNL":   st.column_config.TextColumn("UPNL",   width="medium"),
    },
)

# ═════════════════════════════════════════════════════════════════════════════
# FOOTER
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.caption(
    f"最後更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
    f"Project F.O.X. © 2026 · 僅供參考，非投資建議"
)

if fetch_error:
    st.warning(f"⚠️ 無法取得 BTC 現價：{fetch_error}")

# ═════════════════════════════════════════════════════════════════════════════
# AUTO-REFRESH
# ═════════════════════════════════════════════════════════════════════════════
time.sleep(2)
st.rerun()
