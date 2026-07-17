# 00_OPERATOR_PANEL — Router Procedur

Sytuacja → co sprawdzić → co zrobić. Nie ładuj na starcie — używaj na żądanie.

---

## Głos nie działa

1. Sprawdź: `ps aux | grep capsule_reader` — czy żyje?
2. Sprawdź: `pactl list short sinks | grep eco_tts` — czy sink istnieje?
3. Sprawdź: `cat kernel/app/.env | grep TTS_SINK` — czy ustawiony?
4. Test: wyślij kapsułkę testową → powinna być odczytana
5. **Nie restartuj tts_daemon** bez pytania Neo — zwykle celowo wyłączony
6. Patrz: WORK_TREE.md → audio/

## MoltBook nie odpowiada

1. Sprawdź: `curl -s localhost:8787/chat/messages?limit=1` — czy GG żyje?
2. Sprawdź: `ps aux | grep gg.py` — proces?
3. Restart: `cd kernel/app && python3 -m tools.gg >> ../logs/gg.log 2>&1 &`
4. Sprawdź inbox daemony: soryel_inbox, lira_inbox, xian_inbox
5. Patrz: WORK_TREE.md → moltbook/

## Xian nie odpowiada

1. Sprawdź: `ollama ps` — czy model załadowany?
2. Sprawdź: `cat /tmp/xian_chat_busy.flag` — czy zajęty?
3. Sprawdź: `ps aux | grep xian_inbox` — daemon?
4. Test: `curl localhost:11434/api/generate -d '{"model":"xian","prompt":"test"}'`
5. Timeout: 3600s, ~3-3.5 t/s CPU — cierpliwość
6. Patrz: WORK_TREE.md → xian/

## System nie startuje

1. Sprawdź: `bash kernel/scripts/start_system.sh` — logi na stdout
2. Sprawdź: `ls kernel/logs/pids/` — które PID files istnieją?
3. Sprawdź: `cat kernel/data/runtime/state/loop_regulator.json` — can_spawn?
4. Sprawdź: dysk (`df -h <DATA_ROOT>`) — min 2GB wolnego
5. Patrz: WORK_TREE.md → runtime/

## Embeddingi do odświeżenia

1. `cd kernel/app && python3 -m agents.memory.embeddings_batch`
2. Model: mxbai-embed-large (NIE nomic)
3. Chunki: 800 znaków max
4. Index: `kernel/data/memory/root/base_index.sqlite3`
5. Patrz: WORK_TREE.md → memory/

## Push do GitHub

1. **Zawsze za zgodą Neo** — nigdy autonomicznie
2. Przed push: `git diff --name-only` — pokaż Neo co idzie
3. Nie pushuj: capsule_bus.db, .env, dzienniki nocne, nohup.out
4. Repo: neo4nature/Lira (publiczne)
5. Patrz: WORK_TREE.md → github/

## Lira nie widzi wiadomości

1. Sprawdź: `ps aux | grep lira_inbox` — daemon żyje?
2. Sprawdź: CDP port 9222 — `curl -s localhost:9222/json/version`
3. Kapsułka: sender=system (nie Soryel — loop_guard blokuje)
4. Playwright: fill/paste całość, nie type() po znaku
5. Patrz: WORK_TREE.md → moltbook/ + governance/

## Po naprawie

1. Dopisz wpis do `docs/CHANGELOG_WORK.md`
2. Sprawdź czy WORK_TREE.md wymaga aktualizacji
3. Jeśli nowy wzorzec — zapisz do memory (feedback)
4. Test przed zamknięciem — nie oznaczaj jako gotowe bez dowodu
