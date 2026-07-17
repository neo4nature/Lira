"""RhythmLoop (v0.1)

Implements micro-cycles (phase machine) for the cognitive field.

Phases:
  ACCUMULATE -> INTEGRATE -> DECIDE -> ACT -> REFLECT

This loop is *NOT* allowed to write bus_signals.json (DecisionLoop owns the bus).
It writes phase_state.json only. DecisionLoop mirrors phase snapshot onto the bus.

Gates (minimal v0.1):
  - allow_llm_calls: only in ACT
  - allow_embeddings: only in INTEGRATE
  - allow_memory_write: only in REFLECT

Energy coupling (minimal): uses RegulatorLoop metrics to derive mode
  CALM / FOCUS / STABILIZE and adjust tick duration.

The point is stability and debouncing, not perfect neuroscience.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from config import runtime_dir


PHASES = ["ACCUMULATE", "INTEGRATE", "DECIDE", "ACT", "REFLECT"]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _derive_mode_from_reg(reg: Dict[str, Any]) -> str:
    """Cheap heuristic for v0.1. DecisionLoop computes full energy later."""
    try:
        m = reg.get("metrics") if isinstance(reg.get("metrics"), dict) else {}
        cpu = m.get("cpu_pct")
        ram_av = m.get("ram_avail_mb")
        th = reg.get("thresholds") if isinstance(reg.get("thresholds"), dict) else {}
        cpu_warn = float(th.get("max_cpu_warn_pct", 85))
        cpu_hard = float(th.get("max_cpu_hard_pct", 95))
        ram_warn = float(th.get("min_ram_avail_warn_mb", 2500))
        ram_hard = float(th.get("min_ram_avail_hard_mb", 1200))

        if (cpu is not None and float(cpu) >= cpu_hard) or (ram_av is not None and float(ram_av) <= ram_hard):
            return "STABILIZE"
        if (cpu is not None and float(cpu) >= cpu_warn) or (ram_av is not None and float(ram_av) <= ram_warn):
            return "FOCUS"
        return "CALM"
    except Exception:
        return "FOCUS"


def _tick_period_ms(mode: str) -> int:
    """Adaptive tempo. In survival mode we tick faster but do less."""
    if mode == "STABILIZE":
        return int(os.getenv("LIRA_TICK_MS_STABILIZE", "700"))
    if mode == "CALM":
        return int(os.getenv("LIRA_TICK_MS_CALM", "2400"))
    return int(os.getenv("LIRA_TICK_MS_FOCUS", "1400"))


def _gates_for_phase(phase: str) -> Dict[str, bool]:
    return {
        "accept_user_input": True,
        "allow_llm_calls": phase == "ACT",
        "allow_embeddings": phase == "INTEGRATE",
        "allow_executor": phase == "ACT",
        "allow_memory_write": phase == "REFLECT",
    }


@dataclass
class RhythmConfig:
    poll_s: float = 0.05


def run_rhythm_loop() -> None:
    rt = runtime_dir()
    st = rt / "state"
    phase_path = st / "phase_state.json"
    reg_path = st / "loop_regulator.json"

    tick_id = 0
    phase_idx = 0

    if not phase_path.exists():
        init = {
            "schema": "lira.phase_state.v1",
            "owner": "RhythmLoop",
            "tick_id": 0,
            "phase": PHASES[0],
            "mode": "FOCUS",
            "tick_period_ms": _tick_period_ms("FOCUS"),
            "phase_started_ts": _now_iso(),
            "updated_at": _now_iso(),
            "gates": _gates_for_phase(PHASES[0]),
            "ttl_s": 6,
        }
        _write_json(phase_path, init)

    next_switch = time.time()

    while True:
        reg = _read_json(reg_path, {})
        mode = _derive_mode_from_reg(reg if isinstance(reg, dict) else {})
        period_ms = _tick_period_ms(mode)

        now = time.time()
        if now >= next_switch:
            tick_id += 1
            phase = PHASES[phase_idx % len(PHASES)]
            phase_idx += 1

            doc = {
                "schema": "lira.phase_state.v1",
                "owner": "RhythmLoop",
                "tick_id": tick_id,
                "phase": phase,
                "mode": mode,
                "tick_period_ms": period_ms,
                "phase_started_ts": _now_iso(),
                "updated_at": _now_iso(),
                "gates": _gates_for_phase(phase),
                "ttl_s": max(3, int(period_ms / 1000) + 2),
            }
            _write_json(phase_path, doc)
            next_switch = now + (period_ms / 1000.0)

        time.sleep(RhythmConfig().poll_s)


if __name__ == "__main__":
    run_rhythm_loop()
