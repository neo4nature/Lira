#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Continuous night maintenance loop.
# Run this in a separate terminal when you are not actively using voice/text.
# Stop with Ctrl+C.

INTERVAL_SEC="${NIGHT_INTERVAL_SEC:-180}"  # default: 3 minutes
NIGHT_START="${NIGHT_WINDOW_START:-5}"     # earliest hour (inclusive)
NIGHT_END="${NIGHT_WINDOW_END:-10}"        # latest hour (exclusive)

echo "🌙 Night daemon started. Interval: ${INTERVAL_SEC}s, window: ${NIGHT_START}:00–${NIGHT_END}:00"

while true; do
  HOUR=$(date +%H | sed 's/^0//')
  if [[ "$HOUR" -ge "$NIGHT_START" && "$HOUR" -lt "$NIGHT_END" ]]; then
    echo "
🌙 [$(date +%F\ %T)] tick"
    if ! bash ./run_night_cycle.sh; then
      echo "⚠️ Night cycle error (continuing)"
    fi
  else
    echo "💤 [$(date +%F\ %T)] outside window (${NIGHT_START}–${NIGHT_END}), sleeping"
  fi
  sleep "${INTERVAL_SEC}"
done
