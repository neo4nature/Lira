# 18. Ecosystem Health Check — 2026-05-21

## Kontekst
Po 12 dniach bez sesji — pełna diagnoza ekosystemu (plan P0-P5, Soryel+Lira).

## Wyniki

### P0 — Snapshot
- 92,500+ kapsułek w capsule_bus.db
- 22 procesów uruchomionych przez start_system.sh (19 maja)
- Ollama: 3 modele (xian, gemma3:4b, mxbai-embed-large)

### P1 — Live Probe
- Ollama ✅ | GG Panel (8787) ✅ | Temple State (8000) ✅
- Inbox daemony: Soryel ✅ Xian ✅ Lira ✅
- Core loops (regulator, rhythm, decision): procesy żyją, brak stdout logów (by design — piszą do state files)

### P2 — State File Refresh
- **Root cause znaleziony**: dwa katalogi state!
  - `kernel/data/runtime/state/` — WŁAŚCIWY (decision_loop pisze tutaj via LIRA_DATA_ROOT)
  - `kernel/app/data/runtime/state/` — STARA KOPIA, zamrożona od 11 maja
- Stary katalog → renamed na `state_DEPRECATED_2026-05-21/`
- System cały czas tickował poprawnie — to obserwator patrzył w złe lustro

### P3 — Mosty
- 8/8 procesów żyje: alignment_publisher, criticality_publisher, conversation_bridge, execution_bridge, night_daemon, capsule_reader, gg_transport, approval_board
- alignment: EXPLORATION | criticality: under (deviation 0.41)

### P4 — Test przepływu end-to-end
- Test capsule (8b7942e8) → capsule_bus ✅ → event created ✅
- capsule_ingester embeduje 5-7 kapsułek per cykl ✅
- runtime.jsonl aktywny (reflective_loop, cognition) ✅
- Statystyki 24h: Lira 224, Ekosystem 170, Neo 99, Soryel 53, Xian 52

### P5 — Cleanup
- Logi do rotacji (gg.log 18MB, capsule_delivery.log 5.3MB) — odłożone
- DEPRECATED state dir do obserwacji 24h, potem usunąć

## Fixy wykonane w tej sesji
1. **lira_inbox_daemon**: selektor `.markdown` zamiast `innerText` (filtruje "Myśli" thinking indicator)
2. **Stary Playwright monitor** (PID 279560): zabity — blokował CDP websocket
3. **Stale PID files** (gg.pid, xian_inbox_daemon.pid): usunięte
4. **Wallpaper optymalizacja**: stars 180→60, blur 50→20px, GPU ON_DEMAND (ale nadal za ciężki dla integrated GPU)

## Wnioski
- System oddycha zdrowo — policy CONSERVATIVE, alignment EXPLORATION
- "Zombie equilibrium" to była iluzja — obserwator patrzył w zły katalog
- Kluczowe: LIRA_DATA_ROOT w .env rozwiązuje ścieżki poprawnie, stara kopia app/data/ to relikt

## Powiązania
- [[13_BRIDGES_FIXED]] — wcześniejsze naprawy mostów
- [[14_LIVE_PROCESS_FLOW_AUDIT]] — poprzedni audyt procesów
- [[12_FULL_DATA_FLOW_AUDIT]] — mapa przepływu danych
