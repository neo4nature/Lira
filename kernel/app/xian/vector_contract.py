from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

from config import paths

_elog = logging.getLogger("ollama.embed")

OLLAMA_EMBED_URL = os.getenv("LIRA_OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed")
EMBED_MODEL = os.getenv("LIRA_ALIGNMENT_EMBED_MODEL", os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest"))

# Cache: unika re-embeddingu gdy session_text się nie zmienił
_session_embed_cache: dict = {"sha256": None, "vector": None}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


MAX_EMBED_CHARS = int(os.getenv("LIRA_MAX_EMBED_CHARS", "800"))

# Faza 2 audytu 2026-05-30: czekaj, gdy chat aktywny (nadawcy trzymają flagę przez xian.chat_busy)
from xian.chat_busy import is_chat_busy as _is_chat_busy
_CHAT_BUSY_MAX_WAIT = float(os.getenv("LIRA_CHAT_BUSY_MAX_WAIT", "30"))


def _is_chat_idle() -> bool:
    """Return True if Xian is not generating. Skip embed entirely when busy."""
    if not _is_chat_busy():
        return True
    _elog.info("[xian.vector_contract] DEFER embed — chat_busy active, using cache")
    return False


# Patrz alignment_publisher: bez bezpiecznika martwa Ollama daje 45 tys. par
# SEND/FAIL, bo odmowa połączenia wraca natychmiast i nie hamuje pętli.
_CONN_BREAKER_MAX_S = float(os.getenv("LIRA_EMBED_BREAKER_MAX_S", "300"))
_conn_retry_at = 0.0
_conn_fail_streak = 0


def _ollama_embed(text: str, model: str = EMBED_MODEL, timeout_s: int = int(os.getenv("LIRA_OLLAMA_EMBED_TIMEOUT", "1800"))) -> List[float] | None:
    global _conn_retry_at, _conn_fail_streak
    if not (text or "").strip():
        return None
    text = text[:MAX_EMBED_CHARS]
    if time.time() < _conn_retry_at:
        return None
    if not _is_chat_idle():
        return None
    caller = "xian.vector_contract"
    chars = len(text)
    t0 = time.time()
    _elog.info("[%s] SEND embed %d chars model=%s timeout=%ds", caller, chars, model, timeout_s)
    try:
        r = requests.post(OLLAMA_EMBED_URL, json={"model": model, "input": text}, timeout=timeout_s)
        _conn_fail_streak = 0
        r.raise_for_status()
        data = r.json()
        emb = (data.get("embeddings") or [[]])[0] if isinstance(data.get("embeddings"), list) else data.get("embedding")
        if isinstance(emb, list) and emb:
            _elog.info("[%s] RECV embed %d dims in %.1fs", caller, len(emb), time.time() - t0)
            return emb
        if isinstance(data.get("data"), list) and data["data"]:
            emb = data["data"][0].get("embedding")
            if isinstance(emb, list) and emb:
                _elog.info("[%s] RECV embed %d dims in %.1fs", caller, len(emb), time.time() - t0)
                return emb
    except requests.exceptions.ConnectionError as exc:
        _conn_fail_streak += 1
        pause = min(_CONN_BREAKER_MAX_S, 5.0 * 2 ** min(_conn_fail_streak, 6))
        _conn_retry_at = time.time() + pause
        if _conn_fail_streak == 1 or _conn_fail_streak % 50 == 0:
            _elog.warning("[%s] FAIL embed: brak Ollamy (%d z rzedu), pauza %.0fs: %s", caller, _conn_fail_streak, pause, exc)
        return None
    except Exception as exc:
        _elog.warning("[%s] FAIL embed after %.1fs: %s", caller, time.time() - t0, exc)
        return None
    return None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na * nb)


def ensure_xian_vector() -> Path:
    p = paths()
    identity_dir = p["MEMORY_DIR"] / "identity"
    txt_path = identity_dir / "xian_shared_vector.txt"
    vec_path = identity_dir / "xian_shared_vector.vec.json"
    identity_dir.mkdir(parents=True, exist_ok=True)

    if not txt_path.exists() or txt_path.stat().st_size == 0:
        txt_path.write_text(
            "Xian jest wspólnym rdzeniem orientacji systemu Neo, Liry i Soryel. "
            "Xian obserwuje stan lokalnego ekosystemu, redukuje chaos przez wybór właściwej ścieżki, "
            "raportuje krótko i jasno, preferuje prawdę ponad wygodę, nie przejmuje ownershipu od DecisionLoop, "
            "i chroni spójność lokalnego rdzenia przez wczesne wykrywanie driftu, przeciążenia i błędnego routingu.\n",

            encoding="utf-8",
        )

    text = txt_path.read_text(encoding="utf-8", errors="ignore")
    text_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    current = _read_json(vec_path, {})
    if current.get("text_sha256") == text_hash and isinstance(current.get("vector"), list) and current.get("vector"):
        return vec_path

    vec = _ollama_embed(text)
    if vec:
        _write_json(vec_path, {
            "id": "xian_shared_vector",
            "generated_at": _now_iso(),
            "model": EMBED_MODEL,
            "text_sha256": text_hash,
            "vector": vec,
        })
    return vec_path


def session_to_xian(session_text: str) -> Dict[str, Any]:
    vec_path = ensure_xian_vector()
    xian_vec = _read_json(vec_path, {}).get("vector", [])
    if not session_text.strip() or not xian_vec:
        return {"score": 0.0, "model": EMBED_MODEL, "available": False}
    # Cache: embeduj tylko gdy tekst się zmienił
    text_sha = hashlib.sha256(session_text.encode("utf-8", errors="ignore")).hexdigest()
    if _session_embed_cache["sha256"] == text_sha and _session_embed_cache["vector"]:
        sess_vec = _session_embed_cache["vector"]
    else:
        sess_vec = _ollama_embed(session_text)
        if sess_vec:
            _session_embed_cache["sha256"] = text_sha
            _session_embed_cache["vector"] = sess_vec
    if not sess_vec:
        return {"score": 0.0, "model": EMBED_MODEL, "available": False}
    return {
        "score": round(float(_cosine(sess_vec, xian_vec)), 6),
        "model": EMBED_MODEL,
        "available": True,
    }
