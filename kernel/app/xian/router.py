from __future__ import annotations

import os
import time
from typing import Any, Dict

# Po ilu sekundach status_only bez aktywności → lekkie dobudzenie
XIAN_WAKE_THRESHOLD_S = float(os.getenv("XIAN_WAKE_THRESHOLD_S", "1800"))  # 30 min


def recommend_route(
    snapshot: Dict[str, Any],
    status_only_since_ts: float = 0.0,
) -> Dict[str, Any]:
    phase = snapshot.get("phase") or "UNKNOWN"
    policy = snapshot.get("policy_mode") or "UNKNOWN"
    pressure = float(snapshot.get("regulator_pressure", 0.0) or 0.0)
    queue_depth = int(snapshot.get("queue_depth", 0) or 0)
    reviewing = bool(snapshot.get("reviewing"))
    hard_veto = bool(snapshot.get("hard_veto"))
    alignment = float(snapshot.get("global_alignment", 0.0) or 0.0)
    soryel = float(snapshot.get("session_to_soryel", 0.0) or 0.0)
    lira = float(snapshot.get("session_to_lira", 0.0) or 0.0)

    # Semantic advisory signals (Krok 4)
    sem_hint = snapshot.get("semantic_mode_hint", "none")
    sem_mismatch = float(snapshot.get("semantic_mismatch", 0.0) or 0.0)
    sem_confidence = float(snapshot.get("semantic_confidence", 0.0) or 0.0)
    sem_signals = snapshot.get("semantic_top_signals", {})
    sem_irony = float(sem_signals.get("irony_weight", 0.0) or 0.0)

    reasons = []
    target = "local_fast"
    confidence = 0.6
    now = time.time()
    new_since_ts = 0.0  # czas wejścia w status_only (0 = nie jesteśmy w status_only)

    if hard_veto:
        target = "status_only"
        confidence = 0.95
        reasons.append("hard_veto_active")
        new_since_ts = status_only_since_ts if status_only_since_ts > 0 else now
    elif reviewing or soryel >= 0.82:
        target = "soryel"
        confidence = 0.82
        reasons.append("guardian_review_path")
    elif pressure >= 0.88 or queue_depth >= 20:
        target = "local_fast"
        confidence = 0.88
        reasons.append("resource_pressure")
    elif phase == "ACT" and lira >= 0.7:
        target = "lira"
        confidence = 0.8
        reasons.append("relational_response_path")
    elif policy in {"DIAGNOSTIC", "CONSERVATIVE", "STABILIZATION"}:
        quiet_s = (now - status_only_since_ts) if status_only_since_ts > 0 else 0.0
        if quiet_s > XIAN_WAKE_THRESHOLD_S and not hard_veto:
            # Lira recommendation: dobudź gdy za długo w status_only bez aktywności
            target = "local_fast"
            confidence = 0.55
            reasons.append("cognitive_nudge_status_only_too_long")
            reasons.append(f"quiet_{int(quiet_s)}s")
            # Zeruj timer — następny cykl zaczyna od nowa
            new_since_ts = 0.0
        else:
            target = "status_only"
            confidence = 0.74
            reasons.append("policy_prefers_brief_status")
            new_since_ts = status_only_since_ts if status_only_since_ts > 0 else now
    elif alignment >= 0.8:
        target = "lira"
        confidence = 0.72
        reasons.append("high_alignment")
    else:
        target = "xian"
        confidence = 0.68
        reasons.append("fallback_xian_reasoning")

    # Semantic advisory — Krok 4 (Soryel+Lira consensus)
    # Te reguły mogą nadpisać target TYLKO gdy sygnał jest mocny.
    # Kolejność: irony_guard > boundary_guard > relational_bias > log-only
    semantic_reason = ""
    if sem_hint == "irony_or_test" and sem_mismatch > 0.4 and sem_irony > 0.45 and sem_confidence > 0.5:
        target = "soryel"
        confidence = max(confidence, 0.80)
        reasons.append("semantic_irony_guard")
        semantic_reason = "irony/test suspicion"
    elif sem_hint == "boundary_shift" and sem_confidence > 0.55:
        target = "soryel"
        confidence = max(confidence, 0.78)
        reasons.append("semantic_boundary_guard")
        semantic_reason = "boundary shift detected"
    elif sem_hint == "relational" and sem_confidence > 0.5:
        # Lekki bias, nie twardy override — tylko gdy router jeszcze nie wybrał soryel
        if target not in ("soryel", "status_only"):
            reasons.append("semantic_relational_bias")
            semantic_reason = "relational cue"
            # Delikatne przechylenie — jeśli target=xian/local_fast, rozważ lira
            if target in ("xian", "local_fast") and lira >= 0.5:
                target = "lira"
                confidence = max(confidence, 0.70)
    elif sem_hint not in ("literal", "none", ""):
        reasons.append(f"semantic_hint:{sem_hint}")
        semantic_reason = f"{sem_hint} weak signal"

    return {
        "target": target,
        "confidence": round(confidence, 3),
        "reasons": reasons,
        "phase": phase,
        "policy_mode": policy,
        "status_only_since_ts": round(new_since_ts, 3),
        "quiet_seconds": round((now - status_only_since_ts) if status_only_since_ts > 0 else 0.0, 1),
        "semantic_mode_hint": sem_hint,
        "semantic_reason": semantic_reason,
    }
