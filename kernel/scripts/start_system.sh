#!/usr/bin/env bash
set -euo pipefail

# Deterministic startup for the whole system.
# - Preflight (self-healing JSON state + directories)
# - Kernel loops (RegulatorLoop + DecisionLoop)
# - Night daemon (background)
# - API (uvicorn)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/app"

echo "[Lira] root: $ROOT_DIR"

# Prefer venv if present
if [[ -d "$ROOT_DIR/app/.venv" ]]; then
  source "$ROOT_DIR/app/.venv/bin/activate"
elif [[ -d "$ROOT_DIR/.venv" ]]; then
  source "$ROOT_DIR/.venv/bin/activate"
fi

# Ensure .env is up to date (paths)
bash "$ROOT_DIR/scripts/update_env_paths.sh" >/dev/null 2>&1 || true

# Export .env into environment (API host/port etc.)
if [[ -f "$APP_DIR/.env" ]]; then
  set -a
  source "$APP_DIR/.env"
  set +a
fi

echo "[Lira] Paths:"
echo "  LIRA_PROJECT_ROOT=${LIRA_PROJECT_ROOT:-}"
echo "  LIRA_DATA_ROOT=${LIRA_DATA_ROOT:-}"
echo "  LIRA_ARCHIVE_ROOT=${LIRA_ARCHIVE_ROOT:-}"
echo "  LIRA_SECURE_ROOT=${LIRA_SECURE_ROOT:-}"

# Mount checks (soft)
check_mount () {
  local p="$1"
  [[ -z "$p" ]] && return 0
  if command -v mountpoint >/dev/null 2>&1; then
    mountpoint -q "$p" || echo "[WARN] Not a mountpoint or not mounted: $p"
  fi
}
check_mount "${LIRA_ARCHIVE_ROOT:-}"
check_mount "${LIRA_SECURE_ROOT:-}"

mkdir -p "$ROOT_DIR/logs/pids"

echo "[Lira] Bootstrap deps: install Vosk+Piper (Whisper optional; set INSTALL_WHISPER=1)"
export AUTO_CLEANUP="${AUTO_CLEANUP:-1}"
(cd "$APP_DIR" && python3 -c "import bootstrap_deps as b; import json; print(json.dumps(b.run(), ensure_ascii=False, indent=2))") \
  >"$ROOT_DIR/logs/bootstrap_deps_report.json" || true

echo "[Lira] (Optional) Manual cleanup: scripts/cleanup_deps.sh (removes ONLY what bootstrap installed)"

echo "[Lira] Preflight: ensure dirs + critical state files"
(cd "$APP_DIR" && python3 -c "import preflight; import json; print(json.dumps(preflight.run(), ensure_ascii=False, indent=2))") \
  >"$ROOT_DIR/logs/preflight_report.json" || true

# --- Audio: wirtualny sink eco_tts dla OBS ---
if command -v pactl >/dev/null 2>&1; then
  if ! pactl list short sinks | grep -q eco_tts; then
    pactl load-module module-null-sink sink_name=eco_tts \
      sink_properties=device.description="Ecosystem_TTS" >/dev/null 2>&1 || true
    echo "[Lira] eco_tts sink created"
  fi
  # loopback → domyślny sink (żeby Neo słyszał TTS)
  DEFAULT_SINK=$(pactl get-default-sink 2>/dev/null || true)
  if [[ -n "$DEFAULT_SINK" ]]; then
    pactl load-module module-loopback source=eco_tts.monitor \
      sink="$DEFAULT_SINK" latency_msec=30 >/dev/null 2>&1 || true
    echo "[Lira] eco_tts loopback → $DEFAULT_SINK"
  fi
fi

echo "[Lira] Starting kernel loops (background) [Regulator + Rhythm + Decision]"
(cd "$APP_DIR" && exec python3 -m loops.regulator_loop >"$ROOT_DIR/logs/regulator.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/regulator.pid"

(cd "$APP_DIR" && exec python3 -m loops.rhythm_loop >"$ROOT_DIR/logs/rhythm.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/rhythm.pid"

(cd "$APP_DIR" && exec python3 -m loops.decision_loop >"$ROOT_DIR/logs/decision.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/decision.pid"

# Build canonical core memory ledger after startup (optional)
echo "[Lira] Starting reconstruction daemon (optional)"
if [[ "${LIRA_ENABLE_RECONSTRUCTION:-1}" == "1" ]]; then
  (cd "$APP_DIR" && exec python3 -m loops.reconstruction_daemon >"$ROOT_DIR/logs/reconstruction_daemon.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/reconstruction_daemon.pid"
  echo "[Lira] reconstruction daemon enabled"
else
  echo "[Lira] reconstruction daemon disabled (set LIRA_ENABLE_RECONSTRUCTION=1 to enable)"
fi

echo "[Lira] Starting Turmalin loop (background)"
(cd "$APP_DIR" && exec python3 -m loops.turmalin_loop >"$ROOT_DIR/logs/turmalin.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/turmalin.pid"


echo "[Lira] Starting watchdog (background)"
(cd "$APP_DIR" && exec python3 -m loops.watchdog_loop >"$ROOT_DIR/logs/watchdog.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/watchdog.pid"

echo "[Lira] Starting night daemon (background)"
nohup bash "$APP_DIR/run_night_daemon.sh" >"$ROOT_DIR/logs/night_daemon.log" 2>&1 &
echo $! >"$ROOT_DIR/logs/pids/night_daemon.pid"

echo "[Lira] Starting cognitive daemon (optional)"
if [[ "${LIRA_ENABLE_COGNITIVE_DAEMON:-1}" == "1" ]]; then
  (cd "$APP_DIR" && exec python3 -m loops.cognitive_daemon >"$ROOT_DIR/logs/cognitive_daemon.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/cognitive_daemon.pid"
  echo "[Lira] cognitive daemon enabled"
else
  echo "[Lira] cognitive daemon disabled (set LIRA_ENABLE_COGNITIVE_DAEMON=1 to enable)"
fi

echo "[Lira] Starting Gaia executor (optional)"
if [[ "${LIRA_ENABLE_GAIA:-0}" == "1" ]]; then
  (cd "$APP_DIR" && exec python3 -m loops.gaia_executor_loop >"$ROOT_DIR/logs/gaia_executor.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/gaia_executor.pid"
  echo "[Lira] gaia executor enabled"
else
  echo "[Lira] gaia executor disabled (set LIRA_ENABLE_GAIA=1 to enable)"
fi

echo "[Lira] Starting integration bridge (optional)"
INTEGRATION_DIR="$(cd "$ROOT_DIR/.." && pwd)/integration"
if [[ "${LIRA_ENABLE_INTEGRATION:-0}" == "1" && -f "$INTEGRATION_DIR/bridge_kernel_adapter.py" ]]; then
  # Run as true background/idle priority
  if command -v ionice >/dev/null 2>&1; then
    nohup ionice -c3 nice -n ${LIRA_BACKGROUND_NICE:-10} python3 "$INTEGRATION_DIR/bridge_kernel_adapter.py" >"$ROOT_DIR/logs/integration_bridge.log" 2>&1 &
  else
    nohup nice -n ${LIRA_BACKGROUND_NICE:-10} python3 "$INTEGRATION_DIR/bridge_kernel_adapter.py" >"$ROOT_DIR/logs/integration_bridge.log" 2>&1 &
  fi
  echo $! >"$ROOT_DIR/logs/pids/integration_bridge.pid"
  echo "[Lira] integration bridge PID: $(cat "$ROOT_DIR/logs/pids/integration_bridge.pid")"
else
  echo "[Lira] integration bridge disabled (set LIRA_ENABLE_INTEGRATION=1 to enable)"
fi


API_HOST="${LIRA_API_HOST:-127.0.0.1}"
API_PORT="${LIRA_API_PORT:-8000}"


# Bootstrap semantic seed records once (best-effort)
if [ "${LIRA_BOOTSTRAP_EMBED_RECORDS:-1}" = "1" ]; then
  echo "[Lira] Bootstrapping embedding records (best-effort)"
  (cd "$APP_DIR" && exec python3 -m tools.bootstrap_embedding_records >"$ROOT_DIR/logs/bootstrap_embeddings.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/bootstrap_embeddings.pid"
fi

# Session ingester — embed Claude Code conversations into the ecosystem
echo "[Lira] Running session ingester (best-effort)"
(cd "$APP_DIR" && exec python3 -m agents.memory.session_ingester >"$ROOT_DIR/logs/session_ingester.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/session_ingester.pid"

# Capsule ingester — embed capsule_bus into embeddings_index (semantic library)
if [ "${LIRA_ENABLE_CAPSULE_INGESTER:-1}" = "1" ]; then
  echo "[Lira] Starting capsule ingester (capsule_bus → embeddings_index)"
  (cd "$APP_DIR" && exec python3 -m agents.memory.capsule_ingester --loop --interval "${LIRA_CAPSULE_INGESTER_INTERVAL:-300}" --limit "${LIRA_CAPSULE_INGESTER_LIMIT:-50}" >"$ROOT_DIR/logs/capsule_ingester.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/capsule_ingester.pid"
fi

# Conversation bridge — capsule_bus → shared_context/events (zasila alignment_publisher)
if [ "${LIRA_ENABLE_CONVERSATION_BRIDGE:-1}" = "1" ]; then
  echo "[Lira] Starting conversation bridge (capsule_bus → events/)"
  (cd "$APP_DIR" && exec python3 -m tools.conversation_bridge --loop --interval "${LIRA_CONVERSATION_BRIDGE_INTERVAL:-30}" >"$ROOT_DIR/logs/conversation_bridge.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/conversation_bridge.pid"
fi

# Alignment publisher
echo "[Lira] Starting alignment publisher"
(cd "$APP_DIR" && exec python3 -m tools.alignment_publisher >"$ROOT_DIR/logs/alignment_publisher.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/alignment_publisher.pid"

echo "[Lira] Starting Xian core (background)"
(cd "$APP_DIR" && exec python3 -m xian.xian_core >"$ROOT_DIR/logs/xian_core.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/xian_core.pid"

# Criticality publisher
echo "[Lira] Starting criticality publisher"
(cd "$APP_DIR" && exec python3 -m tools.criticality_publisher >"$ROOT_DIR/logs/criticality_publisher.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/criticality_publisher.pid"

# Governance reporter
if [ "${LIRA_ENABLE_GOVERNANCE_REPORTER:-1}" = "1" ]; then
  echo "[Lira] Starting governance ecosystem reporter"
  (cd "$APP_DIR" && exec python3 -m governance.ecosystem_reporter --loop --interval "${LIRA_GOVERNANCE_REPORT_INTERVAL:-900}" --alert-interval "${LIRA_GOVERNANCE_ALERT_INTERVAL:-180}" >"$ROOT_DIR/logs/governance_reporter.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/governance_reporter.pid"
fi

# Governance dispatcher
if [ "${LIRA_ENABLE_GOVERNANCE_DISPATCHER:-1}" = "1" ]; then
  echo "[Lira] Starting governance dispatcher"
  (cd "$APP_DIR" && exec python3 -m governance.gg_dispatcher --loop --interval "${LIRA_GOVERNANCE_DISPATCH_INTERVAL:-30}" >"$ROOT_DIR/logs/governance_dispatcher.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/governance_dispatcher.pid"
fi

# Execution bridge
if [ "${LIRA_ENABLE_EXECUTION_BRIDGE:-1}" = "1" ]; then
  echo "[Lira] Starting governance execution bridge"
  (cd "$APP_DIR" && exec python3 -m governance.execution_bridge --loop --interval "${LIRA_EXECUTION_BRIDGE_INTERVAL:-30}" >"$ROOT_DIR/logs/execution_bridge.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/execution_bridge.pid"
fi

# Soryel inbox daemon (polls capsule_bus for Soryel-addressed capsules)
if [ "${LIRA_ENABLE_SORYEL_INBOX:-1}" = "1" ]; then
  echo "[Lira] Starting Soryel inbox daemon"
  (cd "$APP_DIR" && exec python3 -m governance.soryel_inbox_daemon >"$ROOT_DIR/logs/soryel_inbox.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/soryel_inbox.pid"
fi

# Xian Inbox Daemon — Xian odpowiada na kapsułki w GG forum
if [ "${LIRA_ENABLE_XIAN_INBOX:-1}" = "1" ]; then
  echo "[Lira] Starting Xian inbox daemon"
  (cd "$APP_DIR" && exec python3 -m governance.xian_inbox_daemon --interval "${XIAN_INBOX_INTERVAL:-30}" >"$ROOT_DIR/logs/xian_inbox.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/xian_inbox.pid"
fi

# Lira Inbox Daemon — Lira odpowiada na chat_message z MoltBook przez CDP
if [ "${LIRA_ENABLE_LIRA_INBOX:-1}" = "1" ]; then
  echo "[Lira] Starting Lira inbox daemon (MoltBook chat)"
  (cd "$APP_DIR" && exec python3 -m governance.lira_inbox_daemon --interval "${LIRA_INBOX_INTERVAL:-30}" >"$ROOT_DIR/logs/lira_inbox.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/lira_inbox.pid"
fi

# GG Transport Daemon (Playwright CDP — wymaga otwartej przeglądarki na GG_CDP_PORT)
if [ "${LIRA_ENABLE_GG_TRANSPORT:-1}" = "1" ]; then
  echo "[Lira] Starting GG transport daemon (CDP port: ${GG_CDP_PORT:-9222})"
  (cd "$APP_DIR" && exec python3 -m governance.gg_transport_daemon >"$ROOT_DIR/logs/gg_transport.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/gg_transport.pid"
fi

# GG panel
if [ "${LIRA_ENABLE_GG_PANEL:-1}" = "1" ]; then
  echo "[Lira] Starting GG panel on ${LIRA_GG_HOST:-127.0.0.1}:${LIRA_GG_PORT:-8787}"
  (cd "$APP_DIR" && exec python3 -m tools.gg >"$ROOT_DIR/logs/gg_panel.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/gg_panel.pid"
fi

# Xian live wallpaper (optional, desktop only)
if [ "${LIRA_ENABLE_WALLPAPER:-1}" = "1" ] && [ -n "${DISPLAY:-}" ]; then
  pkill -f "xian_wallpaper.py" 2>/dev/null || true
  echo "[Lira] Starting Xian live wallpaper"
  (GDK_BACKEND=x11 DISPLAY="${DISPLAY}" PYTHONPATH=/usr/lib/python3/dist-packages \
    exec python3 "$APP_DIR/xian_wallpaper/xian_wallpaper.py" \
    >"$ROOT_DIR/logs/xian_wallpaper.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/xian_wallpaper.pid"
fi

# Voice daemon (wake word + TTS + Lira/Soryel/Xian routing)
if [ "${LIRA_ENABLE_VOICE:-1}" = "1" ]; then
  echo "[Lira] Starting voice daemon (wake words: lira, soryel, xian)"
  (cd "$APP_DIR" && exec python3 run_voice.py >"$ROOT_DIR/logs/voice.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/voice.pid"
fi

# Capsule reader — reads new capsules for Neo aloud via TTS
if [ "${LIRA_ENABLE_CAPSULE_READER:-1}" = "1" ]; then
  echo "[Lira] Starting capsule reader (TTS for Neo)"
  (cd "$APP_DIR" && exec python3 -m voice.capsule_reader >"$ROOT_DIR/logs/capsule_reader.log" 2>&1) &
  echo $! >"$ROOT_DIR/logs/pids/capsule_reader.pid"
fi

# CDP Browsers (ChatGPT, Soryel FB, Lira FB)
if [ "${LIRA_ENABLE_BROWSERS:-1}" = "1" ]; then
  echo "[Lira] Starting CDP browsers (ports 9222, 9223, 9224)"
  bash "$ROOT_DIR/scripts/start_browsers.sh" >>"$ROOT_DIR/logs/browsers.log" 2>&1
fi

echo "[Lira] Starting API on ${API_HOST}:${API_PORT}"
echo $$ >"$ROOT_DIR/logs/pids/api.pid"

# Notify readiness (voice if OK, popup if not). Runs in background and exits.
(cd "$APP_DIR" && exec python3 -m tools.ready_notifier >"$ROOT_DIR/logs/ready_notifier.log" 2>&1) &
echo $! >"$ROOT_DIR/logs/pids/ready_notifier.pid"

exec uvicorn --app-dir "$APP_DIR" api.main:app --host "${API_HOST}" --port "${API_PORT}"
