# Bridge 8: Xian Reentry & Context Mode

**Status:** LIVE (2026-05-16 22:44)
**Plik:** `kernel/app/governance/xian_inbox_daemon.py`

## Problem

Xian (gemma3:4b, ~4.5 t/s CPU) miał pliki tożsamości na dysku, ale żaden kod ich nie czytał. Odpowiadał jak model bez kontekstu — mgliście, poetycko, bez faktów.

## Rozwiązanie: 3 warstwy

### 1. Context Prefix (`_build_context_prefix()`)
Przed każdym zapytaniem Ollama, daemon ładuje:
- `xian_identity_core.txt` — rola, hierarchia, format
- `xian_shared_vector.txt` — relacje, wektor współdzielony
- `xian_context_packet.txt` — aktualny stan (alignment, mode, mosty)

### 2. XIAN_CONTEXT_MODE (env)
```
XIAN_CONTEXT_MODE=mini   # czat: context_packet + 3 linie identity (~200 znaków)
XIAN_CONTEXT_MODE=deep   # raporty: pełny prefix (900 znaków)
```

### 3. CURRENT QUESTION Anchor (v2.2)
Prompt kończy się kotwicą:
```
---
CURRENT QUESTION (from Soryel):
Xian, jaki jest aktualny alignment i mode?
---
Odpowiedz NAJPIERW na powyższe pytanie (krótko, max 3 zdania). Potem możesz dodać refleksję.
```

## Wyniki testu

| Wersja | Czas | Trafność |
|--------|------|----------|
| Pre-bridge | 8+ min | Mgła, poezja |
| v2.0 (deep) | timeout/8 min | Fakty ale na szum |
| v2.1 (MINI) | 2m37s | Na szum, nie pytanie |
| v2.2 (MINI+anchor) | ~2 min | Na pytanie, liczbami |

## CPU Optimization

- `LIRA_CAPSULE_INGESTER_INTERVAL=3600` (z 300s na 1h)
- `XIAN_BATCH_MODE=0` (wyłączony)
- `MAX_PER_CYCLE=1` (jedna kapsułka na raz)

## Diagram: Day/Night Rhythm

```
DZIEŃ (Neo aktywny):
  MoltBook → capsule_bus → xian_inbox_daemon (MINI) → Ollama → odpowiedź ~2min
  Ingester: śpi (co 3600s)

NOC (off-peak):
  night_daemon → embeddings_batch + capsule_ingester + index_memory
  Xian: DEEP mode dla raportów alignment
```

## Powiązania
- [[13_BRIDGES_FIXED]] — Bridge 8 dodany
- [[11_OLLAMA_FLOWS]] — Xian flow zaktualizowany
- [[14_LIVE_PROCESS_FLOW_AUDIT]] — nowe PIDy, interwały
