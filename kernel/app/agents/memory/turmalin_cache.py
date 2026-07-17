"""TurmalinCache — temporary embedding cache (JSONL) + housekeeping.

Philosophy:
  - Daytime memory is a fast, disposable working layer.
  - NightCycle produces one canonical snapshot per day.
  - Turmalin keeps the working layer bounded (TTL, compaction) and promotes stable items to BaseIndex.

Storage:
  - JSONL at memory/embeddings/turmalin_cache.jsonl
  - Each line: {schema, id, ts, kind, text, vector, meta}
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional

from config import paths


SCHEMA = "lira.turmalin_record.v1"


def cache_path() -> Path:
    return Path(os.getenv("LIRA_TURMALIN_CACHE", str(paths()["TURMALIN_CACHE"])) )


def state_path() -> Path:
    return Path(os.getenv("LIRA_TURMALIN_STATE", str(paths()["TURMALIN_STATE"])) )


def _now_ts() -> float:
    return float(time.time())


def make_id(text: str, kind: str = "temp") -> str:
    h = hashlib.sha256((kind + "\n" + (text or "")).encode("utf-8", errors="ignore")).hexdigest()
    return h[:16]


def append_record(
    *,
    kind: str,
    text: str,
    vector: List[float],
    meta: Optional[Dict[str, Any]] = None,
    record_id: Optional[str] = None,
    ts: Optional[float] = None,
) -> Path:
    """Append one record to Turmalin cache."""
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "schema": SCHEMA,
        "id": record_id or make_id(text, kind),
        "ts": float(ts if ts is not None else _now_ts()),
        "kind": kind,
        "text": text,
        "vector": vector,
        "meta": meta or {},
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


def iter_recent(max_records: int = 20_000) -> Iterable[Dict[str, Any]]:
    """Stream recent records (bounded)."""
    p = cache_path()
    if not p.exists():
        return []
    buf: Deque[str] = deque(maxlen=max_records)
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    buf.append(line)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in buf:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("schema") == SCHEMA:
                out.append(obj)
        except Exception:
            continue
    return out


def load_state(default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sp = state_path()
    if sp.exists():
        try:
            return json.loads(sp.read_text(encoding="utf-8", errors="ignore") or "{}")
        except Exception:
            pass
    return default or {
        "schema": "lira.turmalin_state.v1",
        "updated_ts": _now_ts(),
        "seen_counts": {},
        "last_compaction_ts": None,
    }


def save_state(state: Dict[str, Any]) -> None:
    sp = state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    state["updated_ts"] = _now_ts()
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_and_prune(
    *,
    ttl_seconds: int = 7 * 24 * 3600,
    max_records: int = 50_000,
) -> Dict[str, Any]:
    """Prune expired records and compact duplicates by id.

    Returns stats.
    """
    p = cache_path()
    if not p.exists():
        return {"ok": True, "pruned": 0, "kept": 0, "deduped": 0}

    now = _now_ts()
    min_ts = now - float(ttl_seconds)

    buf: Deque[str] = deque(maxlen=max_records)
    with p.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                buf.append(line)

    kept: List[Dict[str, Any]] = []
    seen: set[str] = set()
    pruned = 0
    deduped = 0
    for line in buf:
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict) or obj.get("schema") != SCHEMA:
                continue
            ts = float(obj.get("ts", 0))
            rid = str(obj.get("id", ""))
            if ts < min_ts:
                pruned += 1
                continue
            if rid in seen:
                deduped += 1
                continue
            seen.add(rid)
            kept.append(obj)
        except Exception:
            continue

    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for obj in kept:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    tmp.replace(p)

    return {"ok": True, "pruned": pruned, "kept": len(kept), "deduped": deduped}
