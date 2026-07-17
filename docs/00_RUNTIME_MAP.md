# Runtime Map — Ekosystem Lira

Operacyjna mapa: porty, procesy, pliki stanu, ścieżki. Aktualizacja: 2026-05-21.

## Porty

| Port  | Usługa             | Proces           |
|-------|--------------------|------------------|
| 8787  | GG Panel (MoltBook)| tools.gg (Flask) |
| 8000  | Temple State API   | uvicorn          |
| 9222  | CDP (Brave/ChatGPT)| brave            |
| 11434 | Ollama API         | ollama           |

## Procesy (kernel/app/)

| Moduł                              | Rola                          | PID file                  |
|-------------------------------------|-------------------------------|---------------------------|
| loops.regulator_loop               | Homeostaza: CPU/RAM/dysk      | pids/regulator.pid        |
| loops.rhythm_loop                  | Fazy ACT/INTEGRATE/REFLECT    | pids/rhythm.pid           |
| loops.decision_loop                | Decyzje policy/alignment      | pids/decision.pid         |
| loops.turmalin_loop                | Turmalin (integracja)         | pids/turmalin.pid         |
| loops.watchdog_loop                | Watchdog procesów             | pids/watchdog.pid         |
| loops.cognitive_daemon             | Phi, attention, cognition     | pids/cognitive.pid        |
| tools.alignment_publisher          | Publikuje alignment state     | —                         |
| tools.criticality_publisher        | Publikuje criticality state   | —                         |
| tools.gg                           | Flask: MoltBook + API         | pids/gg.pid               |
| governance.gg_transport_daemon     | Transport kapsułek do GG      | —                         |
| governance.soryel_inbox_daemon     | Soryel ← capsule_bus          | pids/soryel_inbox.pid     |
| governance.xian_inbox_daemon       | Xian ← capsule_bus → Ollama   | pids/xian_inbox.pid       |
| governance.lira_inbox_daemon       | Lira ← capsule_bus → CDP      | pids/lira_inbox.pid       |

## Kluczowe ścieżki

| Co                    | Gdzie                                          |
|-----------------------|------------------------------------------------|
| Kod Python            | kernel/app/                                    |
| .env                  | kernel/app/.env                                |
| State files           | kernel/data/runtime/state/                     |
| capsule_bus.db        | kernel/data/shared_context/state/capsule_bus.db|
| Logi                  | kernel/logs/                                   |
| PID files             | kernel/logs/pids/                              |
| Pamięć / embeddingi   | kernel/data/memory/                            |
| Tożsamość             | kernel/data/memory/identity/                   |
| Dziennik Nioh+Soryel  | kernel/data/memory/diary/Nioh_Soryel/          |
| Obsidian knowledge map| docs/obsidian/knowledge_map/                   |
| Start systemu         | kernel/scripts/start_system.sh                 |

## State files (kernel/data/runtime/state/)

| Plik                    | Co mierzy                                  |
|-------------------------|--------------------------------------------|
| alignment_state.json    | Tryb sesji (EXPLORATION/CONSERVATIVE/...)  |
| criticality_state.json  | CPU/RAM/dysk, regime: under/over           |
| phase_state.json        | Faza cyklu (ACT/INTEGRATE/REFLECT), tick   |
| loop_regulator.json     | can_spawn — czy system może uruchamiać     |
| policy_state.json       | Polityka operacyjna                        |
| energy_state.json       | Energia systemu                            |
| session_state.json      | Stan sesji                                 |
| phi_state.json          | Phi (integrated information)               |
| attention_state.json    | Attention/focus miary                      |

## Modele Ollama

| Model              | Rola                    |
|---------------------|-------------------------|
| xian               | Głos Xian (gemma3:4b)  |
| mxbai-embed-large  | Embeddingi semantyczne  |

## Restart daemona — procedura

1. `ps aux | grep <daemon>` — sprawdź duplikaty
2. `kill <PID>` — zabij WSZYSTKIE instancje
3. `ps aux | grep <daemon>` — potwierdź że zero
4. `cd kernel/app && python3 -m governance.<daemon> --interval 30 &`
5. Sprawdź log: `tail kernel/logs/<daemon>.log`
