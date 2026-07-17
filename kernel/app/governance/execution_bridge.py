from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

from config import ensure_dirs, paths
from governance.capsule_bus import init_db, log_event, update_status
from agents.core.queue_utils import enqueue
from utils.state_io import read_json, write_json_atomic

P = paths()
EXECUTION_QUEUE = P["RUNTIME_DIR"] / "queue" / "execution_queue.jsonl"
NEO_APPROVAL_BOARD = P["SHARED_CONTEXT_DIR"] / "state" / "neo_approval_board.jsonl"
EXECUTION_RESULTS = P["RUNTIME_DIR"] / "logs" / "execution_bridge_results.jsonl"
EXECUTION_STATE = P["STATE_DIR"] / "execution_bridge_state.json"



SAFE_ACTION_TYPES = {
    "trigger_night_cycle",
    "switch_policy_profile",
    "increase_log_level",
    "retry_light_task",
    "refresh_alignment",
    "governance_review",
}

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    out.append(row)
            except Exception:
                continue
    return out


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def ensure_bootstrap() -> None:
    ensure_dirs()
    for path in [EXECUTION_QUEUE, NEO_APPROVAL_BOARD, EXECUTION_RESULTS]:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
    if not EXECUTION_STATE.exists():
        write_json_atomic(str(EXECUTION_STATE), {
            "schema": "lira.execution_bridge_state.v1",
            "updated_at": utc_now(),
            "last_queue_count": 0,
            "last_board_count": 0,
            "last_result": None,
        })


def _already_recorded(path: Path, decision_capsule: str) -> bool:
    for row in _read_jsonl(path):
        if row.get("decision_capsule") == decision_capsule:
            return True
    return False


def _action_contract(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    source_payload = decision.get("source_payload") if isinstance(decision.get("source_payload"), dict) else {}
    action = payload.get("action") or source_payload.get("action") or {}
    if not isinstance(action, dict):
        action = {}
    summary = str(payload.get("summary") or source_payload.get("summary") or "")
    title = str(payload.get("title") or source_payload.get("title") or "Governance action")
    target = str(action.get("target") or "executor_loop")
    requested_type = str(action.get("action_type") or action.get("type") or "governance_review").strip()
    action_type = requested_type if requested_type in SAFE_ACTION_TYPES else "governance_review"
    params = action.get("params") if isinstance(action.get("params"), dict) else {
        "title": title,
        "summary": summary,
    }
    contract = {
        "action_type": action_type,
        "requested_action_type": requested_type,
        "target": target,
        "params": params,
        "dry_run": bool(action.get("dry_run", True)),
        "delegable": bool(action.get("delegable", False)),
        "execute_after": action.get("execute_after"),
    }
    if requested_type not in SAFE_ACTION_TYPES:
        contract["action_type_rejected"] = True
    return contract


def route_decision(decision: dict[str, Any]) -> str | None:
    ensure_bootstrap()
    decision_capsule = str(decision.get("decision_capsule") or decision.get("id") or "")
    if not decision_capsule:
        return None
    verdict = str(decision.get("verdict") or "").lower()
    status = str(decision.get("status") or "").lower()
    risk_class = str(decision.get("risk_class") or "").lower()

    record = {
        "decision_capsule": decision_capsule,
        "source_capsule": decision.get("source_capsule"),
        "thread_id": decision.get("thread_id"),
        "project": decision.get("project"),
        "summary": decision.get("summary"),
        "title": decision.get("title"),
        "verdict": verdict,
        "status": status,
        "risk_class": risk_class,
        "action": _action_contract(decision),
        "created_at": utc_now(),
        "bridge_status": "queued",
    }

    if status == "approved_auto" and risk_class == "safe_auto" and verdict == "approve":
        if _already_recorded(EXECUTION_QUEUE, decision_capsule):
            return "execution_queue_existing"
        _append_jsonl(EXECUTION_QUEUE, record)
        return "execution_queue"

    if status == "awaiting_neo" or verdict == "awaiting_neo" or risk_class in {"deferred_apply", "human_only"}:
        record["bridge_status"] = "awaiting_neo"
        if _already_recorded(NEO_APPROVAL_BOARD, decision_capsule):
            return "neo_board_existing"
        _append_jsonl(NEO_APPROVAL_BOARD, record)
        return "neo_approval_board"

    return None


def _post_n8n(url: str, record: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(record, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlrequest.urlopen(req, timeout=float(os.getenv("LIRA_N8N_TIMEOUT", "10"))) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
        return {"status": resp.status, "body": body[:1000]}


def _append_result(row: dict[str, Any]) -> None:
    _append_jsonl(EXECUTION_RESULTS, row)
    write_json_atomic(str(EXECUTION_STATE), {
        "schema": "lira.execution_bridge_state.v1",
        "updated_at": utc_now(),
        "last_queue_count": len(_read_jsonl(EXECUTION_QUEUE)),
        "last_board_count": len(_read_jsonl(NEO_APPROVAL_BOARD)),
        "last_result": row,
    })


def process_queue_once() -> list[dict[str, Any]]:
    ensure_bootstrap()
    rows = _read_jsonl(EXECUTION_QUEUE)
    changed = False
    results: list[dict[str, Any]] = []
    n8n_webhook = os.getenv("LIRA_N8N_WEBHOOK_URL", "").strip()

    for row in rows:
        if row.get("bridge_status") not in {"queued", "retry"}:
            continue
        action = row.get("action") if isinstance(row.get("action"), dict) else {}
        target = str(action.get("target") or "executor_loop")
        try:
            if target == "n8n" and n8n_webhook:
                response = _post_n8n(n8n_webhook, row)
                row["bridge_status"] = "dispatched_n8n"
                row["dispatched_at"] = utc_now()
                row["n8n_response"] = response
                result = {"decision_capsule": row.get("decision_capsule"), "target": "n8n", "status": "ok", "response": response, "ts": utc_now()}
            else:
                task = {
                    "ts": utc_now(),
                    "kind": "governance_execution",
                    "source": "execution_bridge",
                    "importance": 4,
                    "tags": ["governance", "safe_auto", target],
                    "payload": {
                        "decision_capsule": row.get("decision_capsule"),
                        "source_capsule": row.get("source_capsule"),
                        "thread_id": row.get("thread_id"),
                        "title": row.get("title"),
                        "summary": row.get("summary"),
                        "action": action,
                    },
                    "status": "pending",
                    "delegable": bool(action.get("delegable", False)),
                    "bridge_target": target,
                    "risk_class": row.get("risk_class"),
                    "dry_run": bool(action.get("dry_run", True)),
                    "approved_by": "governance",
                }
                enqueue(task)
                row["bridge_status"] = "queued_for_executor"
                row["dispatched_at"] = utc_now()
                row["queue_task_kind"] = task["kind"]
                result = {"decision_capsule": row.get("decision_capsule"), "target": target, "status": "queued_for_executor", "task_kind": task["kind"], "ts": utc_now()}
            results.append(result)
            _append_result(result)
            changed = True
        except (URLError, HTTPError, OSError, ValueError) as e:
            row["bridge_status"] = "retry"
            row["last_error"] = str(e)
            row["updated_at"] = utc_now()
            result = {"decision_capsule": row.get("decision_capsule"), "target": target, "status": "retry", "error": str(e), "ts": utc_now()}
            results.append(result)
            _append_result(result)
            changed = True

    if changed:
        _write_jsonl(EXECUTION_QUEUE, rows)
    return results


def ingest_decision_status(decision_capsule: str, applied: bool, actor: str = "execution_bridge") -> None:
    for path in [EXECUTION_QUEUE, NEO_APPROVAL_BOARD]:
        rows = _read_jsonl(path)
        changed = False
        for row in rows:
            if row.get("decision_capsule") == decision_capsule:
                row["bridge_status"] = "applied" if applied else row.get("bridge_status", "queued")
                row["applied"] = bool(applied)
                row["applied_at"] = utc_now() if applied else row.get("applied_at")
                changed = True
        if changed:
            _write_jsonl(path, rows)
    try:
        update_status(decision_capsule, "archived" if applied else "awaiting_neo", actor)
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    init_db()
    ensure_bootstrap()
    if not args.loop:
        for result in process_queue_once():
            print(json.dumps(result, ensure_ascii=False))
        return
    while True:
        for result in process_queue_once():
            print(json.dumps(result, ensure_ascii=False), flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
