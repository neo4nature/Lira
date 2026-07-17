import os
import time
import signal
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from config import paths
from agents.memory.memory_loader import read_json, write_json
from agents.core.runtime_stream import append_event
from agents.core.idle_gate import allows_background

def _fresh(path: Path, max_age_s: int) -> bool:
    try:
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return age <= max_age_s
    except Exception:
        return False

def _read_pid(pid_file: Path) -> int:
    try:
        return int(pid_file.read_text().strip())
    except Exception:
        return -1

def _is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def _restart(cmd: list, pid_file: Path, log_file: Path, cwd: Optional[str] = None) -> Dict[str, Any]:
    # restart = zabij wiszącego poprzednika (jeśli żyje), potem nowy proces
    old_pid = _read_pid(pid_file)
    if _is_running(old_pid):
        try:
            os.kill(old_pid, signal.SIGTERM)
        except Exception:
            pass
    log_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "ab") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=lf, cwd=cwd)
    pid_file.write_text(str(p.pid))
    return {"restarted": True, "pid": p.pid, "cmd": cmd}


def _disk_usage(p: Path) -> Dict[str, Any]:
    try:
        import shutil

        total, used, free = shutil.disk_usage(str(p))
        gb = 1024 ** 3
        return {
            "path": str(p),
            "total_gb": round(total / gb, 2),
            "used_gb": round(used / gb, 2),
            "free_gb": round(free / gb, 2),
        }
    except Exception as e:
        return {"path": str(p), "error": str(e)}


def _mount_ok(p: Path) -> bool:
    """Check if path is accessible — path itself or any parent may be the mount point."""
    try:
        if not p:
            return True
        if not p.exists():
            return False
        # Check path itself or walk up to find a mount point
        check = p
        for _ in range(6):
            if os.path.ismount(str(check)):
                return True
            parent = check.parent
            if parent == check:
                break
            check = parent
        # Fallback: path exists and is readable — good enough
        return os.access(str(p), os.R_OK)
    except Exception:
        return False


def _queue_len(queue_file: Path) -> int:
    q = read_json(queue_file, default=[])
    return len(q) if isinstance(q, list) else 0


def _file_age_s(p: Path) -> Optional[float]:
    try:
        if not p.exists():
            return None
        return time.time() - p.stat().st_mtime
    except Exception:
        return None

def run(poll_s: float = 15.0,
        stale_bus_s: int = 15,
        stale_regulator_s: int = 15,
        stale_thermo_s: int = 60,
        base_index_max_age_s: int = 6 * 3600,
        embeddings_max_age_s: int = 48 * 3600,
        emit_every_s: int = 60) -> None:
    """Watchdog that restarts kernel loops if their state files stop updating.

    Assumes PID files are in kernel/logs/pids and logs in kernel/logs/*.log.
    """
    p = paths()
    root = Path(p["PROJECT_ROOT"]).parent  # kernel/
    pids_dir = root / "logs" / "pids"
    logs_dir = root / "logs"

    state_dir = Path(p["RUNTIME_DIR"]) / "state"
    bus = state_dir / "bus_signals.json"
    reg = state_dir / "loop_regulator.json"

    # Optional telemetry from Thermometr (JSONL) - Decision reads it too.
    thermo_path = Path(os.getenv("LIRA_THERMO_PATH", str(root / "logs" / "thermo.jsonl")))

    # Base index produced by nightly prepare_data
    base_index = Path(p.get("BASE_INDEX", Path(p["MEMORY_DIR"]) / "root" / "base_index.json"))
    watchdog_report = Path(p.get("WATCHDOG_REPORT", state_dir / "watchdog_report.json"))
    # Embeddings index produced by nightly embeddings_batch
    embeddings_index = Path(p.get("EMBEDDINGS_INDEX", Path(p["MEMORY_DIR"]) / "embeddings" / "embeddings_index.jsonl"))


    # Disk roots
    data_root = Path(p["DATA_ROOT"])
    archive_root = Path(os.getenv("LIRA_ARCHIVE_ROOT", str(root / "archives")))
    secure_root = Path(os.getenv("LIRA_SECURE_ROOT", str(root / "secure")))

    # Thresholds
    min_free_data_gb = float(os.getenv("LIRA_WATCHDOG_MIN_FREE_DATA_GB", "2.0"))
    min_free_archive_gb = float(os.getenv("LIRA_WATCHDOG_MIN_FREE_ARCHIVE_GB", "1.0"))
    min_free_secure_gb = float(os.getenv("LIRA_WATCHDOG_MIN_FREE_SECURE_GB", "1.0"))
    max_queue_warn = int(os.getenv("LIRA_WATCHDOG_MAX_QUEUE_WARN", "500"))

    decision_pidf = pids_dir / "decision.pid"
    regulator_pidf = pids_dir / "regulator.pid"

    decision_log = logs_dir / "decision.log"
    regulator_log = logs_dir / "regulator.log"

    # moduły jak w start_system.sh — uruchamianie plikiem łamało importy i ścieżki
    app_dir = str(Path(p["PROJECT_ROOT"]).resolve())
    decision_cmd = ["python3", "-m", "loops.decision_loop"]
    regulator_cmd = ["python3", "-m", "loops.regulator_loop"]

    last_emit = 0.0
    last_prepare_data = 0.0
    last_prepare_emb = 0.0

    while True:
        # decision loop health
        decision_pid = _read_pid(decision_pidf)
        regulator_pid = _read_pid(regulator_pidf)

        decision_ok = _is_running(decision_pid) and _fresh(bus, stale_bus_s)
        regulator_ok = _is_running(regulator_pid) and _fresh(reg, stale_regulator_s)

        thermo_age = _file_age_s(thermo_path)
        # thermo is optional — if path not explicitly set, treat as not applicable (True)
        _thermo_explicit = bool(os.getenv("LIRA_THERMO_PATH"))
        thermo_ok = (not _thermo_explicit) or ((thermo_age is not None) and (thermo_age <= stale_thermo_s))

        q_len = _queue_len(Path(p["QUEUE_FILE"]))
        base_age = _file_age_s(base_index)
        emb_age = _file_age_s(embeddings_index)

        data_du = _disk_usage(data_root)
        arch_du = _disk_usage(archive_root)
        sec_du = _disk_usage(secure_root)

        # Build watchdog report (read-only signals for Decision/Cognitive)
        report: Dict[str, Any] = {
            "ts": time.time(),
            "bus_fresh": decision_ok,
            "regulator_fresh": regulator_ok,
            "thermo_fresh": thermo_ok,
            "thermo_age_s": thermo_age,
            "queue_len": q_len,
            "base_index_age_s": base_age,
            "embeddings_age_s": emb_age,
            "embeddings_ready": (emb_age is not None and embeddings_index.exists() and embeddings_index.stat().st_size > 0),
            "mounts": {
                "archive_ok": _mount_ok(archive_root) if os.getenv("LIRA_ARCHIVE_ROOT") else True,
                "secure_ok": _mount_ok(secure_root) if os.getenv("LIRA_SECURE_ROOT") else True,
            },
            "disks": {
                "data_root": data_du,
                "archive_root": arch_du,
                "secure_root": sec_du,
            },
            "alerts": [],
        }

        # Alerts — severity: observe (niski, auto-healing) | diagnose (warto zbadać) | restrict (wymaga interwencji)
        if isinstance(data_du, dict) and data_du.get("free_gb") is not None and data_du["free_gb"] < min_free_data_gb:
            report["alerts"].append({"kind": "disk_low", "severity": "restrict", "target": "data_root", "free_gb": data_du["free_gb"]})
        if isinstance(arch_du, dict) and arch_du.get("free_gb") is not None and arch_du["free_gb"] < min_free_archive_gb:
            report["alerts"].append({"kind": "disk_low", "severity": "restrict", "target": "archive_root", "free_gb": arch_du["free_gb"]})
        if isinstance(sec_du, dict) and sec_du.get("free_gb") is not None and sec_du["free_gb"] < min_free_secure_gb:
            report["alerts"].append({"kind": "disk_low", "severity": "restrict", "target": "secure_root", "free_gb": sec_du["free_gb"]})
        if q_len > max_queue_warn:
            report["alerts"].append({"kind": "queue_high", "severity": "diagnose", "queue_len": q_len, "threshold": max_queue_warn})

        if emb_age is None:
            report["alerts"].append({"kind": "embeddings_missing", "severity": "diagnose"})
        elif emb_age > embeddings_max_age_s:
            report["alerts"].append({"kind": "embeddings_stale", "severity": "observe", "age_s": emb_age, "threshold_s": embeddings_max_age_s})

        # Wyznacz najwyższy poziom alertu
        _severity_rank = {"observe": 1, "diagnose": 2, "restrict": 3}
        _max_sev = max((_severity_rank.get(a.get("severity", "observe"), 1) for a in report["alerts"]), default=0)
        report["watchdog_status"] = {0: "ok", 1: "observe", 2: "diagnose", 3: "restrict"}.get(_max_sev, "ok")

        # Persist watchdog report (atomic)
        try:
            write_json(watchdog_report, report)
        except Exception:
            pass

        now = time.time()
        if now - last_emit >= emit_every_s and report.get("alerts"):
            append_event("watchdog_alert", "watchdog_loop", {"alerts": report["alerts"], "queue_len": q_len})
            last_emit = now

        # If base index is stale, refresh it when system allows background (idle).
        if (base_age is None or base_age > base_index_max_age_s) and (now - last_prepare_data > 300):
            ok_bg, reason = allows_background(require_veto_none=False)
            if ok_bg:
                # run prepare_data_night with low priority
                cmd = ["python3", "-m", "agents.memory.prepare_data_night"]
                log = logs_dir / "prepare_data_night.log"
                with open(log, "ab") as lf:
                    pproc = subprocess.Popen(cmd, stdout=lf, stderr=lf, cwd=str(Path(p["PROJECT_ROOT"]).resolve()))
                last_prepare_data = now
                append_event("watchdog_action", "watchdog_loop", {"action": "prepare_data_night", "pid": pproc.pid, "reason": f"base_index_stale:{base_age}"})

        # If embeddings index is missing/stale, refresh it when system allows background (idle).
        if ((emb_age is None) or (emb_age > embeddings_max_age_s)) and (now - last_prepare_emb > 300):
            ok_bg, reason = allows_background(require_veto_none=True)
            if ok_bg:
                cmd = ["python3", "-m", "agents.memory.embeddings_batch"]
                log = logs_dir / "embeddings_batch.log"
                with open(log, "ab") as lf:
                    pproc = subprocess.Popen(cmd, stdout=lf, stderr=lf, cwd=str(Path(p["PROJECT_ROOT"]).resolve()))
                last_prepare_emb = now
                append_event("watchdog_action", "watchdog_loop", {"action": "embeddings_batch", "pid": pproc.pid, "reason": f"embeddings_stale:{emb_age}"})

        if not regulator_ok:
            append_event("watchdog_action", "watchdog_loop", _restart(regulator_cmd, regulator_pidf, regulator_log, cwd=app_dir))
        if not decision_ok:
            append_event("watchdog_action", "watchdog_loop", _restart(decision_cmd, decision_pidf, decision_log, cwd=app_dir))

        time.sleep(poll_s)

if __name__ == "__main__":
    run()