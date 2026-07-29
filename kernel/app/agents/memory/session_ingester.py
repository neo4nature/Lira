"""Claude Code session ingester — feeds conversations into the embedding system.

Monitors ~/.claude/projects/ for JSONL session files, extracts user+assistant
conversation pairs, chunks and embeds them via Ollama, and appends to the
embeddings index. This closes the gap where Soryel's conversations with Neo
were invisible to the rest of the ecosystem (Xian, Lira, semantic retrieval).

Can run standalone (single pass, ignores the night window):
    python3 -m agents.memory.session_ingester --once

As a daemon that only works inside the night window (how start_system.sh runs it):
    python3 -m agents.memory.session_ingester --loop
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from xian.chat_busy import is_chat_busy as _chat_busy
from agents.memory.embeddings_batch import (
    _chunk,
    _ollama_embed,
    _existing_ids,
    now_iso,
)

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
EMBED_MODEL = os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest")
CHUNK_SIZE = 600
MIN_TEXT_LEN = 30
STATE_FILE = "session_ingester_state.json"

NIGHT_START = int(os.getenv("NIGHT_WINDOW_START", "5"))
NIGHT_END = int(os.getenv("NIGHT_WINDOW_END", "10"))
LOOP_INTERVAL_S = int(os.getenv("LIRA_SESSION_INGEST_INTERVAL", "600"))


def _in_night_window(now: Optional[datetime] = None) -> bool:
    hour = (now or datetime.now()).hour
    if NIGHT_START <= NIGHT_END:
        return NIGHT_START <= hour < NIGHT_END
    return hour >= NIGHT_START or hour < NIGHT_END



def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _find_sessions(projects_dir: str = PROJECTS_DIR) -> List[Path]:
    files = glob.glob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True)
    return sorted([Path(f) for f in files], key=lambda p: p.stat().st_mtime, reverse=True)


def _extract_text(content) -> str:
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = str(block.get("text", "")).strip()
                if txt:
                    parts.append(txt)
        return "\n".join(parts).strip()
    return str(content).strip()


def _clean_assistant_text(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`\n]+`", " ", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"/[a-zA-Z0-9_./-]{10,}", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_conversations(session_path: Path) -> List[Dict]:
    """Extract conversation pairs from a session JSONL file."""
    entries = []
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    msg = entry.get("message", {})
                    role = msg.get("role")
                    if role not in ("user", "assistant"):
                        continue
                    text = _extract_text(msg.get("content", ""))
                    if not text or len(text) < MIN_TEXT_LEN:
                        continue
                    entries.append({
                        "role": role,
                        "text": text,
                        "uuid": entry.get("uuid", ""),
                        "ts": entry.get("timestamp", ""),
                    })
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass
    return entries


def _build_conversation_chunks(entries: List[Dict], session_name: str) -> List[Tuple[str, str, str]]:
    """Build embeddable text chunks from conversation entries.

    Groups user+assistant pairs into conversation segments,
    then chunks them for embedding.

    Returns: list of (record_id, text, source_info)
    """
    chunks = []
    i = 0
    while i < len(entries):
        segment_parts = []
        segment_start_ts = entries[i].get("ts", "")

        window_end = min(i + 4, len(entries))
        for j in range(i, window_end):
            e = entries[j]
            role_label = "Neo" if e["role"] == "user" else "Soryel"
            text = e["text"]
            if e["role"] == "assistant":
                text = _clean_assistant_text(text)
            segment_parts.append(f"{role_label}: {text}")

        segment_text = "\n\n".join(segment_parts)

        for offset, chunk_text in _chunk(segment_text, chunk_size=CHUNK_SIZE):
            sha = _sha256(chunk_text)[:10]
            rid = f"session:{session_name}:{i}:{offset}:{sha}"
            chunks.append((rid, chunk_text, session_name))

        i += 2  # move by conversation pair

    return chunks


def _load_state(state_dir: Path) -> Dict:
    state_path = state_dir / STATE_FILE
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"ingested_sessions": {}, "last_run": None}


def _save_state(state_dir: Path, state: Dict) -> None:
    state_path = state_dir / STATE_FILE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def ingest_sessions(
    model: str = EMBED_MODEL,
    max_sessions: int = 20,
    index_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Ingest Claude Code sessions into the embeddings index."""
    from config import memory_dir

    mem = memory_dir()
    emb_dir = mem / "embeddings"
    emb_dir.mkdir(parents=True, exist_ok=True)

    if index_path is None:
        index_path = emb_dir / "embeddings_index.jsonl"

    state_dir = mem / "embeddings"
    state = _load_state(state_dir)
    existing = _existing_ids(index_path)

    sessions = _find_sessions()
    if not sessions:
        print("[SessionIngester] Brak sesji Claude Code.")
        return {"sessions": 0, "chunks": 0, "skipped": 0}

    sessions = sessions[:max_sessions]

    stats = {"sessions": 0, "chunks": 0, "skipped": 0, "errors": 0, "deferred": 0}

    f = index_path.open("a", encoding="utf-8")
    try:
        for session_path in sessions:
            session_name = session_path.stem
            file_mtime = session_path.stat().st_mtime

            prev_mtime = state["ingested_sessions"].get(session_name, 0)
            if file_mtime <= prev_mtime:
                continue

            if _chat_busy():
                stats["deferred"] += 1
                print("[SessionIngester] defer: Xian chat busy — wracam w kolejnym ticku", flush=True)
                break

            print(f"[SessionIngester] Przetwarzam: {session_name[:12]}...", flush=True)

            entries = _parse_conversations(session_path)
            if not entries:
                state["ingested_sessions"][session_name] = file_mtime
                _save_state(state_dir, state)
                continue

            conv_chunks = _build_conversation_chunks(entries, session_name)
            session_embedded = 0
            deferred = False

            for rid, chunk_text, source in conv_chunks:
                if rid in existing:
                    stats["skipped"] += 1
                    continue

                if _chat_busy():
                    deferred = True
                    break

                vec = _ollama_embed(model, chunk_text)
                if vec is None:
                    stats["errors"] += 1
                    continue

                rec = {
                    "schema": "lira.embedding_record.v1",
                    "id": rid,
                    "ts": now_iso(),
                    "model": model,
                    "source": {
                        "kind": "claude_session",
                        "path": str(session_path),
                        "session": session_name,
                    },
                    "text": chunk_text[:2000],
                    "vector": vec,
                    "meta": {"sha256": _sha256(chunk_text)},
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                existing.add(rid)
                session_embedded += 1
                stats["chunks"] += 1

                if session_embedded % 50 == 0:
                    f.flush()
                    time.sleep(0.05)

            if deferred:
                stats["deferred"] += 1
                print(f"[SessionIngester]   defer w trakcie {session_name[:12]} po {session_embedded} chunkach "
                      f"— sesja NIE odhaczona, dokończę później", flush=True)
                break

            state["ingested_sessions"][session_name] = file_mtime
            stats["sessions"] += 1
            _save_state(state_dir, state)
            print(f"[SessionIngester]   → {session_embedded} chunków, {len(entries)} wpisów", flush=True)

    finally:
        f.close()

    state["last_run"] = now_iso()
    _save_state(state_dir, state)

    print(f"[SessionIngester] Gotowe: {stats['sessions']} sesji, {stats['chunks']} chunków, "
          f"{stats['skipped']} pominięto, {stats['errors']} błędów, {stats['deferred']} odroczeń", flush=True)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Indeksuje sesje Claude Code do pamięci semantycznej.")
    ap.add_argument("--loop", action="store_true",
                    help="daemon: pracuje tylko w oknie nocnym, poza nim śpi")
    ap.add_argument("--once", action="store_true",
                    help="jeden przebieg teraz, ignoruje okno nocne")
    ap.add_argument("--interval", type=int, default=LOOP_INTERVAL_S)
    args = ap.parse_args()

    if not args.loop:
        print("[SessionIngester] Start — jeden przebieg...", flush=True)
        stats = ingest_sessions()
        print(f"[SessionIngester] Wynik: {json.dumps(stats)}", flush=True)
        return

    print(f"[SessionIngester] Daemon start. Okno: {NIGHT_START}:00–{NIGHT_END}:00, "
          f"tick {args.interval}s", flush=True)
    while True:
        if _in_night_window():
            print(f"[SessionIngester] 🌙 {datetime.now():%F %T} tick", flush=True)
            try:
                stats = ingest_sessions()
                print(f"[SessionIngester] Wynik: {json.dumps(stats)}", flush=True)
            except Exception as exc:
                print(f"[SessionIngester] błąd przebiegu (kontynuuję): {exc}", flush=True)
        else:
            print(f"[SessionIngester] 💤 {datetime.now():%F %T} poza oknem "
                  f"({NIGHT_START}–{NIGHT_END}), śpię", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
