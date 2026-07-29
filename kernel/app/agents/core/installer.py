"""
Lira Core: Auto-installer with disk-space safety guard (φ-Limit + φ-Installer)

Rules:
- Prefer local Python modules over external orchestration.
- Auto-install is allowed only if system disk has >= 1 GB free.
- Always log actions to logs/install.log.

This module is intentionally conservative and uses subprocess (not os.system).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_MIN_FREE_GB = float(os.getenv("LIRA_MIN_FREE_GB", "1.0"))
DEFAULT_GUARD_PATHS = [
    os.getenv("LIRA_GUARD_PATH_1", "/home"),
    os.getenv("LIRA_GUARD_PATH_2", "/"),
]

def _log_path() -> Path:
    # ../../../../logs/install.log relative to this file
    return Path(__file__).resolve().parents[4] / "logs" / "install.log"

def _ts() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

def _append_log(line: str) -> None:
    lp = _log_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    with lp.open("a", encoding="utf-8") as f:
        f.write(f"[{_ts()}] {line}\n")

def free_gb(path: str) -> float:
    total, used, free = shutil.disk_usage(path)
    return free / (1024 ** 3)

def has_enough_space(min_free_gb: float = DEFAULT_MIN_FREE_GB, guard_paths: Optional[Sequence[str]] = None) -> bool:
    paths = list(guard_paths) if guard_paths else DEFAULT_GUARD_PATHS
    # If any guard path is below threshold -> block
    for p in paths:
        try:
            fg = free_gb(p)
            if fg < min_free_gb:
                _append_log(f"BLOCKED: low disk space on {p}: free={fg:.2f}GB < {min_free_gb:.2f}GB")
                return False
        except Exception as e:
            _append_log(f"WARN: disk check failed for {p}: {e!r} (continuing)")
    return True

def pip_install(args: Sequence[str]) -> int:
    """
    Install packages via pip. Returns subprocess return code.
    Example: pip_install(['sounddevice', 'webrtcvad'])
    """
    if not has_enough_space():
        return 2
    cmd = [sys.executable, "-m", "pip", "install", *args]
    _append_log(f"pip: {' '.join(cmd)}")
    return subprocess.call(cmd)

def ensure_importable(module: str, pip_name: Optional[str] = None) -> bool:
    """
    Ensure a Python module is importable. If missing, tries to pip install.
    """
    try:
        __import__(module)
        return True
    except Exception:
        pkg = pip_name or module
        rc = pip_install([pkg])
        if rc != 0:
            _append_log(f"FAILED: install {pkg} (rc={rc}) for module {module}")
            return False
        try:
            __import__(module)
            return True
        except Exception as e:
            _append_log(f"FAILED: import {module} after install: {e!r}")
            return False

def ensure_apt_packages(packages: Sequence[str]) -> int:
    """
    Optional helper: install apt packages if available (Linux). Conservative.
    Uses sudo if present; will log and return non-zero if not possible.
    """
    if os.name != "posix":
        _append_log("SKIP: apt install not supported on this OS")
        return 3
    if not has_enough_space():
        return 2
    sudo = shutil.which("sudo")
    apt = shutil.which("apt-get") or shutil.which("apt")
    if not apt:
        _append_log("SKIP: apt not found")
        return 3
    cmd = ([sudo] if sudo else []) + [apt, "update"]
    _append_log(f"apt: {' '.join(cmd)}")
    subprocess.call(cmd)
    cmd = ([sudo] if sudo else []) + [apt, "install", "-y", *packages]
    _append_log(f"apt: {' '.join(cmd)}")
    return subprocess.call(cmd)
