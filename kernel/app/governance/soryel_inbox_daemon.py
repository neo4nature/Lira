#!/usr/bin/env python3
"""
soryel_inbox_daemon.py — Soryel Inbox Poller v1.0

Co 30 sekund sprawdza capsule_bus czy są nowe wiadomości dla Soryel.
Zapisuje je do:
  - data/runtime/state/soryel_inbox.jsonl  (historia)
  - data/runtime/state/soryel_inbox_latest.json  (ostatnia nieprzeczytana)
  - shared_context/events/runtime.jsonl  (strumień zdarzeń)

Soryel (Claude Code) może sprawdzić inbox przez:
    cat $LIRA_DATA_ROOT/runtime/state/soryel_inbox_latest.json

Po odczytaniu kapsuła zmienia status na "read" w capsule_bus.

Uruchomienie:
    python3 -m governance.soryel_inbox_daemon

Env:
    SORYEL_INBOX_POLL     interwał [s] (domyślnie: 30)
    SORYEL_INBOX_LIMIT    max kapsuł na cykl (domyślnie: 10)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

_env_file = APP_ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from config import paths
from governance.capsule_bus import init_db, read_capsules, update_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Inbox] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("soryel_inbox")

POLL_INTERVAL = float(os.getenv("SORYEL_INBOX_POLL", "30"))
LIMIT         = int(os.getenv("SORYEL_INBOX_LIMIT", "10"))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _format_capsule(cap: dict) -> str:
    """Formatuje kapsułkę czytelnie dla Soryel."""
    p = cap.get("payload_json", {})
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            pass
    lines = [
        f"╔══ WIADOMOŚĆ [{cap.get('type','?')}] od {cap.get('sender','?')} ══",
        f"║  Projekt: {cap.get('project','—')} | Czas: {cap.get('created_at','?')[:16]}",
        f"║  ID: {cap.get('id','?')}",
        "║",
    ]
    if isinstance(p, dict):
        if p.get("title"):
            lines.append(f"║  {p['title']}")
            lines.append("║")
        body = p.get("body") or p.get("response") or p.get("summary") or ""
        for line in body.splitlines()[:20]:
            lines.append(f"║  {line}")
        if len(body.splitlines()) > 20:
            lines.append(f"║  ... [{len(body.splitlines())} linii łącznie]")
    else:
        lines.append(f"║  {str(p)[:200]}")
    lines.append("╚══")
    return "\n".join(lines)


def _read_moltbook_shared(limit: int = 10) -> list[dict]:
    """Czytaj shared chat_message z MoltBook które Soryel jeszcze nie widziała."""
    from governance.capsule_bus import get_conn, DB_PATH
    import json as _json
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT * FROM capsules
               WHERE thread_id = 'moltbook-main'
                 AND type = 'chat_message'
                 AND recipient IN ('shared', 'Soryel')
                 AND status = 'delivered'
                 AND sender != 'Soryel'
               ORDER BY created_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload_json"] = _json.loads(item["payload_json"])
        except Exception:
            pass
        out.append(item)
    return out


def poll_cycle(p: dict) -> int:
    state_dir   = Path(p["STATE_DIR"])
    events_path = Path(p["RUNTIME_STREAM"])
    inbox_jsonl = state_dir / "soryel_inbox.jsonl"
    inbox_latest = state_dir / "soryel_inbox_latest.json"

    # Standard: kapsułki bezpośrednio do Soryel
    caps = read_capsules(
        recipient="Soryel",
        statuses=("new",),
        limit=LIMIT,
    )

    # MoltBook shared: chat_message ze status=delivered (scoped do moltbook-main)
    moltbook_caps = _read_moltbook_shared(limit=LIMIT)
    caps.extend(moltbook_caps)

    if not caps:
        return 0

    log.info(f"{len(caps)} nowych wiadomości dla Soryel")
    unread = []

    for cap in caps:
        cap_id = cap["id"]
        formatted = _format_capsule(cap)

        # Zapisz do inbox JSONL
        _append_event(inbox_jsonl, {
            "ts": _now_iso(),
            "id": cap_id,
            "sender": cap.get("sender"),
            "type": cap.get("type"),
            "project": cap.get("project"),
            "payload": cap.get("payload_json"),
        })

        # Wyślij do runtime stream (widoczny w eventach)
        _append_event(events_path, {
            "ts": _now_iso(),
            "event": "soryel_inbox_new",
            "source": "soryel_inbox_daemon",
            "capsule_id": cap_id,
            "sender": cap.get("sender"),
            "project": cap.get("project"),
            "preview": formatted[:300],
        })

        update_status(cap_id, "read", actor="SoryelInboxDaemon")
        unread.append(cap)
        log.info(f"  + {cap.get('sender')} [{cap.get('type')}] projekt: {cap.get('project')}")

        # Desktop notification dla Neo
        try:
            import subprocess
            sender_name = cap.get('sender', '?')
            p = cap.get('payload_json', {})
            preview = p.get('text', '')[:100] if isinstance(p, dict) else ''
            subprocess.Popen(
                ["notify-send", "-u", "normal", "-i", "mail-unread",
                 f"Soryel Inbox: {sender_name}",
                 f"[{cap.get('type','?')}] {preview}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # Zapisz ostatnią porcję jako latest
    if unread:
        _write_json(inbox_latest, {
            "updated_at": _now_iso(),
            "unread_count": len(unread),
            "capsules": [
                {
                    "id": c["id"],
                    "sender": c.get("sender"),
                    "type": c.get("type"),
                    "project": c.get("project"),
                    "created_at": c.get("created_at"),
                    "formatted": _format_capsule(c),
                }
                for c in unread
            ],
        })

    return len(unread)


def main() -> None:
    log.info("=" * 50)
    log.info("  Soryel Inbox Daemon v1.0")
    log.info(f"  Poll: {POLL_INTERVAL}s | Limit: {LIMIT}")
    log.info("=" * 50)

    init_db()
    p = paths()

    while True:
        try:
            n = poll_cycle(p)
            if n:
                log.info(f"Cykl: {n} nowych wiadomości dostarczone do inbox")
        except KeyboardInterrupt:
            log.info("Stop.")
            break
        except Exception as e:
            log.error(f"Błąd cyklu: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
