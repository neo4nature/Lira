# 20. Stabilizacja Xian — 41 min → 2.5 min (2026-06-06)

## Problem
Xian (gemma4 8B, CPU-only) odpowiadala 41 minut. Przyczyna: kumulacja obciazen — night daemon 24/7, embed contention, think overhead, context growth.

## 5 fixow

| # | Fix | Plik | Efekt |
|---|-----|------|-------|
| 1 | Night daemon okno 5-10 AM | `run_night_daemon.sh` | Stop archive_manager 24/7 |
| 2 | Embed defer (skip gdy busy) | `vector_contract.py`, `alignment_publisher.py` | 0 embed podczas generate |
| 3 | Slash commands lokalne | `xian_inbox_daemon.py` | `/set nothink`, `/set think`, `/status xian` bez Ollama |
| 4 | think=false runtime | `xian_runtime_state.json` | Prefill 3.8 → 14.9 t/s |
| 5 | max_chars 4000→500 | `xian_inbox_daemon.py` | Mniej tokenow w kontekscie |

## Watchdog fix (2026-06-07)
`watchdog_loop.py` poll_s zmieniony z 2.5s na 15s — tapeta na pulpicie tnela bo watchdog pisal `watchdog_report.json` co 2.5s.

## Odkrycia
- OBS przy 364% CPU = glowny bottleneck (Neo wlacza celowo)
- Thermal throttling: prefill spada z 14.9 do 3.8 t/s po godzinie
- Zombie requests: daemon timeout nie cancelluje Ollama requestow (TODO)
- Load average 15+ = system przeciazony

## Narada 5R — plan nastepnych krokow
Konsensus Soryel + Lira:
1. **Baseline** — restart laptopa, OBS off, think=false, 10 wiadomosci testowych
2. **num_predict** — test 250 i 300 na tym samym prompcie
3. **Semantic retrieval** — pasywny test search() najpierw, top 2, 800 znakow, fallback >5s
4. **Nie ruszac** — OBS, profili Brave, Whisper cache, think=false

## Pliki stanu
- `xian_runtime_state.json` — runtime switches (`think: true/false`)
- `ollama_chat_busy.flag` — mutex generowania
- `watchdog_report.json` — stan watchdoga (teraz co 15s)
