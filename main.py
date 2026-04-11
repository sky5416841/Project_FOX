"""
Project F.O.X. — Trading Radar
Architecture: Zero external framework, pure ccxt + stdlib
"""

import time
import ctypes
import sys
from collections import deque
from datetime import datetime

import ccxt

# ── ANSI color codes ──────────────────────────────────────────────────────────
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

# ── Alert thresholds ──────────────────────────────────────────────────────────
PRICE_FLOOR          = 70_500.0   # trigger if price drops below this
DROP_PERCENT_1MIN    = 0.5        # trigger if 1-min drop exceeds this %
POLL_INTERVAL_SEC    = 2          # fetch every N seconds
HISTORY_WINDOW_SEC   = 60         # rolling window for % drop calculation

# ── Windows Beep ──────────────────────────────────────────────────────────────
def beep(frequency: int = 1000, duration_ms: int = 500) -> None:
    """Emit a system beep via Windows kernel32 (no external deps)."""
    try:
        ctypes.windll.kernel32.Beep(frequency, duration_ms)
    except Exception:
        print("\a", end="", flush=True)  # fallback: terminal bell


def fire_alert(reason: str, price: float) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(
        f"\n{RED}{BOLD}"
        f"🚨 F.O.X. 警報：市場異動！"
        f"{RESET}"
        f"  {RED}[{ts}]  原因: {reason}  |  現價: {price:,.2f} USDT{RESET}\n"
    )
    beep(frequency=1200, duration_ms=300)
    time.sleep(0.15)
    beep(frequency=900,  duration_ms=500)


# ── Exchange setup ────────────────────────────────────────────────────────────
def build_exchange() -> ccxt.binance:
    exchange = ccxt.binance({
        "options": {
            "defaultType": "future",   # 永續合約 (USDⓈ-M Futures)
        },
        "enableRateLimit": True,       # respect Binance rate limits
    })
    return exchange


# ── Price fetch ───────────────────────────────────────────────────────────────
def fetch_price(exchange: ccxt.binance, symbol: str = "BTC/USDT") -> float:
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker["last"])


# ── Alert logic ───────────────────────────────────────────────────────────────
def check_alerts(
    price: float,
    history: deque,          # deque of (timestamp, price)
    alerted_floor: set,
) -> None:
    now = time.time()

    # 1. Price-floor alert (de-duplicated per breach episode)
    if price < PRICE_FLOOR:
        key = "floor"
        if key not in alerted_floor:
            fire_alert(f"價格跌破 {PRICE_FLOOR:,.0f}", price)
            alerted_floor.add(key)
    else:
        alerted_floor.discard("floor")  # reset when price recovers

    # 2. 1-minute rapid-drop alert
    cutoff = now - HISTORY_WINDOW_SEC
    # oldest price still inside the window
    oldest = next((p for ts, p in history if ts >= cutoff), None)
    if oldest is not None and oldest > 0:
        drop_pct = (oldest - price) / oldest * 100
        if drop_pct >= DROP_PERCENT_1MIN:
            key = f"drop_{int(now // HISTORY_WINDOW_SEC)}"
            if key not in alerted_floor:
                fire_alert(
                    f"1 分鐘急跌 {drop_pct:.2f}%（{oldest:,.2f} → {price:,.2f}）",
                    price,
                )
                alerted_floor.add(key)


# ── Status line ───────────────────────────────────────────────────────────────
def print_status(price: float, prev_price: float | None) -> None:
    ts  = datetime.now().strftime("%H:%M:%S")
    if prev_price is None:
        arrow = "  "
        color = CYAN
    elif price > prev_price:
        arrow = "▲"
        color = GREEN
    elif price < prev_price:
        arrow = "▼"
        color = RED
    else:
        arrow = "─"
        color = YELLOW

    print(
        f"\r{CYAN}[F.O.X.]{RESET} {ts}  "
        f"BTC/USDT  {color}{BOLD}{price:>12,.2f} USDT  {arrow}{RESET}   ",
        end="",
        flush=True,
    )


# ── Main loop ─────────────────────────────────────────────────────────────────
def main() -> None:
    # Enable ANSI on Windows 10+
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )

    print(f"\n{CYAN}{BOLD}  Project F.O.X. — Trading Radar  {RESET}")
    print(f"{CYAN}  Symbol : BTC/USDT (Binance Perpetual){RESET}")
    print(f"{CYAN}  Floor  : {PRICE_FLOOR:,.0f} USDT{RESET}")
    print(f"{CYAN}  Drop   : {DROP_PERCENT_1MIN}% / 1 min{RESET}")
    print(f"{CYAN}  Poll   : every {POLL_INTERVAL_SEC}s{RESET}\n")

    exchange    = build_exchange()
    history     = deque()          # (timestamp, price)
    alerted     = set()
    prev_price  = None

    while True:
        try:
            price = fetch_price(exchange)
            now   = time.time()

            # maintain rolling history
            history.append((now, price))
            cutoff = now - HISTORY_WINDOW_SEC - POLL_INTERVAL_SEC
            while history and history[0][0] < cutoff:
                history.popleft()

            print_status(price, prev_price)
            check_alerts(price, history, alerted)
            prev_price = price

        except ccxt.NetworkError as exc:
            print(f"\n{YELLOW}[WARN] Network error: {exc} — retrying...{RESET}")
        except ccxt.ExchangeError as exc:
            print(f"\n{YELLOW}[WARN] Exchange error: {exc} — retrying...{RESET}")
        except KeyboardInterrupt:
            print(f"\n\n{CYAN}F.O.X. Radar stopped.{RESET}\n")
            sys.exit(0)

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
