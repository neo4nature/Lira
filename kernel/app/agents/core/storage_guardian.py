"""
Lira Core: StorageGuardian

Continuously monitors disk space and writes a state flag to data/shared_context/state/storage.json.
If low space is detected, evolve/auto-install should be treated as read-only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from .installer import DEFAULT_GUARD_PATHS, DEFAULT_MIN_FREE_GB, free_gb

def _state_path() -> Path:
    # ../../../../data/shared_context/state/storage.json relative to this file
    return Path(__file__).resolve().parents[4] / "data" / "shared_context" / "state" / "storage.json"

def _ts() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

def check() -> Dict:
    details = []
    blocked = False
    for p in DEFAULT_GUARD_PATHS:
        try:
            fg = free_gb(p)
            details.append({"path": p, "free_gb": round(fg, 3), "min_free_gb": DEFAULT_MIN_FREE_GB})
            if fg < DEFAULT_MIN_FREE_GB:
                blocked = True
        except Exception as e:
            details.append({"path": p, "error": repr(e)})
    state = {
        "timestamp": _ts(),
        "blocked": blocked,
        "details": details,
    }
    sp = _state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state
