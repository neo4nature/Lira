from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from config import paths, runtime_dir, shared_context_dir


def _load(fp: Path, default: Any):
    try:
        if not fp.exists():
            return default
        return json.loads(fp.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _age_s(fp: Path) -> float | None:
    try:
        if not fp.exists():
            return None
        return round(time.time() - fp.stat().st_mtime, 2)
    except Exception:
        return None



def _read_active_speaker() -> dict[str, Any]:
    rt = runtime_dir()
    sc = shared_context_dir()
    candidates = [
        rt / "state" / "active_speaker.json",
        rt / "voice" / "active_speaker.json",
        rt / "active_speaker.json",
        sc / "state" / "active_speaker.json",
    ]
    for fp in candidates:
        data = _load(fp, None)
        if not isinstance(data, dict):
            continue
        speaker = (
            data.get("active_speaker")
            or data.get("speaker")
            or data.get("name")
            or data.get("who")
        )
        if speaker:
            return {
                "name": str(speaker),
                "updated_at": data.get("updated_at") or data.get("ts") or data.get("timestamp"),
                "freshness_s": _age_s(fp),
                "source": str(fp),
            }
    return {"name": None, "updated_at": None, "freshness_s": None, "source": None}


def _agent_state(name: str, speaker: str | None, route_target: str | None, snapshot: dict[str, Any], queue_depth: int) -> tuple[str, float]:
    nl = name.lower()
    sl = (speaker or "").lower()
    rtl = (route_target or "").lower()

    if sl == nl:
        return "speaking", 1.0

    if nl == "xian":
        if snapshot.get("phase") in {"INTEGRATE", "DECIDE", "REFLECT"}:
            return "active", 0.88
        if route_target and rtl != "status_only":
            return "active", 0.75
        return "idle", 0.48

    if nl == "soryel":
        if rtl == "soryel":
            return "active", 0.86
        return "idle", 0.52

    if nl == "lira":
        if rtl == "lira":
            return "active", 0.8
        return "idle", 0.5

    if nl == "neo":
        if snapshot.get("hard_veto") or queue_depth > 0 or snapshot.get("conversation_active"):
            return "active", 0.83
        return "idle", 0.58

    return "idle", 0.5


def build_temple_state() -> Dict[str, Any]:
    p = paths()
    state_dir = p["STATE_DIR"]

    xian_state = _load(state_dir / "xian_state.json", {})
    xian_report = _load(state_dir / "xian_report.json", {})
    xian_router = _load(state_dir / "xian_router_state.json", {})
    bus = _load(state_dir / "bus_signals.json", {})
    phase_state = _load(state_dir / "phase_state.json", {})
    align_state = _load(state_dir / "alignment_state.json", {})
    crit_state = _load(state_dir / "criticality_state.json", {})
    watchdog = _load(Path(p.get("WATCHDOG_REPORT", state_dir / "watchdog_report.json")), {})
    queue_data = _load(p["QUEUE_FILE"], [])
    speaker = _read_active_speaker()

    snapshot = xian_state.get("snapshot") if isinstance(xian_state, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    route = xian_state.get("route") if isinstance(xian_state, dict) else {}
    route = route if isinstance(route, dict) and route else (xian_router if isinstance(xian_router, dict) else {})
    queue_depth = len(queue_data) if isinstance(queue_data, list) else int(snapshot.get("queue_depth", 0) or 0)

    phase = (
        snapshot.get("phase")
        or xian_report.get("phase")
        or phase_state.get("current_phase")
        or phase_state.get("phase")
        or ((bus.get("phase") or {}).get("phase") if isinstance(bus, dict) else None)
        or "UNKNOWN"
    )
    policy = (
        snapshot.get("policy_mode")
        or xian_report.get("policy_mode")
        or route.get("policy")
        or "UNKNOWN"
    )
    alignment = float(
        snapshot.get("global_alignment")
        or align_state.get("global_alignment")
        or xian_state.get("xian_alignment", {}).get("score")
        or 0.0
    )
    criticality = float(
        snapshot.get("criticality")
        or crit_state.get("criticality_score")
        or crit_state.get("score")
        or 0.0
    )

    watchdog_status = snapshot.get("watchdog_status") or ("alert" if (watchdog.get("alerts") or []) else "ok")
    conversation_active = bool(snapshot.get("conversation_active") or (bus.get("world", {}).get("conversation", {}).get("active") if isinstance(bus, dict) else False))

    # Crystal behavior — intentionally calm by default
    brightness = round(max(0.72, min(1.28, 0.82 + alignment * 0.35 + (0.08 if conversation_active else 0.0))), 3)
    speed = round(max(0.85, min(2.2, 1.0 + criticality * 1.8 + (0.15 if phase == "ACT" else 0.0))), 3)
    scale = round(max(0.96, min(1.14, 1.0 + criticality * 0.08 + (0.02 if watchdog_status == "alert" else 0.0))), 3)

    speaker_name = speaker.get("name")
    route_target = route.get("target")

    agents = {}
    for agent in ("Lira", "Soryel", "Xian", "Neo"):
        state, intensity = _agent_state(agent, speaker_name, route_target, snapshot, queue_depth)
        agents[agent.lower()] = {
            "state": state,
            "intensity": intensity,
        }

    return {
        "schema": "xian.temple_state.v1",
        "updated_at": xian_state.get("updated_at") or xian_report.get("updated_at"),
        "phase": phase,
        "policy_mode": policy,
        "speaker": speaker,
        "routing": {
            "target": route_target,
            "confidence": route.get("confidence"),
        },
        "queue": {"depth": queue_depth},
        "crystal": {
            "alignment": round(alignment, 4),
            "criticality": round(criticality, 4),
            "watchdog_status": watchdog_status,
            "conversation_active": conversation_active,
            "brightness": brightness,
            "speed": speed,
            "scale": scale,
        },
        "agents": agents,
    }
