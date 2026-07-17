from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict


def _iso(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _current_models() -> Dict[str, str]:
    return {
        "fast": os.getenv("MODEL_LIGHT", "qwen2.5:3b"),
        "code": os.getenv("MODEL_CODE", "qwen2.5-coder:7b"),
        "deep": os.getenv("MODEL_DEEP", os.getenv("MODEL_LIGHT", "qwen2.5:3b")),
    }


def _error_pressure(world_state: Dict[str, Any] | None) -> tuple[float, str]:
    """Compute a simple error pressure proxy (0..1) + reason.

    Uses:
      - port probes (api/ollama)
      - watchdog alerts list
    """
    if not isinstance(world_state, dict):
        return 0.0, "no_world"

    ports = world_state.get("ports") if isinstance(world_state.get("ports"), dict) else {}
    api_open = bool(((ports.get("api") or {}) if isinstance(ports.get("api"), dict) else {}).get("open"))
    ollama_open = bool(((ports.get("ollama") or {}) if isinstance(ports.get("ollama"), dict) else {}).get("open"))

    p = 0.0
    reasons = []
    if not api_open:
        p += 0.5
        reasons.append("api_down")
    if not ollama_open:
        p += 0.7
        reasons.append("ollama_down")

    alerts = world_state.get("alerts")
    if isinstance(alerts, list) and alerts:
        p += 0.3
        reasons.append("alerts")

    return _clamp(p, 0.0, 1.0), "+".join(reasons) if reasons else "ok"


def _alignment_mode(world_state: Dict[str, Any] | None) -> str:
    if not isinstance(world_state, dict):
        return "UNKNOWN"
    a = world_state.get("alignment") if isinstance(world_state.get("alignment"), dict) else {}
    return str(a.get("recommended_mode") or a.get("mode") or "UNKNOWN").upper()


def _criticality_deviation(world_state: Dict[str, Any] | None) -> float:
    if not isinstance(world_state, dict):
        return 0.0
    c = world_state.get("criticality") if isinstance(world_state.get("criticality"), dict) else {}
    score = _as_float(c.get("score"), 0.5)
    target = _as_float(c.get("target"), 0.5)
    return abs(score - target)


def compute_policy(
    *,
    now_ts: float,
    world_state: Dict[str, Any] | None,
    energy_state: Dict[str, Any] | None,
    phase_state: Dict[str, Any] | None,
    operational_veto: Dict[str, Any] | None,
    conversation_active: bool,
    prior_policy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Compute policy_state.json (v0.1).

    Meta-policy (profile selection) inputs:
      - energy.mode (CALM/FOCUS/STABILIZE)
      - error pressure proxy
      - operational_veto severity
      - conversation_active

    Output is intentionally simple and testable.
    """

    energy_mode = (energy_state.get("mode") if isinstance(energy_state, dict) else None) or "FOCUS"
    E = _as_float((energy_state or {}).get("E"), 0.5)

    veto_sev = (operational_veto.get("severity") if isinstance(operational_veto, dict) else None) or "none"

    err_p, err_reason = _error_pressure(world_state)
    align_mode = _alignment_mode(world_state)
    crit_dev = _criticality_deviation(world_state)

    # --- meta-policy: choose profile ---
    # Priority order: hard safety > diagnostics > energy
    profile = "CONSERVATIVE"
    reason = []

    if veto_sev == "hard":
        profile = "EFFICIENT"
        reason.append("veto_hard")
    elif err_p >= 0.6:
        profile = "DIAGNOSTIC"
        reason.append(f"error_pressure:{err_reason}")
    elif energy_mode == "STABILIZE" or E >= 0.70:
        profile = "EFFICIENT"
        reason.append("energy_high")
    elif energy_mode == "CALM" and err_p <= 0.2 and veto_sev == "none":
        profile = "EXPLORATORY"
        reason.append("energy_low")
    else:
        profile = "CONSERVATIVE"
        reason.append("default")

    if align_mode == "STABILIZATION":
        if profile == "EXPLORATORY":
            profile = "CONSERVATIVE"
        reason.append("alignment_stabilization")
    elif align_mode == "EXPLORATION" and profile == "CONSERVATIVE" and not conversation_active and veto_sev == "none" and err_p < 0.4 and energy_mode != "STABILIZE":
        profile = "EXPLORATORY"
        reason.append("alignment_exploration")

    if crit_dev >= 0.22 and profile == "EXPLORATORY":
        profile = "CONSERVATIVE"
        reason.append("criticality_deviation_high")
    elif crit_dev >= 0.35:
        profile = "EFFICIENT"
        reason.append("criticality_deviation_extreme")

    # Conversation takes precedence: keep system responsive, avoid deep/background.
    if conversation_active and profile == "EXPLORATORY":
        profile = "CONSERVATIVE"
        reason.append("conversation_active")

    # --- params derived from profile + energy ---
    # v0.1: small set, easy to enforce.
    if profile == "EFFICIENT":
        params = {
            "router_bias": {"fast": 0.80, "code": 0.15, "deep": 0.05},
            "attention_threshold": 0.78,
            "W_max": 7,
            "H_max": 1,
            "allow_embeddings": False,
            "max_llm_calls_per_tick": 1,
            "max_tokens_hint": int(os.getenv("LIRA_MAX_TOKENS_EFFICIENT", "512")),
        }
    elif profile == "DIAGNOSTIC":
        params = {
            "router_bias": {"fast": 0.55, "code": 0.35, "deep": 0.10},
            "attention_threshold": 0.70,
            "W_max": 11,
            "H_max": 2,
            "allow_embeddings": False,  # reduce moving parts during diagnosis
            "max_llm_calls_per_tick": 2,
            "max_tokens_hint": int(os.getenv("LIRA_MAX_TOKENS_DIAGNOSTIC", "900")),
        }
    elif profile == "EXPLORATORY":
        params = {
            "router_bias": {"fast": 0.35, "code": 0.30, "deep": 0.35},
            "attention_threshold": 0.58,
            "W_max": 21,
            "H_max": 4,
            "allow_embeddings": True,
            "max_llm_calls_per_tick": 2,
            "max_tokens_hint": int(os.getenv("LIRA_MAX_TOKENS_EXPLORATORY", "1400")),
        }
    else:  # CONSERVATIVE
        params = {
            "router_bias": {"fast": 0.55, "code": 0.30, "deep": 0.15},
            "attention_threshold": 0.66,
            "W_max": 13,
            "H_max": 2,
            "allow_embeddings": True if energy_mode != "STABILIZE" else False,
            "max_llm_calls_per_tick": 2,
            "max_tokens_hint": int(os.getenv("LIRA_MAX_TOKENS_CONSERVATIVE", "1100")),
        }

    # Phase gating hint: allow LLM calls only in ACT (enforced elsewhere), but keep visible here.
    phase = (phase_state.get("phase") if isinstance(phase_state, dict) else None) or ""
    params["phase"] = phase

    # Keep a stable mode label for dashboards.
    mode = energy_mode

    models = _current_models()
    fallback_model = models["fast"]

    return {
        "schema": "lira.policy_state.v1",
        "owner": "DecisionLoop",
        "updated_at": _iso(now_ts),
        "ttl_s": 10,
        "active_profile": profile,
        "mode": mode,
        "signals": {
            "E": round(E, 4),
            "energy_mode": energy_mode,
            "error_pressure": round(err_p, 4),
            "veto_severity": veto_sev,
            "conversation_active": bool(conversation_active),
            "alignment_mode": align_mode,
            "criticality_deviation": round(crit_dev, 4),
        },
        "models": models,
        "fallback_model": fallback_model,
        # compatibility mirrors for router/dashboard consumers
        "router_bias": params.get("router_bias"),
        "allow_embeddings": params.get("allow_embeddings"),
        "max_llm_calls_per_tick": params.get("max_llm_calls_per_tick"),
        "max_tokens_hint": params.get("max_tokens_hint"),
        "params": params,
        "reason": ";".join(reason),
    }
