from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from config import paths
from agents.memory.semantic_retrieval import search as semantic_search
from agents.memory.daily_context import get_daily_capsules
from agents.memory.base_index_sqlite import init_db as _base_init
from agents.memory.base_index_sqlite import db_path as _base_db_path
from agents.memory.turmalin_cache import cache_path as _turmalin_cache_path

router = APIRouter()


@router.get("/index")
def get_index():
    p = paths()["MEMORY_INDEX"]
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"memory_index not found at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


@router.get("/search")
def search_memory(q: str, top_k: int = 6):
    """Semantic memory search (embeddings). Falls back to empty list if unavailable."""
    try:
        res = semantic_search(q, top_k=top_k)
        return {"query": q, "top_k": top_k, "results": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"semantic search failed: {e}")


@router.get("/embeddings/status")
def embeddings_status():
    """Embeddings index status (read-only)."""
    P = paths()
    idx = P.get("EMBEDDINGS_INDEX")
    report_path = (P["RUNTIME_DIR"] / "state" / "embeddings_batch_report.json")
    out = {
        "index_path": str(idx),
        "exists": bool(idx and idx.exists()),
        "bytes": idx.stat().st_size if idx and idx.exists() else 0,
        "mtime": idx.stat().st_mtime if idx and idx.exists() else None,
        "records_est": 0,
        "batch_report": None,
    }

    # Estimate records by counting lines up to a cap (avoid huge memory use)
    if idx and idx.exists() and idx.stat().st_size > 0:
        try:
            n = 0
            with idx.open("r", encoding="utf-8", errors="ignore") as f:
                for _ in f:
                    n += 1
                    if n >= 200_000:
                        break
            out["records_est"] = n
        except Exception:
            out["records_est"] = 0

    if report_path.exists():
        try:
            out["batch_report"] = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            out["batch_report"] = None

    out["ready"] = out["exists"] and out["bytes"] > 0
    return out


@router.get("/base_index/status")
def base_index_status():
    """Stable BaseIndex (SQLite) status."""
    try:
        _base_init()
    except Exception:
        pass
    db = _base_db_path()
    out = {
        "db_path": str(db),
        "exists": db.exists(),
        "bytes": db.stat().st_size if db.exists() else 0,
        "mtime": db.stat().st_mtime if db.exists() else None,
    }
    return out


@router.get("/turmalin/status")
def turmalin_status():
    """TurmalinCache (temporary JSONL) status."""
    p = _turmalin_cache_path()
    return {
        "cache_path": str(p),
        "exists": p.exists(),
        "bytes": p.stat().st_size if p.exists() else 0,
        "mtime": p.stat().st_mtime if p.exists() else None,
    }


@router.get("/context/daily")
def daily_context(date: str = "", limit: int = 30):
    """Return the daily snapshot capsules for a given date (YYYY-MM-DD).

    This is intended for Cognitive/Reflective loops and dashboards.
    """
    try:
        items = get_daily_capsules(date or None, limit=max(1, min(200, int(limit))))
        return {"date": (date or None), "limit": limit, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"daily context failed: {e}")
