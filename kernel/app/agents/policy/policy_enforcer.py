from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple
import random

from utils.state_io import get_env_bool, get_env_int

@dataclass
class EnforcementDecision:
    selected_model: str
    max_tokens_hint: int
    budget_allowed: int
    enforce: bool
    reason: str

class TickBudget:
    """Simple per-tick budget for LLM calls."""
    def __init__(self, default_budget: int = 2):
        self.default_budget = default_budget
        self.current_tick_id: Optional[int] = None
        self.remaining: int = default_budget

    def reset_if_new_tick(self, tick_id: Optional[int], new_budget: int):
        if tick_id is None:
            # treat as same tick; don't thrash
            return
        if self.current_tick_id != tick_id:
            self.current_tick_id = tick_id
            self.remaining = max(0, int(new_budget))

    def consume(self, n: int = 1) -> bool:
        n = int(n)
        if n <= 0:
            return True
        if self.remaining >= n:
            self.remaining -= n
            return True
        return False

def pick_model_by_bias(router_bias: Dict[str, float], fallback: str) -> str:
    # Deterministic if only one positive weight, otherwise weighted pick.
    items = [(m, float(w)) for m, w in (router_bias or {}).items() if float(w) > 0]
    if not items:
        return fallback
    if len(items) == 1:
        return items[0][0]
    total = sum(w for _, w in items)
    r = random.random() * total
    upto = 0.0
    for m, w in items:
        upto += w
        if upto >= r:
            return m
    return items[-1][0]

def decide_enforcement(policy_state: Dict[str, Any], phase_state: Dict[str, Any]) -> Tuple[EnforcementDecision, int]:
    enforce = get_env_bool("LIRA_POLICY_ENFORCE", False)
    default_budget = get_env_int("LIRA_TICK_LLM_BUDGET_DEFAULT", 2)

    tick_id = phase_state.get("tick_id")
    phase = (phase_state.get("phase") or "").upper()

    # budget only meaningful in ACT
    policy_budget = int((policy_state.get("max_llm_calls_per_tick") or default_budget))
    budget_allowed = policy_budget if phase == "ACT" else 0

    max_tokens = int(policy_state.get("max_tokens_hint") or 512)
    fallback_model = str(policy_state.get("fallback_model") or os.getenv("MODEL_LIGHT", "qwen2.5:3b"))

    router_bias = policy_state.get("router_bias") or {}
    selected_model = pick_model_by_bias(router_bias, fallback=fallback_model)

    reason = f"phase={phase} enforce={enforce} budget_allowed={budget_allowed}"
    return EnforcementDecision(
        selected_model=selected_model,
        max_tokens_hint=max_tokens,
        budget_allowed=budget_allowed,
        enforce=enforce,
        reason=reason,
    ), (tick_id if isinstance(tick_id, int) else None)
