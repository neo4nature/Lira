#!/usr/bin/env python3
"""export_dataset.py

Cel: wygenerować train_raw.jsonl (format rozmów do LoRA) z runtime Lira_A_step1_kernel.

Źródła (priorytet):
1) data/runtime/queue/queue_tasks.json -> task.kind == 'conversation_turn'
2) data/shared_context/events -> fallback (parowanie user_input/assistant_output po czasie)

Wyjście:
- train_raw.jsonl (domyślnie w katalogu uruchomienia lub --out)

Przykładowy rekord:
{"messages": [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]}

Uwaga: skrypt nie ingeruje w dane runtime — tylko czyta.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _read_json(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore") or "")
    except Exception:
        return default


def _iso_to_dt(s: str) -> Optional[datetime]:
    try:
        # heartbeat używa isoformat(timespec='seconds') bez strefy
        return datetime.fromisoformat(s)
    except Exception:
        return None


@dataclass
class Turn:
    ts: Optional[datetime]
    user: str
    assistant: str
    meta: Dict[str, Any]


def _collect_from_queue(queue_file: Path) -> List[Turn]:
    data = _read_json(queue_file, default=[])
    if not isinstance(data, list):
        return []

    turns: List[Turn] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "conversation_turn":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        user = (payload.get("user") or "").strip()
        assistant = (payload.get("assistant") or "").strip()
        if not user or not assistant:
            continue
        ts = _iso_to_dt(item.get("ts", ""))
        meta = {
            "source": "queue",
            "kind": item.get("kind"),
            "importance": item.get("importance"),
            "tags": item.get("tags", []),
            "event_in": payload.get("event_in"),
            "event_out": payload.get("event_out"),
            "latency_s": payload.get("latency_s"),
        }
        turns.append(Turn(ts=ts, user=user, assistant=assistant, meta=meta))

    # sort stabilnie po ts jeśli jest
    turns.sort(key=lambda t: (t.ts or datetime.min))
    return turns


def _collect_from_events(events_dir: Path) -> List[Turn]:
    if not events_dir.exists():
        return []

    # Oczekiwany format z heartbeat.log_event:
    # {"ts": "...", "kind": "user_input"|"assistant_output", "payload": {"text": "..."}}
    events: List[Tuple[Optional[datetime], str, str, Path]] = []
    for fp in sorted(events_dir.glob("*.json")):
        obj = _read_json(fp, default={})
        if not isinstance(obj, dict):
            continue
        kind = str(obj.get("kind") or "")
        ts = _iso_to_dt(str(obj.get("ts") or ""))
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        text = str(payload.get("text") or "").strip()
        if not kind or not text:
            continue
        events.append((ts, kind, text, fp))

    # Proste parowanie: user_input -> najbliższy kolejny assistant_output
    turns: List[Turn] = []
    pending_user: Optional[Tuple[Optional[datetime], str, Path]] = None
    for ts, kind, text, fp in events:
        if kind == "user_input":
            pending_user = (ts, text, fp)
        elif kind == "assistant_output" and pending_user is not None:
            u_ts, u_text, u_fp = pending_user
            meta = {
                "source": "events",
                "user_event": str(u_fp),
                "assistant_event": str(fp),
            }
            turns.append(Turn(ts=ts or u_ts, user=u_text, assistant=text, meta=meta))
            pending_user = None
        else:
            # ignorujemy inne rodzaje
            continue

    turns.sort(key=lambda t: (t.ts or datetime.min))
    return turns


def _dedupe_basic(turns: List[Turn]) -> List[Turn]:
    # Minimalna deduplikacja (dokładne dopasowanie user+assistant)
    seen = set()
    out: List[Turn] = []
    for t in turns:
        key = (" ".join(t.user.split()), " ".join(t.assistant.split()))
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _write_jsonl(turns: List[Turn], out_path: Path, include_meta: bool = False):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for t in turns:
            rec: Dict[str, Any] = {
                "messages": [
                    {"role": "user", "content": t.user},
                    {"role": "assistant", "content": t.assistant},
                ]
            }
            if include_meta:
                rec["meta"] = t.meta
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Export train_raw.jsonl from Lira runtime")
    ap.add_argument("--project", type=str, default=str(Path(__file__).resolve().parents[1]),
                    help="Ścieżka do katalogu projektu (domyślnie: katalog nadrzędny app/)"
                    )
    ap.add_argument("--out", type=str, default="train_raw.jsonl", help="Plik wyjściowy JSONL")
    ap.add_argument("--include-meta", action="store_true", help="Dołącz meta do rekordów")
    ap.add_argument("--prefer", choices=["queue", "events"], default="queue",
                    help="Preferowane źródło danych")

    args = ap.parse_args()

    project = Path(args.project).resolve()
    data_dir = project / "data"
    queue_file = data_dir / "runtime" / "queue" / "queue_tasks.json"
    events_dir = data_dir / "shared_context" / "events"

    turns: List[Turn] = []

    if args.prefer == "queue":
        turns = _collect_from_queue(queue_file)
        if not turns:
            turns = _collect_from_events(events_dir)
    else:
        turns = _collect_from_events(events_dir)
        if not turns:
            turns = _collect_from_queue(queue_file)

    turns = _dedupe_basic(turns)

    out_path = Path(args.out).resolve()
    _write_jsonl(turns, out_path, include_meta=args.include_meta)

    print(f"OK: exported {len(turns)} turns -> {out_path}")
    print(f"Source checked: queue={queue_file} ; events={events_dir}")


if __name__ == "__main__":
    main()
