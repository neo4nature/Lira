from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from config import paths

INTERVAL_S = float(os.getenv("LIRA_CRITICALITY_INTERVAL_S", "5"))
TARGET = float(os.getenv("LIRA_CRITICALITY_TARGET", "0.5"))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def step_once() -> Dict[str, Any]:
    p = paths()
    st = p["STATE_DIR"]
    reg = _read_json(st / "loop_regulator.json", {})
    queue = _read_json(p["QUEUE_FILE"], [])
    bus = _read_json(st / "bus_signals.json", {})
    wd = _read_json(p["WATCHDOG_REPORT"], {})

    metrics = reg.get("metrics") if isinstance(reg.get("metrics"), dict) else {}
    thresholds = reg.get("thresholds") if isinstance(reg.get("thresholds"), dict) else {}
    cpu = float(metrics.get("cpu_pct") or 0.0)
    ram_av = float(metrics.get("ram_avail_mb") or 0.0)
    ram_warn = float(thresholds.get("min_ram_avail_warn_mb") or 2500.0)
    ram_hard = float(thresholds.get("min_ram_avail_hard_mb") or 1200.0)
    disk_free_gb = float(metrics.get("disk_free_gb") or 0.0)
    io_wait = float(metrics.get("io_wait_pct") or 0.0)
    qlen = len(queue) if isinstance(queue, list) else 0

    cpu_p = _clamp(cpu / 100.0)
    if ram_av <= ram_hard:
        ram_p = 1.0
    elif ram_av >= ram_warn:
        ram_p = 0.0
    else:
        ram_p = 1.0 - ((ram_av - ram_hard) / max(1.0, (ram_warn - ram_hard)))
    queue_p = _clamp(qlen / float(os.getenv("LIRA_QUEUE_NORM", "20")))
    disk_p = _clamp((8.0 - disk_free_gb) / 8.0) if disk_free_gb > 0 else 0.0
    io_p = _clamp(io_wait / 100.0)

    ports = bus.get("ports") if isinstance(bus.get("ports"), dict) else {}
    port_fail = 0.0
    if isinstance(ports, dict):
        for name in ("api", "ollama"):
            ent = ports.get(name) if isinstance(ports.get(name), dict) else {}
            if ent and ent.get("open") is False:
                port_fail += 0.15
    alerts = wd.get("alerts") if isinstance(wd.get("alerts"), list) else []
    alert_p = _clamp(len(alerts) / 8.0)

    score = _clamp(0.28 * cpu_p + 0.28 * ram_p + 0.16 * queue_p + 0.10 * disk_p + 0.08 * io_p + 0.05 * alert_p + port_fail)
    deviation = abs(score - TARGET)
    if score < 0.25:
        regime = "under"
    elif score <= 0.75:
        regime = "edge"
    else:
        regime = "over"

    out = {
        "schema": "lira.criticality_state.v1",
        "updated_at": _now_iso(),
        "target": TARGET,
        "score": round(score, 6),
        "deviation": round(deviation, 6),
        "regime": regime,
        "source": "criticality_publisher",
        "signals": {
            "cpu_pressure": round(cpu_p, 6),
            "ram_pressure": round(ram_p, 6),
            "queue_pressure": round(queue_p, 6),
            "disk_pressure": round(disk_p, 6),
            "io_pressure": round(io_p, 6),
            "alert_pressure": round(alert_p, 6),
            "queue_len": qlen,
        },
    }
    _write_json(st / "criticality_state.json", out)
    _append_jsonl(p["RUNTIME_DIR"] / "logs" / "criticality_log.jsonl", out)
    return out


def main() -> int:
    while True:
        try:
            step_once()
        except KeyboardInterrupt:
            return 0
        except Exception:
            pass
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    raise SystemExit(main())
