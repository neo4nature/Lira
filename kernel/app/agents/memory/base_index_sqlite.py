"""BaseIndex (SQLite) — stable embedding store.

Goal:
  - Keep a small, stable set of "promoted" memories (identity, policies, architecture, daily snapshots)
  - Provide fast, safe retrieval without loading huge JSONL files into RAM

Design:
  - SQLite file in memory/root/base_index.sqlite3
  - No special vector extensions required (CPU-only friendly).
  - Retrieval scans only a bounded subset (stable=1) and computes cosine similarity in Python.

This is intentionally minimal; you can swap the backend later (faiss/hnswlib/sqlite-vss) while
keeping the same API.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

_elog = logging.getLogger("ollama.embed")

from config import paths


SCHEMA = "lira.base_index_sqlite.v1"


def _cosine(a: List[float], b: List[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


_CHAT_BUSY_FLAG = Path(os.getenv(
    "LIRA_CHAT_BUSY_FLAG",
    "${LIRA_ROOT:-/opt/lira}/kernel/data/runtime/state/ollama_chat_busy.flag"
))
_DEFER_ON_CHAT = os.getenv("LIRA_DEFER_EMBEDS_ON_CHAT", "1") == "1"


def _ollama_embed(model: str, text: str, timeout_s: int = int(os.getenv("LIRA_OLLAMA_EMBED_TIMEOUT", "1800"))) -> Optional[List[float]]:
    if _DEFER_ON_CHAT and _CHAT_BUSY_FLAG.exists():
        _elog.info("[base_index_sqlite] defer embed: Xian chat busy")
        return None
    url = os.getenv("LIRA_OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embeddings")
    caller = "agents.memory.base_index_sqlite"
    t0 = time.time()
    _elog.info("[%s] SEND embed %d chars model=%s timeout=%ds", caller, len(text), model, timeout_s)
    payload = {"model": model, "input": text}
    try:
        r = requests.post(url, json=payload, timeout=timeout_s)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and isinstance(data.get("embeddings"), list) and data["embeddings"]:
            emb = data["embeddings"][0]
            _elog.info("[%s] RECV embed %d dims in %.1fs", caller, len(emb), time.time() - t0)
            return emb
        if isinstance(data, dict) and "embedding" in data:
            emb = data["embedding"]
            _elog.info("[%s] RECV embed %d dims in %.1fs", caller, len(emb), time.time() - t0)
            return emb
        if isinstance(data, dict) and "data" in data and data["data"]:
            item = data["data"][0]
            if isinstance(item, dict) and "embedding" in item:
                emb = item["embedding"]
                _elog.info("[%s] RECV embed %d dims in %.1fs", caller, len(emb), time.time() - t0)
                return emb
    except Exception as exc:
        _elog.warning("[%s] FAIL embed after %.1fs: %s", caller, time.time() - t0, exc)
        return None
    return None


def db_path() -> Path:
    return Path(os.getenv("LIRA_BASE_INDEX_DB", str(paths()["BASE_INDEX_DB"])) )


def connect(db: Optional[Path] = None) -> sqlite3.Connection:
    p = (db or db_path()).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con


def init_db(db: Optional[Path] = None) -> None:
    con = connect(db)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS base_records (
              id TEXT PRIMARY KEY,
              created_ts REAL NOT NULL,
              stable INTEGER NOT NULL DEFAULT 1,
              kind TEXT NOT NULL,
              text TEXT NOT NULL,
              vector_json TEXT NOT NULL,
              meta_json TEXT NOT NULL
            );
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_base_kind ON base_records(kind);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_base_created ON base_records(created_ts);")
        con.commit()
    finally:
        con.close()


def upsert_record(
    record_id: str,
    *,
    kind: str,
    text: str,
    vector: List[float],
    meta: Optional[Dict[str, Any]] = None,
    stable: int = 1,
    created_ts: Optional[float] = None,
    db: Optional[Path] = None,
) -> None:
    init_db(db)
    con = connect(db)
    try:
        con.execute(
            """
            INSERT INTO base_records (id, created_ts, stable, kind, text, vector_json, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              created_ts=excluded.created_ts,
              stable=excluded.stable,
              kind=excluded.kind,
              text=excluded.text,
              vector_json=excluded.vector_json,
              meta_json=excluded.meta_json;
            """,
            (
                record_id,
                float(created_ts if created_ts is not None else time.time()),
                int(stable),
                kind,
                text,
                json.dumps(vector),
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        con.commit()
    finally:
        con.close()


def search_by_vector(
    qvec: List[float],
    *,
    top_k: int = 6,
    kinds: Optional[List[str]] = None,
    max_scan: int = 5000,
    db: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return top_k stable records using cosine similarity.

    NOTE: We intentionally scan only a bounded number of rows.
    For truly large stores, swap backend to a vector index.
    """
    init_db(db)
    con = connect(db)
    try:
        where = "WHERE stable=1"
        params: List[Any] = []
        if kinds:
            where += " AND kind IN (%s)" % (",".join(["?"] * len(kinds)))
            params.extend(kinds)
        sql = f"SELECT id, kind, text, vector_json, meta_json, created_ts FROM base_records {where} ORDER BY created_ts DESC LIMIT ?"
        params.append(int(max_scan))
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for rid, kind, text, vec_json, meta_json, created_ts in rows:
        try:
            vec = json.loads(vec_json)
            if not isinstance(vec, list):
                continue
            score = _cosine(qvec, vec)
            meta = json.loads(meta_json) if isinstance(meta_json, str) else {}
            scored.append(
                (
                    score,
                    {
                        "id": rid,
                        "score": round(float(score), 5),
                        "source": "base_index",
                        "kind": kind,
                        "text": text,
                        "meta": meta,
                        "created_ts": created_ts,
                    },
                )
            )
        except Exception:
            continue

    scored.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in scored[:top_k]]


def search(query: str, *, top_k: int = 6, kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    model = os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest")
    qvec = _ollama_embed(model, q)
    if qvec is None:
        return []
    return search_by_vector(qvec, top_k=top_k, kinds=kinds)
