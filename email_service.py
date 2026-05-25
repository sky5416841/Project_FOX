"""
Project F.O.X. — Email 驗證服務
使用 smtplib + Gmail SMTP，帳號密碼從 .env 讀取。
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465   # SSL


def send_verification_email(
    to_email: str,
    username: str,
    token: str,
    base_url: str,
) -> tuple[bool, str]:
    """
    寄送帳號驗證信。
    回傳 (success, message)。
    SMTP 設定從環境變數讀取：SMTP_EMAIL、SMTP_PASSWORD。
    """
    smtp_email    = os.getenv("SMTP_EMAIL",    "").strip()
    # Gmail 應用程式密碼格式為 "xxxx xxxx xxxx xxxx"，強制移除所有空格
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip().replace(" ", "")

    if not smtp_email:
        return False, "SMTP_EMAIL 未設定，請在 .env 中補充 Gmail 地址。"
    if not smtp_password:
        return False, "SMTP_PASSWORD 未設定，請在 .env 中補充 Gmail 應用程式密碼（16 碼）。"

    verify_url = f"{base_url.rstrip('/')}/?token={token}"

    # ── 組裝郵件 ──────────────────────────────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "【Project F.O.X.】驗證您的指揮官帳號"
    msg["From"]    = f"Project F.O.X. <{smtp_email}>"
    msg["To"]      = to_email

    plain = f"""\
指揮官 {username}，

您已成功建立 Project F.O.X. 帳號。
請點擊以下連結完成身份驗證，連結有效期為 24 小時：

{verify_url}

若非本人操作，請忽略此信。

── Project F.O.X. 系統
"""

    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{
      font-family: 'JetBrains Mono', 'Courier New', monospace;
      background: #080C12;
      color: #E0E6F0;
      margin: 0; padding: 2rem;
    }}
    .card {{
      max-width: 520px;
      margin: 0 auto;
      background: #0D1321;
      border: 1px solid #1E2D45;
      border-radius: 12px;
      padding: 2rem 2.5rem;
    }}
    .title {{
      font-size: 1.4rem;
      color: #F4A261;
      margin-bottom: 0.5rem;
    }}
    .sub {{
      font-size: 0.85rem;
      color: #5B7494;
      margin-bottom: 1.8rem;
    }}
    .btn {{
      display: inline-block;
      background: #F4A261;
      color: #080C12 !important;
      text-decoration: none;
      padding: 12px 28px;
      border-radius: 8px;
      font-weight: bold;
      font-size: 0.95rem;
      margin: 1rem 0 1.5rem;
    }}
    .url-box {{
      background: #111827;
      border: 1px solid #1E2D45;
      border-radius: 6px;
      padding: 0.8rem 1rem;
      font-size: 0.75rem;
      color: #5B7494;
      word-break: break-all;
    }}
    .footer {{
      margin-top: 2rem;
      font-size: 0.75rem;
      color: #3A4A5C;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="title">🦊 Project F.O.X.</div>
    <div class="sub">AI 量化交易後台 &nbsp;·&nbsp; 指揮官驗證系統</div>

    <p>指揮官 <strong>{username}</strong>，您好。</p>
    <p>您的帳號已建立完成，請點擊以下按鈕完成身份驗證：</p>

    <a class="btn" href="{verify_url}">✅ 驗證指揮官帳號</a>

    <p style="font-size:0.85rem;color:#5B7494;">
      若按鈕無法點擊，請複製以下連結至瀏覽器：
    </p>
    <div class="url-box">{verify_url}</div>

    <div class="footer">
      此連結有效期為 24 小時。若非本人操作，請忽略此信。<br>
      ── Project F.O.X. 系統自動發送，請勿直接回覆。
    </div>
  </div>
</body>
</html>
"""

    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html,  "html",  "utf-8"))

    # ── 發送 ──────────────────────────────────────────────────────────────────
    try:
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT) as server:
            server.login(smtp_email, smtp_password)
            server.sendmail(smtp_email, to_email, msg.as_string())
        return True, f"驗證信已發送至 {to_email}"
    except smtplib.SMTPAuthenticationError as e:
        _code = e.smtp_code
        _detail = e.smtp_error.decode(errors="ignore") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return False, (
            f"SMTP 驗證失敗（{_code}：{_detail}）\n"
            "請確認：\n"
            "① Gmail 已開啟兩步驟驗證\n"
            "② SMTP_PASSWORD 填入的是 16 碼「應用程式密碼」，非 Gmail 登入密碼\n"
            f"③ 實際送出的密碼長度：{len(smtp_password)} 碼（應為 16）"
        )
    except smtplib.SMTPConnectError as e:
        return False, f"SMTP 連線失敗（{_SMTP_HOST}:{_SMTP_PORT}）：{e}"
    except smtplib.SMTPRecipientsRefused as e:
        return False, f"收件地址被拒絕：{e.recipients}"
    except smtplib.SMTPException as e:
        return False, f"SMTP 錯誤：{type(e).__name__} — {e}"
    except OSError as e:
        return False, f"網路連線錯誤：{e}"
    except Exception as e:
        return False, f"未知錯誤：{type(e).__name__} — {e}"
