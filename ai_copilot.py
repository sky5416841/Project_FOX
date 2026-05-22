"""
Project F.O.X. — AI 量化副駕 (Text-to-SQL Copilot)

流程：自然語言問題 → Gemini 生成 SQL → SQLite 執行 → Gemini 生成中文戰報
"""

import os
import re
import sqlite3
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ── DB 路徑（與 database.py 保持一致）────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fox_trading.db")

# ── trade_history 完整 Schema 說明（注入至 Gemini system prompt）────────────
_SCHEMA_DESCRIPTION = """
你是一個專業的 SQLite 資料庫助理，負責查詢 Project F.O.X. 量化交易系統的歷史交易資料。

資料庫只有一張表：trade_history

欄位說明：
  id           INTEGER   — 自增主鍵
  timestamp    TEXT      — 平倉時間，格式為 HH:MM:SS（同一天的交易）
  symbol       TEXT      — 交易幣種代號，例如 SOL、BNB、DOGE（不含 /USDT:USDT）
  side         TEXT      — 交易方向：'Long'（做多）或 'Short'（做空）
  entry_price  REAL      — 開倉均價（USDT，含滑價）
  exit_price   REAL      — 平倉價格（USDT）
  pnl          REAL      — 已實現盈虧（USDT），正值為獲利，負值為虧損
  score        INTEGER   — AI 共振評分（0–100），越高代表信號品質越強
  exit_reason  TEXT      — 平倉原因：'移動停利'、'爆倉' 或 '手動平倉'

重要注意事項：
  - 資料為當日累積，timestamp 只有時間部分（無日期）。
  - 勝率 = 盈利筆數 (pnl > 0) ÷ 總筆數。
  - 所有金額單位皆為 USDT。
  - 只能產生 SELECT 查詢，禁止 INSERT / UPDATE / DELETE / DROP 等寫入或破壞性語句。
""".strip()

_SQL_SYSTEM_PROMPT = f"""{_SCHEMA_DESCRIPTION}

請根據使用者的自然語言問題，產生一條精確的 SQLite SELECT 語句。
規則：
1. 只輸出純 SQL，不要有任何解釋文字、markdown 格式或反引號包裹。
2. 禁止任何 DDL / DML（只允許 SELECT）。
3. 若問題不需要資料庫查詢（例如純聊天），輸出：NO_SQL
4. 使用 LIKE 進行模糊匹配時，幣種名稱請大寫。
5. 若使用者問「最近」或「今天」，直接查全部資料（無日期欄位可篩選）。
"""

_SUMMARY_SYSTEM_PROMPT = f"""{_SCHEMA_DESCRIPTION}

你是 F.O.X. 系統的 AI 量化副駕，代號「狐影」。
你的任務是根據 SQL 查詢結果，以繁體中文撰寫一份專業的量化交易戰報分析。

風格要求：
- 語氣：專業、精準，帶有輕微的軍事指揮官口吻
- 結構：先給核心結論，再列出數字細節
- 若資料為空，清楚說明「目前資料庫尚無符合條件的交易紀錄」
- 數字格式：盈虧加上正負號（+/-），保留兩位小數，USDT 為單位
- 禁止虛構數據，只能根據提供的查詢結果分析
""".strip()


def _get_gemini_client():
    """取得 Gemini client，若 API KEY 未設定則拋出 ValueError。"""
    from google import genai  # 延遲 import，避免未安裝時影響主程式啟動

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY 尚未設定。\n"
            "請在 .env 檔案中加入：GEMINI_API_KEY=你的金鑰\n"
            "金鑰可至 https://aistudio.google.com/apikey 免費申請。"
        )
    return genai.Client(api_key=api_key)


def _call_gemini(client, system_prompt: str, user_message: str) -> str:
    """呼叫 Gemini API，回傳純文字回應。"""
    from google.genai import types

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,           # 低溫確保 SQL 穩定性
            max_output_tokens=2048,
        ),
        contents=user_message,
    )
    return response.text.strip()


def _extract_sql(raw: str) -> str:
    """從 Gemini 回應中提取乾淨的 SQL（移除 markdown 格式）。"""
    # 移除 ```sql ... ``` 或 ``` ... ``` 包裹
    raw = re.sub(r"```(?:sql)?", "", raw, flags=re.IGNORECASE).replace("```", "")
    return raw.strip()


def _is_safe_sql(sql: str) -> bool:
    """安全性驗證：只允許 SELECT 語句，拒絕任何寫入或破壞性操作。"""
    cleaned = sql.strip().upper()
    # 必須以 SELECT 開頭
    if not cleaned.startswith("SELECT"):
        return False
    # 禁止關鍵字
    forbidden = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE",
                 "ALTER", "TRUNCATE", "REPLACE", "ATTACH", "DETACH")
    for kw in forbidden:
        if re.search(rf"\b{kw}\b", cleaned):
            return False
    return True


def _execute_sql(sql: str) -> tuple[list[dict[str, Any]], str | None]:
    """
    執行 SQL 查詢，回傳 (rows, error_message)。
    rows 為 list[dict]；若失敗則 rows=[], error_message 含錯誤說明。
    """
    try:
        if not os.path.exists(DB_PATH):
            return [], "資料庫檔案不存在，尚無任何交易紀錄。"

        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql)
            rows = [dict(row) for row in cur.fetchall()]
        return rows, None

    except sqlite3.OperationalError as e:
        return [], f"SQL 語法錯誤：{e}"
    except Exception as e:
        return [], f"資料庫執行異常：{e}"


def ask_copilot(user_question: str) -> str:
    """
    Text-to-SQL AI 副駕主函式。

    流程：
      1. 呼叫 Gemini 將自然語言翻譯成 SQL
      2. 安全性驗證
      3. 執行 SQL（失敗時帶錯誤訊息重試一次）
      4. 呼叫 Gemini 將結果整理成繁體中文戰報

    Args:
        user_question: 使用者輸入的自然語言問題

    Returns:
        str: AI 副駕的繁體中文回應
    """
    try:
        client = _get_gemini_client()
    except ValueError as e:
        return f"⚠️ **AI 副駕離線**\n\n{e}"

    # ── Step 1：自然語言 → SQL ─────────────────────────────────────────────
    try:
        raw_sql = _call_gemini(client, _SQL_SYSTEM_PROMPT, user_question)
    except Exception as e:
        return f"⚠️ Gemini API 呼叫失敗：{e}"

    sql = _extract_sql(raw_sql)

    # ── Step 2：處理「不需要 SQL」的情況（純問答）──────────────────────────
    if sql.upper() == "NO_SQL" or not sql:
        try:
            answer = _call_gemini(
                client,
                _SUMMARY_SYSTEM_PROMPT,
                f"使用者問了一個不需要查詢資料庫的問題，請直接用繁體中文回答：\n{user_question}"
            )
            return answer
        except Exception as e:
            return f"⚠️ Gemini API 呼叫失敗：{e}"

    # ── Step 3：安全性驗證 ─────────────────────────────────────────────────
    if not _is_safe_sql(sql):
        return (
            "🚫 **安全攔截**：偵測到非 SELECT 語句，已拒絕執行。\n"
            "副駕只允許資料查詢操作，無法執行寫入或破壞性指令。"
        )

    # ── Step 4：執行 SQL，失敗時帶錯誤訊息讓 Gemini 修正並重試 ────────────
    rows, error = _execute_sql(sql)

    if error:
        # 第一次失敗：把錯誤回饋給 Gemini，要求修正 SQL
        retry_prompt = (
            f"原始問題：{user_question}\n\n"
            f"你上一次產生的 SQL 執行失敗了：\n```\n{sql}\n```\n\n"
            f"錯誤訊息：{error}\n\n"
            "請修正 SQL 語法並重新輸出（只輸出純 SQL，不要解釋）。"
        )
        try:
            raw_sql_retry = _call_gemini(client, _SQL_SYSTEM_PROMPT, retry_prompt)
            sql = _extract_sql(raw_sql_retry)
        except Exception as e:
            return f"⚠️ SQL 修正失敗：{e}"

        if not _is_safe_sql(sql):
            return "🚫 修正後的 SQL 仍不符合安全規則，已拒絕執行。"

        rows, error = _execute_sql(sql)
        if error:
            return (
                f"⚠️ **資料查詢失敗**（已重試一次）\n\n"
                f"錯誤訊息：{error}\n\n"
                f"最後嘗試的 SQL：\n```sql\n{sql}\n```"
            )

    # ── Step 5：Gemini 將查詢結果整理成中文戰報 ───────────────────────────
    if not rows:
        data_desc = "查詢結果為空（0 筆紀錄）。"
    else:
        # 限制傳給 Gemini 的資料量（最多 200 筆，防止 token 爆炸）
        sample = rows[:200]
        data_desc = f"查詢回傳 {len(rows)} 筆紀錄（以下為前 {len(sample)} 筆）：\n{sample}"

    summary_prompt = (
        f"使用者的問題：{user_question}\n\n"
        f"執行的 SQL：\n```sql\n{sql}\n```\n\n"
        f"查詢結果：\n{data_desc}"
    )

    try:
        answer = _call_gemini(client, _SUMMARY_SYSTEM_PROMPT, summary_prompt)
        return answer
    except Exception as e:
        return f"⚠️ 戰報生成失敗：{e}\n\n原始資料：\n{rows[:10]}"
