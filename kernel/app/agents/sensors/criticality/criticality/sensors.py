from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Deque, Optional, Tuple, Dict, Any
from collections import deque

import psutil


@dataclass
class ResourceSample:
    ts: float
    cpu_percent: float
    ram_percent: float
    load1: Optional[float]


def sample_resources() -> ResourceSample:
    """Lightweight system resource snapshot."""
    ts = time.time()
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    load1 = None
    try:
        load1 = psutil.getloadavg()[0]
    except Exception:
        load1 = None
    return ResourceSample(ts=ts, cpu_percent=cpu, ram_percent=ram, load1=load1)


def mean(values) -> float:
    v = list(values)
    return sum(v) / len(v) if v else 0.0


def stdev(values) -> float:
    v = list(values)
    n = len(v)
    if n < 2:
        return 0.0
    m = mean(v)
    var = sum((x - m) ** 2 for x in v) / (n - 1)
    return var ** 0.5


def coeff_var(values, eps: float = 1e-9) -> float:
    """Coefficient of variation (stdev/mean). Useful as a dimensionless instability indicator."""
    v = list(values)
    if not v:
        return 0.0
    m = mean(v)
    return stdev(v) / (abs(m) + eps)


class RollingWindow:
    """Simple rolling statistics window."""
    def __init__(self, maxlen: int = 200):
        self.maxlen = maxlen
        self._q: Deque[float] = deque(maxlen=maxlen)

    def add(self, x: float) -> None:
        self._q.append(float(x))

    def values(self):
        return list(self._q)

    def mean(self) -> float:
        return mean(self._q)

    def stdev(self) -> float:
        return stdev(self._q)

    def cv(self) -> float:
        return coeff_var(self._q)

    def last(self) -> float:
        return self._q[-1] if self._q else 0.0
