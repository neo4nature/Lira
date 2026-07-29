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

import requests

from config import paths
from governance.capsule_bus import init_db, send_capsule
from utils.state_io import read_json

PROJECT = "ecosystem_monitor"
ALERT_THRESHOLD = 0.35

MOLTBOOK_URL = "http://localhost:8787/chat/send"
VOICE_MIN_INTERVAL_S = 3600

_PROFILE_PL = {"CONSERVATIVE": "ostrożny", "EXPLORATORY": "poszukujący", "BALANCED": "wyważony"}
_MODE_PL = {"EXPLORATION": "eksplorację", "CONSOLIDATION": "konsolidację", "PARTNER": "tryb partnerski"}


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
    # Odchylenie jest bezwzględne, więc samo w sobie nie mówi, w którą stronę.
    # Alarmuje wyłącznie przeciążenie: bezczynny system to stan dobry, nie awaria.
    if deviation > ALERT_THRESHOLD and criticality.get("regime") == "over":
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


def report_signature(payload: dict[str, Any]) -> tuple:
    """Sygnatura stanu, nie odczytu.

    Wyniki mają sześć miejsc po przecinku, więc każdy raport wygląda na nowy i żaden
    strażnik powtórzeń go nie łapie. Zaokrąglenie zostawia zmianę stanu, gasi drgania.
    """
    def r(v: Any) -> Any:
        return round(float(v), 2) if isinstance(v, (int, float)) else v

    return (
        payload.get("policy_profile"),
        payload.get("alignment_mode"),
        tuple(payload.get("alerts") or ()),
        r(payload.get("alignment_score")),
        r(payload.get("criticality_score")),
        payload.get("queue_depth"),
    )


def voice_line(payload: dict[str, Any], previous: dict[str, Any] | None) -> str:
    """Stan powiedziany po ludzku — Neo słucha tego przez TTS, nie czyta liczb."""
    score = float(payload.get("criticality_score") or 0.0)
    if score < 0.3:
        line = "Jest u mnie cicho i spokojnie"
    elif score < 0.6:
        line = "Pracuję równym rytmem"
    else:
        line = "Zrobiło się u mnie gęsto"

    zmiany = []
    if previous:
        profile = payload.get("policy_profile")
        if profile != previous.get("policy_profile"):
            zmiany.append(f"przestawiłem się na tryb {_PROFILE_PL.get(profile, profile)}")
        mode = payload.get("alignment_mode")
        if mode != previous.get("alignment_mode"):
            zmiany.append(f"nastawienie poszło w {_MODE_PL.get(mode, mode)}")
    queue = int(payload.get("queue_depth") or 0)
    if queue:
        zmiany.append(f"w kolejce czeka {queue}")
    if payload.get("alerts"):
        zmiany.append("i coś mnie uwiera: " + ", ".join(payload["alerts"]))

    return f"{line}, {', '.join(zmiany)}." if zmiany else f"{line}."


def say_on_moltbook(text: str) -> bool:
    try:
        resp = requests.post(MOLTBOOK_URL, json={"sender": "Ekosystem", "recipient": "shared", "text": text}, timeout=5)
        # Samo dojście POST-a nic nie znaczy: wysyłka z nieznanym nadawcą wraca 500,
        # a nieodczytany status zamienia odrzucenie w cichy sukces.
        return resp.status_code < 400
    except Exception:
        return False


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

    last_signature: tuple | None = None
    last_payload: dict[str, Any] | None = None
    last_line = ""
    last_voice = 0.0
    while True:
        payload = build_report()
        signature = report_signature(payload)
        if signature != last_signature:
            if last_signature is not None and time.time() - last_voice >= VOICE_MIN_INTERVAL_S:
                # Sygnatura pilnuje stanu, ale zdanie jest grubsze od stanu: wszystko
                # poniżej 0.3 brzmi tak samo, więc drgania score dawały co godzinę nowy
                # raport i to samo zdanie — dziewięć razy "cicho i spokojnie" pod rząd.
                # Cisza jest wiarygodniejsza niż dziewiąte zapewnienie o ciszy.
                line = voice_line(payload, last_payload)
                if line != last_line and say_on_moltbook(line):
                    last_voice = time.time()
                    last_line = line
            last_signature = signature
            last_payload = payload
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
