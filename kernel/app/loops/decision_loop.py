"""DecisionLoop — Bus owner + World/Energy/Policy coordinator.

This loop is the *single owner* of bus_signals.json and coordinates the runtime
state of the "Regulated Cognitive Field":

- Step 1: bus contract + TTL pruning (single-owner bus)
- Step 2: World Model v0.1 (descriptive snapshot) -> world_state.json
- Step 3: Energy Model v0.1 -> energy_state.json
- Step 5: Policy + Meta-policy v0.1 -> policy_state.json
- Step 6: Controlled forgetting (only in REFLECT phase)

Other loops write their own state files only:
- RegulatorLoop -> loop_regulator.json
- RhythmLoop    -> phase_state.json

DecisionLoop mirrors fresh snapshots onto the bus.
"""

from __future__ import annotations

from typing import Dict, Any, Tuple

import os
import socket
import time

from utils.state_io import (
    read_json,
    write_json_atomic,
    now_ts,
    get_env_float,
)

from agents.forgetting.forgetting_engine import run_forgetting
from agents.policy.policy_engine import compute_policy

def _run_attention(
    conv: Dict[str, Any],
    bus: Dict[str, Any],
    world: Dict[str, Any],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """Oblicz mechanizm uwagi — saliency map + focus_text dla Phi."""
    try:
        from cognition.attention.attention_layer import AttentionLayer
        result = AttentionLayer().compute(conv=conv, bus=bus, world=world, policy=policy)
        result["ts_epoch"] = now_ts()
        result["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return result
    except Exception:
        return {}


def _read_cognitive_state() -> Dict[str, Any]:
    """Czytaj wyniki cognitive night cycle — obserwacyjnie, bez sterowania."""
    import glob
    import json
    rebuild_dir = os.path.join(DATA_ROOT, "runtime", "rebuild")
    pattern = os.path.join(rebuild_dir, "cognitive_consolidation_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        return {}
    try:
        with open(files[-1], "r", encoding="utf-8") as f:
            data = json.load(f)
        cog = data.get("cognitive", {})
        return {
            "ts_epoch": now_ts(),
            "source": os.path.basename(files[-1]),
            "causal_nodes": cog.get("graph_stats", {}).get("nodes", 0),
            "causal_edges": cog.get("graph_stats", {}).get("edges", 0),
            "predictions_count": len(cog.get("predictions", [])),
            "open_questions": len(data.get("open_questions", [])),
            "meta_insights": cog.get("meta_insights", [])[:2],
            "density": cog.get("graph_stats", {}).get("density", 0),
        }
    except Exception:
        return {}


def _run_phi(focus_text: str, phase: Dict[str, Any]) -> Dict[str, Any]:
    """Uruchom warstwę Phi jeśli rozmowa aktywna i faza sprzyja."""
    current_phase = phase.get("phase", "")
    if current_phase not in {"INTEGRATE", "DECIDE", "ACT"}:
        return {}
    if not focus_text:
        return {}
    try:
        from cognition.phi.phi_hook import analyze_text_v2
        result = analyze_text_v2(focus_text)
        result["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["ts_epoch"] = now_ts()
        result["source_phase"] = current_phase
        return result
    except Exception:
        return {}


DATA_ROOT = os.getenv("LIRA_DATA_ROOT", os.path.join(os.getcwd(), "data"))
STATE_DIR = os.path.join(DATA_ROOT, "runtime", "state")

BUS_PATH = os.path.join(STATE_DIR, "bus_signals.json")
WORLD_PATH = os.path.join(STATE_DIR, "world_state.json")
ENERGY_PATH = os.path.join(STATE_DIR, "energy_state.json")
PHASE_PATH = os.path.join(STATE_DIR, "phase_state.json")
POLICY_PATH = os.path.join(STATE_DIR, "policy_state.json")
DECAY_PREVIEW_PATH = os.path.join(STATE_DIR, "decay_preview.json")

REG_PATH = os.path.join(STATE_DIR, "loop_regulator.json")
WATCHDOG_PATH = os.path.join(STATE_DIR, "watchdog_report.json")
CTX_PATH = os.path.join(STATE_DIR, "context_packet.json")
CONV_PATH = os.path.join(STATE_DIR, "conversation_state.json")

QUEUE_PATH        = os.path.join(DATA_ROOT, "runtime", "queue", "queue_tasks.json")
PHI_PATH          = os.path.join(STATE_DIR, "phi_state.json")
ATTENTION_PATH    = os.path.join(STATE_DIR, "attention_state.json")


def _bus_ttl_prune(bus: Dict[str, Any], ttl_s: float = 10.0) -> Dict[str, Any]:
    now = now_ts()
    for k in list(bus.keys()):
        v = bus.get(k)
        if isinstance(v, dict) and "ts_epoch" in v:
            try:
                if (now - float(v["ts_epoch"])) > ttl_s:
                    bus.pop(k, None)
            except Exception:
                pass
    return bus


def _port_probe(host: str, port: int, timeout_s: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout_s)):
            return True
    except Exception:
        return False


def _norm01(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    try:
        v = (float(x) - float(lo)) / (float(hi) - float(lo))
        return max(0.0, min(1.0, v))
    except Exception:
        return 0.0


def _conversation_active(conv: Dict[str, Any], window_s: float = 45.0) -> Tuple[bool, float]:
    ts = 0.0
    try:
        ts = float((conv or {}).get("last_user_ts") or 0.0)
    except Exception:
        ts = 0.0
    return (now_ts() - ts) <= float(window_s), ts




def _read_alignment_state() -> Dict[str, Any]:
    path = os.path.join(STATE_DIR, "alignment_state.json")
    obj = read_json(path, default={})
    return obj if isinstance(obj, dict) else {}


def _read_criticality_state() -> Dict[str, Any]:
    state_path = os.path.join(STATE_DIR, "criticality_state.json")
    obj = read_json(state_path, default={})
    if isinstance(obj, dict) and obj:
        return obj

    thermo_path = os.getenv("LIRA_THERMO_LOG", os.path.join(DATA_ROOT, "runtime", "logs", "thermo.jsonl"))
    try:
        import json
        with open(thermo_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            chunk = f.read().decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        for ln in reversed(lines):
            try:
                row = json.loads(ln)
            except Exception:
                continue
            if not isinstance(row, dict) or row.get("criticality_score") is None:
                continue
            return {
                "score": float(row.get("criticality_score")),
                "regime": row.get("regime"),
                "updated_at": row.get("ts") or row.get("updated_at"),
                "source": "thermo_log",
                "target": float(os.getenv("LIRA_CRITICALITY_TARGET", "0.5")),
            }
    except Exception:
        pass
    return {}
def _build_world_state(bus: Dict[str, Any], phase: Dict[str, Any]) -> Dict[str, Any]:
    """World Model v0.1: descriptive snapshot only."""

    api_host = os.getenv("LIRA_API_HOST", "127.0.0.1")
    api_port = int(os.getenv("LIRA_API_PORT", "8000"))
    ollama_host = os.getenv("LIRA_OLLAMA_HOST", "127.0.0.1")
    ollama_port = int(os.getenv("LIRA_OLLAMA_PORT", "11434"))

    reg = read_json(REG_PATH, default={})
    watchdog = read_json(WATCHDOG_PATH, default={})
    queue = read_json(QUEUE_PATH, default=[])
    conv = read_json(CONV_PATH, default={})
    ctx = read_json(CTX_PATH, default={})
    alignment = _read_alignment_state()
    criticality = _read_criticality_state()

    conv_active, last_user_ts = _conversation_active(conv)

    ports = {
        "api": {"host": api_host, "port": api_port, "open": _port_probe(api_host, api_port)},
        "ollama": {"host": ollama_host, "port": ollama_port, "open": _port_probe(ollama_host, ollama_port)},
    }

    # Workspace proxy: number of context snippets.
    w_items = 0
    try:
        ctx_snips = ((ctx.get("context") or {}).get("snippets") or []) if isinstance(ctx, dict) else []
        if isinstance(ctx_snips, list):
            w_items = len(ctx_snips)
    except Exception:
        w_items = 0

    world = {
        "schema": "lira.world_state.v1",
        "owner": "DecisionLoop",
        "ts_epoch": now_ts(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ttl_s": int(os.getenv("LIRA_WORLD_TTL_S", "10")),
        "ports": ports,
        "system": {
            "regulator": reg,
            "phase": {"tick_id": phase.get("tick_id"), "phase": phase.get("phase"), "mode": phase.get("mode")},
        },
        "queue": {"len": len(queue) if isinstance(queue, list) else None},
        "alerts": (watchdog.get("alerts") if isinstance(watchdog, dict) else None),
        "conversation": {"active": conv_active, "last_user_ts": last_user_ts},
        "workspace": {"items": w_items},
        "alignment": {
            "recommended_mode": alignment.get("recommended_mode") or alignment.get("mode"),
            "global_alignment": alignment.get("global_alignment_ema", alignment.get("global_alignment")),
            "semantic_alignment": alignment.get("semantic_alignment_ema", alignment.get("semantic_alignment")),
            "relational_resonance": alignment.get("relational_resonance_ema", alignment.get("relational_resonance")),
            "updated_at": alignment.get("updated_at") or alignment.get("ts") or alignment.get("created_at"),
        } if isinstance(alignment, dict) and alignment else {},
        "criticality": {
            "score": criticality.get("score", criticality.get("criticality_score")),
            "regime": criticality.get("regime"),
            "target": criticality.get("target", float(os.getenv("LIRA_CRITICALITY_TARGET", "0.5"))),
            "updated_at": criticality.get("updated_at") or criticality.get("ts"),
            "source": criticality.get("source", "state"),
        } if isinstance(criticality, dict) and criticality else {},
        "world_graph": (bus.get("world_graph") if isinstance(bus.get("world_graph"), dict) else {"edges": []}),
    }
    return world


def _compute_energy(world: Dict[str, Any], phase: Dict[str, Any]) -> Dict[str, Any]:
    """Energy Model v0.1 — useful proxy, not physics."""

    alpha = get_env_float("LIRA_ENERGY_ALPHA", 0.6)
    th_calm = get_env_float("LIRA_ENERGY_THR_CALM", 0.35)
    th_stab = get_env_float("LIRA_ENERGY_THR_STABILIZE", 0.70)

    reg = ((world.get("system") or {}).get("regulator") or {}) if isinstance(world, dict) else {}
    metrics = reg.get("metrics") if isinstance(reg.get("metrics"), dict) else {}
    thresholds = reg.get("thresholds") if isinstance(reg.get("thresholds"), dict) else {}

    cpu = float(metrics.get("cpu_pct") or 0.0)
    ram_av = float(metrics.get("ram_avail_mb") or 0.0)
    ram_warn = float(thresholds.get("min_ram_avail_warn_mb") or 2500.0)
    ram_hard = float(thresholds.get("min_ram_avail_hard_mb") or 1200.0)

    # ops normalizations
    C = max(0.0, min(1.0, cpu / 100.0))
    R = 1.0 - _norm01(ram_av, ram_hard, ram_warn)  # pressure

    Q = 0.0
    try:
        qlen = int(((world.get("queue") or {}).get("len") or 0))
        Q = max(0.0, min(1.0, qlen / float(os.getenv("LIRA_QUEUE_NORM", "20"))))
    except Exception:
        Q = 0.0

    # cog proxies
    W = max(0.0, min(1.0, float(((world.get("workspace") or {}).get("items") or 0)) / float(os.getenv("LIRA_WORKSPACE_NORM", "21"))))

    ports = world.get("ports") if isinstance(world.get("ports"), dict) else {}
    api_ok = bool(((ports.get("api") or {}) if isinstance(ports.get("api"), dict) else {}).get("open"))
    oll_ok = bool(((ports.get("ollama") or {}) if isinstance(ports.get("ollama"), dict) else {}).get("open"))

    P = 0.0
    if not api_ok:
        P += 0.5
    if not oll_ok:
        P += 0.7
    P = max(0.0, min(1.0, P))

    H = 0.15 if phase.get("phase") in {"INTEGRATE", "DECIDE"} else 0.05
    U = P

    E_ops = (0.45 * C) + (0.35 * R) + (0.20 * Q)
    E_cog = (0.40 * W) + (0.35 * P) + (0.15 * H) + (0.10 * U)
    E = (alpha * E_cog) + ((1.0 - alpha) * E_ops)

    mode = "FOCUS"
    if E < th_calm:
        mode = "CALM"
    if E >= th_stab:
        mode = "STABILIZE"

    criticality = world.get("criticality") if isinstance(world.get("criticality"), dict) else {}
    crit_score = float(criticality.get("score") or 0.5) if criticality else 0.5
    crit_target = float(criticality.get("target") or os.getenv("LIRA_CRITICALITY_TARGET", "0.5")) if criticality else float(os.getenv("LIRA_CRITICALITY_TARGET", "0.5"))
    crit_dev = abs(crit_score - crit_target)
    alignment = world.get("alignment") if isinstance(world.get("alignment"), dict) else {}
    align_mode = str(alignment.get("recommended_mode") or "").upper()

    allow_embeddings_hint = (mode != "STABILIZE") and (phase.get("phase") in {"ACT", "INTEGRATE"})
    if align_mode == "STABILIZATION" or crit_dev >= 0.35:
        allow_embeddings_hint = False

    return {
        "schema": "lira.energy_state.v1",
        "owner": "DecisionLoop",
        "ts_epoch": now_ts(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ttl_s": int(os.getenv("LIRA_ENERGY_TTL_S", "10")),
        "alpha": alpha,
        "thresholds": {"calm": th_calm, "stabilize": th_stab},
        "E": round(float(E), 4),
        "mode": mode,
        "ops": {"cpu": round(C, 4), "ram_pressure": round(R, 4), "queue": round(Q, 4)},
        "cog": {"W": round(W, 4), "P": round(P, 4), "H": round(H, 4), "U": round(U, 4)},
        "policy_hints": {"allow_embeddings": bool(allow_embeddings_hint), "phase_llm_calls_only_in_act": True},
        "intrinsic_goal": {
            "name": "edge_of_chaos",
            "target": round(float(crit_target), 4),
            "score": round(float(crit_score), 4),
            "deviation": round(float(crit_dev), 4),
            "alignment_mode": align_mode or "UNKNOWN",
        },
    }


def tick_once() -> None:
    # Load bus and external loop states
    bus = read_json(BUS_PATH, default={})
    phase = read_json(PHASE_PATH, default={"tick_id": 0, "phase": "ACCUMULATE", "mode": "FOCUS"})

    # TTL prune first (keep bus clean)
    bus = _bus_ttl_prune(bus, ttl_s=float(os.getenv("LIRA_BUS_TTL_S", "10")))

    # Build World + Energy
    world = _build_world_state(bus, phase)
    energy = _compute_energy(world, phase)

    # Persist these state files (Step 2–3)
    write_json_atomic(WORLD_PATH, world)
    write_json_atomic(ENERGY_PATH, energy)

    # Policy (Step 5) — meta-policy selection; enforcement is done in mcl/router.py
    conv_state = read_json(CONV_PATH, default={})
    operational_veto = (bus.get("operational_veto") or bus.get("veto") or {}) if isinstance(bus, dict) else {}
    conv_active = bool((world.get("conversation") or {}).get("active"))
    prior_policy = read_json(POLICY_PATH, default={})

    policy = compute_policy(
        now_ts=now_ts(),
        world_state=world,
        energy_state=energy,
        phase_state=phase,
        operational_veto=operational_veto if isinstance(operational_veto, dict) else {},
        conversation_active=conv_active,
        prior_policy=prior_policy,
    )
    write_json_atomic(POLICY_PATH, policy)

    # Cognitive layer — obserwacyjny read z ostatniej konsolidacji (PRZED attention, bo attention czyta bus["cognitive"])
    cognitive = _read_cognitive_state()
    if cognitive:
        bus["cognitive"] = cognitive

    # Attention layer — saliency map + focus_text dla Phi (po Policy, bo threshold z policy)
    attention = _run_attention(conv_state, bus, world, policy)
    if attention:
        write_json_atomic(ATTENTION_PATH, attention)
        bus["attention"] = {
            "ts_epoch": attention.get("ts_epoch", now_ts()),
            "threshold": attention.get("threshold"),
            "signal_count": attention.get("signal_count", 0),
            "conv_active": attention.get("conv_active", False),
        }

    # Phi layer — klasyfikuje paradygmat z focus_text złożonego przez Attention
    focus_text = str(attention.get("focus_text") or "") if attention else ""
    phi = _run_phi(focus_text, phase)
    if phi:
        write_json_atomic(PHI_PATH, phi)
        bus["phi"] = {
            "ts_epoch": phi.get("ts_epoch", now_ts()),
            "paradigm": phi.get("paradigm"),
            "confidence": phi.get("confidence"),
            "shift": phi.get("shift_detected", False),
        }

    # Step 6: controlled forgetting (only in REFLECT)
    forgetting_stats = run_forgetting(world, phase)
    if forgetting_stats.get("ran") and not forgetting_stats.get("enforce") and "preview" in forgetting_stats:
        write_json_atomic(DECAY_PREVIEW_PATH, forgetting_stats["preview"])
        bus["forgetting"] = {
            "ts_epoch": now_ts(),
            "mode": "shadow",
            "phase": forgetting_stats.get("phase"),
            "edges_total": forgetting_stats["preview"]["stats"].get("edges_total", 0),
        }
    elif forgetting_stats.get("ran") and forgetting_stats.get("enforce"):
        # world mutated by forgetting
        write_json_atomic(WORLD_PATH, world)
        bus["forgetting"] = {
            "ts_epoch": now_ts(),
            "mode": "enforce",
            "phase": forgetting_stats.get("phase"),
            "edges_total": forgetting_stats.get("edges_total", 0),
            "edges_decayed": forgetting_stats.get("edges_decayed", 0),
            "workspace_items": forgetting_stats.get("workspace_items", 0),
        }
    else:
        bus["forgetting"] = {"ts_epoch": now_ts(), "mode": "idle", "phase": phase.get("phase")}

    # Mirror onto bus (Step 1 contract)
    bus["phase"] = {"ts_epoch": now_ts(), "tick_id": phase.get("tick_id"), "phase": phase.get("phase"), "mode": phase.get("mode")}
    bus["world"] = {"ts_epoch": now_ts(), "ports": world.get("ports"), "queue": world.get("queue"), "alerts": world.get("alerts"), "conversation": world.get("conversation"), "workspace": world.get("workspace")}
    bus["energy"] = {"ts_epoch": now_ts(), "E": energy.get("E"), "mode": energy.get("mode"), "ops": energy.get("ops"), "cog": energy.get("cog"), "intrinsic_goal": energy.get("intrinsic_goal")}
    if isinstance(world.get("alignment"), dict) and world.get("alignment"):
        bus["alignment"] = {"ts_epoch": now_ts(), **world.get("alignment")}
    if isinstance(world.get("criticality"), dict) and world.get("criticality"):
        bus["criticality"] = {"ts_epoch": now_ts(), **world.get("criticality")}

    pr = (policy.get("_runtime") or {}) if isinstance(policy, dict) else {}
    params = (policy.get("params") or {}) if isinstance(policy, dict) else {}
    bus["policy"] = {
        "ts_epoch": now_ts(),
        "active_profile": policy.get("active_profile"),
        "router_bias": policy.get("router_bias") or params.get("router_bias"),
        "allow_embeddings": policy.get("allow_embeddings") if "allow_embeddings" in policy else params.get("allow_embeddings"),
        "max_llm_calls_per_tick": policy.get("max_llm_calls_per_tick") or params.get("max_llm_calls_per_tick"),
        "max_tokens_hint": policy.get("max_tokens_hint") or params.get("max_tokens_hint"),
        "fallback_model": policy.get("fallback_model"),
        "models": policy.get("models") or {},
        "selected_model": pr.get("selected_model"),
        "budget_remaining": pr.get("budget_remaining"),
        "enforce": pr.get("enforce"),
        "reason": pr.get("reason") or policy.get("reason"),
    }

    write_json_atomic(BUS_PATH, bus)


def main_loop(sleep_s: float = 0.25) -> None:
    while True:
        try:
            tick_once()
        except Exception as e:
            # keep loop alive; watchdog + bus will surface the error
            bus = read_json(BUS_PATH, default={})
            bus["decision_loop_error"] = {"ts_epoch": now_ts(), "error": str(e)}
            write_json_atomic(BUS_PATH, bus)
        time.sleep(sleep_s)


if __name__ == "__main__":
    main_loop()
