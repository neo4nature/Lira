from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import argparse
import time
from pathlib import Path
from typing import Any

from config import paths
from governance.capsule_bus import init_db, send_capsule
from utils.state_io import read_json

PROJECT = "ecosystem_monitor"
ALERT_THRESHOLD = 0.35


def _r(name: str) -> dict[str, Any]:
    return read_json(str(paths()["STATE_DIR"] / name), default={})


def build_report() -> dict[str, Any]:
    alignment = _r("alignment_state.json")
    criticality = _r("criticality_state.json")
    policy = _r("policy_state.json")
    bus = _r("bus_signals.json")
    energy = _r("energy_state.json")
    watchdog = _r("watchdog_report.json")
    queue_path = paths()["QUEUE_FILE"]
    queue = read_json(str(queue_path), default={})
    q_items = queue.get("items") if isinstance(queue, dict) else None
    queue_depth = len(q_items) if isinstance(q_items, list) else queue.get("queue_depth", 0) if isinstance(queue, dict) else 0
    deviation = float(criticality.get("deviation", criticality.get("criticality_deviation", 0.0)) or 0.0)
    alerts = []
    if deviation > ALERT_THRESHOLD:
        alerts.append(f"criticality_deviation>{ALERT_THRESHOLD}")
    if watchdog.get("severity") == "hard":
        alerts.append("watchdog_hard")
    if bus.get("operational_veto", {}).get("active"):
        alerts.append("operational_veto")
    policy_profile = policy.get("active_profile") or policy.get("profile")
    alignment_mode = alignment.get("recommended_mode") or alignment.get("alignment_mode")
    alignment_score = alignment.get("global_alignment") if "global_alignment" in alignment else alignment.get("alignment_score")
    return {
        "title": "Raport ekosystemu",
        "summary": f"Policy={policy_profile} alignment={alignment_mode} criticality={criticality.get('score')}",
        "alerts": alerts,
        "alignment_score": alignment_score,
        "alignment_mode": alignment_mode,
        "criticality_score": criticality.get("score"),
        "criticality_deviation": deviation,
        "policy_profile": policy_profile,
        "queue_depth": queue_depth,
        "energy_state": energy,
        "watchdog": watchdog,
        "bus": bus,
    }


def publish_report(force_weight: str | None = None) -> str:
    init_db()
    payload = build_report()
    weight = force_weight or ("changes_trajectory" if payload["alerts"] else "commentary")
    return send_capsule(
        type="fact",
        weight=weight,
        layer="shared",
        owner="shared",
        sender="Ekosystem",
        recipient="shared",
        payload=payload,
        open_question="Zdiagnozuj stan ekosystemu i zaproponuj korekty.",
        project=PROJECT,
        phase="monitor",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-weight", default=None)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=900)
    parser.add_argument("--alert-interval", type=int, default=180)
    args = parser.parse_args()

    if not args.loop:
        cid = publish_report(force_weight=args.force_weight)
        print(cid)
        return

    while True:
        payload = build_report()
        weight = args.force_weight or ("changes_trajectory" if payload["alerts"] else "commentary")
        cid = send_capsule(
            type="fact",
            weight=weight,
            layer="shared",
            owner="shared",
            sender="Ekosystem",
            recipient="shared",
            payload=payload,
            open_question="Zdiagnozuj stan ekosystemu i zaproponuj korekty.",
            project=PROJECT,
            phase="monitor",
        )
        print(cid, flush=True)
        time.sleep(args.alert_interval if payload["alerts"] else args.interval)


if __name__ == "__main__":
    main()
