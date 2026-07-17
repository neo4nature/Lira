# Startup sequence (v16.6)

This repo uses a deterministic boot order so state files exist before any loop consumes them.

## Recommended: ./start_lira.sh (unified entrypoint)

From repo root:

```bash
./start_lira.sh start
```

This is the single recommended entrypoint. It delegates to the kernel's deterministic starter
and also provides `stop/status/logs` helpers.

## Kernel starter (used internally)

`./start_lira.sh` delegates to:

```bash
bash kernel/scripts/start_system.sh
```

It performs:
1. `bootstrap_deps` (Vosk + Piper; Whisper optional)
2. `preflight` (creates critical JSON state files/directories + validates Ollama + checks Piper wiring)
3. starts loops in background:
   - RegulatorLoop (writes `loop_regulator.json` + `memory_mode.json`)
   - DecisionLoop (owns `bus_signals.json`)
   - TurmalinLoop (TTL/compact/promote; obeys `memory_mode.json`)
   - WatchdogLoop
   - Night daemon
4. starts API (uvicorn)

## Memory mode cooldown + policy file

RegulatorLoop reads:

- `kernel/data/config/memory_policy.json`

It applies hysteresis + cooldown (default 30s) to avoid flip-flopping between `normal/degraded/emergency`.

Output control file:

- `kernel/data/runtime/state/memory_mode.json`

Consumers:
- TurmalinLoop: TTL/max_records + promotion toggle
- NightCycle: embedding/capsule work can be skipped in emergency

## Piper location

Bootstrap pins the Piper binary (if present on PATH) to:

- `kernel/models/piper/bin/piper`

Voice models (optional conventional names):

- `kernel/models/piper/voices/pl.onnx`
- `kernel/models/piper/voices/en.onnx`

Or set env:
- `PIPER_BIN`, `PIPER_MODEL_PL`, `PIPER_MODEL_EN`

## Python environment

Unified pip requirements live at:

- `Lira/requirements.txt`

Recommended setup (from repo root):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

System packages (Ubuntu) needed for audio + popups are listed at the top of `requirements.txt`.
