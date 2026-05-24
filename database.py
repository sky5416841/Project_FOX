"""
Project F.O.X. — SQLite 持久化層 (Multi-user SaaS)
"""

import hashlib
import hmac
import os
import sqlite3
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fox_trading.db")

_PBKDF2_ITERATIONS = 260_000
_HASH_ALGO = "sha256"


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """PBKDF2-HMAC-SHA256。回傳格式：algo$iters$salt_hex$dk_hex"""
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_HASH_ALGO}${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """以 constant-time 比對驗證密碼。"""
    try:
        algo, iters, salt_hex, dk_hex = stored_hash.split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


_CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL
)
"""

_CREATE_TRADE_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS trade_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL DEFAULT 0,
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
            conn.execute(_CREATE_USERS_SQL)
            conn.execute(_CREATE_TRADE_HISTORY_SQL)
            # 遷移舊資料庫：若 trade_history 缺少 user_id 欄位則新增
            cols = [row[1] for row in conn.execute("PRAGMA table_info(trade_history)").fetchall()]
            if "user_id" not in cols:
                conn.execute(
                    "ALTER TABLE trade_history ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"
                )
            # 預載管理員帳號（冪等：已存在則跳過）
            _admin_row = conn.execute(
                "SELECT 1 FROM users WHERE username = 'admin'"
            ).fetchone()
            if not _admin_row:
                conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    ("admin", _hash_password("fox123")),
                )
                print("[FOX][DB] admin 帳號已自動建立（預設密碼：fox123）")
            conn.commit()
    except Exception as e:
        print(f"[FOX][DB][WARN] init_db 失敗：{e}")


def create_user(username: str, password: str) -> tuple[bool, str]:
    """建立新使用者。回傳 (success, message)。"""
    username = (username or "").strip()
    if not username or not password:
        return False, "帳號和密碼不得為空。"
    if len(username) < 3:
        return False, "帳號至少需要 3 個字元。"
    if len(password) < 6:
        return False, "密碼至少需要 6 個字元。"
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, _hash_password(password)),
            )
            conn.commit()
        return True, "註冊成功！請使用帳號密碼登入。"
    except sqlite3.IntegrityError:
        return False, "此帳號已被使用，請選擇其他帳號。"
    except Exception as e:
        return False, f"註冊失敗：{e}"


def verify_user(username: str, password: str) -> Optional[int]:
    """驗證帳密，成功回傳 user_id，失敗回傳 None。"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE username = ?",
                ((username or "").strip(),),
            ).fetchone()
        if row is None:
            return None
        user_id, stored_hash = row
        return user_id if _verify_password(password, stored_hash) else None
    except Exception:
        return None


def insert_trade(
    timestamp: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    score: int,
    exit_reason: str,
    user_id: int = 0,
) -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO trade_history
                    (user_id, timestamp, symbol, side, entry_price, exit_price, pnl, score, exit_reason)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
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
