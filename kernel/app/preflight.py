"""Preflight checks + self-healing bootstrap for Lira kernel.

Assumptions:
- Ollama is running (Neo requested to treat it as always up), but we still validate basic connectivity.
- Files are single-owner where specified (DecisionLoop owns bus_signals.json).

Goals:
- Ensure required directories exist.
- Ensure critical JSON state files exist and are valid JSON (repair from .bak or defaults).
- Provide a deterministic, printable report for startup scripts.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import shutil

import requests

from config import ensure_dirs, paths, runtime_dir
from agents.memory.memory_loader import read_json, write_json


def validate_piper_tts() -> Dict[str, Any]:
    """Validate Piper TTS wiring (bin + voice models + player).

    Lightweight and non-fatal: does not attempt to play audio.
    """
    project_root = Path(__file__).resolve().parents[1]
    piper_bin = project_root / "models" / "piper" / "bin" / "piper"
    voices_dir = project_root / "models" / "piper" / "voices"

    pl_model = voices_dir / "pl.onnx"
    en_model = voices_dir / "en.onnx"

    player = "aplay" if shutil.which("aplay") else ("paplay" if shutil.which("paplay") else None)

    return {
        "piper_first": os.environ.get("LIRA_TTS_PIPER_FIRST", "1") == "1",
        "piper_bin": {
            "path": str(piper_bin),
            "exists": piper_bin.exists(),
            "path_fallback": shutil.which("piper") is not None,
        },
        "voices_dir": {"path": str(voices_dir), "exists": voices_dir.exists()},
        "models": {
            "pl": {"path": str(pl_model), "exists": pl_model.exists(), "env": bool(os.getenv("PIPER_MODEL_PL"))},
            "en": {"path": str(en_model), "exists": en_model.exists(), "env": bool(os.getenv("PIPER_MODEL_EN"))},
        },
        "player": {"selected": player, "aplay": shutil.which("aplay") is not None, "paplay": shutil.which("paplay") is not None},
    }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _defaults() -> Dict[str, Any]:
    p = paths()
    state_dir = runtime_dir() / "state"
    return {
        "conversation_state": {
            "path": state_dir / "conversation_state.json",
            "default": {
                "schema": "lira.conversation_state.v1",
                "last_user_ts": 0.0,
                "last_intent": "unknown",
                "updated_at": _now_iso(),
            },
        },
        "model_locks": {
            "path": state_dir / "model_locks.json",
            "default": {"schema": "lira.model_locks.v1", "deep_lock_until": 0.0, "core_lock_until": 0.0},
        },
        "bus_signals": {
            "path": state_dir / "bus_signals.json",
            "default": {
                "schema": "lira.bus.v1",
                "owner": "DecisionLoop",
                "updated_at": _now_iso(),
                "conversation_active": False,
                "models": {
                    "preferred": {"fii": os.getenv("MODEL_LIGHT", "qwen2.5:3b"), "core": os.getenv("MODEL_CODE", "qwen2.5-coder:7b"), "deep": os.getenv("MODEL_DEEP", os.getenv("MODEL_LIGHT", "qwen2.5:3b"))},
                    "active_front": "fii",
                    "allow_heavy_reasoning": False,
                    "allow_background_tasks": False,
                    "deep_busy": False,
                    "core_busy": False,
                },
                "operational_veto": {
                    "active": False,
                    "severity": "none",
                    "reason_code": "OK",
                    "reason_human": "",
                    "recommended_action": "maintain",
                    "metrics": {},
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                    "ttl_s": 120,
                    "override_allowed": True,
                },
                "override": {"active": False, "ttl_s": 300},
                "limits": {"deep_cooldown_seconds": 20},
            },
        },
        "phase_state": {
            "path": state_dir / "phase_state.json",
            "default": {
                "schema": "lira.phase_state.v1",
                "owner": "RhythmLoop",
                "tick_id": 0,
                "phase": "ACCUMULATE",
                "mode": "FOCUS",
                "tick_period_ms": 1400,
                "phase_started_ts": _now_iso(),
                "updated_at": _now_iso(),
                "gates": {
                    "accept_user_input": True,
                    "allow_llm_calls": False,
                    "allow_embeddings": False,
                    "allow_executor": False,
                    "allow_memory_write": False
                },
                "ttl_s": 6
            }
        },
        "world_state": {
            "path": state_dir / "world_state.json",
            "default": {
                "schema": "lira.world_state.v1",
                "owner": "DecisionLoop",
                "updated_at": _now_iso(),
                "ttl_s": 10,
                "ports": {},
                "system": {},
                "queue": {"len": None},
                "alerts": None,
                "conversation": {"active": False, "last_user_ts": 0.0}
            }
        },
        "energy_state": {
            "path": state_dir / "energy_state.json",
            "default": {
                "schema": "lira.energy_state.v1",
                "owner": "DecisionLoop",
                "updated_at": _now_iso(),
                "ttl_s": 10,
                "E": 0.0,
                "mode": "FOCUS",
                "alpha": 0.6,
                "ops": {},
                "cog": {},
                "policy_hints": {"allow_embeddings": True, "phase_llm_calls_only_in_act": True}
            }
        },
        "loop_regulator": {
            "path": state_dir / "loop_regulator.json",
            "default": {
                "schema": "lira.loop_regulator.v1",
                "ts": _now_iso(),
                "gates": {"can_spawn": True, "reasons": []},
                "metrics": {
                    "cpu_pct": 0.0,
                    "ram_total_mb": 0,
                    "ram_avail_mb": 0,
                    "disk_free_gb": 0.0,
                    "swap_used_mb": 0,
                    "io_wait_pct": 0.0,
                },
                "thresholds": {
                    "min_ram_avail_warn_mb": 2500,
                    "min_ram_avail_hard_mb": 1200,
                    "max_cpu_warn_pct": 85,
                    "max_cpu_hard_pct": 95,
                    "min_disk_free_warn_gb": 10,
                    "min_disk_free_hard_gb": 5,
                },
            },
        },
        "collector_state": {
            "path": Path(p["COLLECTOR_STATE"]),
            "default": {"processed": []},
        },
        "queue": {
            "path": Path(p["QUEUE_FILE"]),
            "default": [],
        },
        "context_packet": {
            "path": state_dir / "context_packet.json",
            "default": {
                "schema": "lira.context_packet.v1",
                "trace_id": "bootstrap",
                "created_at": _now_iso(),
                "ttl_seconds": 240,
                "owner": "DecisionLoop",
                "producer": "preflight",
                "request": {"text": "", "intent": "bootstrap", "priority": "normal"},
                "retrieval": {"top_k": 0, "results": [], "scoring": "n/a"},
                "context": {"snippets": [], "assumptions": []},
                "risk": {"error_prediction": 0.0, "cause": "bootstrap"},
            },
        },
    }


def _repair_json_file(path: Path, default_obj: Any) -> Tuple[bool, str]:
    """Ensure JSON file exists + is readable. Returns (changed, note)."""
    obj = read_json(path, default=None)
    if obj is None:
        write_json(path, default_obj)
        return True, "created_or_repaired"
    return False, "ok"


def validate_ollama(url: str = "http://127.0.0.1:11434") -> Tuple[bool, str]:
    # Neo asked to assume Ollama is always up; we still validate quickly for better diagnostics.
    try:
        r = requests.get(url, timeout=1.5)
        return (200 <= r.status_code < 500), f"http {r.status_code}"
    except Exception as e:
        return False, f"unreachable: {e}"


def run() -> Dict[str, Any]:
    ensure_dirs()
    report: Dict[str, Any] = {
        "schema": "lira.preflight_report.v1",
        "ts": _now_iso(),
        "root": str(Path(__file__).resolve().parents[1]),
        "data_root": str(paths()["DATA_ROOT"]),
        "checks": [],
    }

    defs = _defaults()
    changed_any = False
    for name, item in defs.items():
        path = Path(item["path"])
        changed, note = _repair_json_file(path, item["default"])
        changed_any = changed_any or changed
        report["checks"].append({"name": name, "path": str(path), "status": note})

    ok, note = validate_ollama()
    report["checks"].append({"name": "ollama", "path": "http://127.0.0.1:11434", "status": note, "ok": ok})

    # TTS wiring (Piper-first)
    tts = validate_piper_tts()
    report["checks"].append({
        "name": "tts_piper",
        "path": "models/piper",
        "status": "ok" if (tts["piper_bin"]["exists"] or tts["piper_bin"]["path_fallback"]) else "missing_bin",
        "details": tts,
    })

    report["changed_any"] = changed_any
    return report


if __name__ == "__main__":
    rep = run()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
