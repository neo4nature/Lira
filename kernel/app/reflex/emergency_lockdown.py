from __future__ import annotations

import json
import os
import tarfile
import time
import subprocess
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import runtime_dir, ensure_dirs, paths
from agents.memory.memory_loader import write_json

def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore") or "")
    except Exception:
        return default

def _write_json(path: Path, obj: Any) -> None:
    write_json(path, obj)

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _collect_paths() -> List[Path]:
    rt = runtime_dir()
    k = rt / "state"
    logs = rt / "logs"
    base = []
    # state
    for name in ["bus_signals.json", "model_locks.json", "loop_regulator.json", "conversation_state.json", "night_cycle_state.json"]:
        p = k / name
        if p.exists():
            base.append(p)
    # queue
    q = paths().get("QUEUE_FILE")
    if q and Path(q).exists():
        base.append(Path(q))
    # logs (tail files included fully; they are JSONL)
    for name in ["runtime.jsonl", "errors.jsonl"]:
        p = logs / name
        if p.exists():
            base.append(p)
    # reflection
    for name in ["Lira_Hybrid_Self_Reflection.json", "Lira_Hybrid_Self_Reflection.md"]:
        p = Path(__file__).resolve().parents[3] / name
        if p.exists():
            base.append(p)
    return base


def _is_mountpoint(p: Path) -> bool:
    try:
        return os.path.ismount(str(p))
    except Exception:
        return False

def _pick_target_dir() -> Path:
    secure_root = os.environ.get("LIRA_SECURE_ROOT", "").strip() or os.environ.get("SECURE_ROOT", "").strip()
    archive_root = os.environ.get("LIRA_ARCHIVE_ROOT", "").strip() or os.environ.get("ARCHIVE_ROOT", "").strip()
    pr = Path(__file__).resolve().parents[2]  # .../kernel/app
    docker_fallback = pr.parent / "archives" / "emergency"
    sd_fallback = Path(archive_root) / "emergency" if archive_root else None

    candidates = []
    if secure_root:
        candidates.append(Path(secure_root) / "LiraSecure" / "emergency")
    candidates.append(docker_fallback)
    if sd_fallback:
        candidates.append(sd_fallback)

    min_free_gb = float(os.environ.get("LIRA_EMERGENCY_MIN_FREE_GB", "0.5"))
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            if secure_root and str(c).startswith(str(Path(secure_root))):
                if not _is_mountpoint(Path(secure_root)):
                    continue
            total, used, free = shutil.disk_usage(str(c))
            if free / (1024**3) >= min_free_gb:
                return c
        except Exception:
            continue

    rt = runtime_dir()
    fallback = rt / "secure"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _make_tar(snapshot_dir: Path) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    tar_path = snapshot_dir / f"emergency_snapshot_{ts}.tar"
    files = _collect_paths()

    with tarfile.open(tar_path, "w") as tf:
        for f in files:
            try:
                # store relative-ish paths
                arcname = str(f).lstrip("/")
                tf.add(str(f), arcname=arcname)
            except Exception:
                continue
    return tar_path

def _encrypt_openssl(src: Path, dest: Path, password: str) -> Dict[str, Any]:
    """
    Encrypt using OpenSSL AES-256-CBC + PBKDF2.
    Returns {ok, reason}
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "openssl", "enc", "-aes-256-cbc", "-pbkdf2",
            "-salt",
            "-in", str(src),
            "-out", str(dest),
            "-pass", f"pass:{password}",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return {"ok": False, "reason": (r.stderr or r.stdout or "openssl failed").strip()}
        return {"ok": True}
    except FileNotFoundError:
        return {"ok": False, "reason": "openssl not found"}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}

def lockdown(trigger_phrase: str, fast_exit: bool = False) -> Dict[str, Any]:
    """
    Perform emergency lockdown:
    - write emergency state to bus
    - create encrypted snapshot in secure dir
    - request shutdown (flag), optionally exit process
    """
    ensure_dirs()
    rt = runtime_dir()
    st = rt / "state"
    bus_path = st / "bus_signals.json"

    bus = _read_json(bus_path, {})
    if not isinstance(bus, dict):
        bus = {}

    # Force safe mode immediately
    models = bus.get("models") if isinstance(bus.get("models"), dict) else {}
    models["allow_background_tasks"] = False
    models["allow_heavy_reasoning"] = False
    models["deep_locked"] = True
    bus["models"] = models

    bus["emergency"] = {
        "active": True,
        "trigger_phrase": trigger_phrase,
        "ts": _now_iso(),
        "action": "lockdown",
    }

    _write_json(bus_path, bus)

    password = os.environ.get("LIRA_EMERGENCY_KEY", "").strip()

    snapshot_dir = _pick_target_dir()
    tar_path = _make_tar(snapshot_dir)
    out_path = tar_path.with_suffix(".tar.enc")

    if not password:
        # Encrypted dm-0 is secure at rest; allow plaintext tar there
        return {
            "ok": True,
            "reason": "LIRA_EMERGENCY_KEY not set: stored plaintext snapshot (volume encrypted at rest)",
            "snapshot_plain": str(tar_path),
            "snapshot_encrypted": None,
            "secure_dir": str(snapshot_dir),
        }

    enc = _encrypt_openssl(tar_path, out_path, password=password)
    if not enc.get("ok"):
        return {
            "ok": False,
            "reason": f"encryption_failed: {enc.get('reason','')}",
            "snapshot_plain": str(tar_path),
            "snapshot_encrypted": None,
        }

    # safety: ensure encrypted file exists and is non-empty before deleting plaintext
    try:
        if (not out_path.exists()) or out_path.stat().st_size <= 0:
            return {
                "ok": False,
                "reason": "encryption_failed: output_missing_or_empty",
                "snapshot_plain": str(tar_path),
                "snapshot_encrypted": None,
            }
    except Exception:
        return {
            "ok": False,
            "reason": "encryption_failed: output_check_error",
            "snapshot_plain": str(tar_path),
            "snapshot_encrypted": None,
        }
    # remove plaintext tar after successful encryption
    try:
        tar_path.unlink(missing_ok=True)  # py3.11+
    except Exception:
        pass

    # shutdown flag
    flag = st / "SHUTDOWN_REQUESTED"
    try:
        flag.write_text(json.dumps({"ts": _now_iso(), "trigger": trigger_phrase}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    if fast_exit or os.environ.get("LIRA_EMERGENCY_EXIT", "0").strip() == "1":
        os._exit(0)

    return {"ok": True, "snapshot_encrypted": str(out_path), "secure_dir": str(snapshot_dir)}
