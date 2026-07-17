"""Daily context provider (snapshot capsules).

Goal:
  Provide a small, human-scale 'daily capsule' context that other loops can consume.

Sources (in priority order):
  1) TurmalinCache JSONL records with meta.snapshot_date == date
  2) BaseIndex SQLite records with kind == 'daily_snapshot_capsule' and meta.snapshot_date == date

This module intentionally avoids embedding queries; it is a deterministic fetch layer.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.memory.turmalin_cache import iter_recent
from agents.memory.base_index_sqlite import connect, init_db


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_daily_capsules(date: Optional[str] = None, *, limit: int = 40, max_scan_turmalin: int = 50_000) -> List[Dict[str, Any]]:
    """Return up to `limit` capsule records for the given date."""
    date = (date or _today()).strip()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    # 1) Turmalin (temporary, freshest)
    try:
        for rec in iter_recent(max_records=max_scan_turmalin):
            meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
            if meta.get("snapshot_date") != date:
                continue
            rid = str(rec.get("id", ""))
            if not rid or rid in seen:
                continue
            seen.add(rid)
            out.append({
                "id": rid,
                "source": "turmalin",
                "kind": rec.get("kind", "daily_snapshot_capsule"),
                "text": rec.get("text", ""),
                "meta": meta,
                "ts": rec.get("ts"),
            })
            if len(out) >= limit:
                return out
    except Exception:
        pass

    # 2) BaseIndex (stable)
    try:
        init_db()
        con = connect()
        try:
            rows = con.execute(
                "SELECT id, kind, text, meta_json, created_ts FROM base_records WHERE stable=1 AND kind=? ORDER BY created_ts DESC LIMIT ?",
                ("daily_snapshot_capsule", int(limit * 5)),
            ).fetchall()
        finally:
            con.close()
        for rid, kind, text, meta_json, created_ts in rows:
            if rid in seen:
                continue
            try:
                meta = json.loads(meta_json) if isinstance(meta_json, str) else {}
            except Exception:
                meta = {}
            if meta.get("snapshot_date") != date:
                continue
            seen.add(rid)
            out.append({
                "id": rid,
                "source": "base_index",
                "kind": kind,
                "text": text,
                "meta": meta,
                "ts": created_ts,
            })
            if len(out) >= limit:
                break
    except Exception:
        pass

    return out
