#!/usr/bin/env python3
"""
conversation_bridge.py — Most: capsule_bus → shared_context/events/

Czyta ostatnie wiadomości z MoltBook (capsule_bus) i zapisuje je jako
pliki .json w shared_context/events/ w formacie który alignment_publisher
rozumie (kind=user_input / kind=assistant_output).

To jest brakujący kabelek który łączy żywe rozmowy z alignment pipeline.

Uruchomienie:
    python3 -m tools.conversation_bridge          # jednorazowo
    python3 -m tools.conversation_bridge --loop    # daemon (co 30s)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from config import paths

USER_SENDERS = {"Neo", "neo", "Dawid", "dawid"}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _get_recent_messages(limit: int = 20) -> list[dict]:
    from governance.capsule_bus import get_conn, DB_PATH
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT id, sender, payload_json, created_at
               FROM capsules
               WHERE thread_id = 'moltbook-main'
                 AND type = 'chat_message'
                 AND recipient = 'shared'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload_json"] = json.loads(item["payload_json"]) if isinstance(item["payload_json"], str) else item["payload_json"]
        except Exception:
            pass
        out.append(item)
    return list(reversed(out))


def bridge_once() -> int:
    p = paths()
    events_dir = p["EVENTS_DIR"]
    events_dir.mkdir(parents=True, exist_ok=True)
    state_path = events_dir.parent / "conversation_bridge_state.json"
    state = _read_json(state_path, {"last_seen_id": None})

    messages = _get_recent_messages(limit=20)
    if not messages:
        return 0

    last_seen = state.get("last_seen_id")
    new_msgs = []
    if last_seen:
        found = False
        for msg in messages:
            if found:
                new_msgs.append(msg)
            if msg["id"] == last_seen:
                found = True
        if not found:
            new_msgs = messages[-5:]
    else:
        new_msgs = messages[-5:]

    written = 0
    for msg in new_msgs:
        sender = msg.get("sender", "?")
        payload = msg.get("payload_json", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}

        text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        if not text or len(text.strip()) < 2:
            continue

        if text.startswith("[MoltBook"):
            text = text.split("]: ", 1)[-1] if "]: " in text else text

        kind = "user_input" if sender in USER_SENDERS else "assistant_output"
        ts = msg.get("created_at", "")
        ts_safe = ts.replace(":", "-").replace("+", "_")[:25]

        event = {
            "kind": kind,
            "source": f"moltbook/{sender}",
            "ts": ts,
            "payload": {
                "text": text[:800],
                "sender": sender,
                "capsule_id": msg.get("id", ""),
            },
        }

        filename = f"conv_{ts_safe}_{msg['id'][:8]}.json"
        filepath = events_dir / filename
        if not filepath.exists():
            _write_json(filepath, event)
            written += 1

    if new_msgs:
        state["last_seen_id"] = new_msgs[-1]["id"]
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_json(state_path, state)

    if written:
        _cleanup_old_events(events_dir, keep=100)

    return written


def _cleanup_old_events(events_dir: Path, keep: int = 100):
    conv_files = sorted(events_dir.glob("conv_*.json"))
    if len(conv_files) > keep:
        for f in conv_files[:-keep]:
            try:
                f.unlink()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()

    if args.loop:
        print(f"[ConvBridge] Loop mode, interval={args.interval}s")
        while True:
            try:
                n = bridge_once()
                if n:
                    print(f"[ConvBridge] Wrote {n} events")
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[ConvBridge] Error: {e}")
            time.sleep(args.interval)
    else:
        n = bridge_once()
        print(f"[ConvBridge] Wrote {n} events")


if __name__ == "__main__":
    main()
