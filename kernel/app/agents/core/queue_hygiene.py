import time
from typing import Any, Dict, List, Optional
from pathlib import Path

from config import paths
from agents.memory.memory_loader import read_json, write_json

DEFAULT_MAX_TASKS = 500
DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days
DEFAULT_OVERFLOW_KEEP = 200

def _parse_ts(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        # ISO8601-ish, allow 'Z' suffix
        ts2 = ts.replace("Z", "+00:00")
        return time.mktime(time.strptime(ts2[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None

def hygiene(max_tasks: int = DEFAULT_MAX_TASKS,
            ttl_seconds: int = DEFAULT_TTL_SECONDS,
            overflow_keep: int = DEFAULT_OVERFLOW_KEEP) -> Dict[str, Any]:
    """Enforce queue limits + TTL; archive overflow tasks.

    This function is safe to run repeatedly.
    """
    p = paths()
    q_file = Path(p["QUEUE_FILE"])
    q_file.parent.mkdir(parents=True, exist_ok=True)

    queue: List[Dict[str, Any]] = read_json(str(q_file), default=[])
    if not isinstance(queue, list):
        queue = []

    now = time.time()
    kept: List[Dict[str, Any]] = []
    expired: List[Dict[str, Any]] = []
    for t in queue:
        ts = None
        if isinstance(t, dict):
            ts = t.get("ts") or t.get("created_at")
        t_epoch = _parse_ts(ts)
        if t_epoch is not None and (now - t_epoch) > ttl_seconds:
            expired.append(t)
        else:
            kept.append(t)

    # sort: keep higher importance first, then newest
    def _score(t: Dict[str, Any]) -> float:
        imp = 0
        try:
            imp = int(t.get("importance", 0))
        except Exception:
            imp = 0
        ts = _parse_ts(t.get("ts") or t.get("created_at")) or 0.0
        return imp * 1e12 + ts

    kept.sort(key=_score, reverse=True)

    overflow: List[Dict[str, Any]] = []
    if len(kept) > max_tasks:
        overflow = kept[max_tasks:]
        kept = kept[:max_tasks]

    # Archive overflow+expired
    archived = expired + overflow
    archive_path = Path(p["RUNTIME_DIR"]) / "queue" / f"queue_overflow_{time.strftime('%Y%m%d_%H%M%S')}.json"
    if archived:
        # keep only last N archived tasks to avoid huge archive
        archived = archived[:max(1, overflow_keep)]
        write_json(str(archive_path), archived)

    write_json(str(q_file), kept)

    return {
        "ok": True,
        "queue_len_before": len(queue),
        "queue_len_after": len(kept),
        "expired_removed": len(expired),
        "overflow_removed": len(overflow),
        "archived_file": str(archive_path) if archived else None,
    }
