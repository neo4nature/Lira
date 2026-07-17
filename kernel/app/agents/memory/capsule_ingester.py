"""Capsule Ingester — most capsule_bus → embeddings_index.jsonl.

Indeksuje kapsuły z capsule_bus.db do pamięci semantycznej, żeby semantic_retrieval
mógł wyszukiwać po treści rozmów MoltBook, decyzji, review.

Zapisuje rekordy jako kind="capsule" z rich metadata (sender, recipient, type,
thread_id, project, status).

Run:
    python3 -m agents.memory.capsule_ingester           # one-shot
    python3 -m agents.memory.capsule_ingester --loop    # daemon (300s interval)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from agents.memory.embeddings_batch import _ollama_embed, _existing_ids, now_iso
from config import memory_dir, paths

EMBED_MODEL = os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest")
MAX_CHARS = 800
STATE_FILE = "capsule_ingester_state.json"

# Tylko te typy idą do indeksu (rozmowy + decyzje + raporty)
INDEXABLE_TYPES = {"chat_message", "decision", "review", "proposal", "report"}
MIN_TEXT_LEN = 30

# Projekty pomijane (rekursywny spam execution_bridge buga)
SKIP_PROJECTS = set(
    s.strip() for s in os.getenv("LIRA_CAPSULE_INGESTER_SKIP_PROJECTS", "ecosystem_monitor").split(",") if s.strip()
)


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def _capsule_text(payload: Dict[str, Any]) -> str:
    """Buduj tekst kapsuły z payload: title + summary + text."""
    if not isinstance(payload, dict):
        return str(payload or "")
    parts = []
    for key in ("title", "summary", "text"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return "\n".join(parts)


def _load_state(state_dir: Path) -> Dict[str, Any]:
    sp = state_dir / STATE_FILE
    if sp.exists():
        try:
            return json.loads(sp.read_text(encoding="utf-8", errors="ignore") or "{}")
        except Exception:
            pass
    return {"last_capsule_ts": None, "last_run": None, "total_ingested": 0}


def _save_state(state_dir: Path, state: Dict[str, Any]) -> None:
    sp = state_dir / STATE_FILE
    sp.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = now_iso()
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_new_capsules(after_ts: str | None, limit: int = 500) -> List[Dict[str, Any]]:
    from governance.capsule_bus import get_conn, DB_PATH
    type_placeholders = ",".join("?" for _ in INDEXABLE_TYPES)
    skip_clause = ""
    skip_params: tuple = ()
    if SKIP_PROJECTS:
        skip_placeholders = ",".join("?" for _ in SKIP_PROJECTS)
        skip_clause = f" AND (project IS NULL OR project NOT IN ({skip_placeholders}))"
        skip_params = tuple(SKIP_PROJECTS)
    with get_conn(DB_PATH) as conn:
        if after_ts:
            q = f"""SELECT id, sender, recipient, type, thread_id, project, status,
                          payload_json, created_at
                   FROM capsules
                   WHERE type IN ({type_placeholders}){skip_clause}
                     AND created_at > ?
                   ORDER BY created_at ASC LIMIT ?"""
            rows = conn.execute(q, (*INDEXABLE_TYPES, *skip_params, after_ts, limit)).fetchall()
        else:
            q = f"""SELECT id, sender, recipient, type, thread_id, project, status,
                          payload_json, created_at
                   FROM capsules
                   WHERE type IN ({type_placeholders}){skip_clause}
                   ORDER BY created_at ASC LIMIT ?"""
            rows = conn.execute(q, (*INDEXABLE_TYPES, *skip_params, limit)).fetchall()
    return [dict(r) for r in rows]


def ingest_once(model: str = EMBED_MODEL, limit: int = 200, existing: set | None = None) -> Dict[str, int]:
    mem = memory_dir()
    emb_dir = mem / "embeddings"
    emb_dir.mkdir(parents=True, exist_ok=True)
    index_path = emb_dir / "embeddings_index.jsonl"

    state = _load_state(emb_dir)
    if existing is None:
        existing = _existing_ids(index_path)

    capsules = _read_new_capsules(state.get("last_capsule_ts"), limit=limit)
    stats = {"scanned": len(capsules), "embedded": 0, "skipped": 0, "errors": 0}

    if not capsules:
        return stats

    last_ts = state.get("last_capsule_ts")
    with index_path.open("a", encoding="utf-8") as f:
        for cap in capsules:
            cap_id = cap.get("id", "")
            payload = cap.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}

            text = _capsule_text(payload if isinstance(payload, dict) else {})
            if len(text) < MIN_TEXT_LEN:
                stats["skipped"] += 1
                last_ts = cap.get("created_at") or last_ts
                continue

            text = text[:MAX_CHARS]
            rid = f"capsule:{cap_id}:{_sha256(text)[:10]}"
            if rid in existing:
                stats["skipped"] += 1
                last_ts = cap.get("created_at") or last_ts
                continue

            vec = _ollama_embed(model, text)
            if vec is None:
                stats["skipped"] += 1
                continue

            rec = {
                "schema": "lira.embedding_record.v1",
                "id": rid,
                "ts": now_iso(),
                "model": model,
                "source": {
                    "kind": "capsule",
                    "capsule_id": cap_id,
                    "sender": cap.get("sender"),
                    "recipient": cap.get("recipient"),
                    "type": cap.get("type"),
                    "thread_id": cap.get("thread_id"),
                    "project": cap.get("project"),
                    "status": cap.get("status"),
                    "created_at": cap.get("created_at"),
                },
                "text": text,
                "vector": vec,
                "meta": {"sha256": _sha256(text)},
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            existing.add(rid)
            stats["embedded"] += 1
            last_ts = cap.get("created_at") or last_ts

            if stats["embedded"] % 25 == 0:
                time.sleep(0.05)

    state["last_capsule_ts"] = last_ts
    state["total_ingested"] = int(state.get("total_ingested", 0)) + stats["embedded"]
    _save_state(emb_dir, state)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=int(os.getenv("LIRA_CAPSULE_INGESTER_INTERVAL", "300")))
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    if args.loop:
        print(f"[CapsuleIngester] Loop mode interval={args.interval}s", flush=True)
        # Load once: ingest_once() keeps the set current via existing.add();
        # re-reading the >1GB index every cycle pinned 1.8GB RSS (PA-030)
        existing = _existing_ids(memory_dir() / "embeddings" / "embeddings_index.jsonl")
        while True:
            try:
                stats = ingest_once(limit=args.limit, existing=existing)
                if stats["embedded"] or stats["errors"]:
                    print(f"[CapsuleIngester] {stats}", flush=True)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[CapsuleIngester] ERROR: {e!r}", flush=True)
            time.sleep(max(30, args.interval))
    else:
        stats = ingest_once(limit=args.limit)
        print(f"[CapsuleIngester] {stats}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
