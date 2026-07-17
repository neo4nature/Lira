#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/app"

# Ensure .env is up to date
bash "$ROOT_DIR/scripts/update_env_paths.sh" >/dev/null

source "$ROOT_DIR/.venv/bin/activate"

# Export .env into environment
set -a
source "$APP_DIR/.env"
set +a

echo "[Lira] Starting night daemon (background)"
nohup bash "$APP_DIR/run_night_daemon.sh" >"$ROOT_DIR/logs/app.log" 2>&1 &

echo "[Lira] Starting API on ${LIRA_API_HOST}:${LIRA_API_PORT}"
exec uvicorn --app-dir app api.main:app --host "${LIRA_API_HOST}" --port "${LIRA_API_PORT}"
