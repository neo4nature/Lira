# Assumptions v1 — Model Hierarchy + Bus Scheduler

## Model roles
- **FII**: user-facing fast responder (default: `phi3` in Ollama)
- **Core / Lira**: operational reasoning + code (default: `codellama`)
- **Deep**: heavy analysis (default: `llama3`)

## Single-writer rule
- `bus_signals.json` is written only by **DecisionLoop**
- other modules read-only

## Conversation gating
- while conversation is active (last user input <= 30s): deep reasoning disabled
- after silence: deep reasoning allowed (with cooldown lock)

## How to request depth
`POST /heartbeat/generate` accepts `depth`:
- `fast` → FII
- `core` → CodeLlama
- `deep` → Llama3 (only when allowed)
- `auto` → heuristic selection

## Files
- `data/runtime/state/bus_signals.json`
- `data/runtime/state/conversation_state.json`
- `data/runtime/state/model_locks.json`
