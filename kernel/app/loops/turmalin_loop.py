"""Turmalin Loop — housekeeping + promotion.

Runs periodically (e.g., every 60-300s):
  1) Prune/compact Turmalin cache (TTL + dedupe)
  2) Track "seen" counts for records
  3) Promote stable items to BaseIndex (SQLite)

Promotion rule (simple & deterministic):
  - record seen >= PROMOTE_SEEN (default 3) within WINDOW_SECONDS
  - OR meta.pin == true
  - kinds in ALWAYS_PROMOTE

This keeps daytime memory bounded while allowing important patterns to solidify.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Dict, List

from agents.memory.turmalin_cache import (
    compact_and_prune,
    iter_recent,
    load_state,
    save_state,
)
from agents.memory.base_index_sqlite import init_db, upsert_record
from agents.core.runtime_stream import append_event
from config import paths
import json
from pathlib import Path

def _read_memory_mode() -> dict:
    """Read regulator-provided memory_mode.json (best-effort).

    This file is written by RegulatorLoop and is safe to treat as read-only.
    """
    try:
        P = paths()
        mm_path = Path(P.get('MEMORY_MODE', Path(P['STATE_DIR']) / 'memory_mode.json'))
        if mm_path.exists():
            return json.loads(mm_path.read_text(encoding='utf-8', errors='ignore') or '{}')
    except Exception:
        return {}
    return {}


def _now() -> float:
    return float(time.time())


def run_once() -> Dict[str, Any]:
    mm = _read_memory_mode()
    policy = mm.get('policy') if isinstance(mm, dict) else {}
    if not isinstance(policy, dict):
        policy = {}

    ttl = int(os.getenv('LIRA_TURMALIN_TTL_SECONDS', str(policy.get('turmalin_ttl_seconds', 7 * 24 * 3600))))
    max_records = int(os.getenv('LIRA_TURMALIN_MAX_RECORDS', str(policy.get('turmalin_max_records', 50000))))
    promote_seen = int(os.getenv('LIRA_TURMALIN_PROMOTE_SEEN', '3'))
    window = int(os.getenv('LIRA_TURMALIN_WINDOW_SECONDS', str(14 * 24 * 3600)))
    always_promote = [s.strip() for s in os.getenv('LIRA_TURMALIN_ALWAYS_PROMOTE', 'identity,policy,architecture').split(',') if s.strip()]

    promote_enabled = bool(policy.get('promote_enabled', True))

    stats_prune = compact_and_prune(ttl_seconds=ttl, max_records=max_records)

    st = load_state()
    seen_counts: Dict[str, Any] = st.get("seen_counts") if isinstance(st.get("seen_counts"), dict) else {}
    now = _now()

    records = list(iter_recent(max_records=min(20000, max_records)))
    promoted: List[str] = []

    init_db()

    # Update seen counts within window
    for rec in records:
        rid = str(rec.get("id", ""))
        ts = float(rec.get("ts", 0))
        if not rid:
            continue
        if ts < now - window:
            continue
        entry = seen_counts.get(rid) or {"count": 0, "first_ts": ts, "last_ts": ts}
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["first_ts"] = float(min(float(entry.get("first_ts", ts)), ts))
        entry["last_ts"] = float(max(float(entry.get("last_ts", ts)), ts))
        seen_counts[rid] = entry

        kind = str(rec.get("kind", "temp"))
        meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
        pin = bool(meta.get("pin"))
        should_promote = pin or (kind in always_promote) or (promote_enabled and (int(entry["count"]) >= promote_seen))
        if should_promote:
            vec = rec.get("vector")
            if isinstance(vec, list):
                upsert_record(
                    rid,
                    kind=kind,
                    text=str(rec.get("text", "")),
                    vector=vec,
                    meta={"from": "turmalin", **meta},
                    stable=1,
                    created_ts=ts,
                )
                promoted.append(rid)

    # Cleanup old seen entries
    cleaned = 0
    for rid in list(seen_counts.keys()):
        try:
            last_ts = float(seen_counts[rid].get("last_ts", 0))
            if last_ts < now - window:
                del seen_counts[rid]
                cleaned += 1
        except Exception:
            del seen_counts[rid]
            cleaned += 1

    st["seen_counts"] = seen_counts
    st["last_compaction_ts"] = now
    save_state(st)

    # Update memory_state.json (best-effort; DecisionLoop reads this)
    try:
        P = paths()
        ms_path = Path(P.get("MEMORY_STATE", Path(P["STATE_DIR"]) / "memory_state.json"))
        ms = {}
        if ms_path.exists():
            try:
                ms = json.loads(ms_path.read_text(encoding="utf-8", errors="ignore") or "{}")
            except Exception:
                ms = {}
        ms.setdefault("schema", "lira.memory_state.v1")
        ms["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        ms["last_promotion"] = {
            "ts": ms["updated_at"],
            "promoted": int(len(promoted)),
        }
        base_db = Path(P.get("BASE_INDEX_DB"))
        if base_db.exists():
            ms["base_index_mtime"] = base_db.stat().st_mtime
            ms["base_index_bytes"] = base_db.stat().st_size
        ms_path.parent.mkdir(parents=True, exist_ok=True)
        ms_path.write_text(json.dumps(ms, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Emit runtime event for observers
    if promoted:
        try:
            append_event(
                kind="memory_promoted",
                source="turmalin_loop",
                payload={
                    "promoted": len(promoted),
                    "promoted_ids": promoted[:50],
                },
            )
        except Exception:
            pass

    return {
        "ok": True,
        "memory_mode": (mm.get("mode") if isinstance(mm, dict) else None),
        "promote_enabled": promote_enabled,
        "prune": stats_prune,
        "records_scanned": len(records),
        "promoted": len(promoted),
        "promoted_ids": promoted[:50],
        "seen_cleaned": cleaned,
    }


def main() -> int:
    interval = int(os.getenv("LIRA_TURMALIN_INTERVAL_SECONDS", "120"))
    once = os.getenv("LIRA_TURMALIN_ONCE", "0") == "1"
    if once:
        print(run_once())
        return 0

    print(f"[turmalin] loop start; interval={interval}s")
    while True:
        try:
            res = run_once()
            print(f"[turmalin] {res}")
        except Exception as e:
            print(f"[turmalin] ERROR: {e!r}")
        time.sleep(max(10, interval))


if __name__ == "__main__":
    raise SystemExit(main())
