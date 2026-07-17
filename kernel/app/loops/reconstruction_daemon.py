import time
from pathlib import Path

from config import ensure_dirs, env, paths
from agents.memory.memory_loader import read_json
from agents.core.runtime_stream import append_event
from agents.memory.reconstruction import rebuild_core_memory


def _veto_hard(bus: dict) -> bool:
    veto = (bus.get("operational_veto") or {})
    return bool(veto.get("active", False)) and (veto.get("severity") or "").lower() == "hard"


def _memory_emergency(mm: dict) -> bool:
    mode = (mm.get("mode") or mm.get("memory_mode") or "").lower()
    return mode == "emergency"


def main():
    ensure_dirs()
    P = paths()
    state_dir = Path(P["STATE_DIR"]).resolve()
    bus_path = state_dir / "bus_signals.json"
    mm_path = Path(P["MEMORY_MODE"]).resolve()

    poll = float(env("RECONSTRUCTION_POLL_SECONDS", "1.0"))
    max_wait = float(env("RECONSTRUCTION_MAX_WAIT_SECONDS", "60"))

    t0 = time.time()
    append_event("reconstruction_daemon_start", "reconstruction_daemon", {"poll": poll, "max_wait": max_wait})

    # Wait until bus exists (DecisionLoop should publish it)
    while time.time() - t0 < max_wait:
        if bus_path.exists():
            break
        time.sleep(poll)

    bus = read_json(bus_path, default={}) if bus_path.exists() else {}
    mm = read_json(mm_path, default={}) if mm_path.exists() else {}

    if _veto_hard(bus) or _memory_emergency(mm):
        append_event(
            "reconstruction_skipped",
            "reconstruction_daemon",
            {"reason": "veto.hard or emergency", "veto": bus.get("operational_veto"), "memory_mode": mm},
        )
        return

    try:
        rebuild_core_memory(light=True)
    except Exception as e:
        append_event("reconstruction_error", "reconstruction_daemon", {"error": str(e)})


if __name__ == "__main__":
    main()
