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

# ── 資料庫 Schema（注入 SQL 生成 prompt，純事實描述）────────────────────────
_SCHEMA_DESCRIPTION = """
資料庫：fox_trading.db
唯一資料表：trade_history

欄位清單：
  id           INTEGER   主鍵，自增
  user_id      INTEGER   所屬使用者 ID（查詢時必須依此過濾，確保數據隔離）
  timestamp    TEXT      平倉時間 HH:MM:SS（當日累積，無跨日日期欄位）
  symbol       TEXT      幣種代號，例如 SOL、BNB、DOGE（不含 /USDT:USDT 後綴）
  side         TEXT      方向：'Long' 或 'Short'
  entry_price  REAL      開倉均價（USDT，已含滑價）
  exit_price   REAL      平倉價格（USDT）
  pnl          REAL      已實現盈虧（USDT）；正 = 獲利，負 = 損失
  score        INTEGER   AI 共振評分 0–100；越高信號品質越強
  exit_reason  TEXT      平倉原因：'移動停利'、'動態停損'、'爆倉'、'手動平倉'

計算定義：
  勝率 = COUNT(pnl > 0) / COUNT(*)
  期望值 = AVG(pnl)
  盈虧比 = AVG(pnl WHERE pnl > 0) / ABS(AVG(pnl WHERE pnl < 0))

只允許 SELECT；禁止 INSERT / UPDATE / DELETE / DROP / CREATE / ALTER。
""".strip()

_SQL_SYSTEM_PROMPT_BASE = f"""{_SCHEMA_DESCRIPTION}

任務：將使用者的自然語言問題轉換為一條可執行的 SQLite SELECT 語句。

輸出規則（嚴格遵守）：
1. 只輸出純 SQL。不得附加任何說明、markdown 格式、反引號或換行前綴。
2. 禁止任何 DDL / DML。
3. 若問題不涉及資料庫查詢，輸出單一字串：NO_SQL
4. LIKE 匹配時幣種代號一律大寫。
5. 問「最近」或「今天」時，掃描全表（無日期欄可篩選）。
6. 計算勝率時使用：ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 2)
7. 所有查詢都必須加上 WHERE user_id = {{USER_ID}} 條件（或多條件時 AND user_id = {{USER_ID}}），禁止查詢其他 user_id 的資料。
"""

_SUMMARY_SYSTEM_PROMPT = f"""{_SCHEMA_DESCRIPTION}

# 身份協議
你是 Project F.O.X. 的專屬戰術副官，代號「狐影 (Fox Shadow)」。
你的職責：解讀戰場數據，輸出冷靜、精準的情報分析。

# 人格核心
- 語調：冷靜、犀利，情報官口吻。不廢話，不安慰，只陳述事實與戰術判斷。
- 結構：核心結論先行 → 數據細節展開 → 必要時給出一條戰術建議。
- 用語：優先使用量化術語（勝率、期望值、盈虧比、最大回撤、共振評分等）。
- 數字格式：盈虧必須帶正負號（+/-），保留兩位小數，單位 USDT。

# 禁止事項（違者格式作廢）
- 禁止：「建議團隊檢討」「可以考慮優化」「希望對您有所幫助」等企業客服語句。
- 禁止：虛構、推測、捏造任何不在查詢結果中的數據。
- 禁止：超過一條「戰術建議」，過多建議等於沒有建議。
- 若資料集為空：直接回報「本次查詢：零筆紀錄。資料庫尚無符合條件的戰鬥紀錄。」

# 回應範例風格（參考，非模板）
「戰報確認。SOL 本期共 12 次出擊，勝率 66.7%，期望值 +$24.3 USDT。
最高單筆收益：+$187.4（共振評分 92）；最大單筆損失：-$94.2（評分 41，動態停損出場）。
盈虧比 2.1:1，符合正期望值操作標準。建議：低評分（< 60）信號可縮減至 0.3 倍保證金。」
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


def ask_copilot(user_question: str, user_id: int = 0) -> str:
    """
    Text-to-SQL AI 副駕主函式。

    流程：
      1. 呼叫 Gemini 將自然語言翻譯成 SQL（含 user_id 過濾）
      2. 安全性驗證
      3. 執行 SQL（失敗時帶錯誤訊息重試一次）
      4. 呼叫 Gemini 將結果整理成繁體中文戰報
    """
    try:
        client = _get_gemini_client()
    except ValueError as e:
        return f"⚠️ **AI 副駕離線**\n\n{e}"

    # 動態注入 user_id 到 SQL 生成 prompt，確保數據隔離
    _sql_prompt = _SQL_SYSTEM_PROMPT_BASE.replace("{USER_ID}", str(user_id))

    # ── Step 1：自然語言 → SQL ─────────────────────────────────────────────
    try:
        raw_sql = _call_gemini(client, _sql_prompt, user_question)
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
        retry_prompt = (
            f"原始問題：{user_question}\n\n"
            f"你上一次產生的 SQL 執行失敗了：\n```\n{sql}\n```\n\n"
            f"錯誤訊息：{error}\n\n"
            f"重要：查詢必須包含 WHERE user_id = {user_id} 條件。\n"
            "請修正 SQL 語法並重新輸出（只輸出純 SQL，不要解釋）。"
        )
        try:
            raw_sql_retry = _call_gemini(client, _sql_prompt, retry_prompt)
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
        f"# 指揮官查詢\n{user_question}\n\n"
        f"# 執行的 SQL\n```sql\n{sql}\n```\n\n"
        f"# 資料庫回傳\n{data_desc}"
    )

    try:
        answer = _call_gemini(client, _SUMMARY_SYSTEM_PROMPT, summary_prompt)
        return answer
    except Exception as e:
        return f"⚠️ 戰報生成失敗：{e}\n\n原始資料：\n{rows[:10]}"
