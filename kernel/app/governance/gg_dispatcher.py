from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from governance.capsule_bus import (
    claim_capsule, init_db, read_capsules, send_capsule, update_status,
    mark_answered, get_conn, DB_PATH,
)
from governance.decision_memory import remember_decision, ensure_bootstrap
from governance.execution_bridge import route_decision

PROJECT = "ecosystem_monitor"

# Loop guard wg projektu Liry (2026-06-12): hop_count<=5, 2 powtórki sygnatury = stop,
# max 2 wymiany pary agentów bez nowego głosu Neo.
MAX_HOPS = int(os.getenv("LIRA_LOOP_MAX_HOPS", "5"))
SIGNATURE_WINDOW_S = int(os.getenv("LIRA_LOOP_SIGNATURE_WINDOW_S", "3600"))
SIGNATURE_MAX_REPEATS = 2
PAIR_MAX_AGENT_MSGS = 4  # 2 wymiany pary = 4 kapsuły agentów


NEGATION_RE = re.compile(r"\b(nie|brak|bez)\b", re.IGNORECASE)
POSITIVE_APPROVAL_MARKERS = (
    "approve",
    "approved",
    "zatwierdzam",
    "zatwierdzone",
    "można wdrożyć",
    "mozna wdrozyc",
    "safe_auto",
    "approved_auto",
)
DEFER_MARKERS = (
    "awaiting_neo",
    "awaiting neo",
    "czeka na neo",
    "do zatwierdzenia przez neo",
    "wymaga neo",
)


def infer_verdict(summary: str, item: dict[str, Any]) -> tuple[str, str]:
    text = (summary or "").strip().lower()
    risk_class = (item.get("risk_class") or "").strip().lower()
    if any(marker in text for marker in DEFER_MARKERS):
        return "awaiting_neo", "deferred_apply"
    if any(marker in text for marker in POSITIVE_APPROVAL_MARKERS) and not NEGATION_RE.search(text):
        return "approve", "safe_auto" if risk_class != "human_only" else "deferred_apply"
    if risk_class == "safe_auto" and "gotowe do wdrożenia" in text and not NEGATION_RE.search(text):
        return "approve", "safe_auto"
    return "awaiting_neo", "human_only" if risk_class == "human_only" else "deferred_apply"


def _content_hash(item: dict[str, Any]) -> str:
    payload = item.get("payload_json", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {"raw": payload}
    if isinstance(payload, dict):
        text = f"{payload.get('title', '')}|{payload.get('summary', '')}|{payload.get('text', '')}"
    else:
        text = str(payload)
    return hashlib.sha1(text[:500].encode("utf-8", errors="replace")).hexdigest()[:16]


def _signature_repeats(item: dict[str, Any]) -> int:
    """Ile kapsuł o tej samej sygnaturze (sender+recipient+content_hash) w oknie czasowym."""
    since = (datetime.now(timezone.utc) - timedelta(seconds=SIGNATURE_WINDOW_S)).isoformat()
    own_hash = _content_hash(item)
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT id, payload_json FROM capsules
               WHERE sender=? AND recipient=? AND id!=? AND created_at>=?
               ORDER BY created_at DESC LIMIT 30""",
            (item.get("sender"), item.get("recipient"), item.get("id"), since),
        ).fetchall()
    return sum(1 for row in rows if _content_hash(dict(row)) == own_hash)


def _agent_msgs_since_neo(thread_id: str | None) -> int:
    """Liczba kapsuł agentów w wątku od ostatniego głosu Neo (pair guard Liry)."""
    if not thread_id:
        return 0
    with get_conn(DB_PATH) as conn:
        neo_row = conn.execute(
            "SELECT MAX(created_at) AS ts FROM capsules WHERE thread_id=? AND sender='Neo'",
            (thread_id,),
        ).fetchone()
        since = neo_row["ts"] if neo_row and neo_row["ts"] else "1970-01-01"
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM capsules
               WHERE thread_id=? AND sender IN ('Lira','Soryel') AND created_at>?""",
            (thread_id, since),
        ).fetchone()
    return int(row["n"]) if row else 0


def _summary_capsule(source: dict[str, Any], actor: str, response_text: str, status: str, verdict: str | None = None) -> str:
    payload = {
        "title": f"Re: {source.get('payload_json', {}).get('title', 'Kapsuła')}",
        "summary": response_text,
        "source_capsule": source.get("id"),
        "agent": actor,
    }
    return send_capsule(
        type="review" if actor == "Soryel" else "proposal",
        weight="commentary",
        layer="working",
        owner=actor if actor in {"Lira", "Soryel"} else "shared",
        sender=actor,
        recipient="shared" if actor == "Soryel" else "Soryel",
        payload=payload,
        parent_id=source.get("id"),
        thread_id=source.get("thread_id"),
        project=source.get("project") or PROJECT,
        status=status,
        iteration_index=int(source.get("iteration_index") or 0) + 1,
        phase="review" if actor == "Soryel" else "brainstorm",
        verdict=verdict,
    )


def dispatch_once() -> list[str]:
    init_db()
    ensure_bootstrap()
    produced: list[str] = []
    # sequence: shared -> Lira -> Soryel, avoiding race
    for target in ["shared", "Lira", "Soryel"]:
        items = read_capsules(target, project=PROJECT, limit=5)
        for item in items:
            if not claim_capsule(item["id"], actor=f"dispatcher:{target}"):
                continue
            payload = item.get("payload_json", {})
            title = payload.get("title", "Kapsuła") if isinstance(payload, dict) else "Kapsuła"
            summary = payload.get("summary", "") if isinstance(payload, dict) else str(payload)
            # Guard: skip decision/review/proposal cycles produced by dispatcher itself
            if item.get("type") in {"decision", "review", "proposal"} or item.get("sender") in {"Soryel", "Lira"}:
                update_status(item["id"], "archived", "dispatcher:loop_guard", {"reason": "self_product"})
                continue
            # Loop guard Liry: hop_count<=MAX_HOPS (multi-turn odblokowany do 5, było >1)
            if int(item.get("iteration_index") or 0) > MAX_HOPS:
                update_status(item["id"], "archived", "dispatcher:loop_guard_hops",
                              {"reason": "max_hops", "iteration_index": item.get("iteration_index")})
                continue
            # Loop guard Liry: ta sama sygnatura (sender+recipient+treść) >=2 razy w oknie = stop
            repeats = _signature_repeats(item)
            if repeats >= SIGNATURE_MAX_REPEATS:
                update_status(item["id"], "archived", "dispatcher:loop_guard_signature",
                              {"reason": "signature_repeat", "repeats": repeats})
                continue
            # Loop guard Liry: max 2 wymiany pary agentów bez nowego głosu Neo
            agent_msgs = _agent_msgs_since_neo(item.get("thread_id"))
            if agent_msgs >= PAIR_MAX_AGENT_MSGS:
                update_status(item["id"], "blocked", "dispatcher:loop_guard_pair",
                              {"reason": "awaiting_neo_voice", "agent_msgs_since_neo": agent_msgs})
                continue
            if target == "shared":
                cid = _summary_capsule(item, "Lira", f"Analiza w toku: {title}. Priorytet ustalony, przekazuję do Soryel po syntezie.", "review")
                produced.append(cid)
                update_status(item["id"], "read", "dispatcher:shared")
            elif target == "Lira":
                cid = _summary_capsule(item, "Lira", f"Propozycja rozwiązania dla: {title}. {summary[:400]}", "review")
                produced.append(cid)
                mark_answered(item["id"], "dispatcher:Lira", cid)
            elif target == "Soryel":
                verdict, risk_class = infer_verdict(summary, item)
                decision_status = "approved_auto" if verdict == "approve" else "awaiting_neo"
                cid = send_capsule(
                    type="decision",
                    weight="changes_trajectory" if verdict == "approve" else "commentary",
                    layer="shared",
                    owner="shared",
                    sender="Soryel",
                    recipient="shared",
                    payload={
                        "title": f"Decyzja: {title}",
                        "summary": f"Werdykt Soryel: {verdict}. {summary[:500]}",
                        "source_capsule": item.get("id"),
                    },
                    parent_id=item.get("id"),
                    thread_id=item.get("thread_id"),
                    project=item.get("project") or PROJECT,
                    status=decision_status,
                    phase="decide",
                    verdict=verdict,
                    risk_class=risk_class,
                )
                produced.append(cid)
                decision_record = {
                    "decision_capsule": cid,
                    "source_capsule": item.get("id"),
                    "verdict": verdict,
                    "summary": summary[:500],
                    "title": title,
                    "thread_id": item.get("thread_id"),
                    "project": item.get("project") or PROJECT,
                    "status": decision_status,
                    "risk_class": risk_class,
                    "payload": {
                        "title": f"Decyzja: {title}",
                        "summary": f"Werdykt Soryel: {verdict}. {summary[:500]}",
                    },
                    "source_payload": payload,
                }
                remember_decision(decision_record)
                bridge_target = route_decision(decision_record)
                mark_answered(item["id"], "dispatcher:Soryel", cid, details={"bridge_target": bridge_target})
    return produced


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    if not args.loop:
        produced = dispatch_once()
        print("\n".join(produced))
        return

    while True:
        produced = dispatch_once()
        if produced:
            print("\n".join(produced), flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
