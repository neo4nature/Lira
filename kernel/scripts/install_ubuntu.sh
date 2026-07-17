#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Basic deps for sounddevice (PortAudio) and common TTS engines
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip python3-dev build-essential \
  portaudio19-dev \
  speech-dispatcher espeak-ng festival || true

# venv at project root
VENV="$ROOT_DIR/.venv"
python3 -m venv "$VENV"
source "$VENV/bin/activate"

python3 -m pip install --upgrade pip
python3 -m pip install -r "$ROOT_DIR/app/requirements.txt"

echo "[OK] venv ready at $VENV"
echo "Next: bash $ROOT_DIR/scripts/update_env_paths.sh"
