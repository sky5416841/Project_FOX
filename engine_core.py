"""
Project F.O.X. — 交易引擎核心（與 Streamlit 解耦，可在背景執行緒常駐運行）

設計：
- 所有引擎邏輯操作「純 state dict + settings dict」，不碰 st.session_state。
- 狀態持久化在 fox_sandbox_state_{uid}.json（餘額/持倉/歷史/日誌）。
- 使用者設定持久化在 fox_settings_{uid}.json（槓桿/保證金/自動交易開關等），由 UI 寫、引擎讀。
- start_background_engine() 啟動單一常駐 daemon 執行緒，每 INTERVAL 秒跑一輪，
  不依賴瀏覽器分頁；網頁退化為純檢視器。
- _STATE_LOCK 在同一行程內協調背景緒與 UI 對狀態檔的讀寫。
"""

import os
import re
import json
import time
import glob
import threading
from datetime import datetime

import ccxt

from database import insert_trade

# ── 路徑與並發鎖 ──────────────────────────────────────────────────────────────
DATA_DIR = os.getenv("FOX_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
_STATE_LOCK = threading.RLock()
INTERVAL = 15           # 背景引擎每幾秒跑一輪
INITIAL_BALANCE = 10_000.0

# ── 引擎常數（與舊 dashboard 一致）──────────────────────────────────────────
SNIPER_MARGIN_DEFAULT  = 1_000.0
SNIPER_RSI_LONG        = 30.0
SNIPER_CCI_SHORT       = 250.0
SNIPER_VOL_MIN         = 150.0
SNIPER_TREND_BLOCK_PCT = 3.0
AGENT_LOG_MAX          = 50
TRAILING_STOP_PCT      = 0.05
TRAIL_ATR_MULT         = 2.0    # 一般移動停利距離：2×ATR（讓贏單跑）
TRAIL_ATR_MULT_TIGHT   = 1.0    # 大賺後收緊距離：1×ATR（鎖利）
PROFIT_RATCHET_ATR     = 3.0    # 有利移動達 3×ATR 視為「大賺」，啟動收緊
OPEN_FEE_RATE          = 0.0005
CLOSE_FEE_RATE         = 0.0005
SLIPPAGE_PCT           = 0.001
LIQUIDATION_THRESHOLD  = 0.95
DELTA_WICK_RATIO       = 0.60
DELTA_TOTAL_MIN_PCT    = 0.02

_RADAR_PHASE1_VOL_MIN = 10_000_000
_RADAR_TOP_N          = 30

_SCANNER_BLACKLIST = frozenset({
    "XAU", "XAG", "GOLD", "SILVER", "CL", "OIL", "BRENT", "BRENTOIL",
    "GAS", "NG", "COPPER", "HG", "USDC", "USDT", "BUSD", "TUSD", "USDP", "DAI",
    "FDUSD", "PYUSD", "USDE", "FRAX", "LUSD", "GUSD", "EUR", "GBP", "AUD", "JPY",
    "BRL", "BIDR", "IDRT", "NGN", "RUB", "TRY", "PAXG",
})

# 主流大幣：每輪強制納入掃描（即使波動度排不進 Top 30，也讓策略有機會做大幣）
_MAJORS = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE")

# 代幣化股票 / ETF 黑名單（非加密資產，避免污染策略；best-effort 清單）
_STOCK_BLACKLIST = frozenset({
    "QCOM", "SOXL", "SOXX", "COHR", "ARM", "NVDA", "TSLA", "AAPL", "AMZN", "MSFT",
    "GOOGL", "GOOG", "META", "AMD", "INTC", "MSTR", "NFLX", "COIN", "MARA", "RIOT",
    "PLTR", "SMCI", "AVGO", "TSM", "NIO", "GME", "AMC", "TQQQ", "SQQQ", "HOOD",
    "ABNB", "CRM", "ORCL", "ADBE", "MU", "BABA", "DIS", "PYPL", "SHOP", "UBER",
    "SAMSUNG", "SONY", "NKE", "MCD", "SBUX", "PEP", "WMT", "BRK", "JPM", "GS",
})
_CRYPTO_NAME_RE = re.compile(r"^[A-Z0-9]{2,12}$")
_CRYPTO_NAME_BLOCKLIST_PATTERNS = ("XAU", "XAG", "USD", "EUR", "GBP", "CL")


# ════════════════════════════════════════════════════════════════════════════
# 指標計算（純 Python）
# ════════════════════════════════════════════════════════════════════════════
def _calc_rsi(closes, period: int = 14) -> float:
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


def _calc_cci(highs, lows, closes, period: int = 14) -> float:
    if len(closes) < period:
        return float("nan")
    tps = [(highs[i] + lows[i] + closes[i]) / 3.0
           for i in range(len(closes) - period, len(closes))]
    tp_mean  = sum(tps) / period
    mean_dev = sum(abs(tp - tp_mean) for tp in tps) / period
    if mean_dev == 0:
        return 0.0
    return round((tps[-1] - tp_mean) / (0.015 * mean_dev), 1)


def _calc_atr(highs, lows, closes, period: int = 14) -> float:
    if len(closes) < period + 1:
        return float("nan")
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i]  - closes[i - 1]))
        trs.append(tr)
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return round(atr, 8)


def _is_nan(x) -> bool:
    return x != x


def _parse_price(price_str) -> float | None:
    try:
        return float(str(price_str).replace(",", "").strip())
    except Exception:
        return None


def trail_distance(entry: float, hwm: float, atr: float, side: str) -> float:
    """利潤棘輪移動停利距離（價格）：有利移動越大，停利收得越緊以鎖住獲利。

    剛開倉用 2×ATR 讓贏單發展；當水位相對進場的有利移動 ≥ 3×ATR（大賺）→ 收緊成 1×ATR。
    atr 不可用時降級為固定百分比。供引擎結算與 UI 顯示共用，確保一致。
    """
    if atr <= 0:
        return entry * TRAILING_STOP_PCT
    fav_move = (hwm - entry) if side == "Long" else (entry - hwm)
    if fav_move >= PROFIT_RATCHET_ATR * atr:
        return TRAIL_ATR_MULT_TIGHT * atr
    return TRAIL_ATR_MULT * atr


def _is_clean_crypto(base: str) -> bool:
    if base in _SCANNER_BLACKLIST or base in _STOCK_BLACKLIST:
        return False
    if not _CRYPTO_NAME_RE.match(base):
        return False
    for pat in _CRYPTO_NAME_BLOCKLIST_PATTERNS:
        if pat in base:
            return False
    return True


def _calc_resonance_score(rsi, cci, wick_pct, vol_surge, side: str) -> int:
    score = 0
    if side == "Long":
        if rsi < 15:     score += 40
        elif rsi < 20:   score += 30
        elif rsi < 25:   score += 20
        else:            score += 10
    else:
        cci_abs = abs(cci)
        if cci_abs > 400:   score += 40
        elif cci_abs > 300: score += 30
        elif cci_abs > 250: score += 20
        else:               score += 10
    if wick_pct >= 85:   score += 35
    elif wick_pct >= 75: score += 25
    elif wick_pct >= 65: score += 15
    elif wick_pct >= 60: score += 5
    if vol_surge >= 400:   score += 25
    elif vol_surge >= 300: score += 20
    elif vol_surge >= 200: score += 15
    elif vol_surge >= 150: score += 10
    return min(score, 100)


# ════════════════════════════════════════════════════════════════════════════
# 交易所 + 掃描器
# ════════════════════════════════════════════════════════════════════════════
def make_exchange() -> ccxt.binance:
    return ccxt.binance({
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    })


def fetch_scanner_rows(exchange) -> tuple[list, str | None]:
    """兩段式掃描，回傳 (rows, err)。rows 為 list[dict]，欄位與舊 fetch_scanner_data 相同。"""
    try:
        tickers = exchange.fetch_tickers()
        candidates = []
        for sym, t in tickers.items():
            if not sym.endswith(":USDT"):
                continue
            contract_type = (t.get("info") or {}).get("contractType", "PERPETUAL")
            if contract_type and contract_type != "PERPETUAL":
                continue
            sym_base = sym.split("/")[0]
            if not _is_clean_crypto(sym_base):
                continue
            last = float(t.get("last") or 0)
            qv   = float(t.get("quoteVolume") or 0)
            pct  = t.get("percentage")
            if last <= 0 or qv <= 0 or pct is None:
                continue
            if qv < _RADAR_PHASE1_VOL_MIN:
                continue
            candidates.append((sym, sym_base, last, qv, float(pct)))

        candidates.sort(key=lambda x: abs(x[4]), reverse=True)
        fire_control = candidates[:_RADAR_TOP_N]

        # 強制納入主流大幣（若波動度排不進 Top 30，補進來讓它們也被評估）
        _picked = {c[1] for c in fire_control}
        for c in candidates:
            if c[1] in _MAJORS and c[1] not in _picked:
                fire_control.append(c)
                _picked.add(c[1])

        rows = []
        for sym, sym_base, last_price, _, pct24h in fire_control:
            time.sleep(0.1)
            try:
                ohlcv = exchange.fetch_ohlcv(sym, timeframe="15m", limit=100)
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

                _ma_short = sum(closes[-10:]) / min(10, len(closes))
                _ma_long  = sum(closes[-50:]) / min(50, len(closes))
                trend_gap = round((_ma_short - _ma_long) / _ma_long * 100, 2) if _ma_long > 0 else 0.0

                if vol_surge is None or _is_nan(rsi) or _is_nan(cci):
                    continue

                funding_rate = 0.0
                try:
                    fr_data = exchange.fetch_funding_rate(sym)
                    funding_rate = float(fr_data.get("fundingRate", 0.0) or 0.0)
                except Exception:
                    funding_rate = 0.0

                _lc       = ohlcv[-2]
                price_str = f"{last_price:,.2f}" if last_price >= 10 else f"{last_price:,.4f}"
                rows.append({
                    "Symbol":         sym_base,
                    "24h 漲跌幅 (%)": round(pct24h, 2),
                    "價格 (USDT)":    price_str,
                    "RSI 15m":        rsi,
                    "CCI 14":         cci,
                    "Vol Surge (%)":  vol_surge,
                    "趨勢 (%)":       trend_gap,
                    "atr":            atr if not _is_nan(atr) else 0.0,
                    "funding_rate":   funding_rate,
                    "ohlc_o":         float(_lc[1]),
                    "ohlc_h":         float(_lc[2]),
                    "ohlc_l":         float(_lc[3]),
                    "ohlc_c":         float(_lc[4]),
                })
            except (ccxt.NetworkError, ccxt.RequestTimeout):
                continue
            except Exception:
                continue
        return rows, None
    except ccxt.RateLimitExceeded as exc:
        return [], f"Binance Rate Limit 觸發：{exc}"
    except ccxt.NetworkError as exc:
        return [], f"網路連線錯誤：{exc}"
    except Exception as exc:
        return [], f"掃描失敗：{exc}"


# ════════════════════════════════════════════════════════════════════════════
# 引擎：狙擊 / 標記更新 / 移動停利（操作純 state dict）
# ════════════════════════════════════════════════════════════════════════════
def run_sniper(state: dict, settings: dict, scan_rows: list) -> None:
    if not scan_rows:
        return
    leverage     = int(settings.get("sniper_leverage", 10))
    margin       = float(settings.get("sniper_margin", SNIPER_MARGIN_DEFAULT))
    max_pos      = int(settings.get("sniper_max_pos", 5))
    cooldown_min = int(settings.get("sniper_loss_cooldown", 120))
    trend_filter = bool(settings.get("trend_filter_enabled", True))
    allow_short  = bool(settings.get("allow_short", False))   # 預設只做多（做空經實測為負期望值）

    positions = state.setdefault("virtual_positions", [])
    state.setdefault("virtual_history_log", [])
    agent_log = state.setdefault("agent_log", [])

    open_pos = [p for p in positions if p.get("status", "Open") == "Open"]
    existing = {p["symbol"] for p in open_pos}

    for row in scan_rows:
        if len(existing) >= max_pos:
            break
        symbol       = row.get("Symbol", "")
        rsi          = row.get("RSI 15m")
        cci          = row.get("CCI 14")
        vol_surge    = row.get("Vol Surge (%)")
        atr_val      = float(row.get("atr", 0.0) or 0.0)
        price_val    = _parse_price(row.get("價格 (USDT)", ""))
        funding_rate = float(row.get("funding_rate", 0.0) or 0.0)
        trend_gap    = float(row.get("趨勢 (%)", 0.0) or 0.0)

        if not symbol or price_val is None or price_val <= 0:
            continue
        if rsi is None or cci is None or vol_surge is None:
            continue
        if _is_nan(rsi) or _is_nan(vol_surge) or _is_nan(cci):
            continue
        if symbol in existing:
            continue
        rsi = float(rsi); cci = float(cci)

        # 同幣種虧損冷卻
        if cooldown_min > 0:
            settled = [p for p in positions
                       if p.get("symbol") == symbol and p.get("status") == "Closed"]
            settled += [p for p in state.get("virtual_history_log", [])
                        if p.get("symbol") == symbol]
            if settled:
                now_dt = datetime.now()
                min_elapsed = float("inf"); last_pnl = None
                for sp in settled:
                    tpart = str(sp.get("closed_at", "")).split()[-1]
                    try:
                        cdt = datetime.strptime(tpart, "%H:%M:%S").replace(
                            year=now_dt.year, month=now_dt.month, day=now_dt.day)
                        elapsed = (now_dt - cdt).total_seconds()
                        if elapsed < 0:
                            elapsed += 86400
                        if elapsed < min_elapsed:
                            min_elapsed = elapsed
                            last_pnl = float(sp.get("realized_pnl", 0.0))
                    except Exception:
                        continue
                if (last_pnl is not None and last_pnl < 0
                        and min_elapsed / 60 < cooldown_min):
                    continue

        # 協議 Delta：長上影線
        _o = float(row.get("ohlc_o", 0)); _h = float(row.get("ohlc_h", 0))
        _l = float(row.get("ohlc_l", 0)); _c = float(row.get("ohlc_c", 0))
        is_pin_bar = False; wick_pct = 0.0
        if _h > _l and _c > 0:
            upper_wick = _h - max(_o, _c); total_len = _h - _l
            wick_pct = round(upper_wick / total_len * 100, 1)
            if upper_wick > total_len * DELTA_WICK_RATIO and total_len > _c * DELTA_TOTAL_MIN_PCT:
                is_pin_bar = True

        side = None
        if rsi < SNIPER_RSI_LONG and vol_surge > SNIPER_VOL_MIN:
            side = "Long"
        elif (allow_short and trend_gap < -SNIPER_TREND_BLOCK_PCT
              and rsi < 50 and vol_surge > SNIPER_VOL_MIN):
            # 順勢做空（預設關閉）：經兩場實測（兩種做空邏輯）皆為負期望值，
            # 加密幣向上漂移 + 軋空使系統性做空難贏。預設只做多止血，可由開關重啟實驗。
            side = "Short"
        if side is None:
            continue

        # 趨勢濾網
        if trend_filter:
            if side == "Long" and trend_gap < -SNIPER_TREND_BLOCK_PCT:
                continue
            if side == "Short" and trend_gap > SNIPER_TREND_BLOCK_PCT:
                continue

        score = _calc_resonance_score(rsi, cci, wick_pct, float(vol_surge), side)
        if score >= 80:
            dynamic_margin = margin * 1.5
        elif score >= 60:
            dynamic_margin = margin
        else:
            dynamic_margin = margin * 0.5
        dynamic_margin = min(dynamic_margin, state["virtual_balance"] * 0.10)
        if dynamic_margin < 1.0:
            continue

        entry_slipped = (price_val * (1.0 + SLIPPAGE_PCT) if side == "Long"
                         else price_val * (1.0 - SLIPPAGE_PCT))
        nominal  = dynamic_margin * leverage
        qty      = nominal / entry_slipped
        open_fee = nominal * OPEN_FEE_RATE
        if state["virtual_balance"] < dynamic_margin + open_fee:
            continue

        ts = datetime.now().strftime("%H:%M:%S")
        positions.append({
            "symbol": symbol, "side": side, "entry_price": entry_slipped,
            "qty": qty, "mark_price": price_val, "margin": dynamic_margin,
            "leverage": leverage, "nominal": nominal, "opened_at": ts,
            "hwm": entry_slipped, "atr": atr_val, "score": score,
            "funding_rate": funding_rate, "status": "Open",
            "entry_rsi": rsi, "entry_cci": cci,
            "entry_vol_surge": float(vol_surge), "entry_trend_gap": trend_gap,
            "entry_funding_rate": funding_rate,   # 進場當下資金費率快照（funding_rate 會被即時刷新）
        })
        state["virtual_balance"] -= (dynamic_margin + open_fee)
        existing.add(symbol)

        tag = ("🔥 絕殺" if score >= 80 else "✅ 標準" if score >= 60 else "🔬 試水")
        if side == "Short":
            agent_log.insert(0,
                f"[{ts}]　🔻 [順勢做空] **{symbol}** 下跌趨勢 (MA偏離 {trend_gap:+.1f}%, RSI {rsi:.1f}) + "
                f"爆量 {vol_surge:.0f}%　評分 {score} {tag} | {leverage}x"
                f" | 保證金 ${dynamic_margin:,.0f} @ {entry_slipped:,.4f}　手續費 -${open_fee:,.2f}")
        else:
            agent_log.insert(0,
                f"[{ts}]　🔫 **{symbol}** 恐慌超賣 (RSI {rsi:.1f}, 爆量 {vol_surge:.0f}%) 自動做多 {leverage}x"
                f" | 評分 {score} {tag} | 保證金 ${dynamic_margin:,.0f} @ {entry_slipped:,.4f}　手續費 -${open_fee:,.2f}")

    state["agent_log"] = agent_log[:AGENT_LOG_MAX]


def update_mark_prices(state: dict, scan_rows: list, exchange) -> None:
    price_map = {}
    funding_map = {}
    for row in scan_rows:
        p = _parse_price(row.get("價格 (USDT)", ""))
        if p is not None and p > 0:
            price_map[row.get("Symbol", "")] = p
        # 順便建立即時資金費率表（掃描每輪都已抓取）
        if "funding_rate" in row:
            funding_map[row.get("Symbol", "")] = float(row.get("funding_rate", 0.0) or 0.0)

    open_positions = [vp for vp in state.get("virtual_positions", [])
                      if vp.get("status", "Open") == "Open"]
    missing = [vp["symbol"] for vp in open_positions
               if vp.get("symbol") and vp["symbol"] not in price_map]
    if missing:
        try:
            tickers = exchange.fetch_tickers([f"{b}/USDT:USDT" for b in missing])
            for sym_full, t in tickers.items():
                base = sym_full.split("/")[0]
                last = t.get("last") or t.get("close") or 0.0
                if last and float(last) > 0:
                    price_map[base] = float(last)
        except Exception:
            pass

    for vp in open_positions:
        base = vp.get("symbol", "")
        new_mark = price_map.get(base)
        if new_mark and new_mark > 0:
            vp["mark_price"] = new_mark
            vp["price_error"] = False
        else:
            vp["price_error"] = True
        # 即時刷新資金費率（取不到就維持原值，不覆蓋成 0）
        if base in funding_map:
            vp["funding_rate"] = funding_map[base]


def run_trailing_stop(state: dict, user_id: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    agent_log = state.setdefault("agent_log", [])
    for vp in state.get("virtual_positions", []):
        try:
            if vp.get("status", "Open") != "Open":
                continue
            mark   = float(vp.get("mark_price", vp["entry_price"]))
            entry  = float(vp["entry_price"]); qty = float(vp["qty"])
            side   = vp["side"]; hwm = float(vp.get("hwm", entry))
            margin = float(vp.get("margin", SNIPER_MARGIN_DEFAULT))
            atr    = float(vp.get("atr", 0.0) or 0.0)

            # 先更新最高/最低水位（HWM）
            if side == "Long":
                if mark > hwm:
                    vp["hwm"] = mark; hwm = mark
            else:
                if mark < hwm:
                    vp["hwm"] = mark; hwm = mark

            # 利潤棘輪：依「相對進場的有利移動」決定停利距離（大賺收緊鎖利）
            trail_dist = trail_distance(entry, hwm, atr, side)
            upnl = (mark - entry) * qty if side == "Long" else (entry - mark) * qty

            liquidated = upnl <= -(margin * LIQUIDATION_THRESHOLD)
            trailing_triggered = False
            if not liquidated:
                if side == "Long" and mark <= hwm - trail_dist:
                    trailing_triggered = True
                elif side == "Short" and mark >= hwm + trail_dist:
                    trailing_triggered = True
            if not liquidated and not trailing_triggered:
                continue

            pnl           = round(upnl, 4)
            close_fee     = round(qty * mark * CLOSE_FEE_RATE, 4)
            returned      = margin + pnl - close_fee
            state["virtual_balance"] += max(returned, 0.0)

            vp["status"] = "Closed"; vp["closed_at"] = ts
            vp["closed_price"] = mark; vp["realized_pnl"] = pnl; vp["close_fee"] = close_fee

            if liquidated:
                agent_log.insert(0,
                    f"[{ts}]　💥 [爆倉] **{vp['symbol']}** 浮虧 ${pnl:,.2f} 觸及保證金 95% 下限，強制清算！"
                    f"　平倉手續費 -${close_fee:,.2f}")
                exit_reason = "爆倉"
            else:
                label = "移動停利" if pnl >= 0 else "動態停損"
                icon  = "🟢" if pnl >= 0 else "🔴"
                sign  = "+" if pnl >= 0 else ""
                agent_log.insert(0,
                    f"[{ts}]　{icon} [{label}] **{vp['symbol']}** ATR 防守線觸發，"
                    f"結算 {sign}{pnl:,.2f} USDT　平倉手續費 -${close_fee:,.2f}")
                exit_reason = "移動停利" if pnl >= 0 else "動態停損"

            insert_trade(
                timestamp=ts, symbol=vp["symbol"], side=vp.get("side", ""),
                entry_price=float(vp.get("entry_price", 0)), exit_price=float(mark),
                pnl=float(pnl), score=int(vp.get("score") or 0), exit_reason=exit_reason,
                user_id=int(user_id),
                rsi=float(vp.get("entry_rsi", 0.0) or 0.0),
                cci=float(vp.get("entry_cci", 0.0) or 0.0),
                vol_surge=float(vp.get("entry_vol_surge", 0.0) or 0.0),
                trend_gap=float(vp.get("entry_trend_gap", 0.0) or 0.0),
                leverage=int(vp.get("leverage", 0) or 0),
            )
        except Exception as ex:
            print(f"[ENGINE][WARN] trailing_stop {vp.get('symbol', '?')} → {ex}")
            continue
    state["agent_log"] = agent_log[:AGENT_LOG_MAX]


# ════════════════════════════════════════════════════════════════════════════
# 狀態 / 設定 檔案存取（加鎖）
# ════════════════════════════════════════════════════════════════════════════
def _state_path(uid: int) -> str:
    return os.path.join(DATA_DIR, f"fox_sandbox_state_{uid}.json")


def _settings_path(uid: int) -> str:
    return os.path.join(DATA_DIR, f"fox_settings_{uid}.json")


def load_state(uid: int) -> dict | None:
    with _STATE_LOCK:
        path = _state_path(uid)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None


def save_state(uid: int, state: dict) -> None:
    with _STATE_LOCK:
        try:
            payload = {
                "virtual_balance":     state.get("virtual_balance", INITIAL_BALANCE),
                "virtual_positions":   state.get("virtual_positions", []),
                "virtual_history_log": state.get("virtual_history_log", []),
                "agent_log":           state.get("agent_log", []),
            }
            with open(_state_path(uid), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as ex:
            print(f"[ENGINE][WARN] save_state {uid} → {ex}")


def load_settings(uid: int) -> dict | None:
    with _STATE_LOCK:
        path = _settings_path(uid)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None


def save_settings(uid: int, settings: dict) -> None:
    with _STATE_LOCK:
        try:
            with open(_settings_path(uid), "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as ex:
            print(f"[ENGINE][WARN] save_settings {uid} → {ex}")


def list_user_ids() -> list[int]:
    uids = []
    for path in glob.glob(os.path.join(DATA_DIR, "fox_sandbox_state_*.json")):
        m = re.search(r"fox_sandbox_state_(\d+)\.json$", os.path.basename(path))
        if m:
            uids.append(int(m.group(1)))
    return uids


# ════════════════════════════════════════════════════════════════════════════
# 背景引擎主迴圈
# ════════════════════════════════════════════════════════════════════════════
def engine_tick(exchange) -> None:
    """跑一輪：掃描一次 → 對每個使用者存檔執行引擎。"""
    scan_rows, _err = fetch_scanner_rows(exchange)
    if scan_rows is None:
        scan_rows = []
    for uid in list_user_ids():
        with _STATE_LOCK:
            state = load_state(uid)
            if state is None:
                continue
            settings = load_settings(uid) or {}
            try:
                if settings.get("auto_trade_enabled"):
                    run_sniper(state, settings, scan_rows)
                update_mark_prices(state, scan_rows, exchange)   # 標記價照常更新
                run_trailing_stop(state, uid)                    # 既有持倉照常管理
                save_state(uid, state)
            except Exception as ex:
                print(f"[ENGINE][WARN] tick user {uid} → {ex}")


_ENGINE_STARTED = False
_ENGINE_START_LOCK = threading.Lock()


def _engine_loop() -> None:
    exchange = make_exchange()
    print("[ENGINE] 背景交易引擎已啟動（不依賴瀏覽器分頁）")
    while True:
        try:
            engine_tick(exchange)
        except Exception as ex:
            print(f"[ENGINE][WARN] loop → {ex}")
        time.sleep(INTERVAL)


def start_background_engine() -> None:
    """啟動單一常駐 daemon 執行緒（冪等：重複呼叫只會啟動一次）。"""
    global _ENGINE_STARTED
    with _ENGINE_START_LOCK:
        if _ENGINE_STARTED:
            return
        t = threading.Thread(target=_engine_loop, name="fox-engine", daemon=True)
        t.start()
        _ENGINE_STARTED = True
