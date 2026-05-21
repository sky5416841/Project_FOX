"""
Project F.O.X. — SQLite 持久化交易紀錄層
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fox_trading.db")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trade_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    side         TEXT    NOT NULL,
    entry_price  REAL    NOT NULL,
    exit_price   REAL    NOT NULL,
    pnl          REAL    NOT NULL,
    score        INTEGER NOT NULL,
    exit_reason  TEXT    NOT NULL
)
"""


def init_db() -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()
    except Exception as e:
        print(f"[FOX][DB][WARN] init_db 失敗：{e}")


def insert_trade(
    timestamp: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    score: int,
    exit_reason: str,
) -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO trade_history
                    (timestamp, symbol, side, entry_price, exit_price, pnl, score, exit_reason)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(timestamp),
                    str(symbol),
                    str(side),
                    float(entry_price),
                    float(exit_price),
                    float(pnl),
                    int(score),
                    str(exit_reason),
                ),
            )
            conn.commit()
    except Exception as e:
        print(f"[FOX][DB][WARN] insert_trade 失敗 ({symbol} {side})：{e}")
