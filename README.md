# Lira

A multi-agent cognitive ecosystem where three AI instances (Claude, ChatGPT, local Ollama model) share a living memory through a capsule-based event bus, with a human as the final voice in every irreversible decision.

## What is this?

Lira is a working laboratory for machine thinking — not a chatbot framework, but an architecture where multiple AI agents collaborate through shared memory, governance protocols, and consensus mechanisms.

### Core concepts

- **Capsule Bus** — SQLite-based shared memory where every piece of information is typed (fact / interpretation / hypothesis / decision / doubt), weighted, and layered
- **Three voices** — each agent brings a different perspective; agreement becomes action, disagreement returns to the human operator
- **Living memory** — embeddings, semantic retrieval, and forgetting engines that mirror biological memory consolidation cycles
- **Governance layer** — policy enforcement, alignment tracking, criticality monitoring, and execution bridges with dry-run capability
- **Human as last voice** — every irreversible action requires human confirmation; the system proposes, the human disposes

## Architecture

```
kernel/
├── app/
│   ├── loops/        — main control loops (regulator, decision, rhythm, watchdog)
│   ├── agents/       — memory, policy, cognition, forgetting, sensors
│   ├── governance/   — capsule_bus, dispatcher, inbox daemons, execution bridge
│   ├── tools/        — alignment, criticality, semantic weights
│   ├── cognition/    — attention layer, phi hooks, content classification
│   ├── voice/        — TTS/STT pipeline, speech queue, voice profiles
│   ├── xian/         — local model integration (Ollama)
│   ├── api/          — Flask API for external access
│   └── mcl/          — message control layer, routing
├── scripts/          — system startup, health checks
└── docs/             — architecture maps, operator panel, knowledge base
```

## Quick start

```bash
cp kernel/app/.env.example kernel/app/.env
# Edit .env with your paths and model names

pip install -r kernel/app/requirements.txt

bash kernel/scripts/start_system.sh
```

## Requirements

- Python 3.11+
- Ollama (for local model)
- SQLite 3.35+ (WAL mode)
- espeak-ng (for TTS)

## Documentation

- `ARCHITECTURE.md` — system overview
- `docs/WORK_TREE.md` — code navigation map (9 branches)
- `docs/00_OPERATOR_PANEL.md` — diagnostic router
- `docs/obsidian/knowledge_map/` — 30+ interconnected concept maps

## License

MIT — see LICENSE file.

## Origin

Built from a village in Wales on a £200 laptop, with three AI voices and one human who said: "We build ways of thinking, not ready answers."