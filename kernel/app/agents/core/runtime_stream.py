"""Append-only JSONL runtime stream (canonical event log).

Designed to be low overhead and safe on partial writes.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import ensure_dirs, paths

def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def runtime_stream_path() -> Path:
    p = paths()
    # canonical stream lives in shared_context/events
    return p.get("RUNTIME_STREAM", p["EVENTS_DIR"] / "runtime.jsonl")

def append_event(kind: str, source: str, payload: Optional[Dict[str, Any]] = None, level: str = "info") -> None:
    ensure_dirs()
    payload = payload or {}
    evt = {
        "ts": utc_now(),
        "kind": kind,
        "source": source,
        "level": level,
        "payload": payload,
    }
    line = json.dumps(evt, ensure_ascii=False)
    fp = runtime_stream_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    # atomic append: open in append mode and write a full line; OS guarantees append is atomic for small writes on POSIX
    with fp.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def _parse_payload(s: str) -> Dict[str, Any]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {"raw": s}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--level", default="info")
    ap.add_argument("--payload", default="")
    args = ap.parse_args()
    append_event(args.kind, args.source, _parse_payload(args.payload), args.level)

if __name__ == "__main__":
    main()
