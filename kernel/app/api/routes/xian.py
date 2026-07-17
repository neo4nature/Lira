from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter
from config import runtime_dir, paths

router = APIRouter()


def _load(fp: Path, default):
    try:
        if not fp.exists():
            return default
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return default


@router.get("/status")
def status():
    state_dir = runtime_dir() / "state"
    state = _load(state_dir / "xian_state.json", {})
    snapshot = state.get("snapshot") if isinstance(state, dict) else {}
    route = state.get("route") if isinstance(state, dict) else {}
    xal = state.get("xian_alignment") if isinstance(state, dict) else {}
    return {
        "status": "running" if state else "missing",
        "updated_at": state.get("updated_at") if isinstance(state, dict) else None,
        "role": state.get("role") if isinstance(state, dict) else None,
        "phase": snapshot.get("phase") if isinstance(snapshot, dict) else None,
        "policy_mode": snapshot.get("policy_mode") if isinstance(snapshot, dict) else None,
        "listening": snapshot.get("listening") if isinstance(snapshot, dict) else False,
        "reviewing": snapshot.get("reviewing") if isinstance(snapshot, dict) else False,
        "drift_count": snapshot.get("drift_count") if isinstance(snapshot, dict) else 0,
        "queue_depth": snapshot.get("queue_depth") if isinstance(snapshot, dict) else 0,
        "recommended_target": route.get("target") if isinstance(route, dict) else None,
        "recommended_confidence": route.get("confidence") if isinstance(route, dict) else None,
        "session_to_xian": xal.get("score") if isinstance(xal, dict) else None,
        "embed_available": xal.get("available") if isinstance(xal, dict) else False,
    }


@router.get("/report")
def report():
    return _load(runtime_dir() / "state" / "xian_report.json", {})


@router.get("/router")
def router_state():
    return _load(runtime_dir() / "state" / "xian_router_state.json", {})


@router.get("/vector")
def vector_state():
    p = paths()
    return _load(p["MEMORY_DIR"] / "identity" / "xian_shared_vector.vec.json", {})


@router.get("/temple_state")
def temple_state():
    from xian.temple_state import build_temple_state
    return build_temple_state()
