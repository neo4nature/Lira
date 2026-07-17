# Lira Criticality Thermometer (Edge-of-Chaos Monitor)

This is a small, self-contained Python module that estimates whether your system is drifting toward:
- **Over-order** (too rigid / sluggish adaptation)
- **Edge-of-chaos** (highly adaptive, stable)
- **Chaos** (unstable / noisy / thrashing)

It does **not** try to compute true spectral radius ρ(W). Instead it uses **observable proxies** you can
measure in real systems (latency variance, error rate, queue pressure, CPU/RAM pressure, output jitter).

## What you get
- `criticality/thermometer.py` – core logic
- `criticality/sensors.py` – psutil-based resource sensing + helper stats
- `criticality/logger.py` – JSONL event logger
- `run_thermometer.py` – CLI that prints a live score and writes logs
- `example_integration.py` – shows how to wrap your pipeline
- `config.example.json` – thresholds/weights you can tune

## Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (live)
```bash
python run_thermometer.py --config config.example.json --out logs/thermo.jsonl
```

## How to interpret the score
The thermometer outputs:
- `criticality_score` in [0,1] where **~0.5** aims for "edge-of-chaos"
- `regime`: `ORDER`, `EDGE`, `CHAOS`
- Diagnostics: `latency_cv`, `output_jitter`, `cpu`, `ram`, `error_rate`, `queue_pressure`

## Where to plug it in
Call `Thermometer.observe_event(...)` around your main loop:
- before routing (input arrival)
- after model call (latency, token_count if you have it)
- after action completion (success/failure)
- after reflection (summary size, memory writes)

The module will compute rolling stats and give a score you can use to:
- slow down exploration when near CHAOS
- increase exploration when too ORDERED
- adjust your gating thresholds dynamically

---

This is intentionally simple and safe: no external network calls, no heavy dependencies.
