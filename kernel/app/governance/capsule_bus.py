from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from config import paths

DB_PATH = paths()["SHARED_CONTEXT_DIR"] / "state" / "capsule_bus.db"
ALLOWED_TYPES = {"fact", "interpretation", "hypothesis", "decision", "doubt", "task", "implementation", "result", "summary", "proposal", "review", "stalemate", "chat_message"}
ALLOWED_WEIGHTS = {"changes_trajectory", "commentary", "archive"}
ALLOWED_LAYERS = {"shared", "working", "reflective"}
ALLOWED_OWNERS = {"Neo", "Soryel", "Lira", "Xian", "Ekosystem", "shared"}
ALLOWED_STATUSES = {"new", "read", "in_progress", "answered", "archived", "blocked", "proposal", "review", "revise", "approved_auto", "awaiting_neo", "rejected", "stalemate", "claimed", "delivered", "queued", "retry_pending"}

# Lease/retry (plan szyny 2026-06-12, Soryel+Lira): 30 min awaryjnego TTL,
# heartbeat odnawia co 5 min — bo daemon zablokowany na Ollamie/CDP sam nie odświeży.
LEASE_S = int(os.getenv("LIRA_BUS_LEASE_S", "1800"))
MAX_ATTEMPTS = int(os.getenv("LIRA_BUS_MAX_ATTEMPTS", "3"))
HEARTBEAT_S = int(os.getenv("LIRA_BUS_HEARTBEAT_S", "300"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def build_signal_profile(
    *,
    semantic_weight: float = 0.0,
    operational_weight: float = 0.0,
    memory_weight: float = 0.0,
    criticality: float = 0.0,
    confidence: float = 0.0,
    entities: list[str] | None = None,
    signals: list[str] | None = None,
    tags: list[str] | None = None,
    why_weighted: str = "",
) -> dict[str, Any]:
    """Buduje signal_profile dla payload kapsułki (Capsule v2)."""
    return {
        "semantic_weight": semantic_weight,
        "operational_weight": operational_weight,
        "memory_weight": memory_weight,
        "criticality": criticality,
        "confidence": confidence,
        "entities": entities or [],
        "signals": signals or [],
        "tags": tags or [],
        "why_weighted": why_weighted,
    }


def _ensure_json_text(payload: Any) -> str:
    if isinstance(payload, str):
        json.loads(payload)
        return payload
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def init_db(db_path: Path = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS capsules (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                type TEXT NOT NULL,
                weight TEXT NOT NULL,
                layer TEXT NOT NULL,
                owner TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                status TEXT NOT NULL,
                thread_id TEXT,
                parent_id TEXT,
                project TEXT,
                open_question TEXT,
                payload_json TEXT NOT NULL,
                iteration_index INTEGER DEFAULT 0,
                phase TEXT,
                risk_class TEXT,
                verdict TEXT,
                claimed_by TEXT,
                claimed_at TEXT,
                FOREIGN KEY(parent_id) REFERENCES capsules(id)
            );
            CREATE TABLE IF NOT EXISTS capsule_events (
                id TEXT PRIMARY KEY,
                capsule_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                details_json TEXT,
                FOREIGN KEY(capsule_id) REFERENCES capsules(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_capsules_status ON capsules(status);
            CREATE INDEX IF NOT EXISTS idx_capsules_recipient ON capsules(recipient);
            CREATE INDEX IF NOT EXISTS idx_capsules_project ON capsules(project);
            CREATE INDEX IF NOT EXISTS idx_capsules_thread_id ON capsules(thread_id);
            """
        )
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(capsules)")}
        for col, ddl in (
            ("attempt_count", "ALTER TABLE capsules ADD COLUMN attempt_count INTEGER DEFAULT 0"),
            ("lease_expires_at", "ALTER TABLE capsules ADD COLUMN lease_expires_at TEXT"),
            ("response_capsule_id", "ALTER TABLE capsules ADD COLUMN response_capsule_id TEXT"),
        ):
            if col not in existing:
                conn.execute(ddl)


def log_event(capsule_id: str, event_type: str, actor: str, details: Optional[dict[str, Any]] = None, db_path: Path = DB_PATH) -> str:
    event_id = str(uuid.uuid4())
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO capsule_events (id, capsule_id, created_at, event_type, actor, details_json) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, capsule_id, utc_now(), event_type, actor, _ensure_json_text(details or {})),
        )
    return event_id


def send_capsule(*, type: str, weight: str, layer: str, owner: str, sender: str, recipient: str, payload: Any,
                 open_question: str = "", thread_id: Optional[str] = None, parent_id: Optional[str] = None,
                 project: Optional[str] = None, status: str = "new", iteration_index: int = 0,
                 phase: Optional[str] = None, risk_class: Optional[str] = None, verdict: Optional[str] = None,
                 db_path: Path = DB_PATH) -> str:
    init_db(db_path)
    if type not in ALLOWED_TYPES:
        raise ValueError(type)
    if weight not in ALLOWED_WEIGHTS:
        raise ValueError(weight)
    if layer not in ALLOWED_LAYERS:
        raise ValueError(layer)
    if owner not in ALLOWED_OWNERS:
        raise ValueError(owner)
    if status not in ALLOWED_STATUSES:
        raise ValueError(status)
    payload_json = _ensure_json_text(payload)
    capsule_id = str(uuid.uuid4())
    now = utc_now()
    if thread_id is None:
        thread_id = capsule_id
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO capsules (
                id, created_at, updated_at, type, weight, layer, owner, sender, recipient, status,
                thread_id, parent_id, project, open_question, payload_json, iteration_index, phase, risk_class, verdict
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (capsule_id, now, now, type, weight, layer, owner, sender, recipient, status,
             thread_id, parent_id, project, open_question, payload_json, int(iteration_index), phase, risk_class, verdict)
        )
    log_event(capsule_id, "capsule_created", sender, {"recipient": recipient, "type": type, "project": project}, db_path=db_path)
    return capsule_id


def read_capsules(recipient: str | list[str], statuses: tuple[str, ...] = ("new", "proposal", "review", "revise", "awaiting_neo"),
                  project: Optional[str] = None, limit: int = 20, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    recipients = [recipient] if isinstance(recipient, str) else recipient
    placeholders_r = ",".join("?" * len(recipients))
    placeholders_s = ",".join("?" * len(statuses))
    query = f"SELECT * FROM capsules WHERE recipient IN ({placeholders_r}) AND status IN ({placeholders_s})"
    params: list[Any] = list(recipients) + list(statuses)
    if project:
        query += " AND project = ?"
        params.append(project)
    query += " ORDER BY created_at ASC LIMIT ?"
    params.append(limit)
    with get_conn(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload_json"] = json.loads(item["payload_json"])
        except Exception:
            pass
        out.append(item)
    return out


def _lease_deadline(lease_s: int = LEASE_S) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=lease_s)).isoformat()


def claim_capsule(capsule_id: str, actor: str, db_path: Path = DB_PATH, lease_s: int = LEASE_S) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE capsules SET status='claimed', claimed_by=?, claimed_at=?, lease_expires_at=?, updated_at=? WHERE id=? AND status IN ('new','proposal','review','revise','awaiting_neo','retry_pending') AND claimed_by IS NULL",
            (actor, utc_now(), _lease_deadline(lease_s), utc_now(), capsule_id),
        )
        ok = cur.rowcount > 0
    if ok:
        log_event(capsule_id, "claimed", actor, {}, db_path=db_path)
    return ok


def update_status(capsule_id: str, status: str, actor: str, details: Optional[dict[str, Any]] = None, db_path: Path = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.execute("UPDATE capsules SET status=?, updated_at=? WHERE id=?", (status, utc_now(), capsule_id))
    log_event(capsule_id, "status_changed", actor, {"status": status, **(details or {})}, db_path=db_path)


def begin_processing(capsule_id: str, actor: str, db_path: Path = DB_PATH, lease_s: int = LEASE_S) -> bool:
    """Atomowo przejmij kapsułę do przetwarzania (new/retry_pending → in_progress).

    Podbija attempt_count i nadaje lease — warunek WHERE eliminuje wyścig
    dwóch konsumentów (rowcount rozstrzyga, kto wygrał).
    """
    now = utc_now()
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE capsules SET status='in_progress', claimed_by=?, claimed_at=?, lease_expires_at=?, "
            "attempt_count=COALESCE(attempt_count,0)+1, updated_at=? "
            "WHERE id=? AND status IN ('new','retry_pending')",
            (actor, now, _lease_deadline(lease_s), now, capsule_id),
        )
        ok = cur.rowcount > 0
    if ok:
        log_event(capsule_id, "processing_started", actor, {"lease_s": lease_s}, db_path=db_path)
    return ok


def heartbeat_lease(capsule_id: str, actor: str, db_path: Path = DB_PATH, extend_s: int = LEASE_S) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE capsules SET lease_expires_at=?, updated_at=? WHERE id=? AND claimed_by=? AND status IN ('in_progress','claimed')",
            (_lease_deadline(extend_s), utc_now(), capsule_id, actor),
        )
        return cur.rowcount > 0


@contextmanager
def lease_heartbeat(capsule_ids: str | list[str], actor: str, db_path: Path = DB_PATH,
                    interval_s: int = HEARTBEAT_S, extend_s: int = LEASE_S):
    """Wątek-strażnik odnawiający lease, gdy główny wątek wisi na Ollamie/CDP."""
    ids = [capsule_ids] if isinstance(capsule_ids, str) else list(capsule_ids)
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(interval_s):
            for cid in ids:
                try:
                    heartbeat_lease(cid, actor, db_path=db_path, extend_s=extend_s)
                except Exception:
                    pass

    t = threading.Thread(target=_beat, daemon=True, name="bus-lease-heartbeat")
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=2)


def mark_answered(capsule_id: str, actor: str, response_capsule_id: str,
                  details: Optional[dict[str, Any]] = None, db_path: Path = DB_PATH) -> None:
    """answered TYLKO z dowodem — id kapsuły odpowiedzi (ustalenie z Lirą 2026-06-12)."""
    if not response_capsule_id:
        raise ValueError("mark_answered wymaga response_capsule_id — answered bez dowodu odpowiedzi jest zabronione")
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE capsules SET status='answered', response_capsule_id=?, lease_expires_at=NULL, updated_at=? WHERE id=?",
            (response_capsule_id, utc_now(), capsule_id),
        )
    log_event(capsule_id, "answered", actor,
              {"response_capsule_id": response_capsule_id, **(details or {})}, db_path=db_path)


def mark_retry(capsule_id: str, actor: str, reason: str, db_path: Path = DB_PATH, max_attempts: int = MAX_ATTEMPTS) -> str:
    """Brak odpowiedzi → retry_pending; po max_attempts → blocked (dead letter, widoczny)."""
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT COALESCE(attempt_count,0) AS ac FROM capsules WHERE id=?", (capsule_id,)).fetchone()
        attempts = int(row["ac"]) if row else 0
        new_status = "blocked" if attempts >= max_attempts else "retry_pending"
        conn.execute(
            "UPDATE capsules SET status=?, claimed_by=NULL, claimed_at=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
            (new_status, utc_now(), capsule_id),
        )
    log_event(capsule_id, "retry_scheduled" if new_status == "retry_pending" else "max_attempts_reached",
              actor, {"reason": reason, "attempt_count": attempts}, db_path=db_path)
    return new_status


def recover_stale(actor: str, db_path: Path = DB_PATH, fallback_age_s: int = 7200) -> int:
    """Kapsuły in_progress/claimed z wygasłym lease (lub bez lease, starsze niż fallback) → retry_pending."""
    now = utc_now()
    fallback_before = (datetime.now(timezone.utc) - timedelta(seconds=fallback_age_s)).isoformat()
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM capsules WHERE status IN ('in_progress','claimed') AND "
            "((lease_expires_at IS NOT NULL AND lease_expires_at < ?) OR (lease_expires_at IS NULL AND updated_at < ?))",
            (now, fallback_before),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE capsules SET status='retry_pending', claimed_by=NULL, claimed_at=NULL, lease_expires_at=NULL, updated_at=? WHERE id IN ({placeholders})",
                [now] + ids,
            )
    for cid in ids:
        log_event(cid, "lease_recovered", actor, {}, db_path=db_path)
    return len(ids)
