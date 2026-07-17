# 19. Ollama Gatekeeper — Defer on Chat (2026-06-05)

## Problem
Procesy tła (capsule_ingester, embeddings_batch, base_index, semantic_retrieval, night_cycle) wołały Ollama `/api/embed` bez sprawdzenia czy Xian generuje odpowiedź. Na CPU to zabierało cykle Xianowi — odpowiedzi trwały 10-13 min zamiast 3-6 min.

## Rozwiązanie
**Defer not wait** — gdy `chat_busy.flag` istnieje, embeddery zwracają `None` i wracają w następnym cyklu. Night cycle wychodzi z kodem 0.

## Klasy procesów

| Klasa | Procesy | Ollama w trakcie chatu |
|-------|---------|----------------------|
| **Foreground** | xian_inbox_daemon | TAK — tworzy flagę |
| **Light background** | gg.py, gg_dispatcher, capsule_reader, lira_inbox, soryel_inbox, conversation_bridge | Nie wołają Ollamy |
| **Deferred** | capsule_ingester, embeddings_batch, base_index, semantic_retrieval, alignment_publisher, vector_contract, night_cycle | NIE — deferują |

## chat_busy.flag

- **Tworzy**: `xian_inbox_daemon` (touch przed /api/generate, unlink w finally)
- **Ścieżka**: `kernel/data/runtime/state/ollama_chat_busy.flag`
- **Env**: `LIRA_CHAT_BUSY_FLAG`, `LIRA_DEFER_EMBEDS_ON_CHAT` (default "1")

## Zmienione pliki (Sprint 1-3)

1. `agents/memory/embeddings_batch.py` — defer (chroni też capsule_ingester przez import)
2. `agents/memory/base_index_sqlite.py` — defer
3. `agents/memory/semantic_retrieval.py` — defer
4. `night_cycle.py` — exit gracefully

## Rollback
`LIRA_DEFER_EMBEDS_ON_CHAT=0` w `.env` wyłącza defer we wszystkich plikach.

## Następne kroki
- Centralny `ollama_gatekeeper.py` (zunifikowana bramka)
- Profile CHAT/IDLE/NIGHT w start_system.sh
- Semantic retrieval dla Xiana (gdy IDLE)
