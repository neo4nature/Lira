"""Cognitive daemon.

Purpose:
  - Keep the heavy/experimental cognitive module decoupled from the core API.
  - React to new daily snapshots (or periodically) and run the external cognitive night cycle.

This daemon is SAFE by default:
  - It will not run when background work is not allowed.
  - It will not run under veto != none.
  - It will not run under memory_mode == emergency.

Enable with:
  LIRA_ENABLE_COGNITIVE_DAEMON=1
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict

from agents.core.idle_gate import allows_background
from agents.core.runtime_stream import append_event
from agents.memory.memory_loader import read_json
from agents.cognition import cognitive_bridge
from config import runtime_dir, paths, ensure_dirs


def _memory_mode() -> str:
    try:
        P = paths()
        mm_path = Path(P.get("MEMORY_MODE", runtime_dir() / "state" / "memory_mode.json"))
        mm = read_json(mm_path, default={})
        return str(mm.get("mode") or "normal").lower()
    except Exception:
        return "normal"


def _latest_snapshot_date() -> str:
    """Read latest snapshot date from bus_signals (preferred) or memory_state."""
    st = runtime_dir() / "state"
    bus = read_json(st / "bus_signals.json", default={})
    if isinstance(bus, dict):
        mem = bus.get("memory")
        if isinstance(mem, dict):
            d = mem.get("snapshot_latest_date")
            if isinstance(d, str) and d:
                return d
    ms = read_json(st / "memory_state.json", default={})
    if isinstance(ms, dict):
        d = ms.get("snapshot_latest_date")
        if isinstance(d, str) and d:
            return d
    return ""


def main() -> None:
    ensure_dirs()
    poll_s = float(os.getenv("LIRA_COGNITIVE_POLL_SECONDS", "30"))
    last_seen = ""

    append_event("cognitive_daemon_start", "cognitive_daemon", {"poll_s": poll_s})
    while True:
        try:
            if os.getenv("LIRA_ENABLE_COGNITIVE_DAEMON", "0") != "1":
                time.sleep(poll_s)
                continue

            # Hard safety gates
            if _memory_mode() == "emergency":
                time.sleep(poll_s)
                continue

            ok, _reason = allows_background(require_veto_none=True)
            if not ok:
                time.sleep(poll_s)
                continue

            d = _latest_snapshot_date()
            if d and d != last_seen:
                append_event("cognitive_daemon_trigger", "cognitive_daemon", {"snapshot_date": d})
                cognitive_bridge.main()
                last_seen = d
        except Exception as e:
            append_event("cognitive_daemon_error", "cognitive_daemon", {"error": str(e)})

        time.sleep(poll_s)


if __name__ == "__main__":
    main()
