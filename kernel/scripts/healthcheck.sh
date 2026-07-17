#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/app"

echo "[1/4] .env"
if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "Missing app/.env (run scripts/update_env_paths.sh)"; exit 1
fi

echo "[2/4] venv"
if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  echo "Missing .venv (run scripts/install_ubuntu.sh)"; exit 1
fi

echo "[3/4] Python imports"
source "$ROOT_DIR/.venv/bin/activate"
python3 -c "import requests; import fastapi; import uvicorn" 

echo "[4/4] Ollama endpoint"
if command -v curl >/dev/null 2>&1; then
  curl -sf "http://127.0.0.1:11434/api/tags" >/dev/null && echo "[OK] Ollama reachable" || echo "[WARN] Ollama not reachable (is ollama serve running?)"
else
  echo "[WARN] curl not installed; skipping Ollama check"
fi

echo "[OK] healthcheck finished"
