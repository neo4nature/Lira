"""prepare_data_night.py

Nocny "przegląd systemu" (prepare data): buduje lekki indeks bazy i stanu.

Cel:
- Jedno miejsce, z którego Cognitive / Reflective / Watchdog mogą brać kontekst:
  * świeżość bus/telemetry/runtime
  * stan kolejki
  * liczność eventów
  * podstawowa lista plików pamięci (memory_index)
  * podstawowe metryki dysków (Docker1/SD/dm-0 przez LIRA_* rooty)

Wyjście:
- data/memory/root/base_index.json

Zasada bezpieczeństwa:
- Skrypt nie dotyka busa (single-writer: DecisionLoop).
- Skrypt jest lekki (bez LLM).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import ensure_dirs, paths, archive_root, secure_root
from agents.memory.index_memory import build_index
from agents.memory.memory_loader import read_json, write_json
from agents.core.runtime_stream import append_event


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _disk_usage(p: Path) -> Dict[str, Any]:
    try:
        import shutil

        total, used, free = shutil.disk_usage(str(p))
        gb = 1024 ** 3
        return {
            "path": str(p),
            "total_gb": round(total / gb, 2),
            "used_gb": round(used / gb, 2),
            "free_gb": round(free / gb, 2),
        }
    except Exception as e:
        return {"path": str(p), "error": str(e)}


def _stat_file(p: Path) -> Dict[str, Any]:
    try:
        if not p.exists():
            return {"path": str(p), "exists": False}
        st = p.stat()
        return {
            "path": str(p),
            "exists": True,
            "bytes": int(st.st_size),
            "mtime": datetime.utcfromtimestamp(st.st_mtime).isoformat(timespec="seconds") + "Z",
            "age_s": round(time.time() - st.st_mtime, 3),
        }
    except Exception as e:
        return {"path": str(p), "error": str(e)}


def _count_files(dir_path: Path, pattern: str = "*") -> int:
    try:
        if not dir_path.exists():
            return 0
        return sum(1 for _ in dir_path.glob(pattern))
    except Exception:
        return 0


def _count_jsonl_lines(p: Path, max_lines: int = 200_000) -> int:
    """Count JSONL lines with a safety cap (best-effort)."""
    try:
        if not p.exists() or not p.is_file():
            return 0
        n = 0
        with p.open("rb") as f:
            for _ in f:
                n += 1
                if n >= max_lines:
                    break
        return n
    except Exception:
        return 0


def _embeddings_status(p: dict) -> Dict[str, Any]:
    """Return a compact status of embeddings index + last batch report."""
    emb_index = Path(p.get("EMBEDDINGS_INDEX", Path(p["MEMORY_DIR"]) / "embeddings" / "embeddings_index.jsonl"))
    report_p = Path(p["RUNTIME_DIR"]) / "state" / "embeddings_batch_report.json"
    report = read_json(report_p, default={})

    line_count = _count_jsonl_lines(emb_index)
    model = None
    if isinstance(report, dict):
        model = report.get("model") or report.get("embed_model")

    ready = bool(emb_index.exists() and line_count > 0)

    return {
        "ready": ready,
        "index": _stat_file(emb_index),
        "records_est": line_count,
        "batch_report": _stat_file(report_p),
        "model": model,
        "notes": "records_est is line count (capped) of embeddings_index.jsonl",
    }


def _queue_stats(queue_file: Path) -> Dict[str, Any]:
    q = read_json(queue_file, default=[])
    if not isinstance(q, list):
        return {"len": 0, "error": "queue_not_list"}
    oldest = None
    try:
        for item in q:
            ts = item.get("ts") if isinstance(item, dict) else None
            if ts and (oldest is None or ts < oldest):
                oldest = ts
    except Exception:
        pass
    return {"len": len(q), "oldest_ts": oldest}


def build_base_index() -> Dict[str, Any]:
    ensure_dirs()
    p = paths()
    md = Path(p["MEMORY_DIR"])
    rt = Path(p["RUNTIME_DIR"])
    sc = Path(p["SHARED_CONTEXT_DIR"])

    bus = rt / "state" / "bus_signals.json"
    reg = rt / "state" / "loop_regulator.json"
    thermo = Path(os.getenv("LIRA_THERMO_PATH", str(Path(p.get("DATA_ROOT", rt.parent)) / "logs" / "thermo.jsonl")))
    runtime_stream = Path(p["RUNTIME_STREAM"])

    base: Dict[str, Any] = {
        "schema": "lira.base_index.v1",
        "updated_at": utc_now(),
        "paths": {
            "docker1_root": str(Path(p["PROJECT_ROOT"]).parent),
            "data_root": str(p["DATA_ROOT"]),
            "archive_root": str(archive_root()),
            "secure_root": str(secure_root()),
        },
        "freshness": {
            "bus": _stat_file(bus),
            "regulator": _stat_file(reg),
            "thermo": _stat_file(thermo),
            "runtime_stream": _stat_file(runtime_stream),
        },
        "queue": _queue_stats(Path(p["QUEUE_FILE"])),
        "counts": {
            "events_json": _count_files(Path(p["EVENTS_DIR"]), "*.json"),
            "rebuild_files": _count_files(Path(p["REBUILD_DIR"]), "*")
        },
        "embeddings": _embeddings_status(p),
        "disks": {
            "data_root": _disk_usage(Path(p["DATA_ROOT"])),
            "archive_root": _disk_usage(archive_root()),
            "secure_root": _disk_usage(secure_root()),
        },
        "memory_index": build_index(md, limit=2000),
    }

    # small summary for dashboards
    base["summary"] = {
        "memory_files": base.get("memory_index", {}).get("count"),
        "events_json": base.get("counts", {}).get("events_json"),
        "queue_len": base.get("queue", {}).get("len"),
        "embeddings_ready": base.get("embeddings", {}).get("ready"),
        "embeddings_records_est": base.get("embeddings", {}).get("records_est"),
    }
    return base


def run() -> Dict[str, Any]:
    p = paths()
    out = Path(p["BASE_INDEX"])
    base = build_base_index()
    write_json(out, base)

    append_event(
        kind="base_index_updated",
        source="prepare_data_night",
        payload={
            "out": str(out),
            "memory_count": base.get("memory_index", {}).get("count"),
            "events_json": base.get("counts", {}).get("events_json"),
            "queue_len": base.get("queue", {}).get("len"),
            "embeddings_ready": base.get("embeddings", {}).get("ready"),
            "embeddings_records_est": base.get("embeddings", {}).get("records_est"),
        },
        level="info",
    )
    return {"ok": True, "out": str(out), "updated_at": base["updated_at"]}


def main():
    res = run()
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
