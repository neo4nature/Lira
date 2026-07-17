# Regulator Memory Mode (v1)

Ten dokument opisuje nowy mechanizm „trybu pamięci”, który pozwala systemowi Lira **automatycznie hamować** operacje pamięci (embeddingi, promocje do bazy, housekeeping) w sytuacji presji zasobów.

## Pliki

- `kernel/app/loops/regulator_loop.py` — wylicza metryki CPU/RAM/Dysk i zapisuje:
  - `kernel/data/runtime/state/loop_regulator.json`
  - `kernel/data/runtime/state/memory_mode.json` (**NOWE**)

- `kernel/app/loops/decision_loop.py` — jedyny właściciel `bus_signals.json`; czyta `memory_mode.json` i publikuje skrót w `bus_signals.json`.

- `kernel/app/loops/turmalin_loop.py` — czyta `memory_mode.json` i dynamicznie dostosowuje TTL/max_records oraz czy promocja jest dozwolona.

- `kernel/app/night_cycle.py` — czyta `memory_mode.json` i w trybie degradacji może **pominąć** embeddingi i kapsuły snapshota.

## memory_mode.json — schema

Minimalny kształt:

```json
{
  "schema": "lira.memory_mode.v1",
  "owner": "RegulatorLoop",
  "ts": 0,
  "mode": "normal|degraded|emergency",
  "policy": {
    "turmalin_ttl_seconds": 604800,
    "turmalin_max_records": 50000,
    "promote_enabled": true,
    "embed_enabled": true
  },
  "reasons": ["RAM_LOW", "CPU_HIGH", "DISK_LOW"],
  "metrics": {
    "cpu_pct": 0.0,
    "ram_avail_mb": 0,
    "disk_free_gb": 0.0,
    "io_wait_pct": 0.0
  }
}
```

## Zasady

### Mode = normal
- standardowy TTL Turmalina (domyślnie 7 dni)
- promocje do BaseIndex dozwolone
- embeddingi dozwolone (NightCycle kapsuły)

### Mode = degraded
- bardziej agresywny TTL (domyślnie 3 dni)
- domyślnie **promocje wyłączone** (można włączyć env: `LIRA_MM_PROMOTE_DEGRADED=1`)
- domyślnie **embeddingi wyłączone** (można włączyć env: `LIRA_MM_EMBED_DEGRADED=1`)

### Mode = emergency
- TTL krótki (domyślnie 1 dzień)
- promocje OFF
- embeddingi OFF
- DecisionLoop dodatkowo wymusza brak background + brak heavy reasoning

## Env overrides (opcjonalne)

RegulatorLoop pozwala nadpisać polityki:

- `LIRA_MM_TURMALIN_TTL_NORMAL`
- `LIRA_MM_TURMALIN_MAX_NORMAL`
- `LIRA_MM_TURMALIN_TTL_DEGRADED`
- `LIRA_MM_TURMALIN_MAX_DEGRADED`
- `LIRA_MM_PROMOTE_DEGRADED=1`
- `LIRA_MM_EMBED_DEGRADED=1`
- `LIRA_MM_TURMALIN_TTL_EMERGENCY`
- `LIRA_MM_TURMALIN_MAX_EMERGENCY`

## Dlaczego tak?

- **Bus pozostaje lekki**: sterowanie to flagi; dane to eventy + store.
- **CPU-only friendly**: unika się embedów/promocji gdy system dusi się zasobami.
- **Deterministyczność**: degradacja jest policzalna na podstawie progów.
