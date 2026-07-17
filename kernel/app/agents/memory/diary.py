"""Diary logger (JSONL) — human-auditable trace.

We keep it lightweight:
  - One JSONL per day
  - Append-only (safe)
  - Used by NightCycle to generate MD "Dzienny Obraz" with task trail
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import memory_dir


def diary_path(ts: Optional[float] = None) -> Path:
    dt = datetime.fromtimestamp(ts or time.time())
    year = dt.strftime("%Y")
    day = dt.strftime("%Y-%m-%d")
    p = memory_dir() / "diary" / year
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{day}.diary.jsonl"


def append_entry(entry: Dict[str, Any], ts: Optional[float] = None) -> Path:
    t = float(ts if ts is not None else time.time())
    entry = {
        "schema": "lira.diary_entry.v1",
        "ts": t,
        **entry,
    }
    p = diary_path(t)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return p
