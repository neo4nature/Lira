# TurmalinCache + BaseIndex (SQLite)

## Idea

System działa w dwóch warstwach pamięci:

1) **BaseIndex (stabilny)** — mały, trwały zbiór „promoted” rekordów (tożsamość, polityki, architektura, ważne wnioski).
   - backend: SQLite (`data/memory/root/base_index.sqlite3`)
   - retrieval: cosine similarity liczona w Pythonie (bez rozszerzeń wektorowych)

2) **TurmalinCache (tymczasowy)** — szybka warstwa robocza (dzień), która:
   - jest ograniczona TTL i maksymalną liczbą rekordów
   - jest kompresowana/deduplikowana
   - promuje stabilne wzorce do BaseIndex

## Pliki

- `kernel/app/agents/memory/base_index_sqlite.py`
- `kernel/app/agents/memory/turmalin_cache.py`
- `kernel/app/loops/turmalin_loop.py`

## Uruchomienie (jednorazowo)

```bash
cd Lira/kernel/app
python3 -m loops.turmalin_loop  # loop

# albo jednorazowo:
LIRA_TURMALIN_ONCE=1 python3 -m loops.turmalin_loop
```

## Zmienne środowiskowe

- `LIRA_ENABLE_BASE_INDEX=1`
- `LIRA_ENABLE_TURMALIN=1`
- `LIRA_TURMALIN_TTL_SECONDS=604800` (7 dni)
- `LIRA_TURMALIN_PROMOTE_SEEN=3`
- `LIRA_TURMALIN_WINDOW_SECONDS=1209600` (14 dni)

## NightCycle

NightCycle generuje:

- snapshot JSON: `data/memory/snapshots/YYYY/YYYY-MM-DD.snapshot.json`
- raport MD: `data/memory/snapshots_md/YYYY/YYYY-MM-DD.md`

MD zawiera też tabelę „Task Trail” z `data/memory/diary/YYYY/YYYY-MM-DD.diary.jsonl`.
