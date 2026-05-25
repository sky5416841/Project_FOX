"""
Project F.O.X. — SQLite 持久化層 (Multi-user SaaS)
"""

import hashlib
import hmac
import os
import sqlite3
import uuid
from typing import Optional

_DATA_DIR = os.getenv("FOX_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH    = os.path.join(_DATA_DIR, "fox_trading.db")

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
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    username           TEXT    NOT NULL UNIQUE,
    password_hash      TEXT    NOT NULL,
    email              TEXT,
    is_verified        INTEGER NOT NULL DEFAULT 0,
    verification_token TEXT
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

            # ── trade_history 遷移：補齊 user_id ───────────────────────────
            th_cols = [row[1] for row in conn.execute("PRAGMA table_info(trade_history)").fetchall()]
            if "user_id" not in th_cols:
                conn.execute("ALTER TABLE users ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")

            # ── users 遷移：補齊 email / is_verified / verification_token ──
            u_cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "email" not in u_cols:
                conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            if "is_verified" not in u_cols:
                conn.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 0")
                # 遷移前的既有帳號視為已驗證，避免舊使用者被鎖定
                conn.execute("UPDATE users SET is_verified = 1")
            if "verification_token" not in u_cols:
                conn.execute("ALTER TABLE users ADD COLUMN verification_token TEXT")

            # ── 預載管理員帳號（冪等：已存在則跳過）──────────────────────
            _admin_row = conn.execute("SELECT 1 FROM users WHERE username = 'admin'").fetchone()
            if not _admin_row:
                _default_pwd = os.getenv("ADMIN_DEFAULT_PWD")
                if _default_pwd:
                    conn.execute(
                        "INSERT INTO users (username, password_hash, is_verified) VALUES (?, ?, 1)",
                        ("admin", _hash_password(_default_pwd)),
                    )
                    print("[FOX][DB] admin 帳號已自動建立（is_verified=1）")
                else:
                    print("[FOX][DB][WARN] ADMIN_DEFAULT_PWD 未設定，跳過 admin 自動建立")

            conn.commit()
    except Exception as e:
        print(f"[FOX][DB][WARN] init_db 失敗：{e}")


def create_user(username: str, password: str, email: str) -> tuple[bool, str, str | None]:
    """
    建立新使用者。
    回傳 (success, message, verification_token)。
    失敗時 token 為 None。
    """
    username = (username or "").strip()
    email    = (email    or "").strip()

    if not username or not password:
        return False, "帳號和密碼不得為空。", None
    if len(username) < 3:
        return False, "帳號至少需要 3 個字元。", None
    if len(password) < 6:
        return False, "密碼至少需要 6 個字元。", None
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return False, "請輸入有效的電子信箱。", None

    token = str(uuid.uuid4())
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, email, is_verified, verification_token)"
                " VALUES (?, ?, ?, 0, ?)",
                (username, _hash_password(password), email, token),
            )
            conn.commit()
        return True, "帳號建立成功！請前往信箱點擊驗證連結後即可登入。", token
    except sqlite3.IntegrityError:
        return False, "此帳號已被使用，請選擇其他帳號。", None
    except Exception as e:
        return False, f"註冊失敗：{e}", None


def verify_user(username: str, password: str) -> tuple[int | None, bool]:
    """
    驗證帳密。
    回傳 (user_id, is_verified)。
    帳密錯誤時 user_id = None。
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, password_hash, is_verified FROM users WHERE username = ?",
                ((username or "").strip(),),
            ).fetchone()
        if row is None:
            return None, False
        user_id, stored_hash, is_verified = row
        if not _verify_password(password, stored_hash):
            return None, False
        return user_id, bool(is_verified)
    except Exception:
        return None, False


def verify_email_token(token: str) -> bool:
    """
    驗證 email token：比對資料庫、更新 is_verified=1、清空 token。
    成功回傳 True，token 無效或已使用回傳 False。
    """
    if not token:
        return False
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE verification_token = ?", (token.strip(),)
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE users SET is_verified = 1, verification_token = NULL WHERE id = ?",
                (row[0],),
            )
            conn.commit()
        return True
    except Exception:
        return False


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
