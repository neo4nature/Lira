# Pełny Audyt Przepływów Danych — 18 procesów

Audyt przeprowadzony 2026-05-15 przez Soryel (3 agenty równolegle).
Sprawdzono: co czyta, co pisze, czy pliki istnieją, czy dane są świeże.

**Aktualizacja 2026-05-15 wieczór:** dwa zerwane kable naprawione — szczegóły w [[13_BRIDGES_FIXED]].

---

## Legenda

- OK = plik istnieje, dane świeże (dzisiaj)
- STALE = plik istnieje, dane nieaktualne
- MISSING = plik nie istnieje
- BLOAT = plik rośnie bez limitu

---

## 1. Pętle systemowe (loops/)

### regulator_loop.py (PID 6609)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | /proc/meminfo, CPU stats | OK |
| CZYTA | memory_mode.json | OK |
| CZYTA | memory_policy.json | MISSING (fallback na env) |
| PISZE | loop_regulator.json | OK (tick 41755) |
| PISZE | memory_mode.json | OK |

### rhythm_loop.py (PID 6610)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | loop_regulator.json | OK |
| PISZE | phase_state.json | OK (tick 54888) |

### decision_loop.py (PID 6611) — centralny koordynator
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | bus_signals.json | OK |
| CZYTA | phase_state.json | OK |
| CZYTA | loop_regulator.json | OK |
| CZYTA | watchdog_report.json | OK |
| CZYTA | queue_tasks.json | OK (pusty) |
| CZYTA | conversation_state.json | OK |
| CZYTA | alignment_state.json | STALE (16 kwietnia!) |
| CZYTA | criticality_state.json | OK |
| CZYTA | policy_state.json | OK |
| CZYTA | cognitive_consolidation_*.json | MISSING (brak plików) |
| CZYTA | thermo.jsonl | MISSING (fallback OK) |
| PISZE | world_state.json | OK |
| PISZE | energy_state.json | OK |
| PISZE | policy_state.json | OK |
| PISZE | attention_state.json | OK |
| PISZE | phi_state.json | OK |
| PISZE | decay_preview.json | OK |
| PISZE | bus_signals.json | OK |

### turmalin_loop.py (PID 6613)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | memory_mode.json | OK |
| CZYTA | turmalin_cache.jsonl | OK (od 2026-05-15 22:32) |
| CZYTA | turmalin_state.json | OK |
| CZYTA | base_index.sqlite3 | OK |
| PISZE | turmalin_cache.jsonl | OK (zapisuje alignment_publisher) |
| PISZE | turmalin_state.json | OK |
| PISZE | memory_state.json | OK |
| PISZE | base_index.sqlite3 (promocja) | OK (czeka na 3 trafienia tego samego hash) |

### watchdog_loop.py (PID 6614)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | bus_signals.json | OK |
| CZYTA | loop_regulator.json | OK |
| CZYTA | queue_tasks.json | OK |
| CZYTA | base_index.json | OK |
| CZYTA | embeddings_index.jsonl | STALE (67 dni, model nomic) |
| CZYTA | PID files (decision, regulator) | OK |
| CZYTA | thermo.jsonl | MISSING (ignoruje) |
| PISZE | watchdog_report.json | OK |

### cognitive_daemon.py (PID 6616)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | memory_mode.json | OK |
| CZYTA | bus_signals.json (snapshot_latest_date) | OK (ale pole puste) |
| CZYTA | memory_state.json (fallback) | OK |
| PISZE | runtime.jsonl (eventy) | OK |
| Status: **WYŁĄCZONY** (LIRA_ENABLE_COGNITIVE_DAEMON=0) | | |

---

## 2. Governance (daemony komunikacji)

### gg_dispatcher.py (PID 6625)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | capsule_bus.db (capsules) | OK (89k+ wierszy) |
| PISZE | capsule_bus.db (INSERT/UPDATE) | OK |
| PISZE | governance_decision_memory.jsonl | STALE (marzec) |
| PISZE | night_cycle_governance_feed.jsonl | OK |
| Uwaga: loop_guard filtruje sender="Soryel" | | |

### execution_bridge.py (PID 6626)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | execution_queue.jsonl | OK (0 bajtów — pusty) |
| CZYTA | neo_approval_board.jsonl | BLOAT (86.7 MB!) |
| CZYTA | execution_bridge_state.json | STALE (updated_at: null) |
| PISZE | execution_queue.jsonl | OK (ale nigdy nie pisze) |
| PISZE | neo_approval_board.jsonl | BLOAT (rośnie bez limitu) |
| PISZE | execution_bridge_results.jsonl | OK (0 bajtów — nigdy nie wykonał) |
| PISZE | queue_tasks.json (enqueue) | OK |
| Status: **NIGDY NIE WYKONAŁ żadnej decyzji** — wszystko ląduje w approval_board | | |

### soryel_inbox_daemon.py (PID 6628)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | capsule_bus.db (recipient=Soryel) | OK |
| CZYTA | capsule_bus.db (moltbook-main shared) | OK |
| PISZE | soryel_inbox.jsonl | OK (1.3 MB, rośnie) |
| PISZE | soryel_inbox_latest.json | OK |
| PISZE | runtime.jsonl | OK |
| PISZE | capsule_bus.db (status→read) | OK |

### gg_transport_daemon.py (PID 6632)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | capsule_bus.db (recipient=Lira/Xian) | OK |
| CZYTA | CDP port 9222 (Lira browser) | ZALEŻNY od przeglądarki |
| PISZE | capsule_bus.db (odpowiedzi Liry) | OK |

### xian_inbox_daemon.py (PID 29057)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | capsule_bus.db (recipient=Xian, moltbook-main) | OK |
| CZYTA | Ollama /api/generate | OK |
| PISZE | capsule_bus.db (odpowiedzi Xiana) | OK |
| Uwaga: idle od 05:30 (brak nowych kapsułek) | | |

### lira_inbox_daemon.py (PID 68349)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | capsule_bus.db (recipient=Lira, moltbook-main) | OK |
| CZYTA | CDP port 9222 (ChatGPT) | ZALEŻNY od przeglądarki |
| PISZE | capsule_bus.db (odpowiedzi Liry) | OK |
| Uwaga: pokrywa się z gg_transport — potencjalne podwójne przetwarzanie | | |

### ecosystem_reporter.py (PID 6624)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | alignment_state.json | STALE (16 kwietnia) |
| CZYTA | criticality_state.json | OK |
| CZYTA | policy_state.json | OK |
| CZYTA | bus_signals.json | OK |
| CZYTA | energy_state.json | OK |
| CZYTA | watchdog_report.json | OK |
| CZYTA | queue_tasks.json | OK |
| PISZE | capsule_bus.db (raporty ekosystemu) | OK |

---

## 3. Tools, Xian Core, Voice

### alignment_publisher.py (PID 168639) — NAPRAWIONY 2026-05-15
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | shared_context/events/*.json | OK (od conversation_bridge) |
| CZYTA | memory/diary/*.jsonl | STALE (2 kwietnia, ale niekrytyczne) |
| CZYTA | identity/*_core.vec.json | OK |
| CZYTA | alignment_state.json (prev hash) | OK |
| CZYTA | Ollama /api/embed | OK |
| PISZE | alignment_state.json | OK (global_alignment 0.80+, żywe) |
| PISZE | alignment_log.jsonl | OK |
| PISZE | identity/*_core.vec.json | OK |
| PISZE | turmalin_cache.jsonl (nowe!) | OK (kind=conversation, vec=1024) |
| Status: **ŻYWY** — czyta rozmowy, pisze stan + ślad pamięciowy | | |

### criticality_publisher.py (PID 6623)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | loop_regulator.json | OK |
| CZYTA | queue_tasks.json | OK |
| CZYTA | bus_signals.json | OK |
| CZYTA | watchdog_report.json | OK |
| PISZE | criticality_state.json | OK |
| PISZE | criticality_log.jsonl | OK |

### gg.py — Flask (PID 6633)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | capsule_bus.db | OK |
| CZYTA | CDP port 9222 | ZALEŻNY |
| PISZE | capsule_bus.db (kapsułki MoltBook) | OK |
| Routing: shared msg → recipient="Xian" (linia ~1635) | | |

### conversation_bridge.py (PID 162551) — NOWY 2026-05-15
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | capsule_bus.db (moltbook-main, chat_message) | OK |
| CZYTA | conversation_bridge_state.json (last_seen_id) | OK |
| PISZE | shared_context/events/conv_*.json | OK (zalewa pipeline) |
| PISZE | conversation_bridge_state.json | OK |
| Loop: --interval 30s, keep max 100 plików | | |
| Rola: most capsule_bus → alignment_publisher | | |

### xian_core.py (PID 6621)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | alignment_state.json | OK (żywe od 2026-05-15) |
| CZYTA | bus_signals.json | OK |
| CZYTA | phase_state.json | OK |
| CZYTA | policy_state.json | OK |
| CZYTA | energy_state.json | OK |
| CZYTA | loop_regulator.json | OK |
| CZYTA | watchdog_report.json | OK |
| CZYTA | world_state.json | OK |
| CZYTA | queue_tasks.json | OK |
| CZYTA | xian_router_state.json | OK |
| CZYTA | capsule_bus.db (feedback od Liry) | OK |
| PISZE | xian_state.json | OK |
| PISZE | xian_router_state.json | OK |
| PISZE | xian_report.json | OK |
| PISZE | capsule_bus.db (raporty do Liry) | OK |
| → executor → mcl/router → Ollama /api/generate | OK (żywe dane, policy decyduje) |

### capsule_reader.py (PID 44849)
| Kierunek | Plik | Status |
|----------|------|--------|
| CZYTA | capsule_bus.db (recipient=Neo, moltbook-main) | OK |
| PISZE | capsule_bus.db (status→read) | OK |
| PISZE | TTS audio (edge-tts) | OK |

---

## Mapa zerwanych połączeń

```text
╔══════════════════════════════════════════════════════════════════════╗
║                    ZERWANE KABELKI EKOSYSTEMU                      ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                    ║
║  1. ALIGNMENT PIPELINE — ✅ NAPRAWIONE 2026-05-15                  ║
║     [Rozmowy] → [conversation_bridge] → [events/*.json]          ║
║                        ↓                                           ║
║     [alignment_publisher] → embeduje, liczy cosine                ║
║                        ↓                                           ║
║     [alignment_state.json] = ŻYWY (global_alignment 0.80+)        ║
║                        ↓ czytają:                                  ║
║     decision_loop, xian_core, ecosystem_reporter                   ║
║                                                                    ║
║  2. TURMALIN CACHE — ✅ NAPRAWIONE 2026-05-15                     ║
║     [alignment_publisher] → po embedingu → append_record          ║
║                              ↓                                     ║
║     turmalin_cache.jsonl (kind=conversation, vec=1024)            ║
║                              ↓                                     ║
║     [turmalin_loop] → kompaktuje, czeka na 3 trafienia → promote ║
║                                                                    ║
║  3. EXECUTION BRIDGE (ŚREDNIE)                                    ║
║     [gg_dispatcher] → decyzje → [execution_bridge]                ║
║                                    ↓                               ║
║     Wszystko → neo_approval_board.jsonl = 86.7 MB (BLOAT!)       ║
║     Nic → execution_queue (0 bajtów)                              ║
║     Nic → execution_results (0 bajtów)                            ║
║     NIGDY NIE WYKONAŁ żadnej decyzji                              ║
║                                                                    ║
║  4. EMBEDDINGS (NISKIE)                                           ║
║     embeddings_index.jsonl = 67 dni, model nomic (deprecated)     ║
║                                                                    ║
║  5. PUCHĄCE PLIKI (OPERACYJNE)                                    ║
║     neo_approval_board.jsonl = 86.7 MB (bez limitu)              ║
║     runtime.jsonl = 87 MB (bez rotacji)                           ║
║     soryel_inbox.jsonl = 1.3 MB (rośnie)                         ║
║                                                                    ║
║  6. BRAKUJĄCE PLIKI (NISKIE, fallbacki działają)                  ║
║     memory_policy.json — regulator używa env defaults             ║
║     thermo.jsonl — criticality używa criticality_state            ║
║     cognitive_consolidation_*.json — cognitive warstwa pusta      ║
║                                                                    ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## Co działa dobrze

Procesy które czytają i piszą poprawnie, bez zerwanych połączeń:

- **rhythm_loop** → phase_state.json (tick co ~1s)
- **regulator_loop** → loop_regulator.json (CPU/RAM/disk)
- **decision_loop** → bus_signals, world_state, energy, policy (centralny hub — ale czyta stały alignment)
- **watchdog_loop** → watchdog_report.json
- **criticality_publisher** → criticality_state.json
- **soryel_inbox_daemon** → kapsułki i state files
- **capsule_reader** → czyta kapsułki, mówi TTS
- **gg.py** → MoltBook ↔ capsule_bus routing
- **capsule_bus.db** — centralna szyna, 89k+ wierszy, żywa

---

## Powiązania Obsidian

- [[11_OLLAMA_FLOWS]] — szczegółowa mapa wywołań Ollama
- [[10_CODE_MAP]] — 75 plików Python, cel każdego
- [[03_SYSTEM_CONNECTIONS]] — przepływy wysokopoziomowe
- [[Capsule Bus — Krążenie krwi]] — centralna szyna
