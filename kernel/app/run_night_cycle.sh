#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Load .env so child processes get correct MODEL_CODE, paths, etc.
if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

if ! python3 - <<'PY'
from agents.core.idle_gate import allows_background
ok, reason = allows_background()
print(f"[night] gate: {ok} ({reason})")
raise SystemExit(0 if ok else 1)
PY
then
  echo "⏸️ Night cycle skipped (not idle / veto / background off)"
  exit 0
fi

NICE="${LIRA_BACKGROUND_NICE:-10}"
IONICE="${LIRA_BACKGROUND_IONICE:-3}"

run_bg () {
  if command -v ionice >/dev/null 2>&1; then
    ionice -c${IONICE} nice -n ${NICE} "$@"
  else
    nice -n ${NICE} "$@"
  fi
}

run_bg python3 -m agents.core.runtime_stream --kind night_start --source run_night_cycle.sh --payload "{}"
run_bg python3 -m agents.memory.index_memory
run_bg python3 -m agents.core.event_collector
run_bg python3 start_reconstruction.py

# PrepareData: nightly base index (for Watchdog/Cognitive/Reflective)
run_bg python3 -m agents.memory.prepare_data_night || true

# Optional: nightly embeddings batch for semantic retrieval (CPU-safe; uses Ollama embeddings)
if [[ "${LIRA_ENABLE_EMBEDDINGS_BATCH:-0}" == "1" ]]; then
  if python3 - <<'PY'
from agents.core.idle_gate import allows_background
ok, reason = allows_background(require_veto_none=True)
print(f"[night] embeddings gate: {ok} ({reason})")
raise SystemExit(0 if ok else 1)
PY
  then
    run_bg python3 -m agents.memory.embeddings_batch || true
  else
    echo "⏸️ Embeddings batch skipped (needs idle + veto=none)"
  fi
fi

run_bg python3 -c "from agents.core.queue_hygiene import hygiene; import json; print(json.dumps(hygiene(), ensure_ascii=False))" || true
if [[ "${LIRA_ENABLE_REFLECTIVE:-1}" == "1" ]]; then
  run_bg python3 reflective_loop.py
else
  echo "⏸️ Reflective loop disabled (LIRA_ENABLE_REFLECTIVE=0)"
fi

# (Optional) lightweight outcomes (kept for compatibility; can be disabled)
run_bg python3 -m agents.cognition.outcome_insights --window 60 || true

# Optional: full Cognitive Night Cycle (heavier deps). Default OFF.
if [[ "${LIRA_ENABLE_COGNITIVE:-0}" == "1" ]]; then
  if python3 - <<'PY'
from agents.core.idle_gate import allows_background
ok, reason = allows_background(require_veto_none=True)
print(f"[night] cognitive gate: {ok} ({reason})")
raise SystemExit(0 if ok else 1)
PY
  then
    COG_PATH="../../../cognitive/night_cycle/cognitive_night_cycle.py"
    if [[ -f "$COG_PATH" ]]; then
      run_bg python3 -m agents.cognition.cognitive_bridge || true
    else
      echo "⚠️  Cognitive night cycle not found at $COG_PATH"
    fi
  else
    echo "⏸️ Cognitive night cycle skipped (needs idle + veto=none)"
  fi
fi

# Jedna pozycja PLAN_ACTIVE.md do kolejki Xiana. Sam feeder nie woła modelu —
# tylko dokłada zadanie; wykona je executor, gdy trafi na fazę DECIDE.
run_bg python3 -m xian.plan_feeder || true

# Jedna awaria z logów do kolejki Xiana — osobne, wąskie pytanie. Feeder tylko
# czyta logi i dokłada zadanie; nie ma prawa wywalić nocnego cyklu.
run_bg python3 -m xian.error_feeder || true

# Archive/rotate (to SD) when idle
run_bg python3 -m agents.core.archive_manager || true
run_bg python3 -m agents.core.runtime_stream --kind night_end --source run_night_cycle.sh --payload "{}"
echo "✅ Night cycle complete."
