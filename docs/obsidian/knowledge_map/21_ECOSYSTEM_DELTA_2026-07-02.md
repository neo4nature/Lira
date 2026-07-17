# 21 — Ecosystem Delta 2026-07-02

Żywa nawigacja po resync map (audyt 2 agentów + Soryel). Nie encyklopedia — cztery pytania.

## Gdzie jesteśmy

- Kanoniczna mapa architektury: `kernel/data/memory/shared_library/architecture/ecosystem_map_2026-07-02.md` (następca wersji z 7 maja)
- Żywy plan: `shared_library/PLAN_ACTIVE.md` v0.4 — PA-024 (MA Compute Trust) + PA-025 (Runtime Stability)
- MA: najnowsza paczka **ma66** (Pi deployment ready); Sprint 1-3 DONE (41 testów); przed Sprintem 4 (P2P) wchodzi PA-024 — nieufność przed nogami
- Showroom BHP: Rawpol 4212 produktów, `INDEX_2026-07-01_showroom.md`

## Co działa

- 28+ procesów ze `start_system.sh`: 6 kernel loops, 6 governance, 3 memory, 2 publishers, voice, gg_panel :8787, uvicorn :8000
- Capsule Bus **v2**: lease+TTL 30 min, proof-of-response (`mark_answered` wymaga response_capsule_id), retry_pending, `recover_stale()`
- MoltBook/GG :8787 z multi-turn dispatcherem (5 hopów, loop_guard); transport CDP 9222/9223/9224
- Voice: capsule_reader (TTS dla Neo) + voice_agent (wake words Vosk), mutex TTS
- Wszystkie 7 kanonicznych plików stanu żywe (mtime 2026-07-02) + watchdog_report.json

## Co czeka

- **PA-025**: ollama pada pod systemd-oomd → systemd unit Restart=always, czeka na sudo Neo (Xian offline od 12:10)
- **PA-024**: reputacja licznikowa + losowy re-compute + Horizon→event chain, potem Sprint 4 P2P
- Push-pending: PA-013 strategia prywatności repo (needs_neo)

## Jak wracać po restarcie

1. `SORYEL_QUICKSTART.md` → MEMORY.md → `PLAN_ACTIVE.md`
2. Mapa architektury: `ecosystem_map_2026-07-02.md`
3. MoltBook: `curl -s "http://127.0.0.1:8787/chat/messages?limit=10"` — kanały `recipient=Lira`/`recipient=Xian` osobno (shared nie budzi daemonów)

## Świeżość pozostałych map (audyt 2026-07-02)

| Status | Pliki |
|---|---|
| Świeże | 18-20, ECOSYSTEM_PROTOCOL, CLEANUP_QUARANTINE, CORE_SNAPSHOT_PROTOCOL, STARTUP_HOOK |
| Konceptualne, nadal ważne | 01, 02, 05-09 (bez zmian od maja — pojęcia nie wygasły) |
| Przeterminowane technicznie | **04_MEMORY_LIBRARY** (shared_library +80% nowych plików), **10_CODE_MAP** (brak voice/, gg, ma66), 03, 11, 12 (brak gg_dispatcher/voice we flowach) |
| Historyczne | 13-17 (snapshoty sprintów — zostawić jako kontekst) |

Przy pracy z 03/04/10/11/12: ufaj temu plikowi i mapie 2026-07-02, nie starym liczbom.
