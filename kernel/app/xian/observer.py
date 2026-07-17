from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from config import paths


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def build_snapshot() -> Dict[str, Any]:
    p = paths()
    state_dir = p["STATE_DIR"]

    bus = _read_json(state_dir / "bus_signals.json", {})
    phase = _read_json(state_dir / "phase_state.json", {})
    policy = _read_json(state_dir / "policy_state.json", {})
    energy = _read_json(state_dir / "energy_state.json", {})
    alignment = _read_json(state_dir / "alignment_state.json", {})
    watchdog = _read_json(Path(p.get("WATCHDOG_REPORT", state_dir / "watchdog_report.json")), {})
    regulator = _read_json(state_dir / "loop_regulator.json", {})
    world = _read_json(state_dir / "world_state.json", {})
    queue_data = _read_json(p["QUEUE_FILE"], [])

    queue_depth = len(queue_data) if isinstance(queue_data, list) else 0
    current_phase = (
        phase.get("current_phase")
        or phase.get("phase")
        or (bus.get("rhythm") or {}).get("phase")
        or "UNKNOWN"
    ) if isinstance(phase, dict) and isinstance(bus, dict) else "UNKNOWN"

    current_policy = (
        policy.get("current_policy")
        or policy.get("selected_mode")
        or policy.get("active_profile")
        or (bus.get("policy_state") or {}).get("current_policy")
        or (bus.get("policy_state") or {}).get("selected_mode")
        or (bus.get("policy_state") or {}).get("active_profile")
        or "UNKNOWN"
    ) if isinstance(policy, dict) and isinstance(bus, dict) else "UNKNOWN"

    energy_score = None
    if isinstance(energy, dict):
        for key in ("energy_score", "score", "current_energy"):
            if isinstance(energy.get(key), (int, float)):
                energy_score = float(energy[key])
                break
    if energy_score is None:
        energy_score = 0.5

    regulator_pressure = None
    if isinstance(regulator, dict):
        for key in ("pressure", "system_pressure", "load_score"):
            if isinstance(regulator.get(key), (int, float)):
                regulator_pressure = float(regulator[key])
                break
    if regulator_pressure is None:
        cpu = float(regulator.get("cpu_pct", regulator.get("cpu", 0.0)) or 0.0)
        mem = float(regulator.get("mem_pct", regulator.get("memory", 0.0)) or 0.0)
        regulator_pressure = max(cpu, mem) / 100.0 if max(cpu, mem) > 1.0 else max(cpu, mem)

    veto = (bus.get("operational_veto") or {}) if isinstance(bus, dict) else {}
    hard_veto = bool(veto.get("hard") or veto.get("active") is True)
    warn_veto = bool(veto.get("warn"))
    drift = (bus.get("drift") or {}) if isinstance(bus, dict) else {}
    drift_count = int(drift.get("count", 0) or 0)

    listening = current_phase == "ACCUMULATE"
    reviewing = hard_veto or warn_veto or bool((watchdog.get("alerts") or []))

    # Criticality: kombinacja pressure + drift + queue jako sygnał "ile się dzieje"
    _crit = 0.0
    if isinstance(regulator, dict):
        _crit += min(1.0, float(regulator.get("metrics", {}).get("cpu_pct", 0.0) or 0.0) / 100.0) * 0.4
    _crit += min(1.0, drift_count / 5.0) * 0.35
    _crit += min(1.0, queue_depth / 20.0) * 0.25
    criticality = round(min(1.0, _crit), 4)

    # Semantic interpretation — advisory signal z alignment_publisher (Krok 4)
    semantic = alignment.get("semantic_interpretation", {}) if isinstance(alignment, dict) else {}
    semantic_mode_hint = semantic.get("mode_hint", "none")
    semantic_mismatch = float(semantic.get("context_mismatch_score", 0.0) or 0.0)
    semantic_confidence = float(semantic.get("confidence", 0.0) or 0.0)
    semantic_top_signals = semantic.get("top_signals", {})
    semantic_triggers = semantic.get("trigger_terms", [])

    return {
        "phase": current_phase,
        "policy_mode": current_policy,
        "energy_score": round(float(energy_score), 6),
        "global_alignment": float(alignment.get("global_alignment", 0.0) or 0.0),
        "session_to_neo": float(alignment.get("session_to_neo", 0.0) or 0.0),
        "session_to_lira": float(alignment.get("session_to_lira", 0.0) or 0.0),
        "session_to_soryel": float(alignment.get("session_to_soryel", 0.0) or 0.0),
        "recommended_mode": alignment.get("recommended_mode") or current_policy,
        "regulator_pressure": round(float(regulator_pressure), 6),
        "queue_depth": queue_depth,
        "watchdog_status": "alert" if (watchdog.get("alerts") or []) else "ok",
        "conversation_active": bool(bus.get("conversation_active")) if isinstance(bus, dict) else False,
        "listening": listening,
        "reviewing": reviewing,
        "hard_veto": hard_veto,
        "warn_veto": warn_veto,
        "drift_count": drift_count,
        "criticality": criticality,
        "models": bus.get("models") if isinstance(bus, dict) else None,
        "world": world if isinstance(world, dict) else {},
        "semantic_mode_hint": semantic_mode_hint,
        "semantic_mismatch": round(semantic_mismatch, 4),
        "semantic_confidence": round(semantic_confidence, 4),
        "semantic_top_signals": semantic_top_signals,
        "semantic_triggers": semantic_triggers[:5],
    }
