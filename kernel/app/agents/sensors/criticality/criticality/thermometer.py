from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .sensors import RollingWindow, sample_resources


@dataclass
class ThermoState:
    criticality_score: float
    regime: str
    diagnostics: Dict[str, Any]


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def smoothstep(x: float) -> float:
    """Maps x in [0,1] to a smooth curve."""
    x = clamp01(x)
    return x * x * (3 - 2 * x)


class Thermometer:
    """
    Practical "edge-of-chaos" monitor using observable proxies.

    Events you can feed:
    - latency_s: float (end-to-end step latency)
    - output_len: int (chars/tokens) OR any scalar output measure
    - success: bool (task success/failure)
    - queue_depth: int (optional; if you have a task queue)
    """

    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        win = int(config.get("window", 120))

        self.latency = RollingWindow(maxlen=win)
        self.output_len = RollingWindow(maxlen=win)
        self.success = RollingWindow(maxlen=win)
        self.queue_depth = RollingWindow(maxlen=win)

        self.cpu = RollingWindow(maxlen=win)
        self.ram = RollingWindow(maxlen=win)

        self._last_sample_ts = 0.0
        self._sample_every_s = float(config.get("resource_sample_every_s", 1.0))

    def _maybe_sample_resources(self) -> None:
        now = time.time()
        if now - self._last_sample_ts >= self._sample_every_s:
            rs = sample_resources()
            self.cpu.add(rs.cpu_percent)
            self.ram.add(rs.ram_percent)
            self._last_sample_ts = now

    def observe_event(
        self,
        *,
        latency_s: Optional[float] = None,
        output_len: Optional[float] = None,
        success: Optional[bool] = None,
        queue_depth: Optional[int] = None,
    ) -> ThermoState:
        if latency_s is not None:
            self.latency.add(float(latency_s))
        if output_len is not None:
            self.output_len.add(float(output_len))
        if success is not None:
            self.success.add(1.0 if success else 0.0)
        if queue_depth is not None:
            self.queue_depth.add(float(queue_depth))

        self._maybe_sample_resources()
        return self.compute()

    def compute(self) -> ThermoState:
        """
        Score interpretation:
          0.0  -> too ORDERED (rigid / low variation / low exploration)
          0.5  -> EDGE (healthy variability, stable)
          1.0  -> CHAOS (high jitter, errors, resource thrash)
        """
        # Observable proxies
        latency_cv = self.latency.cv()
        output_jitter = self.output_len.cv()
        cpu = self.cpu.mean()
        ram = self.ram.mean()

        # error rate is 1 - mean(success)
        succ_mean = self.success.mean()
        error_rate = clamp01(1.0 - succ_mean) if self.success.values() else 0.0

        # queue pressure scaled
        q = self.queue_depth.mean()
        q_ref = float(self.cfg.get("queue_ref", 10.0))
        queue_pressure = clamp01(q / max(1e-9, q_ref))

        # Normalize each proxy into [0,1] "instability"
        # Thresholds define where "too chaotic" begins.
        t = self.cfg.get("thresholds", {})
        lat_hi = float(t.get("latency_cv_hi", 0.8))
        out_hi = float(t.get("output_jitter_hi", 0.9))
        cpu_hi = float(t.get("cpu_hi", 85.0))
        ram_hi = float(t.get("ram_hi", 85.0))
        err_hi = float(t.get("error_hi", 0.20))
        q_hi = float(t.get("queue_hi", 1.0))

        inst_latency = smoothstep(clamp01(latency_cv / max(1e-9, lat_hi)))
        inst_output = smoothstep(clamp01(output_jitter / max(1e-9, out_hi)))
        inst_cpu = smoothstep(clamp01(cpu / max(1e-9, cpu_hi)))
        inst_ram = smoothstep(clamp01(ram / max(1e-9, ram_hi)))
        inst_err = smoothstep(clamp01(error_rate / max(1e-9, err_hi)))
        inst_q = smoothstep(clamp01(queue_pressure / max(1e-9, q_hi)))

        # Combine with weights
        w = self.cfg.get("weights", {})
        weights = {
            "latency": float(w.get("latency", 0.22)),
            "output": float(w.get("output", 0.16)),
            "cpu": float(w.get("cpu", 0.16)),
            "ram": float(w.get("ram", 0.12)),
            "error": float(w.get("error", 0.22)),
            "queue": float(w.get("queue", 0.12)),
        }
        s = (
            weights["latency"] * inst_latency +
            weights["output"]  * inst_output +
            weights["cpu"]     * inst_cpu +
            weights["ram"]     * inst_ram +
            weights["error"]   * inst_err +
            weights["queue"]   * inst_q
        )
        s = clamp01(s)

        # Map to regimes: ordered < edge < chaos
        edge_low = float(self.cfg.get("edge_low", 0.35))
        edge_high = float(self.cfg.get("edge_high", 0.65))
        if s < edge_low:
            regime = "ORDER"
        elif s > edge_high:
            regime = "CHAOS"
        else:
            regime = "EDGE"

        diagnostics = {
            "latency_cv": latency_cv,
            "output_jitter": output_jitter,
            "cpu": cpu,
            "ram": ram,
            "error_rate": error_rate,
            "queue_pressure": queue_pressure,
            "instability_components": {
                "latency": inst_latency,
                "output": inst_output,
                "cpu": inst_cpu,
                "ram": inst_ram,
                "error": inst_err,
                "queue": inst_q,
            }
        }
        return ThermoState(criticality_score=s, regime=regime, diagnostics=diagnostics)

    def recommend_gating(self, state: ThermoState) -> Dict[str, Any]:
        """
        Turn the thermometer into actionable knobs:
        - reduce exploration if CHAOS
        - increase exploration if ORDER
        - keep steady if EDGE
        """
        s = state.criticality_score
        if state.regime == "CHAOS":
            return {
                "mode": "stabilize",
                "max_parallel_tasks": int(self.cfg.get("stabilize_max_parallel", 1)),
                "allow_web": False,
                "memory_write_rate": "low",
                "notes": "High instability: reduce concurrency, avoid noisy sources, slow updates."
            }
        if state.regime == "ORDER":
            return {
                "mode": "explore",
                "max_parallel_tasks": int(self.cfg.get("explore_max_parallel", 3)),
                "allow_web": True,
                "memory_write_rate": "normal",
                "notes": "Low variability: safe to explore, widen search, increase branching."
            }
        return {
            "mode": "balanced",
            "max_parallel_tasks": int(self.cfg.get("edge_max_parallel", 2)),
            "allow_web": True,
            "memory_write_rate": "normal",
            "notes": "Near edge-of-chaos: keep steady; small controlled exploration."
        }
