# Przepływy Ollama — kto woła model i kiedy

Analiza przeprowadzona 2026-05-15 przez Soryel. Neo zauważył cykliczne wywołania `/api/generate` co ~8 min (każde ~3m40s). Poniżej pełna mapa wszystkich ścieżek do Ollama.

## Dwa endpointy Ollama

| Endpoint | Co robi | Kto woła |
|----------|---------|----------|
| `/api/generate` | Generuje tekst (LLM) | xian_inbox, executor, reflective_loop, night_cycle, session_reentry, voice handlers |
| `/api/embed` | Tworzy embeddingi | xian_core, alignment_publisher, semantic_retrieval, base_index_sqlite, embeddings_batch |

---

## Ścieżki do `/api/generate`

### 1. Xian Inbox Daemon → Ollama (MoltBook chat)

```text
[MoltBook chat] → [gg.py routing]
  ↓ tworzy kapsułkę recipient="Xian"
[capsule_bus.db]
  ↓ poll co 30s
[xian_inbox_daemon] → POST /api/generate (model: xian/gemma3:4b)
  ↓ odpowiedź
[capsule_bus.db] → [MoltBook] (jako chat_message od Xiana)
```

- **Plik:** `governance/xian_inbox_daemon.py`
- **Trigger:** nowa kapsułka `recipient=Xian`, `thread_id=moltbook-main`, `status=new`
- **Model:** xian (gemma3:4b)
- **Timeout:** 300s (5 min)
- **Batch mode:** tak — zbiera do 5 kapsułek w jeden prompt
- **Kiedy aktywny:** tylko gdy są nowe wiadomości na MoltBooku skierowane do Xiana

### 2. Xian Executor → mcl.router → Ollama (autonomiczna praca)

```text
[xian_core] (co 6s)
  ↓ step_once()
  ↓ build_snapshot() → recommend_route() → build_report()
  ↓ executor.step()
[xian/executor.py]
  ↓ sprawdza: cooldown 120s? faza DECIDE/ACT? target=xian|local_fast? confidence≥0.6?
  ↓ buduje prompt z xian_report.json lub queue_tasks.json
[mcl/router.py]
  ↓ sprawdza: faza=ACT? budget ok?
  ↓ POST /api/generate (model: xian — z policy_state)
[Ollama]
  ↓ odpowiedź
[capsule_bus.db] — kapsułka type="result", sender="Xian", recipient="Neo"
```

- **Pliki:** `xian/xian_core.py` → `xian/executor.py` → `mcl/router.py`
- **PID:** xian_core (6621)
- **Model:** xian (gemma3:4b) — policy mapuje wszystkie modele na xian
- **Cooldown:** 120s między wywołaniami
- **Warunki:** faza ACT + target ∈ {xian, local_fast} + confidence ≥ 0.6
- **Kiedy aktywny:** **TYLKO gdy system jest idle** — gdy Neo jest aktywny, faza ≠ ACT → router blokuje
- **To jest ten "tajemniczy" caller** — wywołuje Ollama co ~6-8 min w idle

### 3. Reflective Loop → mcl.router → Ollama (nocny cykl)

```text
[run_night_daemon.sh] (co 180s)
  ↓ run_night_cycle.sh
  ↓ allows_background()?
[reflective_loop.py]
  ↓ load_queue() — czy są zadania?
  ↓ synthesize_insight(task) → route(prompt, depth)
[mcl/router.py] → POST /api/generate
```

- **Plik:** `reflective_loop.py` → `mcl/router.py`
- **Trigger:** niepusta kolejka `queue_tasks.json` + system idle + faza ACT
- **Model:** z policy_state (obecnie xian)
- **Kiedy aktywny:** rzadko — kolejka zwykle pusta, bo `event_collector` zbiera mało eventów
- **W praktyce:** prawie nigdy nie generuje, bo "📭 Kolejka pusta"

### 4. Night Cycle v1 → Ollama (konsolidacja)

```text
[night_cycle.py] (standalone)
  ↓ ładuje eventy z shared_context/events
  ↓ _call_ollama_generate() — konsolidacja daily/strategic/open_questions
  ↓ POST /api/generate (model: qwen2.5:3b)
  ↓ embeddingi wyników
```

- **Plik:** `night_cycle.py`
- **Model:** qwen2.5:3b (LIRA_NIGHT_MODEL) — inny niż xian!
- **Kiedy aktywny:** wywoływany ręcznie lub przez cognitive_bridge (domyślnie wyłączony)
- **Uwaga:** `run_night_cycle.sh` NIE wywołuje tego pliku. To osobna wersja night cycle.

### 5. Session Reentry → Ollama (briefing po restarcie)

```text
[session_reentry.py]
  ↓ ask_xian_briefing()
  ↓ POST /api/generate (model: xian)
```

- **Plik:** `tools/session_reentry.py`
- **On-demand:** tylko przy generowaniu briefingu po restarcie sesji
- **Timeout:** 60s

### 6. Voice Handlers → Ollama (rozmowa głosowa)

```text
[wake_gate.py] → wake word detected
  ↓
[voice_agent.py] → routing
  ├── [xian_handler.py] → POST /api/generate (model: xian)
  └── [soryel_handler.py] → POST /api/generate (model: xian)
```

- **Pliki:** `voice/xian_handler.py`, `voice/soryel_handler.py`
- **Trigger:** wake word + głos
- **Kiedy:** tylko podczas interakcji głosowej

### 7. Content Classifier → Ollama (klasyfikacja)

```text
[content_classifier.py]
  ↓ POST /api/generate
```

- **Plik:** `cognition/attention/content_classifier.py`
- **Status:** nie zaimportowany przez żaden aktywny proces — nieużywany

---

## Ścieżki do `/api/embed`

| Caller | Plik | Kiedy |
|--------|------|-------|
| xian_core (session_to_xian) | `xian/vector_contract.py` | Co 6s — embedduje tekst sesji |
| alignment_publisher | `tools/alignment_publisher.py` | Cyklicznie — embedduje alignment |
| semantic_retrieval | `agents/memory/semantic_retrieval.py` | Na żądanie — szukanie semantyczne |
| base_index_sqlite | `agents/memory/base_index_sqlite.py` | Przy upsert — embedduje rekordy |
| embeddings_batch | `agents/memory/embeddings_batch.py` | Nocny batch (jeśli LIRA_ENABLE_EMBEDDINGS_BATCH=1) |

---

## Diagram: kto co wywołuje i kiedy

```text
                    ┌─────────────────────────┐
                    │   Ollama (port 11434)    │
                    │  gemma3:4b (xian) — CPU  │
                    │  3.6 GiB, 0 GPU layers   │
                    └────────┬────────┬────────┘
                             │        │
                    /generate│        │/embed
                             │        │
         ┌───────────────────┤        ├──────────────────┐
         │                   │        │                  │
    ┌────┴────┐        ┌─────┴──┐  ┌──┴───┐       ┌─────┴──────┐
    │  Xian   │        │ Xian   │  │Xian  │       │ Alignment  │
    │ Inbox   │        │Executor│  │Core  │       │ Publisher  │
    │ Daemon  │        │(idle)  │  │(6s)  │       │            │
    └────┬────┘        └────┬───┘  └──┬───┘       └────────────┘
         │                  │         │
    [MoltBook          [mcl/router]   [vector_contract]
     capsules]          ↑
                        │
                   [reflective_loop]
                   (night daemon, jeśli
                    kolejka niepusta)
```

---

## Rozwiązanie zagadki 2026-05-15

**Pytanie Neo:** Co wywołuje Ollama `/api/generate` co ~8 min?

**Odpowiedź:** `xian_core` → `xian/executor.py` → `mcl/router.py`

**Dowody:**
1. Model: gemma3 (Q4_K_M) = xian — z logu Ollama
2. Tylko gdy idle — potwierdzone przez Neo (25 min ciszy gdy aktywny)
3. xian_inbox_daemon śpi od 05:30 (brak nowych kapsułek MoltBook)
4. Night daemon ma pustą kolejkę refleksji
5. Router blokuje non-interactive calls poza fazą ACT
6. Executor: cooldown 120s + ~3m40s generacji + czas na fazę ACT ≈ 6-8 min

**Droga dochodzenia:**
1. Grep po `api/generate` → 7 plików
2. Sprawdzenie procesów (`ps aux`) → 18 procesów Python
3. Sprawdzenie logów (xian_inbox — cichy od 05:30, night_daemon — pusta kolejka)
4. Obserwacja Neo: "tylko gdy idle" → szukanie procesów z idle gate
5. Analiza `xian/executor.py` → cooldown + phase gate + mcl.router
6. Log Ollama: model=gemma3 → potwierdza że to xian, nie qwen
7. Monitor `ss -tnp` → złapał połączenie o 13:29

---

---

## Alignment Publisher — przerwany łańcuch danych

### Przepływ (jak powinien działać)

```text
[Rozmowa Neo/Soryel/Lira]
       ↓ ktoś zapisuje event
[shared_context/events/*.json]    ← kind=user_input / kind=assistant_output
       ↓ _tail_events() (max 80 plików, od końca)
[alignment_publisher] (co 8s)
       ↓ embed sesji → cosine z neo/lira/soryel core vectors
[alignment_state.json]            ← session_to_neo, global_alignment, recommended_mode
       ↓ czyta xian/observer.py
[xian_state → xian_report → executor]
       ↓
[Ollama /api/generate]            ← sensowna refleksja bo dane się zmieniają
```

### Gdzie się rwie (stan na 2026-05-15)

```text
[shared_context/events/]  ← PUSTY (0 plików .json, jest runtime.jsonl ale to logi systemowe)
[memory/diary/]           ← ostatni wpis: 2026-04-02, i nie ma pól user_text/reply
       ↓
_latest_session() → te same dane od 16 kwietnia:
  user: "czesc. Jak sie masz dzisiaj?"
  assistant: "[router_blocked]..."
       ↓
session_hash = 20c90f5e... (niezmienny)
       ↓
if prev.hash == hash: return prev  ← STOP, nie aktualizuje
       ↓
alignment_state.json zamrożony od 2026-04-16
```

### Co musiałoby się zmienić

Ktoś musi pisać eventy `user_input` / `assistant_output` do `shared_context/events/` jako pliki `.json`.

**Kandydaci:**
- `session_ingester.py` — monitoruje sesje Claude Code, ale pisze do embeddingów, nie do events
- `event_collector.py` — zbiera eventy, ale z runtime_stream do queue, nie z rozmów
- `capsule_bus` → konwersja chat_message na event — brakujący most

**Efekt domina zamrożonego alignment:**
1. alignment_state ❄️ → snapshot Xiana bez zmian
2. xian_report zawsze "pressure=0, drift=0, alignment=0.65"
3. executor generuje pusty przebieg (67x/dzień, ~4h CPU)
4. conversation_active: false (bo alignment nie raportuje)
5. energy_score: 0.5 (default, bo brak sygnałów)

### Pliki alignment

| Plik | Ścieżka | Rola |
|------|---------|------|
| alignment_publisher.py | `tools/alignment_publisher.py` | Główny publisher — biegnie co 8s (PID 6620) |
| alignment_state.json | `runtime/state/` | Wynik — zamrożony od 16.04 |
| alignment_log.jsonl | `runtime/state/` | Log historyczny (append) |
| neo_core.vec.json | `memory/identity/` | Wektor referencyjny Neo |
| lira_core.vec.json | `memory/identity/` | Wektor referencyjny Liry |
| soryel_core.vec.json | `memory/identity/` | Wektor referencyjny Soryel |
| neo_core.txt | `memory/identity/` | Tekst tożsamości Neo (źródło wektora) |
| lira_core.txt | `memory/identity/` | Tekst tożsamości Liry |
| soryel_core.txt | `memory/identity/` | Tekst tożsamości Soryel |

---

## Powiązania Obsidian

- [[10_CODE_MAP]] — pełna mapa 75 plików
- [[03_SYSTEM_CONNECTIONS]] — przepływy wysokopoziomowe
- [[Xian — Obserwacja i niski szum]] — rola Xiana
- [[Capsule Bus — Krążenie krwi]] — szyna komunikacji
