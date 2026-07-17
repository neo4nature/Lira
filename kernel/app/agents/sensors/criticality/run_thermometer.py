from __future__ import annotations

import argparse
import json
import os
import time
import random

from criticality import Thermometer
from criticality.logger import JsonlLogger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.example.json")
    ap.add_argument("--out", default="logs/thermo.jsonl")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    logger = JsonlLogger(args.out)

    t = Thermometer(cfg)

    # Demo loop: replace the random numbers with real measurements from your pipeline.
    print("Running thermometer (CTRL+C to stop).")
    while True:
        # Example signals (replace!)
        latency_s = max(0.01, random.gauss(0.7, 0.15))
        output_len = max(10, random.gauss(900, 120))
        success = random.random() > 0.05
        queue_depth = max(0, int(random.gauss(3, 2)))

        state = t.observe_event(
            latency_s=latency_s,
            output_len=output_len,
            success=success,
            queue_depth=queue_depth,
        )
        rec = t.recommend_gating(state)

        event = {
            "criticality_score": state.criticality_score,
            "regime": state.regime,
            "diagnostics": state.diagnostics,
            "recommendation": rec,
        }
        logger.log(event)

        print(
            f"score={state.criticality_score:.3f}  regime={state.regime:<5}  "
            f"lat_cv={state.diagnostics['latency_cv']:.2f}  "
            f"out_jit={state.diagnostics['output_jitter']:.2f}  "
            f"cpu={state.diagnostics['cpu']:.1f}%  ram={state.diagnostics['ram']:.1f}%  "
            f"err={state.diagnostics['error_rate']:.2f}  q={state.diagnostics['queue_pressure']:.2f}  "
            f"mode={rec['mode']}"
        )
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
