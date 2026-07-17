from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from config import paths
from agents.memory.memory_loader import read_json
from agents.memory.daily_context import get_daily_capsules

def load_snapshot() -> Dict[str, Any]:
    """Lightweight snapshot for Cognitive/Reflective/Decision.
    Reads base_index + watchdog_report (if present) and returns a compact dict.
    """
    P = paths()
    snap: Dict[str, Any] = {}

    base = read_json(Path(P.get("BASE_INDEX", Path(P["MEMORY_DIR"]) / "root" / "base_index.json")), default=None)
    if isinstance(base, dict):
        snap["base_index"] = {
            "updated_at": base.get("updated_at"),
            "counts": base.get("counts"),
            "queue": base.get("queue"),
            "disks": base.get("disks"),
            "mounts": base.get("mounts"),
            "freshness": base.get("freshness"),
        }

    wd = read_json(Path(P.get("WATCHDOG_REPORT", Path(P["STATE_DIR"]) / "watchdog_report.json")), default=None)
    if isinstance(wd, dict):
        # compact to avoid huge payload
        snap["watchdog"] = {
            "ts": wd.get("ts"),
            "alerts": wd.get("alerts"),
            "disks": wd.get("disks"),
            "mounts": wd.get("mounts"),
            "freshness": wd.get("freshness"),
            "queue": wd.get("queue"),
        }

    # Memory state (written by NightCycle/TurmalinLoop; consumed by loops)
    mem_state = read_json(Path(P.get("MEMORY_STATE", Path(P["STATE_DIR"]) / "memory_state.json")), default=None)
    if isinstance(mem_state, dict):
        snap["memory_state"] = {
            "snapshot_latest_date": mem_state.get("snapshot_latest_date"),
            "snapshot_ready": mem_state.get("snapshot_ready"),
            "base_index_mtime": mem_state.get("base_index_mtime"),
            "base_index_bytes": mem_state.get("base_index_bytes"),
            "last_promotion": mem_state.get("last_promotion"),
            "updated_at": mem_state.get("updated_at"),
        }

        # Pull a small daily capsule context for reflective/cognitive loops
        date = mem_state.get("snapshot_latest_date")
        if isinstance(date, str) and date:
            try:
                snap["daily_capsules"] = get_daily_capsules(date, limit=20)
            except Exception:
                pass

    return snap
