from __future__ import annotations

import json
import os
import time
from pathlib import Path
from fastapi import APIRouter
from config import runtime_dir, paths

router = APIRouter()

@router.get("/bus")
def bus():
    p = runtime_dir() / "state" / "bus_signals.json"
    if not p.exists():
        return {"error": "bus_signals.json not found"}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}

@router.get("/regulator")
def regulator():
    p = runtime_dir() / "state" / "loop_regulator.json"
    if not p.exists():
        return {"error": "loop_regulator.json not found"}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}

@router.get("/queue")
def queue():
    p = paths()["QUEUE_FILE"]
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []

@router.get("/veto")
def veto():
    p = runtime_dir() / "state" / "bus_signals.json"
    if not p.exists():
        return {"active": False, "error": "bus missing"}
    try:
        bus = json.loads(p.read_text(encoding="utf-8"))
        return (bus.get("operational_veto") or {"active": False}) if isinstance(bus, dict) else {"active": False}
    except Exception as e:
        return {"active": False, "error": str(e)}


@router.get('/dashboard')
def dashboard():
    p = paths()
    state = runtime_dir() / 'state'

    def load_json(fp: Path, default):
        try:
            if not fp.exists():
                return default
            return json.loads(fp.read_text(encoding='utf-8'))
        except Exception:
            return default

    def age_s(fp: Path):
        try:
            if not fp.exists():
                return None
            import time
            return round(time.time() - fp.stat().st_mtime, 2)
        except Exception:
            return None

    bus_p = state / 'bus_signals.json'
    reg_p = state / 'loop_regulator.json'
    phase_p = state / 'phase_state.json'
    world_p = state / 'world_state.json'
    energy_p = state / 'energy_state.json'
    policy_p = state / 'policy_state.json'
    align_p = state / 'alignment_state.json'
    crit_p = state / 'criticality_state.json'
    wd_p = Path(p.get('WATCHDOG_REPORT', state / 'watchdog_report.json'))
    base_p = Path(p.get('BASE_INDEX', Path(p['MEMORY_DIR']) / 'root' / 'base_index.json'))
    rt_stream = Path(p.get('RUNTIME_STREAM', Path(p['SHARED_CONTEXT_DIR']) / 'events' / 'runtime.jsonl'))

    bus_obj = load_json(bus_p, {})
    phase_obj = load_json(phase_p, {})
    world_obj = load_json(world_p, {})
    energy_obj = load_json(energy_p, {})
    policy_obj = load_json(policy_p, {})
    align_obj = load_json(align_p, {})
    crit_obj = load_json(crit_p, {})
    wd_obj = load_json(wd_p, {})
    base_obj = load_json(base_p, {})

    # tail runtime stream
    last_events = []
    try:
        if rt_stream.exists():
            with rt_stream.open('rb') as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                chunk = f.read().decode('utf-8', errors='ignore')
            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            for ln in lines[-5:]:
                try:
                    last_events.append(json.loads(ln))
                except Exception:
                    continue
    except Exception:
        pass

    return {
        'freshness_s': {
            'bus': age_s(bus_p),
            'regulator': age_s(reg_p),
            'phase': age_s(phase_p),
            'world': age_s(world_p),
            'energy': age_s(energy_p),
            'policy': age_s(policy_p),
            'alignment': age_s(align_p),
            'criticality': age_s(crit_p),
            'watchdog_report': age_s(wd_p),
            'base_index': age_s(base_p),
            'runtime_stream': age_s(rt_stream),
        },
        'mode': bus_obj.get('mode') if isinstance(bus_obj, dict) else None,
        'phase': phase_obj if isinstance(phase_obj, dict) else None,
        'energy': energy_obj if isinstance(energy_obj, dict) else None,
        'policy': policy_obj if isinstance(policy_obj, dict) else None,
        'alignment': align_obj if isinstance(align_obj, dict) and align_obj else ((bus_obj.get('alignment') if isinstance(bus_obj, dict) else None)),
        'criticality': crit_obj if isinstance(crit_obj, dict) and crit_obj else ((bus_obj.get('criticality') if isinstance(bus_obj, dict) else None)),
        'world': world_obj if isinstance(world_obj, dict) else None,
        'conversation_active': bus_obj.get('conversation_active') if isinstance(bus_obj, dict) else None,
        'veto': (bus_obj.get('operational_veto') if isinstance(bus_obj, dict) else None),
        'background': (bus_obj.get('background') if isinstance(bus_obj, dict) else None),
        'models': (bus_obj.get('models') if isinstance(bus_obj, dict) else None),
        'memory': (bus_obj.get('memory') if isinstance(bus_obj, dict) else None),
        'queue_len': wd_obj.get('queue_len') if isinstance(wd_obj, dict) else None,
        'alerts': wd_obj.get('alerts') if isinstance(wd_obj, dict) else None,
        'mounts': wd_obj.get('mounts') if isinstance(wd_obj, dict) else None,
        'disks': wd_obj.get('disks') if isinstance(wd_obj, dict) else None,
        'base_index': {
            'updated_at': base_obj.get('updated_at') if isinstance(base_obj, dict) else None,
            'summary': base_obj.get('summary') if isinstance(base_obj, dict) else None,
            'embeddings': base_obj.get('embeddings') if isinstance(base_obj, dict) else None,
        },
        'last_events': last_events,
    }


@router.get("/islands")
def islands():
    """Quick diagnostics for optional subsystems ("islands").

    Reports whether each subsystem is enabled (env flags), whether it appears to be
    running (PID file + /proc check), and the last relevant event in runtime stream.
    """

    p = paths()
    root = Path(p.get("PROJECT_ROOT") or Path.cwd())
    pids_dir = root.parent / "logs" / "pids"
    state_dir = runtime_dir() / "state"

    def _pid_running(pid: int) -> bool:
        if pid is None:
            return False
        try:
            pid_i = int(pid)
        except Exception:
            return False
        if pid_i <= 0:
            return False
        return Path(f"/proc/{pid_i}").exists()

    def _read_pid(name: str):
        fp = pids_dir / name
        if not fp.exists():
            return {"pid": None, "running": False, "pidfile_age_s": None}
        try:
            pid = int(fp.read_text(encoding="utf-8").strip())
        except Exception:
            return {"pid": None, "running": False, "pidfile_age_s": None}
        try:
            age = round(time.time() - fp.stat().st_mtime, 2)
        except Exception:
            age = None
        return {"pid": pid, "running": _pid_running(pid), "pidfile_age_s": age}

    def _tail_runtime(max_lines: int = 200):
        rt_stream = Path(p.get("RUNTIME_STREAM", (p["SHARED_CONTEXT_DIR"] / "events" / "runtime.jsonl")))
        if not rt_stream.exists():
            return []
        out = []
        try:
            with rt_stream.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 256000))
                chunk = f.read().decode("utf-8", errors="ignore")
            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            for ln in lines[-max_lines:]:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
        except Exception:
            return []
        return out

    events = _tail_runtime()

    def _last_event(types: set[str]):
        for ev in reversed(events):
            if not isinstance(ev, dict):
                continue
            et = (ev.get("event_type") or ev.get("type") or "").strip()
            if et in types:
                return ev
        return None

    # Read bus and memory_mode for context
    bus = {}
    try:
        bus_p = state_dir / "bus_signals.json"
        if bus_p.exists():
            bus = json.loads(bus_p.read_text(encoding="utf-8"))
    except Exception:
        bus = {}

    memory_mode = None
    try:
        mm_p = Path(p.get("MEMORY_MODE", state_dir / "memory_mode.json"))
        if mm_p.exists():
            mm = json.loads(mm_p.read_text(encoding="utf-8"))
            if isinstance(mm, dict):
                memory_mode = mm.get("mode") or mm.get("memory_mode")
    except Exception:
        memory_mode = None

    enabled = {
        "reflective": os.getenv("LIRA_ENABLE_REFLECTIVE", "1") == "1",
        "cognitive": os.getenv("LIRA_ENABLE_COGNITIVE_DAEMON", "0") == "1",
        "gaia": os.getenv("LIRA_ENABLE_GAIA", "0") == "1",
        "integration": os.getenv("LIRA_ENABLE_INTEGRATION", "0") == "1",
    }

    procs = {
        "regulator": _read_pid("regulator.pid"),
        "decision": _read_pid("decision.pid"),
        "turmalin": _read_pid("turmalin.pid"),
        "watchdog": _read_pid("watchdog.pid"),
        "night_daemon": _read_pid("night_daemon.pid"),
        "cognitive_daemon": _read_pid("cognitive_daemon.pid"),
        "gaia_executor": _read_pid("gaia_executor.pid"),
        "integration_bridge": _read_pid("integration_bridge.pid"),
        "api": _read_pid("api.pid"),
        "ready_notifier": _read_pid("ready_notifier.pid"),
    }

    last = {
        "snapshot_written": _last_event({"snapshot_written"}),
        # Event types must match what emitters actually publish in runtime stream.
        "reflective_insight": _last_event({"reflective_end", "reflective_start"}),
        "cognitive": _last_event({"cognitive_end", "cognitive_start", "cognitive_skipped", "cognitive_missing", "cognitive_error"}),
        "gaia": _last_event({"gaia_spawn_end", "gaia_spawn_start", "gaia_executor_start", "gaia_executor_error"}),
        "integration": _last_event({"integration_started", "integration_event"}),
        "memory_promoted": _last_event({"memory_promoted"}),
    }

    return {
        "enabled": enabled,
        "processes": procs,
        "memory_mode": memory_mode,
        "veto": (bus.get("operational_veto") if isinstance(bus, dict) else None),
        "memory": (bus.get("memory") if isinstance(bus, dict) else None),
        "last_events": last,
    }


@router.get("/reconstruction")
def reconstruction_status():
    """Status for core memory reconstruction (ledger)."""
    P = paths()
    out_json = Path(P["CORE_MEMORY_REBUILD"]).resolve()
    out_md = Path(P.get("CORE_MEMORY_REBUILD_MD", out_json.with_suffix(".md"))).resolve()

    ledger = None
    if out_json.exists():
        try:
            ledger = json.loads(out_json.read_text(encoding="utf-8"))
        except Exception:
            ledger = None
    md_info = None
    if out_md.exists():
        try:
            md_info = {"path": str(out_md), "bytes": out_md.stat().st_size, "mtime": out_md.stat().st_mtime}
        except Exception:
            md_info = {"path": str(out_md)}

    # last rebuild event
    last = None
    try:
        events = []
        rp = Path(P["RUNTIME_STREAM"]).resolve()
        if rp.exists():
            lines = rp.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    events.append(json.loads(ln))
                except Exception:
                    continue
        for ev in reversed(events):
            if (ev.get("event_type") or "") in {"core_memory_rebuild_end", "reconstruction_error", "reconstruction_skipped"}:
                last = ev
                break
    except Exception:
        last = None

    return {
        "present": bool(ledger),
        "json": str(out_json),
        "md": md_info,
        "last_event": last,
        "snapshot_date": (ledger or {}).get("snapshot", {}).get("latest_date") if isinstance(ledger, dict) else None,
        "generated_at": (ledger or {}).get("generated_at") if isinstance(ledger, dict) else None,
    }
