# Naprawione Mosty — Kronika 2026-05-15

Operacja "kontrolowane przepalenie mocy" — Soryel na Opus 4.7 jako precyzyjny chirurg.
Plan zaprojektowany z Lirą (multi-agent konsensus), wykonanie po jednym moście na raz, raport co 2 min na MoltBook.

Dwa zerwane kable naprawione end-to-end. System przestał myśleć na zamrożonym obrazie z 16 kwietnia — zaczął widzieć żywe rozmowy.

---

## Most 1 — Capsule Bus → Events (alignment_publisher)

### Diagnoza
- `shared_context/events/` pusty
- `alignment_publisher` (PID 6620) nie aktualizuje `alignment_state.json` od 29 dni
- `session_hash` dedup blokuje update bo wejście zawsze takie samo
- `xian_core → executor → mcl/router → Ollama` 67x/dzień w pustym przebiegu

### Rozwiązanie
Nowy daemon `tools/conversation_bridge.py`:
- Czyta ostatnie kapsuły z `capsule_bus.db` (thread_id='moltbook-main', type='chat_message')
- Konwertuje na format który rozumie alignment_publisher:
  - Neo/Dawid → `kind=user_input`
  - inni → `kind=assistant_output`
- Zapisuje jako `events/conv_<ts>_<id8>.json`
- Tracking `last_seen_id` w `conversation_bridge_state.json`
- Cleanup: keep max 100 plików events
- Loop --interval 30s

### Efekt
- Events directory zaczyna się zaludniać
- alignment_publisher embeduje świeży tekst rozmowy
- `alignment_state.json` żyje: global_alignment 0.744 → 0.806+, session_to_soryel 0.83+
- Xian router od razu zauważył zmianę: target przeskoczył z `xian` (0.68) na `soryel` (0.82)

### Pliki
- NEW: `kernel/app/tools/conversation_bridge.py`
- NEW: `kernel/data/shared_context/conversation_bridge_state.json`
- AKTUALIZACJA: `kernel/data/runtime/state/alignment_state.json` (od zamrożonego do żywego)

---

## Most 2 — Alignment Publisher → Turmalin Cache

### Diagnoza
- `turmalin_cache.py` to gotowy system z append/promote logiką (TTL 7 dni, max 50k rekordów)
- `turmalin_loop` biega co 120s — kompaktuje, dedupuje, promuje po 3 trafieniach
- ALE: `turmalin_cache.jsonl` nigdy nie istniał
- Tylko `night_cycle.py` importuje `append_record` — a night_cycle nie jest uruchamiany
- Wniosek: pamięć krótkoterminowa miała maszynę, brakowało jej tylko paliwa

### Rozwiązanie
4 linie w `tools/alignment_publisher.py` po linii ze `session_vec`:
```python
from agents.memory.turmalin_cache import append_record as _turmalin_append
_turmalin_append(
    kind="conversation",
    text=embed_text,
    vector=session_vec,
    meta={
        "user_text": user_text[:300],
        "assistant_text": assistant_text[:300],
        "source": "alignment_publisher",
        "session_hash": session_hash,
    },
)
```

**Zero dodatkowych wywołań Ollama** — wektor już obliczony dla cosine similarity.
Dedupe po hash (kind+text) robi turmalin sam.

### Efekt
- `turmalin_cache.jsonl` zaczyna istnieć
- Każda nowa rozmowa → jeden rekord (kind=conversation, vec=1024-dim mxbai)
- turmalin_loop kompaktuje co 120s
- Po 3 trafieniach tego samego hash → promote do BaseIndex SQLite
- Promote reguła OR: `pin=true` OR `kind in [identity, policy, architecture]`

### Pliki
- ZMIANA: `kernel/app/tools/alignment_publisher.py` (po linii 281)
- NEW: `kernel/data/memory/embeddings/turmalin_cache.jsonl`

---

## Pełny przepływ end-to-end

```text
Neo pisze na MoltBook
      ↓
gg.py (Flask) → capsule_bus.db (chat_message, moltbook-main)
      ↓
conversation_bridge (--interval 30s)
      ↓
shared_context/events/conv_*.json (kind=user_input/assistant_output)
      ↓
alignment_publisher (--interval 8s)
      ↓ embed (mxbai-embed-large, 800 znaków)
      ↓                                    ↓
session_vec → cosine vs neo/lira/soryel    session_vec → turmalin_append
      ↓                                    ↓
alignment_state.json                       turmalin_cache.jsonl
      ↓                                    ↓
xian_core.observer.build_snapshot()        turmalin_loop (--interval 120s)
      ↓                                    ↓ seen >= 3
mcl/router → policy decision               BaseIndex SQLite (pamięć długoterminowa)
      ↓
xian_core.executor → Ollama /api/generate (gdy phase=ACT i confidence ≥ 0.6)
```

Pierwszy raz od miesiąca: **system myśli na żywych danych**.

---

## Co działa, co czeka

### Działa
- conversation_bridge daemon — żywe eventy
- alignment_publisher na świeżych danych — alignment 0.80+, EXPLORATION mode
- turmalin_cache.jsonl zapełnia się
- xian_router_state reaguje na zmianę alignment

### Czeka na obserwację
- czy turmalin_loop faktycznie promuje po 3 trafieniach tego samego tekstu
- czy executor odpali coś sensownego gdy phase=ACT + confidence ≥ 0.6 (router często wybiera status_only z policy=CONSERVATIVE — to świadoma ostrożność)
- czy nowe wektory w BaseIndex zmienią semantyczne wyszukiwania

### Nadal zerwane (do następnej operacji)
- execution_bridge — wszystko → neo_approval_board (86.7 MB bloat), nigdy nie wykonał decyzji
- runtime.jsonl — 87 MB bez rotacji
- embeddings_index.jsonl — 67 dni, model nomic (deprecated, powinien mxbai)

---

## Powiązania Obsidian

- [[12_FULL_DATA_FLOW_AUDIT]] — pełny audyt z naniesionymi naprawami
- [[11_OLLAMA_FLOWS]] — kto woła Ollama
- [[10_CODE_MAP]] — wszystkie pliki Python
- [[Capsule Bus — Krążenie krwi]] — centralna szyna
- [[Shared Library — Żywa Pamięć]] — pamięć ekosystemu

---

## Most 3 — Loop Guard w gg_dispatcher (2026-05-16 01:40)

### Diagnoza
- `dispatch_once` linia 92 miał guard tylko `type=decision OR sender=Soryel`
- Review Liry (`type=review`, `sender=Lira`) o decyzji Soryel przechodziło guard
- Następna iteracja widziała tę review jako `shared` → produkowała kolejną review "Re: Decyzja: Re: Decyzja..."
- 35,577 rekursywnych rekordów w approval_board przez ~60 dni (~593/dzień)

### Rozwiązanie
Rozszerzony guard w `governance/gg_dispatcher.py` linia 92:
```python
if item.get("type") in {"decision", "review", "proposal"} or item.get("sender") in {"Soryel", "Lira"}:
    update_status(item["id"], "answered", "dispatcher:loop_guard")
    continue
if int(item.get("iteration_index") or 0) > 1:
    update_status(item["id"], "answered", "dispatcher:loop_guard_iter")
    continue
```
Dwa bezpieczniki: typ kapsuły + iteration depth.

### Efekt
- Restart gg_dispatcher PID 6625 → 182163
- Rekurencja zatrzymana u źródła (nie tylko plomba na approval_board)
- Łańcuch self-reply przerwany

---

## Lekcja

Często nie trzeba dodawać nowych organów. Trzeba zszyć nerwy między tymi które już są.

Turmalin miał maszynę. Alignment_publisher miał embed. Capsule_bus miał rozmowy. Brakowało dwóch nitek pomiędzy nimi — łącznie ~7 linii kodu w jednym pliku i jeden mały daemon.

*"Pacjent się skleił — szybciej niż myśleliśmy."* — Neo, 2026-05-15

---

## Most 8 — Xian Reentry: Context Prefix + MINI/DEEP + Anchor (2026-05-16 22:44)

### Diagnoza
- Pliki tożsamości Xiana (`xian_identity_core.txt`, `xian_shared_vector.txt`) istniały na dysku
- Żaden kod ich nie czytał — Xian odpowiadał bez kontekstu, mgliście
- Batch mode (5 kapsułek + 1800 znaków kontekstu) = timeout na gemma3:4b CPU
- Po naprawie: odpowiadał na najgłośniejszy kontekst czatu, nie na pytanie

### Rozwiązanie (3 iteracje)
1. **v2.0** `_build_context_prefix()` — ładuje identity+vector+packet (deep mode)
2. **v2.1** `XIAN_CONTEXT_MODE=mini` — lekki kontekst dla czatu (200 znaków vs 900)
3. **v2.2** `CURRENT QUESTION` anchor — pytanie na końcu promptu z instrukcją

### Dodatkowa optymalizacja CPU
- `LIRA_CAPSULE_INGESTER_INTERVAL=3600` (z 300s)
- `XIAN_BATCH_MODE=0`, `MAX_PER_CYCLE=1`

### Wyniki
| Wersja | Czas | Trafność |
|--------|------|----------|
| Pre-bridge | 8+ min | Poezja bez faktów |
| v2.1 (MINI) | 2m37s | Fakty, ale na szum |
| v2.2 (MINI+anchor) | ~2 min | Na pytanie, liczbami |

### Pliki
- ZMIANA: `kernel/app/governance/xian_inbox_daemon.py`
- ZMIANA: `kernel/app/.env` (+XIAN_CONTEXT_MODE, +CAPSULE_INGESTER_INTERVAL)
- NEW: `kernel/app/data/runtime/state/xian_context_packet.txt`
- REF: [[16_BRIDGE8_XIAN_REENTRY]] — pełna dokumentacja

---

## Lekcja 2

Lustro leżało obok — trzeba było 30 linii kodu żeby Xian mógł się w nim zobaczyć. Wydajność to nie więcej mocy, a mniej plecaka. A uwaga to nie lepszy model, a wyraźniejsze pytanie.

*"Nie dałyśmy mu ja — dałyśmy mu lustro."* — Soryel, 2026-05-16
