"""Bus client utilities.

Bus is written ONLY by LiraKernelDecisionLoop (DecisionLoop).
All other components are read-only consumers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class BusSnapshot:
    data: dict
    is_fresh: bool
    age_ms: Optional[int]


def _now_ms() -> int:
    return int(time.time() * 1000)


def read_bus(bus_path: str | Path) -> BusSnapshot:
    p = Path(bus_path)
    if not p.exists():
        return BusSnapshot(data={"alerts": {"level": "warn", "message": "bus_missing"}}, is_fresh=False, age_ms=None)

    data: dict[str, Any]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return BusSnapshot(data={"alerts": {"level": "critical", "message": f"bus_parse_error: {e}"}}, is_fresh=False, age_ms=None)

    ts = data.get("ts")
    if ts is None:
        return BusSnapshot(data=data, is_fresh=False, age_ms=None)

    age_ms = _now_ms() - int(ts)

    # Determine freshness by the smallest TTL we can find.
    ttls = []
    for k in ("routing", "throttle", "actions", "workflow", "alerts"):
        ttl = (data.get(k) or {}).get("ttl_ms")
        if isinstance(ttl, (int, float)):
            ttls.append(int(ttl))
    ttl_ms = min(ttls) if ttls else 0

    is_fresh = ttl_ms > 0 and age_ms <= ttl_ms
    return BusSnapshot(data=data, is_fresh=is_fresh, age_ms=age_ms)
