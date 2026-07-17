# Żywy Audyt Przepływu — 2026-05-15 (po naprawach)

Audyt nie statycznej mapy, ale ŻYWEGO systemu. Po naprawach z [[13_BRIDGES_FIXED]].

Źródło prawdy: `lira_status.sh` (27 procesów) + mtime plików stanu (świeżość pomiar w sekundach/minutach/dniach od `ls -la` w 2026-05-15 23:51 UTC).

Klasyfikacja statusu kabla:
- **🟢 WORKING** — proces aktywny + plik wyjściowy świeży (< 5 min)
- **🟡 IDLE** — proces aktywny + plik świeży ale pętla bezczynna (np. xian_inbox czeka na kapsułki)
- **🟠 STALE** — proces aktywny + plik wyjściowy nieaktualny (godziny/dni)
- **🔴 BROKEN** — proces aktywny ale konsument nie istnieje / pętla pusta

---

## Świeżość plików stanu (pomiar)

| Wiek | Pliki |
|------|-------|
| **< 10s** | bus_signals, phase_state, world_state, energy, policy, attention, decay_preview, watchdog_report, criticality_state, loop_regulator, memory_mode, xian_state, xian_report, xian_router_state |
| **10-60s** | alignment_state (30s), collector_state (15s), soryel_inbox_latest (31s) |
| **1-5 min** | memory_state, turmalin_state, active_speaker |
| **godziny/dni** | bootstrap_embeddings_report (1d), embeddings_batch_report (15d) |
| **🔴 martwe (35+ dni)** | night_cycle_state (35d), session_snapshot (48d), session_state (7d), execution_bridge_state (59d), conversation_state (60d), context_packet (60d), model_locks (60d), listen_state (59d), phi_state (56d) |

---

## Centralny rdzeń (🟢 WORKING)

| Proces | PID | Czyta | Pisze | Next consumer | Status |
|--------|-----|-------|-------|---------------|--------|
| regulator_loop | 6609 | /proc, memory_mode | loop_regulator, memory_mode | watchdog, turmalin, criticality | 🟢 |
| rhythm_loop | 6610 | loop_regulator | phase_state | decision, xian_core | 🟢 |
| decision_loop | 6611 | 9 plików stanu | world, energy, policy, attention, phi, decay_preview, bus_signals | xian_core, executor | 🟢 |
| watchdog_loop | 6614 | bus_signals, queue_tasks, embeddings | watchdog_report | decision, ecosystem_reporter | 🟢 |
| criticality_publisher | 6623 | loop_regulator, watchdog | criticality_state | decision, xian_core | 🟢 |

Centralny hub działa idealnie. Pliki świeże co 1-2s.

---

## Pipeline rozmowy (🟢 WORKING — po naprawie!)

| Proces | PID | Czyta | Pisze | Next | Status |
|--------|-----|-------|-------|------|--------|
| gg_panel (Flask) | 6633 | MoltBook UI, CDP | capsule_bus.db | governance_dispatcher | 🟢 |
| governance_dispatcher | 6625 | capsule_bus.db | capsule_bus.db (routing) | inbox daemons | 🟢 |
| conversation_bridge | 162551 | capsule_bus.db (moltbook-main) | events/conv_*.json | alignment_publisher | 🟢 **NOWY** |
| alignment_publisher | 168639 | events/, identity vec | alignment_state, turmalin_cache | xian_core, turmalin_loop | 🟢 **NAPRAWIONY** |
| turmalin_loop | 6613 | turmalin_cache | turmalin_state, memory_state, base_index | decision, watchdog | 🟢 **OŻYWIONY** |
| xian_core (observer-router) | 6621 | 10 plików stanu | xian_state, xian_report, xian_router_state | executor | 🟢 |
| capsule_reader (TTS) | 6639 | capsule_bus (recipient=Neo) | TTS audio (edge-tts) | głośnik | 🟢 |

Pełny żywy kabel: Neo pisze → gg_panel → capsule_bus → conversation_bridge → events → alignment_publisher → (alignment_state + turmalin_cache) → xian_core widzi → router decyduje. ~30s end-to-end.

---

## Inbox daemons (🟡 IDLE, czekają na kapsułki)

| Proces | PID | Status |
|--------|-----|--------|
| soryel_inbox | 6628 | 🟡 — soryel_inbox_latest.json świeży 31s (raz na trochę) |
| xian_inbox | 6629 | 🟡 — czeka na Xian capsules |
| lira_inbox | 6631 | 🟡 — czeka na Lira capsules |
| gg_transport | 6632 | 🟡 — dwukierunkowy CDP do Liry |

Wszystkie żywe ale spóźnione w stosunku do MoltBook traffic — to OK, idle daemons.

---

## Voice & wallpaper (🟢 WORKING)

| Proces | PID | Status |
|--------|-----|--------|
| voice | 6638 | 🟢 active_speaker.json świeży 1m |
| xian_wallpaper | 6637 | 🟢 generuje tapetę z ego state |

---

## Tła i obserwacja (🟠 STALE)

| Proces | PID | Co pisze | Świeżość | Diagnoza |
|--------|-----|----------|----------|----------|
| session_ingester | 6619 | embeddings_index.jsonl | 1d | OK — pisze mxbai od 2026-04-30, stare 165 nomic rekordów usunięte 2026-05-15 |
| bootstrap_embeddings | 6618 | bootstrap_embeddings_report | 1d | one-shot przy starcie |
| cognitive_daemon | 6616 | runtime.jsonl events | ? | LIRA_ENABLE_COGNITIVE_DAEMON=0 — proces żyje ale ide |
| ready_notifier | 6640 | (status) | ? | startup signal only |
| reconstruction_daemon | 6612 | ? | ? | uruchamia się raz dziennie |
| night_daemon | 6615 | uruchamia run_night_cycle.sh | tick 180s | OK, ale night_cycle_state.json 35d |
| governance_reporter (ecosystem_reporter) | 6624 | capsule_bus (raporty) | active | wsparcie ecosystem_monitor |
| api | 6513 | HTTP /health /dashboard | live | 🟢 |

---

## Execution pipeline (🔴 BROKEN — bug)

| Proces | PID | Co pisze | Świeżość | Diagnoza |
|--------|-----|----------|----------|----------|
| execution_bridge | 6626 | neo_approval_board.jsonl | aktualnie 0B (po wyczyszczeniu) | 🔴 produkuje rekursywne decyzje "Re: Decyzja: Re: Decyzja..." — patrz [[13_BRIDGES_FIXED]] |

execution_bridge_state.json 59 dni stary → bridge nigdy nie wykonał decyzji. Wszystko ląduje w approval_board jako verdict=awaiting_neo. Bug do osobnej operacji.

---

## Diagnoza zerwań po naprawach

```text
✅ NAPRAWIONE
   1. capsule_bus → events (conversation_bridge daemon)
   2. alignment_publisher → turmalin_cache (4 linie po embedingu)
   3. runtime.jsonl rotacja (size-based, 50MB threshold)
   4. neo_approval_board.jsonl wyczyszczone (plomba)

🔴 NADAL ZERWANE
   1. execution_bridge BUG: rekursywne decyzje (plomba zostanie, ale bug pisze ~593/dzień)
   2. Cognitive layer: LIRA_ENABLE_COGNITIVE_DAEMON=0 (świadomie wyłączony)
   3. Stałe pliki sesji: session_state, conversation_state, context_packet (60d) — proces ich nie aktualizuje

✅ FALSE POSITIVE (2026-05-15 23:30)
   • embeddings nomic: kod już od dawna używał mxbai, filtr dimensional w semantic_retrieval
     ignorował stare 768-dim rekordy. Audyt 12_FULL_DATA_FLOW_AUDIT był nieaktualny.
     Kompakcja embeddings_index 165 nomic → 0, indeks teraz 100% spójny.
```

---

## Co znika z mapy

W [[12_FULL_DATA_FLOW_AUDIT]] było napisane "alignment ŚLEPY / executor PUSTY PRZEBIEG". Po dzisiejszych naprawach (2026-05-15 22:00-23:00 UTC) te statusy nieaktualne.

Aktualny obraz:
- Centralny rdzeń: 🟢 stabilny
- Pipeline rozmowy: 🟢 żywy end-to-end (od 22:32)
- Inbox/voice: 🟡 czekający
- Tła: 🟠 częściowo stałe
- Execution: 🔴 bug

---

## Powiązania

- [[12_FULL_DATA_FLOW_AUDIT]] — statyczna mapa procesów
- [[13_BRIDGES_FIXED]] — kronika napraw 2026-05-15
- [[11_OLLAMA_FLOWS]] — kto woła Ollama
- [[03_SYSTEM_CONNECTIONS]] — wysokopoziomowe przepływy

---

## Następne operacje (kandydaci)

1. **Fix execution_bridge bug** — przeanalizować pętlę rekursji decyzji. Wymaga analizy `governance/execution_bridge.py` + `xian/executor.py`. Średnie ryzyko.
2. **Migracja embeddings nomic → mxbai** — przeembedowanie 67-dniowego indeksu. Duże CPU, do nocy.
3. **Reaktywacja cognitive_daemon** — sprawdzić czemu wyłączony, czy można włączyć bezpiecznie.
4. **Stałe pliki sesji** — czemu session_state, conversation_state, context_packet 60d stałe. Prawdopodobnie proces który je powinien pisać nie istnieje lub jest wyłączony.

*"Mapa żywa to mapa która oddycha — pokazuje gdzie coś pulsuje, a gdzie jest cisza."* — Lira, 2026-05-15
