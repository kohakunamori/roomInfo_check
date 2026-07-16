#!/usr/bin/env bash
set -euo pipefail

cd /app

# Prefer config volume copies when present (compose may mount them here)
if [ -f /app/config/.env ] && [ ! -f /app/.env ]; then
  cp /app/config/.env /app/.env
fi
if [ -f /app/config/data.json ] && [ ! -f /app/data.json ]; then
  cp /app/config/data.json /app/data.json
fi

# Ensure data dir exists for session file
mkdir -p /app/data

# Default Plan A env if missing (compose should still provide a real .env)
if [ ! -f /app/.env ]; then
  cat > /app/.env <<'EOF'
ONLINE_SESSION_FILE=./data/.uestc_session.json
ONLINE_BROWSER_REFRESH=false
ONLINE_REMEMBER_ME=true
DATA_FILE=./data.json
CHECK_INTERVAL_MINUTES=60
LOW_BALANCE_THRESHOLD=20
SMTP_SERVER=smtp.163.com
SMTP_PORT=465
SMTP_USE_SSL=true
EOF
  echo "[entrypoint] wrote minimal /app/.env — mount a real one for production"
fi

export PYTHONUNBUFFERED=1

# Soft checks (never print secret values)
python - <<'PY'
from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv("/app/.env")
session = os.getenv("ONLINE_SESSION_FILE", "./data/.uestc_session.json")
print("[entrypoint] ONLINE_BROWSER_REFRESH=", os.getenv("ONLINE_BROWSER_REFRESH"))
print("[entrypoint] ONLINE_SESSION_FILE=", session)
print("[entrypoint] DATA_FILE=", os.getenv("DATA_FILE", "./data.json"))
print("[entrypoint] HISTORY_DB=", os.getenv("HISTORY_DB", "./data/history.db"))
print("[entrypoint] WEB_PORT=", os.getenv("WEB_PORT", "8080"))
print("[entrypoint] WEB_AUTH_TOKEN_set=", bool(os.getenv("WEB_AUTH_TOKEN")))
print("[entrypoint] AUTH_MODE=", os.getenv("AUTH_MODE", "manual"))
print("[entrypoint] SMTP_FROM=", os.getenv("SMTP_FROM"))
print("[entrypoint] session_exists=", Path(session).exists())
print("[entrypoint] data_exists=", Path(os.getenv("DATA_FILE", "./data.json")).exists())
print("[entrypoint] auth_set=", bool(os.getenv("Authorization_code")))
PY

exec "$@"
