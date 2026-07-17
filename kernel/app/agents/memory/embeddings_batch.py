"""Nightly embeddings batch for semantic memory.

Design goals:
- CPU/local-first: use local Ollama embeddings endpoint.
- Lightweight: chunk text, embed, append to JSONL index.
- Safe: gated by idle/veto via Night Cycle; best-effort failures.

Outputs:
- data/memory/embeddings/embeddings_index.jsonl (one record per chunk)

Record schema:
{
  "schema": "lira.embedding_record.v1",
  "id": "...",
  "ts": "...",
  "model": "mxbai-embed-large:latest",
  "source": {"kind":"file|runtime", "path":"...", "offset":0, "len":1234},
  "text": "...",  # truncated chunk
  "vector": [..],
  "meta": {"sha256":"..."}
}
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from config import memory_dir, shared_context_dir, runtime_dir
from agents.memory.memory_loader import write_json


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _read_text_safe(path: Path, max_bytes: int = 2_000_000) -> str:
    try:
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[:max_bytes]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _split_sentences(text: str) -> List[str]:
    """Dziel tekst na zdania (PL/EN)."""
    import re
    parts = re.split(r'(?<=[.!?…])\s+', text)
    return [p for p in parts if p.strip()]


def _chunk(text: str, chunk_size: int = 900, overlap: int = 120) -> Iterable[Tuple[int, str]]:
    """Semantic hybrid chunker: akapity → zdania → twardy split."""
    if not text:
        return
    yield from _chunk_pass1(text, chunk_size)


def _chunk_pass1(text: str, chunk_size: int) -> Iterable[Tuple[int, str]]:
    """Pomocnicza: akapity → zdania → twardy split."""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        return

    current = ""
    current_offset = 0
    pos = 0

    for para in paragraphs:
        idx = text.find(para, pos)
        if idx == -1:
            idx = pos

        if len(para) > chunk_size:
            # Akapit za duży — podziel po zdaniach
            if current.strip():
                yield current_offset, current.strip()
                current = ""
            sentences = _split_sentences(para)
            sent_buf = ""
            sent_offset = idx
            for sent in sentences:
                if not sent_buf:
                    sent_buf = sent
                    sent_offset = idx + para.find(sent)
                elif len(sent_buf) + 1 + len(sent) <= chunk_size:
                    sent_buf = sent_buf + " " + sent
                else:
                    if sent_buf.strip():
                        yield sent_offset, sent_buf.strip()
                    sent_buf = sent
                    sent_offset = idx + para.find(sent)
            if sent_buf.strip():
                yield sent_offset, sent_buf.strip()
        elif not current:
            current = para
            current_offset = idx
        elif len(current) + 2 + len(para) <= chunk_size:
            current = current + "\n\n" + para
        else:
            if current.strip():
                yield current_offset, current.strip()
            current = para
            current_offset = idx

        pos = idx + len(para)

    if current.strip():
        yield current_offset, current.strip()


_EMBED_MAX_CHARS = int(os.getenv("LIRA_EMBED_MAX_CHARS", "600"))

_CHAT_BUSY_FLAG = Path(os.getenv(
    "LIRA_CHAT_BUSY_FLAG",
    "${LIRA_ROOT:-/opt/lira}/kernel/data/runtime/state/ollama_chat_busy.flag"
))
_DEFER_ON_CHAT = os.getenv("LIRA_DEFER_EMBEDS_ON_CHAT", "1") == "1"


def _ollama_embed(model: str, text: str, timeout_s: int = int(os.getenv("LIRA_OLLAMA_EMBED_TIMEOUT", "1800"))) -> Optional[List[float]]:
    """Call Ollama embeddings endpoint. Supports both /api/embed (new) and /api/embeddings (legacy)."""
    if _DEFER_ON_CHAT and _CHAT_BUSY_FLAG.exists():
        print("[embeddings_batch] defer embed: Xian chat busy", flush=True)
        return None
    text = text[:_EMBED_MAX_CHARS]
    url = os.getenv("LIRA_OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed")
    payload = {"model": model, "input": text}
    try:
        r = requests.post(url, json=payload, timeout=timeout_s)
        r.raise_for_status()
        data = r.json()
        # Ollama 0.11+ /api/embed uses key 'embeddings' (list of lists)
        if isinstance(data, dict) and isinstance(data.get("embeddings"), list) and data["embeddings"]:
            vec = data["embeddings"][0]
            if vec:
                return vec
        # Legacy /api/embeddings uses key 'embedding'
        if isinstance(data, dict) and "embedding" in data and data["embedding"]:
            return data["embedding"]
        # Some variants return 'data': [{'embedding': ...}]
        if isinstance(data, dict) and "data" in data and data["data"]:
            item = data["data"][0]
            if isinstance(item, dict) and "embedding" in item and item["embedding"]:
                return item["embedding"]
    except Exception:
        return None
    return None


_ID_RE = re.compile(r'"id"\s*:\s*"([^"]+)"')


def _existing_ids(index_path: Path, limit: int = 200_000) -> set:
    """Load existing record ids to avoid duplication. Best-effort.

    Streams line-by-line and extracts only the id — read_text() of the whole
    index (>1GB) pinned RSS at ~1.8GB in the ingester loop (PA-030 2026-07-13).
    """
    ids: set = set()
    if not index_path.exists():
        return ids
    try:
        with index_path.open("rb") as f:
            for raw in f:
                # id is one of the first keys; a full json.loads would parse
                # the 1024-float vector too
                m = _ID_RE.search(raw[:512].decode("utf-8", errors="ignore"))
                if m:
                    ids.add(m.group(1))
                    if len(ids) >= limit:
                        break
    except Exception:
        return set()
    return ids


def build_embeddings(
    model: str = "mxbai-embed-large:latest",
    max_files: int = 400,
    max_runtime_lines: int = 2000,
    chunk_size: int = 900,
    overlap: int = 120,
    top_dirs: Optional[List[Path]] = None,
) -> Dict[str, int]:
    """Run embeddings batch and append new records to the JSONL index."""
    mem = memory_dir()
    emb_dir = mem / "embeddings"
    emb_dir.mkdir(parents=True, exist_ok=True)
    index_path = emb_dir / "embeddings_index.jsonl"

    existing = _existing_ids(index_path)

    files_indexed = 0
    chunks_embedded = 0
    skipped = 0

    sources: List[Tuple[str, Path]] = []

    if top_dirs is None:
        # default: memory root + diary + rebuild + recent events
        top_dirs = [
            mem,
            runtime_dir() / "rebuild",
            shared_context_dir() / "events",
        ]

    # collect candidate files
    cand: List[Path] = []
    for d in top_dirs:
        if not d.exists():
            continue
        cand.extend(sorted(d.rglob("*.md")))
        cand.extend(sorted(d.rglob("*.json")))
        cand.extend(sorted(d.rglob("*.txt")))

    # prioritize newest
    cand = sorted(set(cand), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    cand = cand[:max_files]

    # also embed last window from runtime.jsonl (if exists)
    runtime_stream = shared_context_dir() / "events" / "runtime.jsonl"
    runtime_lines: List[str] = []
    if runtime_stream.exists():
        try:
            lines = runtime_stream.read_text(encoding="utf-8", errors="ignore").splitlines()
            runtime_lines = lines[-min(len(lines), max_runtime_lines):]
        except Exception:
            runtime_lines = []

    # open index for append
    index_path.parent.mkdir(parents=True, exist_ok=True)
    f = index_path.open("a", encoding="utf-8")
    try:
        # files
        for path in cand:
            text = _read_text_safe(path)
            if not text.strip():
                skipped += 1
                continue
            files_indexed += 1
            for offset, ch in _chunk(text, chunk_size=chunk_size, overlap=overlap):
                rid = f"file:{path}:{offset}:{_sha256(ch)[:10]}"
                if rid in existing:
                    skipped += 1
                    continue
                vec = _ollama_embed(model, ch)
                if vec is None:
                    skipped += 1
                    continue
                rec = {
                    "schema": "lira.embedding_record.v1",
                    "id": rid,
                    "ts": now_iso(),
                    "model": model,
                    "source": {"kind": "file", "path": str(path), "offset": offset, "len": len(ch)},
                    "text": ch[:2000],
                    "vector": vec,
                    "meta": {"sha256": _sha256(ch)},
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                chunks_embedded += 1
                # tiny sleep to reduce pressure
                if chunks_embedded % 50 == 0:
                    time.sleep(0.05)

        # runtime window (embed event summaries)
        for i, line in enumerate(runtime_lines):
            # keep it short
            ch = line.strip()
            if not ch:
                continue
            rid = f"runtime:{i}:{_sha256(ch)[:10]}"
            if rid in existing:
                skipped += 1
                continue
            vec = _ollama_embed(model, ch)
            if vec is None:
                skipped += 1
                continue
            rec = {
                "schema": "lira.embedding_record.v1",
                "id": rid,
                "ts": now_iso(),
                "model": model,
                "source": {"kind": "runtime", "path": str(runtime_stream), "offset": i, "len": len(ch)},
                "text": ch[:2000],
                "vector": vec,
                "meta": {"sha256": _sha256(ch)},
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            chunks_embedded += 1
    finally:
        f.close()

    # write batch report
    report = {
        "schema": "lira.embeddings_batch_report.v1",
        "ts": now_iso(),
        "model": model,
        "files_scanned": len(cand),
        "files_indexed": files_indexed,
        "chunks_embedded": chunks_embedded,
        "skipped": skipped,
        "index_path": str(index_path),
    }
    write_json(runtime_dir() / "state" / "embeddings_batch_report.json", report)
    return {"files_indexed": files_indexed, "chunks_embedded": chunks_embedded, "skipped": skipped}


def main():
    model = os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest")
    max_files = int(os.getenv("LIRA_EMBED_MAX_FILES", "400"))
    chunk_size = int(os.getenv("LIRA_EMBED_CHUNK_SIZE", "900"))
    overlap = int(os.getenv("LIRA_EMBED_OVERLAP", "120"))
    res = build_embeddings(model=model, max_files=max_files, chunk_size=chunk_size, overlap=overlap)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
