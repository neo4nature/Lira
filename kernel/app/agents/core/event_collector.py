"""Hybrid bridge: shared_context/events -> runtime queue.

Reads event JSON files from EVENTS_DIR and enqueues them into queue_tasks.json
exactly once (tracked by COLLECTOR_STATE).
"""

from __future__ import annotations

import json
from pathlib import Path

from config import ensure_dirs, paths
from agents.core.queue_utils import enqueue, make_task
from agents.core.runtime_stream import append_event
from agents.memory.memory_loader import read_json as safe_read_json, write_json


def load_state(state_path: Path):
    s = safe_read_json(state_path, default={"processed": []})
    if "processed" not in s or not isinstance(s.get("processed"), list):
        s["processed"] = []
    return s


def save_state(state_path: Path, state):
    write_json(state_path, state)


def main(limit: int = 500):
    ensure_dirs()
    p = paths()
    events_dir = p["EVENTS_DIR"]
    state_path = p["COLLECTOR_STATE"]
    state = load_state(state_path)

    processed = set(state["processed"])
    files = sorted([x for x in events_dir.glob("*.json") if x.is_file()])

    added = 0
    for f in files:
        if f.name in processed:
            continue

        data = safe_read_json(f, default={})

        if isinstance(data, list):
            kind = "list_event"
            payload = data
        elif isinstance(data, dict):
            kind = data.get("kind", "event")
            payload = data.get("payload", data)
        else:
            kind = "unknown_event"
            payload = {"raw": str(data)}

        enqueue(make_task(kind=f"event:{kind}", source=str(f), payload=payload, importance=2, tags=["event"]))
        state["processed"].append(f.name)
        added += 1
        if added >= limit:
            break

    save_state(state_path, state)
    append_event("event_collected", "agents.core.event_collector", {"added": added, "queue_file": str(p["QUEUE_FILE"])})
    print(f"OK: collected {added} new event(s) -> {p['QUEUE_FILE']}")


if __name__ == "__main__":
    main()
