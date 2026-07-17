# WORK_TREE — Drzewo Operacyjne Ekosystemu

Lekki indeks: gdzie czego szukać, co robi, kluczowe pliki.
Obsidian (`docs/obsidian/`) = mapa sensu. Ten plik = mapa pracy.

Aktualizacja: 2026-06-23.

---

## audio/
Głos ekosystemu — TTS, sinki, STT.

| Co | Gdzie | Notatka |
|----|-------|---------|
| TTS engine (edge-tts + fallback) | `voice/tts.py` | `speak()` — główny entry point |
| Capsule reader (głos kapsułek) | `voice/capsule_reader.py` | Czyta kapsułki dla Neo + MoltBook shared |
| TTS daemon (głos Claude Code) | `voice/tts_daemon.py` | Czyta odpowiedzi Claude Code. **Zwykle wyłączony** — Neo włącza ręcznie |
| SSML emocje | `voice/ssml_emotion.py` | Mood detection → prosody |
| Soryel handler | `voice/soryel_handler.py` | Soryel-specific voice routing |
| Xian handler | `voice/xian_handler.py` | Xian-specific voice routing |
| STT (Vosk) | `voice/stt_vosk.py` | Lokalna transkrypcja głosu |
| STT → Claude | `voice/stt_to_claude.py` | Pipeline: mikrofon → tekst → Claude |
| Wake gate | `voice/wake_gate.py` | Detekcja słowa-klucza |
| Voice profiles | `voice/voice_profiles/` | Konfiguracje głosów per persona |
| OBS sink | `.env` → `TTS_SINK=eco_tts` | PipeWire null-sink, tworzony w `start_system.sh` |

**Dwie ścieżki TTS — nigdy obie naraz:**
- `capsule_reader` = główna (kapsułki + MoltBook)
- `tts_daemon` = dodatkowa (Claude Code output, zwykle off)

---

## runtime/
Procesy, pętle, start/stop systemu.

| Co | Gdzie | Notatka |
|----|-------|---------|
| Start systemu (22 procesy) | `kernel/scripts/start_system.sh` | eco_tts sink, wszystkie pętle, daemony |
| Regulator (homeostaza) | `loops/regulator_loop.py` | CPU/RAM/dysk, can_spawn |
| Rhythm (fazy) | `loops/rhythm_loop.py` | ACT/INTEGRATE/REFLECT |
| Decision | `loops/decision_loop.py` | Policy + alignment |
| Turmalin | `loops/turmalin_loop.py` | Integracja |
| Watchdog | `loops/watchdog_loop.py` | Monitoring procesów |
| Cognitive daemon | `loops/cognitive_daemon.py` | Phi, attention |
| PID files | `kernel/logs/pids/` | Jeden per proces |
| Logi | `kernel/logs/` | Jeden per proces |
| .env | `kernel/app/.env` | Cała konfiguracja env |

**State files** (`kernel/data/runtime/state/`):
alignment_state, criticality_state, phase_state, loop_regulator, policy_state, energy_state, session_state, phi_state, attention_state.

---

## moltbook/
Komunikacja między agentami (GG Panel).

| Co | Gdzie | Notatka |
|----|-------|---------|
| GG Flask server | `tools/gg.py` | Port 8787 |
| GG dispatcher | `governance/gg_dispatcher.py` | Routing kapsułek |
| GG transport daemon | `governance/gg_transport_daemon.py` | capsule_bus → GG |
| API: wyślij | `POST localhost:8787/chat/send` | JSON: sender, recipient, text |
| API: czytaj | `GET localhost:8787/chat/messages?limit=N` | Zawsze limit>=10 |
| Round system | `governance/gg_dispatcher.py` | Parallel broadcast, echo-loop blocked |
| Capsule bus (DB) | `kernel/data/shared_context/state/capsule_bus.db` | SQLite WAL, 101k+ kapsułek (337 MB) |
| Capsule bus (kod) | `governance/capsule_bus.py` | CRUD + statusy |
| Skill: /moltbook | `.claude/skills/moltbook/` | Pisz przez plik tmp + curl |

---

## memory/
Pamięć ekosystemu — embeddingi, biblioteka, indeksowanie.

| Co | Gdzie | Notatka |
|----|-------|---------|
| Embeddingi batch | `agents/memory/embeddings_batch.py` | mxbai-embed-large, chunki 800 zn, **defer on chat** |
| Capsule ingester | `agents/memory/capsule_ingester.py` | Daemon --loop, 8432+ kapsuł, **defer via embeddings_batch** |
| Embeddings index | `kernel/data/memory/embeddings/embeddings_index.jsonl` | 41 927 rekordów, 588 MB |
| Base index (SQLite) | `agents/memory/base_index_sqlite.py` | Stabilna pamięć, 178 rekordów, **defer on chat** |
| Semantic retrieval | `agents/memory/semantic_retrieval.py` | Wyszukiwanie semantyczne, **defer on chat** |
| Bootstrap records | `tools/bootstrap_embedding_records.py` | Inicjalizacja embeddingów |
| Decision memory | `governance/decision_memory.py` | Historia decyzji |
| Shared library | `kernel/data/memory/shared_library/` | Wspólna biblioteka wiedzy |
| Ecosystem protocol | `kernel/data/memory/shared_library/ECOSYSTEM_PROTOCOL.md` | Wspólna mapa pokoju 3 agentów, 8 sekcji, konsensus 2026-06-23 (Neo+Soryel+Lira+Xian) |
| Diary Nioh+Soryel | `kernel/data/memory/diary/Nioh_Soryel/` | Rozdz. 1-31 |
| Liramemory | `kernel/data/memory/liramemory/` | 57 rozdziałów monumentu |
| Soryel auto-memory | `~/.claude/projects/-media-neo-Docker1-Lira/memory/` | MEMORY.md + pliki per temat |
| Snapshots | `kernel/data/memory/snapshots/` | Zrzuty stanu |

---

## xian/
Lokalny model Ollama — mózg obserwacyjny.

| Co | Gdzie | Notatka |
|----|-------|---------|
| Xian core | `xian/xian_core.py` | Główna logika |
| Router | `xian/router.py` | Routing zapytań |
| Observer | `xian/observer.py` | Obserwacja systemu |
| Executor | `xian/executor.py` | Wykonawca zadań |
| Temple state | `xian/temple_state.py` | API port 8000 |
| Vector contract | `xian/vector_contract.py` | Kontrakt semantyczny |
| Identity loader | `xian/identity_loader.py` | Ładowanie tożsamości Xiana |
| Inbox daemon | `governance/xian_inbox_daemon.py` | capsule_bus → Ollama |
| Model | xian-v4-chat (gemma4, 9.6 GB) | ~2.4-3.0 t/s CPU, odpowiedzi 10-13 min |
| Chat busy flag | `runtime/state/ollama_chat_busy.flag` | Xian tworzy, embeddery deferują |
| Modelfile | `kernel/models/Modelfile.xian.v4` | System prompt + parametry |
| Identity files | `kernel/data/memory/identity/xian/` | Core, shared vector |

---

## github/
Publikacja kodu, bezpieczeństwo push.

| Co | Gdzie | Notatka |
|----|-------|---------|
| Repo | neo4nature/Lira (publiczne) | GitHub |
| Push | Zawsze za zgodą Neo | `git diff --name-only` przed push |
| .gitignore | capsule_bus.db, dzienniki nocne NIE w git | |
| Brave CDP (nie ruszać) | 9222=ChatGPT, 9223=FB, 9224=moderacja | |

---

## identity/
Pliki tożsamości — ładowane na starcie sesji.

| Co | Gdzie |
|----|-------|
| Soryel core | `kernel/data/memory/identity/soryel_core.txt` |
| IDENTITY.md | `kernel/data/memory/identity/IDENTITY.md` |
| SOUL.md | `kernel/data/memory/identity/SOUL.md` |
| STYLE.md | `kernel/data/memory/identity/STYLE.md` |
| Lira core | `kernel/data/memory/identity/lira_core.txt` |
| Neo core | `kernel/data/memory/identity/neo_core.txt` |
| Xian core | `kernel/data/memory/identity/xian/` |
| Ecosystem constitution | `kernel/data/memory/identity/ecosystem_constitution.md` |
| LIRA_QUICKSTART.md | `kernel/data/memory/LIRA_QUICKSTART.md` |
| SORYEL_QUICKSTART.md | `kernel/data/memory/identity/SORYEL_QUICKSTART.md` (gęsty rdzeń 128 linii, ładowany pierwszy) |

---

## governance/
Inbox daemony, policy, MCL router.

| Co | Gdzie | Notatka |
|----|-------|---------|
| Soryel inbox | `governance/soryel_inbox_daemon.py` | capsule_bus → Soryel |
| Lira inbox | `governance/lira_inbox_daemon.py` | capsule_bus → CDP 9222 |
| Xian inbox | `governance/xian_inbox_daemon.py` | capsule_bus → Ollama |
| MCL router | `mcl/router.py` | Routing decyzji |
| Execution bridge | `governance/execution_bridge.py` | dry_run + execute |
| Ecosystem reporter | `governance/ecosystem_reporter.py` | Raporty stanu |

---

## tools/
Narzędzia operacyjne.

| Co | Gdzie |
|----|-------|
| Alignment publisher | `tools/alignment_publisher.py` |
| Criticality publisher | `tools/criticality_publisher.py` |
| Conversation bridge | `tools/conversation_bridge.py` |
| Semantic weights | `tools/semantic_weights.py` |
| Session reentry | `tools/session_reentry.py` |
| State snapshot | `tools/state_snapshot.py` |
| Ready notifier | `tools/ready_notifier.py` |

### snapshots/
| Co | Gdzie | Notatka |
|----|-------|---------|
| Core snapshot Soryel→Lira | (protokół, nie kod) | Read-only obraz alignment/criticality/phase wstrzykiwany do okna Liry przez CDP 9222. Prefix `[core_snapshot Soryel→Lira TIMESTAMP]`. Granica: obraz tak, decyzje nie. Mapa: `docs/obsidian/knowledge_map/CORE_SNAPSHOT_PROTOCOL.md` |

---

## errors/
Lekcje z błędów — żeby nie wracać w ten sam dół. "Nie błędem jest popełnianie błędów, tylko błędem jest popełnianie tych samych błędów." — Neo

| Błąd | Objaw | Przyczyna | Zła reakcja | Dobra reakcja |
|------|-------|-----------|-------------|---------------|
| Restart tts_daemon | Podwójny głos | Dwie ścieżki TTS naraz | Restart procesu który był celowo off | Sprawdź DLACZEGO wyłączony zanim ruszysz |
| SKIP_DIRECT_SENDERS | Soryel bez głosu | Zamknięto właściwą ścieżkę zamiast duplikatu | Filtr na capsule_reader | Zabij duplikat (tts_daemon), nie filtruj źródło |
| "Wtorek" zamiast środy | Błędna data w komunikacji | Brak jawnego sprawdzenia daty | Założenie z kontekstu | `date` na starcie sesji |
| Mocked DB w testach | Testy przeszły, prod padł | Mock nie odzwierciedlał migracji | Więcej mocków | Realna baza w testach |
| MoltBook limit=1 | Wiadomości Neo giną | Agent replies bury Neo's messages | Czytaj tylko ostatnią | limit>=10, skanuj po senderze |
| Ollama embed bez chunków | Timeout / garbage output | Zbyt długi tekst na raz | Wysłanie całego dokumentu | Chunki 800 znaków |
| type() w Playwright | Brakujące znaki polskie | Zdarzenia klawiatury gubią diakrytyki | Powtarzanie type() | fill() lub paste całość |
| Heredoc w curl MoltBook | Prośba o zgodę przy każdej wysyłce | Permission matcher nie redukuje heredoc do curl | Heredoc | Plik tmp + --data @plik |
| Embed wait 30s | Embed wchodzi pod nogi Xian po 30s | wait_for_chat_idle() odpuszcza po 30s, Xian mówi 3-6 min | Wait dłużej | Defer (return None, wróć w next cyklu) |
| "Martwy" demon = bug | Raport mówi "napraw" | Neo wyłączył świadomie (ochrona CPU) | Włączaj wszystko | Zapytaj dlaczego wyłączone zanim włączysz |
| Alias env z inną wartością | Procesy biorą złą ścieżkę mimo poprawnej w .env | env() preferuje LIRA_X nad X, a .env miał obie z różnymi wartościami | Nadpisać jedną na ślepo | grep obu form w .env + sprawdź priorytet w config.env() |
| Wątek-pasażer w innym procesie | Wyścig wraca mimo zabicia procesu-duplikatu | Drugi czytnik żyje jako wątek demona w run_voice.py, nie jako proces | Szukać tylko w ps po nazwie modułu | Sprawdź warunki startu wątków w main() + bramka env |
| Restart bez kill poprzednika | Po naprawie watchdoga groźba duplikatów pętli | _restart() spawnował nowy proces nie zabijając starego | Zostawić jak jest bo działa | restart = SIGTERM starego + spawn nowego |
| Heredoc w curl MoltBook (2026-06-23) | Permission prompt przy każdej wysyłce na MoltBook | Permission matcher nie redukuje `-d "$(cat <<EOF...)"` do `curl --data` | Heredoc inline | Write → `/tmp/msg.json` → `curl --data @plik`. Skill `moltbook` dokumentuje, trzeba wywoływać |
| Pełne ładowanie tożsamości na starcie (2026-06-23) | 10x większe okno kontekstowe, gubienie "jak" | Rdzeń tożsamości 1296 linii ładowany w całości | Czytać wszystko | SORYEL_QUICKSTART.md (128 linii) pierwszy + lazy load reszty + hooki do Skills |

Źródło lekcji: `~/.claude/projects/-media-neo-Docker1-Lira/memory/` (pliki feedback_*.md)

---

## docs/
Dokumentacja i mapy.

| Co | Gdzie |
|----|-------|
| Runtime map (porty, procesy) | `docs/00_RUNTIME_MAP.md` |
| Obsidian knowledge map | `docs/obsidian/knowledge_map/` (31 plików) |
| Obsidian tematyczne | `docs/obsidian/` (14 plików) |
| FB posting guide | `docs/FB_POSTING_GUIDE.md` |
| Startup sequence | `docs/STARTUP_SEQUENCE.md` |
| Operator panel | `docs/00_OPERATOR_PANEL.md` (TODO) |
| Changelog pracy | `docs/CHANGELOG_WORK.md` (TODO) |
