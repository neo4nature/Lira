from __future__ import annotations

from typing import Any, Dict


def build_report(snapshot: Dict[str, Any], route: Dict[str, Any], xian_alignment: float) -> Dict[str, Any]:
    phase = snapshot.get("phase") or "UNKNOWN"
    policy = snapshot.get("policy_mode") or "UNKNOWN"
    pressure = float(snapshot.get("regulator_pressure", 0.0) or 0.0)
    queue_depth = int(snapshot.get("queue_depth", 0) or 0)
    drift_count = int(snapshot.get("drift_count", 0) or 0)
    reviewing = bool(snapshot.get("reviewing"))

    if pressure >= 0.88:
        top_risk = "resource_pressure"
    elif drift_count > 0:
        top_risk = "drift_detected"
    elif reviewing:
        top_risk = "guardian_review"
    elif queue_depth >= 20:
        top_risk = "queue_backlog"
    else:
        top_risk = "stable"

    # Semantic advisory (Krok 4)
    sem_hint = route.get("semantic_mode_hint", "none")
    sem_reason = route.get("semantic_reason", "")
    sem_part = f", semantic={sem_hint}" if sem_hint not in ("none", "literal", "") else ""

    summary = (
        f"Xian sees phase={phase}, policy={policy}, route={route.get('target')}, "
        f"pressure={pressure:.2f}, drift={drift_count}, xian_alignment={xian_alignment:.2f}"
        f"{sem_part}."
    )
    return {
        "summary": summary,
        "top_risk": top_risk,
        "recommended_target": route.get("target"),
        "recommended_confidence": route.get("confidence"),
        "phase": phase,
        "policy_mode": policy,
        "queue_depth": queue_depth,
        "drift_count": drift_count,
        "reviewing": reviewing,
        "semantic_mode_hint": sem_hint,
        "semantic_reason": sem_reason,
    }
