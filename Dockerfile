# ── Build Stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# 系統依賴（gcc 供部分 C-extension 編譯，之後不帶入 runtime）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime Stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# 從 builder 複製已安裝的套件
COPY --from=builder /install /usr/local

# 複製應用程式原始碼（排除 .env / db / venv 等由 .dockerignore 控管）
COPY . .

# 持久化資料目錄：fox_trading.db + fox_sandbox_state_*.json 掛載於此
RUN mkdir -p /data
ENV FOX_DATA_DIR=/data

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "dashboard.py", \
            "--server.port=8501", \
            "--server.address=0.0.0.0", \
            "--server.headless=true", \
            "--browser.gatherUsageStats=false"]
