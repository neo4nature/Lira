from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict

import requests

from config import runtime_dir
from xian.chat_busy import chat_busy, is_chat_busy
from reflex.reflex_commands import try_handle_reflex
from agents.memory.memory_loader import read_json, write_json
from agents.policy.policy_enforcer import TickBudget

OLLAMA_URL = os.getenv("LIRA_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
_DEFAULT_MODELS = {
    "fast": os.getenv("MODEL_LIGHT", "qwen2.5:3b"),
    "code": os.getenv("MODEL_CODE", "qwen2.5-coder:7b"),
    "deep": os.getenv("MODEL_DEEP", os.getenv("MODEL_LIGHT", "qwen2.5:3b")),
}
_DEFAULT_BUDGET = int(os.getenv("LIRA_TICK_LLM_BUDGET_DEFAULT", "2"))
_budget = TickBudget(default_budget=_DEFAULT_BUDGET)


def _read_json(path: Path, default: Any) -> Any:
    return read_json(path, default=default)


def _write_json(path: Path, obj: Any) -> None:
    write_json(path, obj)


def _state_dir() -> Path:
    return runtime_dir() / "state"


def _get_bus() -> Dict[str, Any]:
    st = _state_dir()
    bus = _read_json(st / "bus_signals.json", {})
    if not isinstance(bus, dict) or not bus:
        return {
            "schema": "lira.bus.v1",
            "owner": "DecisionLoop",
            "conversation_active": True,
            "models": {
                "preferred": {"fii": _DEFAULT_MODELS["fast"], "core": _DEFAULT_MODELS["code"], "deep": _DEFAULT_MODELS["deep"]},
                "allow_heavy_reasoning": False,
                "allow_background_tasks": False,
                "deep_busy": True,
            },
            "operational_veto": {
                "active": True,
                "severity": "warn",
                "reason_code": "OK",
                "reason_human": "bus missing → safe defaults",
                "recommended_action": "stabilize",
                "metrics": {},
                "ttl_s": 120,
                "override_allowed": True,
            },
            "override": {"active": False, "ttl_s": 300},
            "limits": {"deep_cooldown_seconds": 20},
        }
    return bus


def _get_policy() -> Dict[str, Any] | None:
    try:
        st = _state_dir()
        pol = _read_json(st / "policy_state.json", {})
        return pol if isinstance(pol, dict) and pol else None
    except Exception:
        return None


def _get_phase() -> Dict[str, Any]:
    try:
        phase = _read_json(_state_dir() / "phase_state.json", {"tick_id": 0, "phase": "ACT"})
        return phase if isinstance(phase, dict) else {"tick_id": 0, "phase": "ACT"}
    except Exception:
        return {"tick_id": 0, "phase": "ACT"}


def _policy_envelope(bus: Dict[str, Any]) -> str:
    try:
        mode = bus.get("mode") or ("active" if bus.get("conversation_active") else "idle")
        veto = bus.get("operational_veto") if isinstance(bus.get("operational_veto"), dict) else {}
        sev = veto.get("severity", "none")
        mem = bus.get("memory") if isinstance(bus.get("memory"), dict) else {}
        emb_ready = bool(mem.get("embeddings_ready", False))
        # veto_code: ok / warn / hard
        if not veto or sev in ("none", "ok", ""):
            veto_code = "ok"
        elif sev == "hard":
            veto_code = "hard"
        else:
            veto_code = "warn"
        embed_status = "emb" if emb_ready else "no-emb"
        return f"[sys:{mode}|{veto_code}|{embed_status}]"
    except Exception:
        return "[sys:unknown|ok|no-emb]"


_identity_core_cache: str | None = None


def _get_identity_core() -> str:
    """Legacy fallback — gdy model nie jest xian-v4, używamy starego pliku."""
    global _identity_core_cache
    if _identity_core_cache is not None:
        return _identity_core_cache
    try:
        p = Path(__file__).parent.parent / "data" / "config" / "xian_identity_core.txt"
        _identity_core_cache = p.read_text(encoding="utf-8").strip()
        import logging
        logging.getLogger("mcl.router").info("[router] xian_identity_core loaded (%d chars)", len(_identity_core_cache))
    except Exception:
        _identity_core_cache = ""
    return _identity_core_cache


# Modelfile xian-v4-chat niesie tożsamość w SYSTEM (550 znaków, 6 zasad rdzeniowych)
# i renderer gemma4 ją dostarcza — potwierdzone pomiarem: 12-tokenowe pytanie daje
# prompt_eval_count=228, a model przedstawia się sam. Doklejanie tu pełnych 12,4 KB
# z plików dawało 5975 tokenów przy num_ctx=4096, więc Ollama ucinała 1879 od przodu
# (WARN truncating, keep=4) — kasując dokładnie tę tożsamość, po cichu.
_XIAN_V4_NOTE = (
    "Tryb: kanał automatyczny ekosystemu, nie rozmowa na żywo z Neo.\n"
    "Rozmówcy: Soryel (precyzja, straż), Lira (intuicja, relacja), Neo (ostatni głos).\n"
    "Odpowiadasz zwięźle i konkretnie. Brak danych nazywasz wprost, nie zgadujesz."
)


def _wrap_prompt(user_prompt: str, model: str = "") -> str:
    parts = []
    try:
        from xian.identity_loader import is_xian_model
        use_v4 = is_xian_model(model) and model != "xian:latest" and model != "xian"
    except Exception:
        use_v4 = False
    identity = _XIAN_V4_NOTE if use_v4 else _get_identity_core()
    if identity:
        parts.append(identity)
    parts.append(_policy_envelope(_get_bus()))
    parts.append(user_prompt)
    return "\n\n".join(parts).strip()


def _locks_path() -> Path:
    return _state_dir() / "model_locks.json"


def _lock_deep(seconds: int) -> None:
    p = _locks_path()
    locks = _read_json(p, {"schema": "lira.model_locks.v1", "deep_lock_until": 0, "core_lock_until": 0})
    locks["schema"] = "lira.model_locks.v1"
    locks["deep_lock_until"] = max(float(locks.get("deep_lock_until", 0)), time.time() + seconds)
    _write_json(p, locks)


def _normalize_pref(bus: Dict[str, Any], policy: Dict[str, Any] | None) -> Dict[str, str]:
    models = dict(_DEFAULT_MODELS)
    if isinstance(policy, dict) and isinstance(policy.get("models"), dict):
        models.update({k: str(v) for k, v in policy["models"].items() if v})
    bus_models = bus.get("models") if isinstance(bus.get("models"), dict) else {}
    preferred = bus_models.get("preferred") if isinstance(bus_models.get("preferred"), dict) else {}
    if preferred:
        models["fast"] = preferred.get("fii") or preferred.get("fast") or models["fast"]
        models["code"] = preferred.get("core") or preferred.get("code") or models["code"]
        models["deep"] = preferred.get("deep") or models["deep"]
    return models


def _extract_router_bias(policy: Dict[str, Any] | None) -> Dict[str, float]:
    if not isinstance(policy, dict):
        return {}
    params = policy.get("params") if isinstance(policy.get("params"), dict) else {}
    rb = policy.get("router_bias") if isinstance(policy.get("router_bias"), dict) else params.get("router_bias")
    if not isinstance(rb, dict):
        return {}
    # support new aliases and old model-name keys
    return {
        "fast": float(rb.get("fast", rb.get("phi3", 0.0)) or 0.0),   # phi3 = legacy alias
        "code": float(rb.get("code", rb.get("codellama", 0.0)) or 0.0),  # codellama = legacy alias
        "deep": float(rb.get("deep", rb.get("llama3", 0.0)) or 0.0),  # llama3 = legacy alias
    }


def _policy_limits(policy: Dict[str, Any] | None) -> tuple[int, int]:
    params = policy.get("params") if isinstance(policy, dict) and isinstance(policy.get("params"), dict) else {}
    max_calls = int((policy or {}).get("max_llm_calls_per_tick") or params.get("max_llm_calls_per_tick") or _DEFAULT_BUDGET)
    max_tokens = int((policy or {}).get("max_tokens_hint") or params.get("max_tokens_hint") or 512)
    return max_calls, max_tokens


def _looks_like_code(prompt: str) -> bool:
    p = (prompt or "").lower()
    markers = ["python", "bash", "shell", "error", "traceback", "stack", "json", "docker", "pip", "apt", "venv", "php", "sql", "javascript", "typescript", "css", "html"]
    return any(k in p for k in markers)


def _select_model(prompt: str, depth: str, bus: Dict[str, Any], policy: Dict[str, Any] | None) -> str:
    pref = _normalize_pref(bus, policy)
    rb = _extract_router_bias(policy)
    convo = bool(bus.get("conversation_active", False))
    models = bus.get("models") if isinstance(bus.get("models"), dict) else {}
    allow_heavy = bool(models.get("allow_heavy_reasoning", False))
    deep_busy = bool(models.get("deep_busy", False))

    if depth == "fast":
        return pref["fast"]
    if depth == "core":
        return pref["code"]
    if depth == "deep":
        if (not convo) and allow_heavy and (not deep_busy):
            _lock_deep(seconds=int(bus.get("limits", {}).get("deep_cooldown_seconds", 20)))
            return pref["deep"]
        return pref["fast"]

    looks_like_code = _looks_like_code(prompt)
    if looks_like_code and rb.get("code", 0.0) >= (rb.get("fast", 0.0) * 0.5):
        return pref["code"]

    if rb:
        best = max(rb, key=lambda k: rb.get(k, 0.0))
        if best == "deep":
            if (not convo) and allow_heavy and (not deep_busy):
                _lock_deep(seconds=int(bus.get("limits", {}).get("deep_cooldown_seconds", 20)))
                return pref["deep"]
            return pref["fast"]
        return pref[best]

    if looks_like_code:
        return pref["code"]
    if (not convo) and allow_heavy and (not deep_busy) and len((prompt or "")) > 500:
        _lock_deep(seconds=int(bus.get("limits", {}).get("deep_cooldown_seconds", 20)))
        return pref["deep"]
    return pref["fast"]


def _update_policy_runtime(policy: Dict[str, Any] | None, *, selected_model: str, budget_allowed: int, budget_remaining: int, enforce: bool, reason: str) -> None:
    if not isinstance(policy, dict):
        return
    path = _state_dir() / "policy_state.json"
    policy["_runtime"] = policy.get("_runtime") or {}
    policy["_runtime"].update({
        "selected_model": selected_model,
        "budget_allowed": budget_allowed,
        "budget_remaining": budget_remaining,
        "enforce": enforce,
        "reason": reason,
        "updated_at_epoch": time.time(),
    })
    _write_json(path, policy)


def route(prompt: str, depth: str = "auto", num_predict: int | None = None, *, interactive: bool = False) -> str:
    """Route prompt to Ollama model.

    Args:
        interactive: True for direct user interaction (voice, chat).
                     Bypasses phase gate and tick budget — Neo always gets a response.
    """
    reflex = try_handle_reflex(prompt)
    if reflex is not None:
        return reflex

    bus = _get_bus()
    policy = _get_policy() or {}
    phase = _get_phase()
    phase_name = str(phase.get("phase") or "").upper()
    tick_id = phase.get("tick_id")
    max_calls, max_tokens = _policy_limits(policy)
    budget_allowed = max_calls if phase_name == "ACT" else 0
    _budget.reset_if_new_tick(tick_id if isinstance(tick_id, int) else None, budget_allowed)

    model = _select_model(prompt, depth, bus, policy)
    enforce = bool(os.getenv("LIRA_POLICY_ENFORCE", "0").strip().lower() in {"1", "true", "yes", "on"})
    reason = f"phase={phase_name or 'UNKNOWN'} budget_allowed={budget_allowed}"
    _update_policy_runtime(policy, selected_model=model, budget_allowed=budget_allowed, budget_remaining=_budget.remaining, enforce=enforce, reason=reason)

    if not interactive:
        if phase_name != "ACT":
            return f"[router_blocked] LLM calls allowed only in ACT phase (current={phase_name or 'UNKNOWN'})"
        # Flaga była dotąd jednokierunkowa: generatory ją podnosiły, ustępowały jej
        # tylko embeddery. Nocny cykl i executor mieliły więc równolegle na czterech
        # rdzeniach — 2026-07-28 jedno zapytanie rosło z ~2m20s do 6m03s. Sprawdzamy
        # przed consume(), żeby odrzucone wywołanie nie zjadało budżetu ticku.
        if is_chat_busy():
            return "[router_busy] generation in progress elsewhere"
        if not _budget.consume(1):
            _update_policy_runtime(policy, selected_model=model, budget_allowed=budget_allowed, budget_remaining=_budget.remaining, enforce=enforce, reason=reason + ";budget_exhausted")
            return f"[router_budget_exhausted] model={model} tick={tick_id} remaining={_budget.remaining}"

    wrapped = _wrap_prompt(prompt, model=model)
    effective_tokens = num_predict if num_predict is not None else max_tokens
    payload = {"model": model, "prompt": wrapped, "stream": False, "options": {"num_predict": effective_tokens}}
    # qwen3: think on dla głębokich, off dla fast
    if "qwen3" in model.lower():
        payload["think"] = depth in ("core", "deep")
    # gemma4 / xian-v4: thinking trace wyłączony — refleksja idzie PO odpowiedzi
    try:
        from xian.identity_loader import needs_think_disabled
        if needs_think_disabled(model):
            payload["think"] = False
    except Exception:
        pass
    try:
        with chat_busy():
            r = requests.post(OLLAMA_URL, json=payload, timeout=(10, int(os.getenv("LIRA_OLLAMA_GENERATE_TIMEOUT", "10800"))))
            r.raise_for_status()
            data = r.json()
        _update_policy_runtime(policy, selected_model=model, budget_allowed=budget_allowed, budget_remaining=_budget.remaining, enforce=enforce, reason=reason + ";ok")
        return (data.get("response") or "").strip()
    except Exception as e:
        _update_policy_runtime(policy, selected_model=model, budget_allowed=budget_allowed, budget_remaining=_budget.remaining, enforce=enforce, reason=reason + f";error={type(e).__name__}")
        return f"[router_error] {type(e).__name__}: {e}"
