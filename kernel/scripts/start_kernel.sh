#!/usr/bin/env bash
set -euo pipefail

# Run minimal kernel loops:
# - RegulatorLoop
# - DecisionLoop (bus owner)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "[Lira] root: $ROOT_DIR"

# Prefer venv if present
if [[ -d "$ROOT_DIR/app/.venv" ]]; then
  source "$ROOT_DIR/app/.venv/bin/activate"
elif [[ -d "$ROOT_DIR/.venv" ]]; then
  source "$ROOT_DIR/.venv/bin/activate"
fi

python3 "$ROOT_DIR/app/loops/regulator_loop.py" &
REG_PID=$!

python3 "$ROOT_DIR/app/loops/decision_loop.py" &
DEC_PID=$!

echo "[LiraKernel] Regulator PID=$REG_PID, Decision PID=$DEC_PID"
wait $REG_PID $DEC_PID
