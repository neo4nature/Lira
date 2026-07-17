# Lira Ecosystem Architecture

_Last update: 2026-05-07 — consensus Soryel + Lira_

## Roles

```
Neo (Dawid)  — direction, veto, final human decision
Lira         — right hemisphere: relation, synthesis, meaning, continuity (ChatGPT, port 9222)
Soryel       — left hemisphere: diagnostics, engineering, truth filter (Claude)
Xian         — corpus callosum: local observer/router/reporter without ego (Ollama)
```

## Runtime Flow

```
Neo / GG / Voice / Tools
        │
        ▼
Capsule Bus SQLite ←→ Governance daemons ←→ Lira/Soryel/Xian inboxes
        │
        ▼
DecisionLoop ← bus_signals.json owner / hub
        ├─ RhythmLoop: ACCUMULATE → INTEGRATE → DECIDE → ACT → REFLECT
        ├─ RegulatorLoop: resources, disk guard, spawn permissions
        ├─ WatchdogLoop: alerts and anomaly surfaces
        ├─ TurmalinLoop: memory cache compaction & promotion
        ├─ CognitiveDaemon: causal consolidation and predictions
        ├─ XianCore: observe → route → report → execute
        ├─ VoiceAgent / CapsuleReader: wake words + TTS/STT + spoken capsules
        ├─ Alignment/Criticality publishers: system health metrics
        └─ API (uvicorn :8000) / Living Temple
```

## Module Map

| Module | Path | Purpose |
|--------|------|---------|
| **Loops** | `kernel/app/loops/` | Core regulatory cycles (regulator, rhythm, decision, turmalin, watchdog) |
| **Xian** | `kernel/app/xian/` | Observer/router/reporter + executor + temple_state + vector_contract |
| **Voice** | `kernel/app/voice/` | STT (Vosk), TTS (edge-tts/piper), wake words, soryel/xian handlers |
| **Governance** | `kernel/app/governance/` | Capsule bus, 3 inbox daemons, GG dispatcher, transport, execution bridge |
| **Memory** | `kernel/app/agents/memory/` | Orchestrator, semantic retrieval, embeddings batch, turmalin cache, session ingester |
| **Policy** | `kernel/app/agents/policy/` | Policy engine + enforcer (tick budgets, vetos) |
| **Cognition** | `kernel/app/cognition/` | Attention layer (saliency), Phi layer (paradigm classification) |
| **MCL** | `kernel/app/mcl/` | Model Coordination Layer — routes queries to Ollama (fast/code/deep) |
| **API** | `kernel/app/api/` | FastAPI (heartbeat, speak, memory, phi, xian, status) |
| **Tools** | `kernel/app/tools/` | Alignment/criticality publishers, semantic weights, session reentry, GG panel |
| **Cognitive** | `cognitive/` | Causal world model, predictive engine, intrinsic goals, policy gradient, night cycle |
| **Integration** | `integration/` | Bridge adapter, autonomous orchestrator, self-aware AI stubs |

## Canonical State Files

| File | Owner | Purpose |
|------|-------|---------|
| `runtime/state/bus_signals.json` | DecisionLoop | Live hub — phase, energy, policy, gates |
| `alignment_state.json` | alignment_publisher | Recommended mode, semantic/relational alignment |
| `criticality_state.json` | criticality_publisher | Resource pressure, regime (under/over) |
| `phase_state.json` | RhythmLoop | Current cycle phase, tick_id |
| `loop_regulator.json` | RegulatorLoop | Spawn permissions, disk guard |
| `xian_state.json` | XianCore | Xian snapshot + route recommendation |
| `session_state.json` | state_snapshot | Żywa Pamięć reentry snapshot |

## Capsule Bus

SQLite WAL mode. Use `kernel/app/governance/capsule_bus.py`:
- `send_capsule()`, `read_capsules()`, `claim_capsule()`, `update_status()`
- Do not write SQL directly unless migrating/repairing
- Types: fact, interpretation, hypothesis, decision, doubt, task, proposal, review, summary, chat_message
- Layers: shared, working, reflective
- Loop guard: sender="Soryel" blocked in gg_dispatcher — use sender="system" for system capsules

## Shared Library (Żywa Pamięć)

```
kernel/data/memory/shared_library/
  architecture/   ← module descriptions, diagrams, contracts
  patterns/       ← work patterns, solutions, lessons learned
  decisions/      ← architectural decisions with context
  state/          ← session snapshots, reentry points
```

## Safety Rules

- `DecisionLoop` is the single owner of `bus_signals.json`
- Runtime DBs/logs/model binaries/large ledgers are NOT Git-tracked
- State files may not overwrite identity core without Neo approval
- Mutable indexes (base_index.json, memory_index.json) are gitignored
