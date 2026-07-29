"""Semantic retrieval using local Ollama embeddings + cosine similarity.

This module is designed to be fast and safe:
- Loads embeddings from JSONL index (memory/memory/embeddings/embeddings_index.jsonl)
- Embeds the query using Ollama embeddings endpoint
- Returns top_k matches with scores and pointers

If numpy is available, it will be used for speed; otherwise pure python.
"""

from __future__ import annotations

import json
import math
from collections import deque
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import logging

import requests

from config import memory_dir, shared_context_dir

log = logging.getLogger("memory.semantic_retrieval")

# Optional backends
try:
    from agents.memory.base_index_sqlite import search_by_vector as _base_search_by_vec
except Exception:
    _base_search_by_vec = None

try:
    from agents.memory.turmalin_cache import iter_recent as _turmalin_iter_recent
except Exception:
    _turmalin_iter_recent = None


from xian.chat_busy import is_chat_busy as _is_chat_busy

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _ollama_embed(
    model: str,
    text: str,
    timeout_s: int = int(os.getenv("LIRA_OLLAMA_EMBED_TIMEOUT", "1800")),
    defer_on_chat: bool = True,
) -> Optional[List[float]]:
    if defer_on_chat and _is_chat_busy():
        log.info("[semantic_retrieval] defer embed: Xian chat busy")
        return None
    text = text[:600]
    url = os.getenv("LIRA_OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed")
    payload = {"model": model, "input": text}
    try:
        r = requests.post(url, json=payload, timeout=timeout_s)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and isinstance(data.get("embeddings"), list) and data["embeddings"]:
            vec = data["embeddings"][0]
            if vec:
                return vec
        if isinstance(data, dict) and "embedding" in data and data["embedding"]:
            return data["embedding"]
        if isinstance(data, dict) and "data" in data and data["data"]:
            item = data["data"][0]
            if isinstance(item, dict) and "embedding" in item and item["embedding"]:
                return item["embedding"]
    except Exception:
        return None
    return None


def _cosine(a: List[float], b: List[float]) -> float:
    # pure python cosine
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


def _iter_records(index_path: Path, max_records: int = 50_000) -> Iterable[Dict[str, Any]]:
    """Iterate embedding records from a JSONL file without loading it all into RAM.

    Keeps only the last `max_records` lines in memory (deque) so large indexes stay safe.
    """
    if not index_path.exists():
        return

    buf = deque(maxlen=max_records)
    try:
        with index_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line:
                    buf.append(line)
    except Exception:
        return

    for line in buf:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get('schema') == 'lira.embedding_record.v1':
                yield obj
        except Exception:
            continue



def search_live(query: str, top_k: int = 4) -> List[Dict[str, Any]]:
    """Wyszukiwanie dla ścieżki rozmowy: ograniczone z góry i bez czekania.

    Pomija embeddings_index.jsonl (setki MB, skan liniowy) i pyta tylko indeks
    stabilny oraz bufor turmalina. Nie ustępuje fladze zajętości: flaga chroni
    rozmowę przed hurtem embeddingów, a to jest jedno krótkie liczenie wektora
    NA POTRZEBY tej rozmowy — deferral uciszyłby pamięć dokładnie wtedy, gdy
    jest potrzebna. Hurtownicy (embeddings_batch, turmalin) dalej czekają.

    Pytanie dostaje przedrostek zalecany przez mxbai (model asymetryczny:
    przedrostek TYLKO po stronie zapytania, dokumenty w indeksie są bez niego).
    Bez niego temat nieobecny w indeksie dostawał 0.78, a obecny 0.74 — czyli
    ranking mierzył rejestr wypowiedzi, nie treść. Z przedrostkiem: 0.66 wobec
    0.86. Dlatego próg ma sens dopiero razem z przedrostkiem.

    Margines jest wąski: szum siada na 0.66-0.71 (rośnie z długością pytania),
    trafienie na 0.78-0.86, a parafraza omijająca słowa kluczowe ledwie na 0.78.
    Próg tego nie naprawi — przyczyną jest ziarno indeksu: rekord to całe
    wejście plus cała odpowiedź, więc wektor uśrednia wymianę zdań i gubi
    pojedynczy fakt. Właściwa poprawka to drobniejsze kawałki przy indeksowaniu.
    """
    q = (query or "").strip()
    if not q:
        return []

    model = os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest")
    timeout_s = int(os.getenv("LIRA_LIVE_EMBED_TIMEOUT", "25"))
    min_score = float(os.getenv("LIRA_LIVE_MIN_SCORE", "0.75"))
    qvec = _ollama_embed(model, QUERY_PREFIX + q, timeout_s=timeout_s, defer_on_chat=False)
    if qvec is None:
        return []

    merged: List[Dict[str, Any]] = []
    try:
        if _base_search_by_vec is not None:
            merged.extend(_base_search_by_vec(qvec, top_k=top_k))
    except Exception as exc:
        log.warning("[semantic_live] base_index search failed: %s", exc)

    try:
        if _turmalin_iter_recent is not None:
            scored: List[Tuple[float, Dict[str, Any]]] = []
            cap = int(os.getenv("LIRA_LIVE_TURMALIN_MAX", "2000"))
            for rec in _turmalin_iter_recent(max_records=cap):
                vec = rec.get("vector")
                if not isinstance(vec, list) or len(vec) != len(qvec):
                    continue
                score = _cosine(qvec, vec)
                scored.append((score, {
                    "id": rec.get("id"),
                    "score": round(float(score), 5),
                    "source": "turmalin_cache",
                    "text": rec.get("text", ""),
                    "kind": rec.get("kind", "temp"),
                }))
            scored.sort(key=lambda t: t[0], reverse=True)
            merged.extend([d for _, d in scored[:top_k]])
    except Exception as exc:
        log.warning("[semantic_live] turmalin_cache search failed: %s", exc)

    best: Dict[str, Dict[str, Any]] = {}
    for item in merged:
        rid = str(item.get("id") or "")
        if not rid:
            continue
        if rid not in best or float(item.get("score", 0)) > float(best[rid].get("score", 0)):
            best[rid] = item
    # Najświeższe rekordy to ta sama rozmowa, którą wołający i tak ma w oknie —
    # bez tego pamięć oddaje echo ostatnich minut zamiast tego, co wypadło z okna.
    min_age_s = float(os.getenv("LIRA_LIVE_MIN_AGE_S", "1800"))
    now = time.time()
    hits = sorted(
        (
            d for d in best.values()
            if float(d.get("score", 0)) >= min_score
            and (not d.get("created_ts") or now - float(d["created_ts"]) >= min_age_s)
        ),
        key=lambda d: float(d.get("score", 0)),
        reverse=True,
    )
    # Indeks trzyma powtórzenia tej samej rozmowy pod różnymi id — bez tego
    # jeden fragment zajmuje cztery miejsca w oknie Xiana.
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in hits:
        key = " ".join((item.get("text") or "").split())[:120].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= top_k:
            break
    return out


def search(query: str, top_k: int = 6) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []

    model = os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest")
    index_path = memory_dir() / "embeddings" / "embeddings_index.jsonl"
    qvec = _ollama_embed(model, q)
    if qvec is None:
        return []

    # 0) BaseIndex (stable, small) — fast & reliable
    merged: List[Dict[str, Any]] = []
    try:
        if _base_search_by_vec is not None and os.getenv("LIRA_ENABLE_BASE_INDEX", "1") == "1":
            merged.extend(_base_search_by_vec(qvec, top_k=top_k))
    except Exception as exc:
        log.warning("[semantic] base_index search failed: %s", exc)

    # 0.5) TurmalinCache (temporary, bounded)
    try:
        if _turmalin_iter_recent is not None and os.getenv("LIRA_ENABLE_TURMALIN", "1") == "1":
            cache_recs = list(_turmalin_iter_recent(max_records=int(os.getenv("LIRA_TURMALIN_SEARCH_MAX", "5000"))))
            tmp_scored: List[Tuple[float, Dict[str, Any]]] = []
            for rec in cache_recs:
                vec = rec.get("vector")
                if not isinstance(vec, list):
                    continue
                score = _cosine(qvec, vec)
                tmp_scored.append((score, {
                    "id": rec.get("id"),
                    "score": round(float(score), 5),
                    "source": "turmalin_cache",
                    "path": str(memory_dir() / "embeddings" / "turmalin_cache.jsonl"),
                    "offset": 0,
                    "text": rec.get("text", ""),
                    "model": model,
                    "kind": rec.get("kind", "temp"),
                }))
            tmp_scored.sort(key=lambda t: t[0], reverse=True)
            merged.extend([d for _, d in tmp_scored[:top_k]])
    except Exception as exc:
        log.warning("[semantic] turmalin_cache search failed: %s", exc)

    # optional numpy path
    use_numpy = False
    np = None
    try:
        import numpy as _np  # type: ignore
        np = _np
        use_numpy = True
    except Exception:
        use_numpy = False

    scored: List[Tuple[float, Dict[str, Any]]] = []

    qdim = len(qvec)

    if use_numpy and np is not None:
        qv = np.array(qvec, dtype=float)
        qn = float(np.linalg.norm(qv)) + 1e-12
        for rec in _iter_records(index_path):
            vec = rec.get("vector")
            if not isinstance(vec, list) or len(vec) != qdim:
                continue
            v = np.array(vec, dtype=float)
            dn = float(np.linalg.norm(v)) + 1e-12
            score = float(np.dot(qv, v) / (qn * dn))
            scored.append((score, rec))
    else:
        for rec in _iter_records(index_path):
            vec = rec.get("vector")
            if not isinstance(vec, list) or len(vec) != qdim:
                continue
            score = _cosine(qvec, vec)
            scored.append((score, rec))

    scored.sort(key=lambda t: t[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, rec in scored[:top_k]:
        src = rec.get("source", {}) or {}
        out.append({
            "id": rec.get("id"),
            "score": round(float(score), 5),
            "source": src.get("kind", "unknown"),
            "path": src.get("path"),
            "offset": src.get("offset", 0),
            "text": rec.get("text", ""),
            "model": rec.get("model"),
        })

    # Merge and dedupe (prefer higher score)
    merged.extend(out)
    best: Dict[str, Dict[str, Any]] = {}
    for item in merged:
        rid = str(item.get("id") or "")
        if not rid:
            continue
        if rid not in best or float(item.get("score", 0)) > float(best[rid].get("score", 0)):
            best[rid] = item

    final = sorted(best.values(), key=lambda d: float(d.get("score", 0)), reverse=True)
    return final[:top_k]
