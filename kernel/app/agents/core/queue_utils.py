import json
import os
import fcntl
from datetime import datetime
from typing import Any, Dict, List
from config import ensure_dirs, paths
from agents.memory.memory_loader import read_json, write_json

def now():
    return datetime.now().isoformat(timespec="seconds")

def _locked_read_write(qf, update_fn):
    """Atomic update for the JSON queue file using an exclusive file lock (Linux)."""
    ensure_dirs()
    qf.parent.mkdir(parents=True, exist_ok=True)
    # a+ creates file if missing
    with open(qf, 'a+', encoding='utf-8') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            raw = f.read().strip()
            data = []
            if raw:
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, list):
                        data = obj
                except Exception:
                    data = []
            new_data = update_fn(data)
            # write back
            f.seek(0)
            f.truncate()
            json.dump(new_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
            return new_data
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def load_queue() -> List[Dict[str, Any]]:
    qf = paths()["QUEUE_FILE"]
    def _noop(data):
        return data
    data = _locked_read_write(qf, _noop)
    return data if isinstance(data, list) else []


def save_queue(queue: List[Dict[str, Any]]):
    qf = paths()["QUEUE_FILE"]
    def _set(_):
        return queue
    _locked_read_write(qf, _set)


def enqueue(task: Dict[str, Any]):
    qf = paths()["QUEUE_FILE"]
    def _add(data):
        data = data if isinstance(data, list) else []
        data.append(task)
        return data
    _locked_read_write(qf, _add)
    return qf


def make_task(kind: str, source: str, payload: Any, importance: int = 3, tags=None) -> Dict[str, Any]:
    return {
        "ts": now(),
        "kind": kind,
        "source": source,
        "importance": int(importance),
        "tags": tags or [],
        "payload": payload,
    }
