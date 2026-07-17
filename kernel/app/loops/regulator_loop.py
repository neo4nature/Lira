"""RegulatorLoop (v3)

Responsibilities:
- write loop_regulator.json with CPU/RAM/DISK/IO metrics
- compute gates (can_spawn) using thresholds
- provide thresholds defaults (can be overridden via env)

Notes:
- RegulatorLoop does NOT write bus_signals.json (DecisionLoop owns bus).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from config import paths


@dataclass
class RegulatorConfig:
    project_root: Path
    state_path: Path
    tick_interval_ms: int = 2000


def now_ms() -> int:
    return int(time.time() * 1000)


def atomic_write_json(path: Path, data: dict) -> None:
    import os as _os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{_os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _try_psutil():
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:
        return None


def _cpu_pct() -> Optional[float]:
    ps = _try_psutil()
    if ps:
        try:
            return float(ps.cpu_percent(interval=0.1))
        except Exception:
            pass
    # fallback via loadavg normalized
    try:
        load1, _, _ = os.getloadavg()
        cpus = os.cpu_count() or 1
        return float(min(100.0, (load1 / cpus) * 100.0))
    except Exception:
        return None


def _mem_linux_avail_mb() -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (total_mb, avail_mb, swap_used_mb)"""
    ps = _try_psutil()
    if ps:
        try:
            vm = ps.virtual_memory()
            sm = ps.swap_memory()
            return int(vm.total / (1024**2)), int(vm.available / (1024**2)), int(sm.used / (1024**2))
        except Exception:
            pass
    try:
        mem_total = None
        mem_avail = None
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])  # kB
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1])  # kB
                if mem_total and mem_avail:
                    break
        if mem_total and mem_avail is not None:
            return int(mem_total / 1024), int(mem_avail / 1024), None
    except Exception:
        pass
    return None, None, None


def _disk_free_gb(path: str) -> Optional[float]:
    try:
        import shutil
        total, used, free = shutil.disk_usage(path)
        return float(free / (1024**3))
    except Exception:
        return None


def _io_wait_pct() -> Optional[float]:
    ps = _try_psutil()
    if not ps:
        return None
    try:
        # crude: compare iowait from cpu_times_percent if available
        ctp = ps.cpu_times_percent(interval=0.1)
        if hasattr(ctp, "iowait"):
            return float(getattr(ctp, "iowait"))
    except Exception:
        return None
    return None


class RegulatorLoop:
    LOOP_ID = "RegulatorLoop"
    OWNER = "RegulatorLoop"

    def __init__(self, cfg: RegulatorConfig):
        self.cfg = cfg
        self.seq = 0

        # thresholds (env override)
        self.min_ram_warn_mb = float(os.getenv("LIRA_MIN_RAM_WARN_MB", "2500"))
        self.min_ram_hard_mb = float(os.getenv("LIRA_MIN_RAM_HARD_MB", "1200"))
        self.max_cpu_warn_pct = float(os.getenv("LIRA_MAX_CPU_WARN_PCT", "85"))
        self.max_cpu_hard_pct = float(os.getenv("LIRA_MAX_CPU_HARD_PCT", "95"))
        self.min_disk_warn_gb = float(os.getenv("LIRA_MIN_DISK_WARN_GB", "10"))
        self.min_disk_hard_gb = float(os.getenv("LIRA_MIN_DISK_HARD_GB", "5"))
        self.disk_path = os.getenv("LIRA_DISK_GUARD_PATH", "/")

    def tick(self) -> None:
        cpu = _cpu_pct()
        mem_total, mem_avail, swap_used = _mem_linux_avail_mb()
        disk_free = _disk_free_gb(self.disk_path)
        io_wait = _io_wait_pct()

        # gates
        can_spawn = True
        reasons = []
        if mem_avail is not None and mem_avail < self.min_ram_warn_mb:
            can_spawn = False
            reasons.append("RAM_LOW")
        if cpu is not None and cpu > self.max_cpu_warn_pct:
            can_spawn = False
            reasons.append("CPU_HIGH")
        if disk_free is not None and disk_free < self.min_disk_warn_gb:
            can_spawn = False
            reasons.append("DISK_LOW")

        state = {
            "schema": "lira.loop_regulator.v3",
            "loop_id": self.LOOP_ID,
            "owner": self.OWNER,
            "ts": now_ms(),
            "seq": self.seq,
            "status": "running",
            "metrics": {
                "cpu_pct": cpu,
                "ram_total_mb": mem_total,
                "ram_avail_mb": mem_avail,
                "swap_used_mb": swap_used,
                "disk_free_gb": disk_free,
                "io_wait_pct": io_wait,
            },
            "thresholds": {
                "min_ram_avail_warn_mb": self.min_ram_warn_mb,
                "min_ram_avail_hard_mb": self.min_ram_hard_mb,
                "max_cpu_warn_pct": self.max_cpu_warn_pct,
                "max_cpu_hard_pct": self.max_cpu_hard_pct,
                "min_disk_free_warn_gb": self.min_disk_warn_gb,
                "min_disk_free_hard_gb": self.min_disk_hard_gb,
                "disk_guard_path": self.disk_path,
            },
            "gates": {
                "can_spawn": can_spawn,
                "reasons": reasons,
            },
            "last_error": None,
        }
        atomic_write_json(self.cfg.state_path, state)

        

        # --- memory mode (read-only control file for other loops) ---
        try:
            mm_path = self.cfg.state_path.parent / "memory_mode.json"  # fallback
            try:
                mm_path = Path(paths().get("MEMORY_MODE", mm_path))
            except Exception:
                pass
            root = self.cfg.project_root
            policy_path = root / "data" / "config" / "memory_policy.json"

            # Load policy (file wins over env defaults)
            policy_doc = None
            try:
                if policy_path.exists():
                    policy_doc = json.loads(policy_path.read_text(encoding="utf-8"))
            except Exception:
                policy_doc = None

            cooldown_s = float((policy_doc or {}).get("cooldown_seconds", 30))
            hyst = (policy_doc or {}).get("hysteresis", {})

            # Helpers to read hysteresis thresholds (fallback to existing env defaults)
            def _h(key: str, field: str, fallback: float) -> float:
                try:
                    return float(hyst.get(key, {}).get(field, fallback))
                except Exception:
                    return float(fallback)

            ram_warn_enter = _h("ram_avail_mb", "warn_enter", self.min_ram_warn_mb)
            ram_warn_exit  = _h("ram_avail_mb", "warn_exit",  self.min_ram_warn_mb + 700)
            ram_hard_enter = _h("ram_avail_mb", "hard_enter", self.min_ram_hard_mb)
            ram_hard_exit  = _h("ram_avail_mb", "hard_exit",  self.min_ram_hard_mb + 400)

            cpu_warn_enter = _h("cpu_pct", "warn_enter", self.max_cpu_warn_pct)
            cpu_warn_exit  = _h("cpu_pct", "warn_exit",  max(0.0, self.max_cpu_warn_pct - 10))
            cpu_hard_enter = _h("cpu_pct", "hard_enter", self.max_cpu_hard_pct)
            cpu_hard_exit  = _h("cpu_pct", "hard_exit",  max(0.0, self.max_cpu_hard_pct - 5))

            disk_warn_enter = _h("disk_free_gb", "warn_enter", self.min_disk_warn_gb)
            disk_warn_exit  = _h("disk_free_gb", "warn_exit",  self.min_disk_warn_gb + 5)
            disk_hard_enter = _h("disk_free_gb", "hard_enter", self.min_disk_hard_gb)
            disk_hard_exit  = _h("disk_free_gb", "hard_exit",  self.min_disk_hard_gb + 3)

            # Read last mode to apply hysteresis + cooldown
            last_mode = "normal"
            last_change_ms = 0
            try:
                if mm_path.exists():
                    prev = json.loads(mm_path.read_text(encoding="utf-8"))
                    last_mode = str(prev.get("mode") or "normal")
                    last_change_ms = int(prev.get("last_change_ms") or prev.get("ts") or 0)
            except Exception:
                pass

            reasons_mode = []
            hard = False
            warn = False

            # --- compute target severity with hysteresis ---
            def _is_hard() -> bool:
                if mem_avail is not None and mem_avail < ram_hard_enter:
                    return True
                if cpu is not None and cpu > cpu_hard_enter:
                    return True
                if disk_free is not None and disk_free < disk_hard_enter:
                    return True
                return False

            def _is_warn() -> bool:
                if mem_avail is not None and mem_avail < ram_warn_enter:
                    return True
                if cpu is not None and cpu > cpu_warn_enter:
                    return True
                if disk_free is not None and disk_free < disk_warn_enter:
                    return True
                return False

            # Exit conditions depend on last mode
            def _can_exit_emergency() -> bool:
                if mem_avail is not None and mem_avail < ram_hard_exit:
                    return False
                if cpu is not None and cpu > cpu_hard_exit:
                    return False
                if disk_free is not None and disk_free < disk_hard_exit:
                    return False
                return True

            def _can_exit_degraded() -> bool:
                if mem_avail is not None and mem_avail < ram_warn_exit:
                    return False
                if cpu is not None and cpu > cpu_warn_exit:
                    return False
                if disk_free is not None and disk_free < disk_warn_exit:
                    return False
                return True

            # Determine target mode
            if _is_hard():
                hard = True
            elif _is_warn() or (not can_spawn):
                warn = True

            target_mode = "normal"
            if hard:
                target_mode = "emergency"
            elif warn:
                target_mode = "degraded"

            # Apply hysteresis: don't exit until exit thresholds satisfied
            if last_mode == "emergency" and target_mode != "emergency":
                if not _can_exit_emergency():
                    target_mode = "emergency"
            if last_mode == "degraded" and target_mode == "normal":
                if not _can_exit_degraded():
                    target_mode = "degraded"

            # Cooldown: don't flip-flop too fast
            ts_ms = now_ms()
            if target_mode != last_mode and (ts_ms - last_change_ms) < int(cooldown_s * 1000):
                target_mode = last_mode

            mode = target_mode
            if mode == "degraded":
                reasons_mode.append("WARN_OR_GATE")
            if mode == "emergency":
                reasons_mode.append("HARD_LIMIT")

            # Policy suggestions (file wins; env can override via consumers)
            mm_policy_by_mode = (policy_doc or {}).get("memory_mode_policy", {})
            policy_mode = mm_policy_by_mode.get(mode)
            if not isinstance(policy_mode, dict):
                # fallback to sensible defaults
                policy_mode = {
                    "turmalin_ttl_seconds": int(os.getenv("LIRA_MM_TURMALIN_TTL_NORMAL", str(7 * 24 * 3600))),
                    "turmalin_max_records": int(os.getenv("LIRA_MM_TURMALIN_MAX_NORMAL", "50000")),
                    "promote_enabled": True,
                    "embed_enabled": True,
                }
                if mode == "degraded":
                    policy_mode.update({
                        "turmalin_ttl_seconds": int(os.getenv("LIRA_MM_TURMALIN_TTL_DEGRADED", str(3 * 24 * 3600))),
                        "turmalin_max_records": int(os.getenv("LIRA_MM_TURMALIN_MAX_DEGRADED", "30000")),
                        "promote_enabled": os.getenv("LIRA_MM_PROMOTE_DEGRADED", "0") == "1",
                        "embed_enabled": os.getenv("LIRA_MM_EMBED_DEGRADED", "0") == "1",
                    })
                if mode == "emergency":
                    policy_mode.update({
                        "turmalin_ttl_seconds": int(os.getenv("LIRA_MM_TURMALIN_TTL_EMERGENCY", str(24 * 3600))),
                        "turmalin_max_records": int(os.getenv("LIRA_MM_TURMALIN_MAX_EMERGENCY", "15000")),
                        "promote_enabled": False,
                        "embed_enabled": False,
                    })

            # If mode changed, update last_change_ms
            if mode != last_mode:
                last_change_ms = ts_ms

            mm = {
                "schema": "lira.memory_mode.v2",
                "owner": self.OWNER,
                "ts": ts_ms,
                "mode": mode,
                "last_mode": last_mode,
                "last_change_ms": last_change_ms,
                "cooldown_seconds": cooldown_s,
                "policy": policy_mode,
                "reasons": sorted(set(reasons + reasons_mode)),
                "metrics": {
                    "cpu_pct": cpu,
                    "ram_avail_mb": mem_avail,
                    "disk_free_gb": disk_free,
                    "io_wait_pct": io_wait,
                },
            }
            atomic_write_json(mm_path, mm)
        except Exception:
            pass

        self.seq += 1

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:
                err = {
                    "schema": "lira.loop_regulator.v3",
                    "loop_id": self.LOOP_ID,
                    "owner": self.OWNER,
                    "ts": now_ms(),
                    "seq": self.seq,
                    "status": "error",
                    "metrics": {},
                    "thresholds": {},
                    "gates": {"can_spawn": False, "reasons": ["ERROR"]},
                    "last_error": str(e),
                }
                atomic_write_json(self.cfg.state_path, err)
                self.seq += 1
            time.sleep(self.cfg.tick_interval_ms / 1000.0)


def _infer_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    root = _infer_project_root()
    state_path = root / "data" / "runtime" / "state" / "loop_regulator.json"
    RegulatorLoop(RegulatorConfig(project_root=root, state_path=state_path)).run_forever()


if __name__ == "__main__":
    main()
