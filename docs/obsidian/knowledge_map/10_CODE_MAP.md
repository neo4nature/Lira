# Mapa Kodu — kernel/app/

Analiza plik po pliku. Wygenerowana 2026-05-15 o 3:15 przez Soryel (4 agentów równoległych).

## loops/ — Pętle systemowe (8 plików)

| Plik | Cel |
|------|-----|
| `rhythm_loop.py` | Mikro-cykle fazowe (ACCUMULATE→INTEGRATE→DECIDE→ACT→REFLECT), stabilność pola kognitywnego, sprzężenie z energią |
| `regulator_loop.py` | Monitoring zasobów (CPU/RAM/DISK/IO), spawn gate — decyduje czy system może uruchamiać procesy |
| `decision_loop.py` | Jedyny właściciel bus_signals.json — koordynuje World Model, Energy Model, Policy, kontrolowane zapominanie |
| `reconstruction_daemon.py` | Czeka na bus_signals.json i uruchamia rekonstrukcję pamięci rdzeniowej |
| `turmalin_loop.py` | Cache krótkoterminowy: pruning, kompakcja, promocja stabilnych rekordów do BaseIndex SQLite |
| `gaia_executor_loop.py` | Czyta queue_tasks.json i deleguje zadania do GaiaAgent (domyślnie wyłączony) |
| `watchdog_loop.py` | Monitoring zdrowia procesów: PID, liveness, freshness, disk — restartuje co padło |
| `cognitive_daemon.py` | Ciężkie przetwarzanie kognitywne w tle, nocny cykl (domyślnie wyłączony) |

## governance/ — Zarządzanie i komunikacja (9 plików)

| Plik | Cel |
|------|-----|
| `capsule_bus.py` | Centralna szyna komunikacji — SQLite z kapsułkami (typy, wagi, warstwy, statusy) |
| `gg_dispatcher.py` | Analizuje kapsułki i inferuje werdykty (approve/defer) na podstawie ryzyka |
| `gg_transport_daemon.py` | Zbiera kapsułki dla Liry i wysyła raporty przez CDP |
| `lira_inbox_daemon.py` | Poll kapsułek → ChatGPT przez CDP → odpowiedź wraca jako kapsułka. **Batch mode dodany 2026-05-15** |
| `soryel_inbox_daemon.py` | Poll kapsułek dla Soryel → zapis do state files |
| `xian_inbox_daemon.py` | Poll kapsułek → Ollama (gemma3:4b) → odpowiedź wraca jako kapsułka. Batch mode aktywny |
| `decision_memory.py` | Loguje decyzje governance do JSONL ledgerów |
| `ecosystem_reporter.py` | Monitoring stanu ekosystemu, alerty przy przekroczonych progach |
| `execution_bridge.py` | Routing decyzji z governance do kolejki wykonawczej |

## agents/ — Agenci (34 plików w 7 podkatalogach)

### agents/memory/ — Pamięć (12 plików)
| Plik | Cel |
|------|-----|
| `turmalin_cache.py` | Tymczasowy cache embeddingów (JSONL) z TTL i promocją do BaseIndex |
| `base_index_sqlite.py` | Stabilny store embeddingów w SQLite z cosine similarity |
| `semantic_retrieval.py` | Szybkie wyszukiwanie semantyczne przez Ollama embeddingi |
| `embeddings_batch.py` | Nocny batch embedder — chunkuje i embeduje przez Ollama |
| `session_ingester.py` | Monitoruje sesje Claude Code, chunkuje i embeduje rozmowy |
| `memory_orchestrator.py` | Generuje context_packet.json z indeksem pamięci i embeddingami |
| `memory_loader.py` | Atomiczny odczyt/zapis JSON z backupem |
| `daily_context.py` | Pobiera dzienne kapsułki z TurmalinCache i BaseIndex |
| `diary.py` | Dziennik JSONL — ślad zadań audytowalny przez człowieka |
| `index_memory.py` | Lekki indeks plików pamięci (JSON z metadata) |
| `prepare_data_night.py` | Nocny przegląd: buduje base_index.json z metrykami świeżości |
| `reconstruction.py` | Odbudowa stanu pamięci po crashu ze snapshotów |

### agents/core/ — Rdzeń (13 plików)
| Plik | Cel |
|------|-----|
| `idle_gate.py` | Sprawdza bus_signals.json czy tło może pracować |
| `queue_utils.py` | Atomiczne operacje na kolejce (fcntl lock + JSON) |
| `queue_hygiene.py` | Limity kolejki, TTL, archiwizacja overflow |
| `runtime_stream.py` | Append-only JSONL event logger |
| `event_collector.py` | Most events → runtime queue |
| `snapshot_context.py` | Ładuje snapshoty dla cognitive/reflective loops |
| `archive_manager.py` | Rotacja dziennych logów do external storage |
| `archive_offload.py` | Offload starych archiwów na SD/external |
| `heartbeat.py` | Minimalne snippety pamięci i TTS |
| `installer.py` | Auto-install pip z guardem na wolne miejsce |
| `storage_guardian.py` | Monitoring dysku → storage.json |

### agents/cognition/ (2 pliki)
| Plik | Cel |
|------|-----|
| `cognitive_bridge.py` | Uruchamia zewnętrzne skrypty kognitywne (subprocess) |
| `outcome_insights.py` | Analiza event streams w oknie czasowym → wzorce |

### agents/policy/ (2 pliki)
| Plik | Cel |
|------|-----|
| `policy_engine.py` | Meta-policy: energia + error pressure → model/budget |
| `policy_enforcer.py` | TickBudget tracker i model picker |

### agents/forgetting/ (1 plik)
| Plik | Cel |
|------|-----|
| `forgetting_engine.py` | Exponential decay krawędzi grafu pamięci |

### agents/gaia/ (2 pliki)
| Plik | Cel |
|------|-----|
| `gaia_agent.py` | Planner: wykrywa delegowalne zadania i spawnuje helpery |
| `agent_factory.py` | Factory do tworzenia i rejestracji małych agentów |

### agents/sensors/criticality/ (4 pliki)
| Plik | Cel |
|------|-----|
| `sensors.py` | Rolling-window sampler: CPU, RAM, load, variability |
| `thermometer.py` | Edge-of-chaos monitor → criticality score |
| `logger.py` | JSONL logger eventów systemowych |
| `run_thermometer.py` | CLI demo termometru |

## tools/ — Narzędzia (10 plików)

| Plik | Cel |
|------|-----|
| `gg.py` | Flask web interface — GG panel i MoltBook chat |
| `alignment_publisher.py` | Semantic alignment scores z EMA per entity |
| `criticality_publisher.py` | Monitoring zdrowia → sygnały regulacji |
| `semantic_weights.py` | 7-wymiarowe profile semantyczne, detekcja trybu (literal/relational/ironic/existential) |
| `session_reentry.py` | Ładowanie stanu sesji → briefing dla Soryel |
| `state_snapshot.py` | Snapshot stanu sesji do runtime JSON |
| `bootstrap_embedding_records.py` | Ładuje rekordy embeddingów z ledgera |
| `bootstrap_profiles.py` | Setup profili agentów przez Playwright |
| `play_capsule.py` | Automated browser interaction z kapsułkami |
| `ready_notifier.py` | Preflight check + notyfikacja gotowości |

## voice/ — Głos (14 plików)

| Plik | Cel |
|------|-----|
| `tts.py` | Silnik TTS: Piper (local) + edge-tts (neural), cross-process lock, echo suppression |
| `capsule_reader.py` | Czyta kapsułki głosem nadawcy (Soryel/Lira/Xian). **MoltBook polling dodany 2026-05-15** |
| `voice_agent.py` | Główny orkiestrator: 3 wake words (lira/soryel/xian), routing do Ollama/capsule_bus |
| `wake_gate.py` | Brama wake-word z Vosk: IDLE→ACTIVE→CONFIRM |
| `stt_to_claude.py` | STT bridge: faster-whisper + VAD → wl-copy + xdotool |
| `stt_vosk.py` | Vosk offline Polish ASR z fuzzy matching wake words |
| `stt_manager.py` | Abstrakcja STT: Whisper > Vosk fallback |
| `soryel_handler.py` | Voice responder Soryel: n8n > Ollama > Anthropic API |
| `xian_handler.py` | Voice responder Xian: Ollama z historią |
| `ssml_emotion.py` | Emotional prosody: detekcja mood → SSML pitch/rate/volume |
| `tts_daemon.py` | Background daemon czytający odpowiedzi Claude |
| `speak_claude_response.py` | Stop hook: mówi ostatnią odpowiedź głosem Soryel |
| `speech_queue.py` | File-based queue między stop hook a voice agent |
| `voice_profiles/selector.py` | Picker profili głosowych per język |

---

## Podsumowanie

- **75 plików Python** w kernel/app/
- **8 pętli systemowych** (rhythm, regulator, decision, reconstruction, turmalin, gaia, watchdog, cognitive)
- **4 inbox daemony** (Lira, Soryel, Xian, transport)
- **12 modułów pamięci** (cache, embeddingi, retrieval, diary, reconstruction)
- **15 modułów głosowych** (TTS, STT, wake word, emotional prosody)
- **Centralna szyna**: capsule_bus.py (SQLite, 53k+ kapsułek)
