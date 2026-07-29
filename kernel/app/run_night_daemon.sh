#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Continuous night maintenance loop.
# Run this in a separate terminal when you are not actively using voice/text.
# Stop with Ctrl+C.

INTERVAL_SEC="${NIGHT_INTERVAL_SEC:-180}"  # default: 3 minutes
NIGHT_START="${NIGHT_WINDOW_START:-5}"     # earliest hour (inclusive)
NIGHT_END="${NIGHT_WINDOW_END:-10}"        # latest hour (exclusive)

# Okno mówi KIEDY wolno, stempel mówi ILE RAZY. Bez stempla tick co 180 s
# powtarzał całą konsolidację ~35× na poranek (2026-07-27), bo cykl nie jest
# idempotentny i każdy przebieg liczył wszystko od nowa.
STAMP_FILE="${NIGHT_STAMP_FILE:-../data/runtime/state/night_daemon_last_day}"

echo "🌙 Night daemon started. Interval: ${INTERVAL_SEC}s, window: ${NIGHT_START}:00–${NIGHT_END}:00, stamp: ${STAMP_FILE}"

while true; do
  HOUR=$(date +%H | sed 's/^0//')
  TODAY=$(date +%F)
  if [[ "$HOUR" -ge "$NIGHT_START" && "$HOUR" -lt "$NIGHT_END" ]]; then
    LAST_DAY=""
    [[ -f "$STAMP_FILE" ]] && LAST_DAY=$(<"$STAMP_FILE")
    if [[ "$LAST_DAY" == "$TODAY" && "${NIGHT_FORCE:-0}" != "1" ]]; then
      echo "✅ [$(date +%F\ %T)] cykl już był dzisiaj (${TODAY}), pomijam"
    else
      echo "
🌙 [$(date +%F\ %T)] tick"
      RUN_LOG=$(mktemp)
      if bash ./run_night_cycle.sh 2>&1 | tee "$RUN_LOG"; then
        # Stempel tylko po pełnym przebiegu — "skipped (not idle)" ma wrócić za 3 min.
        if grep -q "Night cycle complete" "$RUN_LOG"; then
          mkdir -p "$(dirname "$STAMP_FILE")"
          printf '%s' "$TODAY" >"$STAMP_FILE"
          echo "🔒 [$(date +%F\ %T)] stempel ${TODAY} — następny przebieg jutro (NIGHT_FORCE=1 wymusza)"
        fi
      else
        echo "⚠️ Night cycle error (continuing)"
      fi
      rm -f "$RUN_LOG"
    fi
  else
    echo "💤 [$(date +%F\ %T)] outside window (${NIGHT_START}–${NIGHT_END}), sleeping"
  fi
  sleep "${INTERVAL_SEC}"
done
