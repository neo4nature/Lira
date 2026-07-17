"""
ArchiveOffload

Goal: free space by moving non-essential archives/log bundles to external storage (e.g. SD card).

Safety:
- Only moves files from known "safe" directories (logs/, data/runtime/rebuild/, data/runtime/exports/)
- Never deletes by default. Moves to OFFLOAD_ROOT/ with a dated subfolder.
- If OFFLOAD_ROOT is missing, returns a message and does nothing.

Env:
- LIRA_OFFLOAD_ROOT (default: /media/neo/SD128/Lira_offload)  # example
- LIRA_OFFLOAD_DAYS (default: 7)  # move files older than N days
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _project_root() -> Path:
    # .../kernel/app/agents/core/archive_offload.py -> parents[4] is kernel
    return Path(__file__).resolve().parents[4]


def _safe_sources(root: Path) -> List[Path]:
    return [
        root / "logs",
        root / "data" / "runtime" / "rebuild",
        root / "data" / "runtime" / "exports",
    ]


def _older_than(path: Path, days: int) -> bool:
    try:
        st = path.stat()
        age_s = time.time() - st.st_mtime
        return age_s > days * 86400
    except Exception:
        return False


def plan(days: int | None = None) -> Dict:
    root = _project_root()
    days = int(days or int(os.getenv("LIRA_OFFLOAD_DAYS", "7")))
    items: List[Dict] = []
    for src in _safe_sources(root):
        if not src.exists():
            continue
        for p in src.rglob("*"):
            if p.is_file() and _older_than(p, days):
                try:
                    items.append({"path": str(p), "bytes": p.stat().st_size, "mtime": p.stat().st_mtime})
                except Exception:
                    items.append({"path": str(p)})
    return {"ts": _iso(), "days": days, "count": len(items), "items": items}


def run(days: int | None = None) -> Dict:
    root = _project_root()
    offload_root = Path(os.getenv("LIRA_OFFLOAD_ROOT", "/media/neo/SD128/Lira_offload")).expanduser()
    days = int(days or int(os.getenv("LIRA_OFFLOAD_DAYS", "7")))

    if not offload_root.exists():
        return {"ts": _iso(), "ok": False, "reason": f"OFFLOAD_ROOT missing: {offload_root}"}

    plan_obj = plan(days=days)
    items = plan_obj.get("items", [])
    if not items:
        return {"ts": _iso(), "ok": True, "moved": 0, "note": "Nothing to offload."}

    dest_dir = offload_root / f"offload_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    errors: List[Dict] = []
    for it in items:
        src_path = Path(it["path"])
        try:
            rel = src_path.relative_to(root)
            dst = dest_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst))
            moved += 1
        except Exception as e:
            errors.append({"path": it.get("path"), "error": repr(e)})

    return {"ts": _iso(), "ok": True, "moved": moved, "errors": errors, "dest": str(dest_dir)}
