from __future__ import annotations
from typing import Dict, Any, Tuple
import time
import math

from utils.state_io import get_env_bool, get_env_float, get_env_int

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _tags(edge: Dict[str, Any]) -> set:
    t = edge.get("tags") or []
    if isinstance(t, str):
        t = [t]
    return set(str(x) for x in t)

def decay_graph_edges(graph: Dict[str, Any], dt_hours: float, base_decay: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # graph format expected: {"edges":[{"strength":0.5,"activation_count":3,"last_activated_ts":..., "tags":[...]}], ...}
    edges = graph.get("edges") or []
    if not isinstance(edges, list):
        return graph, {"edges_total": 0, "edges_decayed": 0}

    edges_decayed = 0
    for e in edges:
        if not isinstance(e, dict):
            continue
        s = float(e.get("strength") or 0.0)
        tags = _tags(e)
        act_count = int(e.get("activation_count") or 0)

        # exceptions slow decay
        decay = base_decay
        if "strategic" in tags:
            decay = max(decay, 0.9999)
        if "critical_error" in tags:
            decay = max(decay, 0.999)
        if act_count >= 20:
            decay = max(decay, 0.9995)

        # apply decay over dt
        s2 = s * (decay ** max(0.0, dt_hours))
        # clamp
        s2 = max(0.0, min(1.0, s2))
        if abs(s2 - s) > 1e-9:
            edges_decayed += 1
        e["strength"] = s2

    graph["edges"] = edges
    return graph, {"edges_total": len(edges), "edges_decayed": edges_decayed}

def decay_workspace(workspace: Dict[str, Any], factor: float, threshold: float = 0.05) -> Dict[str, Any]:
    items = workspace.get("items") or []
    if not isinstance(items, list):
        return workspace
    kept = []
    for it in items:
        if not isinstance(it, dict):
            continue
        a = float(it.get("activation") or 1.0)
        a2 = a * factor
        it["activation"] = max(0.0, min(1.0, a2))
        if it["activation"] >= threshold:
            kept.append(it)
    workspace["items"] = kept
    workspace["items_count"] = len(kept)
    return workspace

def run_forgetting(world_state: Dict[str, Any], phase_state: Dict[str, Any]) -> Dict[str, Any]:
    """Returns (possibly updated) world_state + forgetting stats. Mutates only if enforce enabled."""
    enforce = get_env_bool("LIRA_DECAY_ENFORCE", False)
    run_every = get_env_int("LIRA_FORGETTING_RUN_EVERY_N_TICKS", 1)
    base_decay = get_env_float("LIRA_GRAPH_DECAY_FACTOR", 0.995)  # per hour
    ws_decay = get_env_float("LIRA_WORKSPACE_DECAY_FACTOR", 0.85)

    phase = (phase_state.get("phase") or "").upper()
    tick_id = phase_state.get("tick_id") or 0

    allowed = phase in {"REFLECT", "DECAY", "REFLECT/DECAY"}
    if not allowed:
        return {"ran": False, "allowed": False, "phase": phase, "enforce": enforce, "tick_id": tick_id}

    if run_every > 1 and (int(tick_id) % int(run_every) != 0):
        return {"ran": False, "allowed": True, "phase": phase, "enforce": enforce, "tick_id": tick_id}

    # compute dt_hours from last run
    now = time.time()
    last = float(world_state.get("_forgetting_last_run_epoch") or now)
    dt_hours = max(0.0, (now - last) / 3600.0)

    graph = world_state.get("world_graph") or {"edges": []}
    workspace = world_state.get("workspace") or {"items": []}

    # Perform decay on copies if shadow mode
    if not enforce:
        graph_copy = {"edges": [dict(e) for e in (graph.get("edges") or [])]}
        workspace_copy = {"items": [dict(i) for i in (workspace.get("items") or [])]}
        g2, gstats = decay_graph_edges(graph_copy, dt_hours, base_decay)
        w2 = decay_workspace(workspace_copy, ws_decay)
        preview = {"world_graph": g2, "workspace": w2, "stats": gstats, "dt_hours": dt_hours, "ts": _now_iso()}
        return {"ran": True, "allowed": True, "phase": phase, "enforce": False, "tick_id": tick_id, "preview": preview}

    # Enforce: mutate world_state
    graph2, gstats = decay_graph_edges(graph, dt_hours, base_decay)
    workspace2 = decay_workspace(workspace, ws_decay)
    world_state["world_graph"] = graph2
    world_state["workspace"] = workspace2
    world_state["_forgetting_last_run_epoch"] = now

    return {"ran": True, "allowed": True, "phase": phase, "enforce": True, "tick_id": tick_id, "dt_hours": dt_hours, **gstats, "workspace_items": workspace2.get("items_count", len(workspace2.get("items") or [])), "ts": _now_iso()}
