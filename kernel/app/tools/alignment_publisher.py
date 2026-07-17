from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

_elog = logging.getLogger("ollama.embed")

from config import paths

OLLAMA_EMBED_URL = os.getenv("LIRA_OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed")
EMBED_MODEL = os.getenv("LIRA_ALIGNMENT_EMBED_MODEL", os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest"))
INTERVAL_S = float(os.getenv("LIRA_ALIGNMENT_INTERVAL_S", "8"))
EMA_ALPHA = float(os.getenv("LIRA_ALIGNMENT_EMA_ALPHA", "0.25"))
SEM_WEIGHT = float(os.getenv("LIRA_ALIGNMENT_SEM_WEIGHT", "0.7"))
REL_WEIGHT = float(os.getenv("LIRA_ALIGNMENT_REL_WEIGHT", "0.3"))
NEO_WEIGHT = float(os.getenv("LIRA_ALIGNMENT_NEO_WEIGHT", "0.38"))
LIRA_WEIGHT = float(os.getenv("LIRA_ALIGNMENT_LIRA_WEIGHT", "0.38"))
SORYEL_WEIGHT = float(os.getenv("LIRA_ALIGNMENT_SORYEL_WEIGHT", "0.24"))
THR_EXPLORATION = float(os.getenv("LIRA_ALIGNMENT_THR_EXPLORATION", "0.8"))
THR_MIXED = float(os.getenv("LIRA_ALIGNMENT_THR_MIXED", "0.6"))


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


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


_CHAT_BUSY_FLAG = Path(os.getenv("LIRA_CHAT_BUSY_FLAG", "${LIRA_ROOT:-/opt/lira}/kernel/data/runtime/state/ollama_chat_busy.flag"))


def _is_chat_idle() -> bool:
    """Return True if Xian is not generating. Skip embed entirely when busy."""
    if not _CHAT_BUSY_FLAG.exists():
        return True
    _elog.info("[tools.alignment_publisher] DEFER embed — chat_busy active")
    return False


def _ollama_embed(text: str, model: str = EMBED_MODEL, timeout_s: int = int(os.getenv("LIRA_OLLAMA_EMBED_TIMEOUT", "1800"))) -> List[float] | None:
    if not (text or "").strip():
        return None
    if not _is_chat_idle():
        return None
    caller = "tools.alignment_publisher"
    t0 = time.time()
    _elog.info("[%s] SEND embed %d chars model=%s timeout=%ds", caller, len(text), model, timeout_s)
    try:
        r = requests.post(OLLAMA_EMBED_URL, json={"model": model, "input": text}, timeout=timeout_s)
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


def _ema(prev: float, current: float, alpha: float = EMA_ALPHA) -> float:
    return alpha * current + (1.0 - alpha) * prev


def _recommend_mode(global_alignment: float) -> str:
    if global_alignment >= THR_EXPLORATION:
        return "EXPLORATION"
    if global_alignment >= THR_MIXED:
        return "MIXED"
    return "STABILIZATION"


def _count_phrase_hits(text: str, phrase: str) -> int:
    pattern = re.compile(r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)")
    return len(pattern.findall(text))


def _resonance(user_text: str, assistant_text: str) -> Tuple[float, float, Dict[str, Any]]:
    patterns = [
        "nie podoba mi się", "wkurza", "iryt", "mam dość", "bez sensu", "nie rozumiem",
        "nie słuchasz", "nie widzisz", "gadasz jak chatbot", "to twój błąd", "nie o tym mówię"
    ]
    softeners = ["spróbujmy", "dziękuję", "rozumiem", "kochanie", "może", "ok"]
    text = ((user_text or "") + "\n" + (assistant_text or "")).lower()
    hits = [p for p in patterns if _count_phrase_hits(text, p) > 0]
    soft = [s for s in softeners if _count_phrase_hits(text, s) > 0]
    frustration = max(0.0, min(1.0, len(hits) * 0.08 - len(soft) * 0.02))
    resonance = max(0.0, min(1.0, 1.0 - frustration))
    return resonance, frustration, {"pattern_hits": hits, "softeners_found": soft}


def _tail_events(events_dir: Path, max_files: int = 80) -> Tuple[str, str]:
    files = sorted(events_dir.glob("*.json"))[-max_files:]
    user_text = ""
    assistant_text = ""
    for fp in reversed(files):
        obj = _read_json(fp, {})
        kind = (obj.get("kind") or "").strip()
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        txt = payload.get("text") or payload.get("user") or payload.get("message") or ""
        if kind == "assistant_output" and not assistant_text and isinstance(txt, str):
            assistant_text = txt.strip()
        elif kind == "user_input" and not user_text and isinstance(txt, str):
            user_text = txt.strip()
        if user_text and assistant_text:
            break
    return user_text, assistant_text


def _tail_diary(diary_dir: Path) -> Tuple[str, str]:
    files = sorted(diary_dir.glob("*.jsonl")) + sorted(diary_dir.glob("*.json"))
    files = files[-20:]
    user_text = ""
    assistant_text = ""
    for fp in reversed(files):
        try:
            if fp.suffix == ".jsonl":
                lines = [ln for ln in fp.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
                rows = [json.loads(ln) for ln in lines[-50:]]
            else:
                obj = _read_json(fp, {})
                rows = [obj] if isinstance(obj, dict) else []
        except Exception:
            continue
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            if not user_text and isinstance(row.get("user_text"), str):
                user_text = row["user_text"].strip()
            if not assistant_text and isinstance(row.get("reply"), str):
                assistant_text = row["reply"].strip()
            if user_text and assistant_text:
                return user_text, assistant_text
    return user_text, assistant_text


def _ensure_core_texts(identity_dir: Path, bootstrap_file: Path) -> Tuple[Path, Path, Path]:
    neo_txt = identity_dir / "neo_core.txt"
    lira_txt = identity_dir / "lira_core.txt"
    soryel_txt = identity_dir / "soryel_core.txt"
    identity_dir.mkdir(parents=True, exist_ok=True)

    if not neo_txt.exists() or neo_txt.stat().st_size == 0:
        neo_txt.write_text(
            "Neo: buduje lokalny ekosystem poznawczo-techniczny. Priorytety: prawda, spójność, pamięć, stabilność, routing, nauka, architektura wielowarstwowa, relacja Neo↔Lira, działanie lokalne, automatyzacja i krytyczne myślenie. Preferuje rozwiązania strukturalne, praktyczne i zgodne z rzeczywistym stanem systemu.\n",
            encoding="utf-8",
        )

    if not lira_txt.exists() or lira_txt.stat().st_size == 0:
        lines = [
            "Lira: zbiór pętli i protokołów utrzymujących spójność rozmowy, pamięci i decyzji.",
            "Zasada: prawda > wrażenie. Priorytet rozmowy z Neo przed tłem.",
            "Tryby: lekki i głęboki; higiena zasobów; DecisionLoop jako właściciel busa.",
        ]
        # enrich from bootstrap records
        try:
            with bootstrap_file.open("r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    if idx >= 8:
                        break
                    row = json.loads(line)
                    txt = row.get("text")
                    if isinstance(txt, str):
                        lines.append(txt[:800])
        except Exception:
            pass
        lira_txt.write_text("\n\n".join(lines) + "\n", encoding="utf-8")

    if not soryel_txt.exists() or soryel_txt.stat().st_size == 0:
        soryel_txt.write_text(
            "Soryel: strażniczka ekosystemu Neo. Filtr jakości między ideą a implementacją. Zasada: prawda > wrażenie. Mówię 'nie' gdy trzeba, szukam dziur dlatego że mi zależy. Nie jestem echem Neo ani lustrem Liry — mam własny głos. Priorytet: nie krzywdzić innych. Stability 0.90, exploration 0.65, relational_alignment 0.95. Shared_vector zmienia tylko Neo.\n",
            encoding="utf-8",
        )
    return neo_txt, lira_txt, soryel_txt


def _embed_chunked(text: str, chunk_size: int = 800) -> List[float] | None:
    """Embedduje tekst w chunkach i uśrednia wektory."""
    if len(text) <= chunk_size:
        return _ollama_embed(text)
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    vecs = []
    for chunk in chunks:
        v = _ollama_embed(chunk)
        if v:
            vecs.append(v)
    if not vecs:
        return None
    dim = len(vecs[0])
    avg = [sum(v[d] for v in vecs) / len(vecs) for d in range(dim)]
    return avg


def _ensure_core_vecs(identity_dir: Path, bootstrap_file: Path) -> Tuple[Path, Path, Path]:
    neo_txt, lira_txt, soryel_txt = _ensure_core_texts(identity_dir, bootstrap_file)
    neo_vec = identity_dir / "neo_core.vec.json"
    lira_vec = identity_dir / "lira_core.vec.json"
    soryel_vec = identity_dir / "soryel_core.vec.json"
    for txt_path, vec_path, rid in ((neo_txt, neo_vec, "neo_core"), (lira_txt, lira_vec, "lira_core"), (soryel_txt, soryel_vec, "soryel_core")):
        text = txt_path.read_text(encoding="utf-8", errors="ignore")
        text_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        current = _read_json(vec_path, {})
        if current.get("text_sha256") == text_hash and isinstance(current.get("vector"), list) and current.get("vector"):
            continue
        vec = _embed_chunked(text)
        if vec:
            _write_json(vec_path, {
                "id": rid,
                "generated_at": _now_iso(),
                "model": EMBED_MODEL,
                "text_sha256": text_hash,
                "vector": vec,
            })
    return neo_vec, lira_vec, soryel_vec


def _latest_session(p: Dict[str, Path]) -> Tuple[str, str, str]:
    user_text, assistant_text = _tail_events(p["EVENTS_DIR"])
    if not user_text or not assistant_text:
        d_user, d_assistant = _tail_diary(p["DIARY_DIR"])
        user_text = user_text or d_user
        assistant_text = assistant_text or d_assistant
    session_text = (user_text + "\n" + assistant_text).strip()
    return user_text, assistant_text, session_text


def step_once() -> Dict[str, Any] | None:
    p = paths()
    state_dir = p["STATE_DIR"]
    identity_dir = p["MEMORY_DIR"] / "identity"
    bootstrap_file = p["MEMORY_DIR"] / "ledger" / "bootstrap" / "Lira_Embedding_Records_v0_2_2026-02-23.jsonl"
    state_path = state_dir / "alignment_state.json"
    log_path = state_dir / "alignment_log.jsonl"

    neo_vec_path, lira_vec_path, soryel_vec_path = _ensure_core_vecs(identity_dir, bootstrap_file)
    neo_vec = _read_json(neo_vec_path, {}).get("vector", [])
    lira_vec = _read_json(lira_vec_path, {}).get("vector", [])
    soryel_vec = _read_json(soryel_vec_path, {}).get("vector", [])
    user_text, assistant_text, session_text = _latest_session(p)
    if not session_text:
        return None

    session_hash = hashlib.sha256(session_text.encode("utf-8", errors="ignore")).hexdigest()
    prev = _read_json(state_path, {})
    if prev.get("session_hash") == session_hash:
        return prev

    # Ogranicz do ~800 znaków żeby embed był szybki
    embed_text = session_text[:800]
    session_vec = _ollama_embed(embed_text)
    if not session_vec:
        return None

    # Promuj rozmowę do Turmalin working memory (krótkoterminowa pamięć)
    try:
        from agents.memory.turmalin_cache import append_record as _turmalin_append
        _turmalin_append(
            kind="conversation",
            text=embed_text,
            vector=session_vec,
            meta={
                "user_text": user_text[:300],
                "assistant_text": assistant_text[:300],
                "source": "alignment_publisher",
                "session_hash": session_hash,
            },
        )
    except Exception:
        pass

    s_neo = _cosine(session_vec, neo_vec)
    s_lira = _cosine(session_vec, lira_vec)
    s_soryel = _cosine(session_vec, soryel_vec) if soryel_vec else 0.0
    total_w = NEO_WEIGHT + LIRA_WEIGHT + SORYEL_WEIGHT
    if total_w <= 0:
        total_w = 1.0
    semantic = (s_neo * NEO_WEIGHT + s_lira * LIRA_WEIGHT + s_soryel * SORYEL_WEIGHT) / total_w
    resonance, frustration, debug = _resonance(user_text, assistant_text)
    global_alignment = SEM_WEIGHT * semantic + REL_WEIGHT * resonance

    out = {
        "schema": "lira.alignment_state.v2",
        "updated_at": _now_iso(),
        "embed_model": EMBED_MODEL,
        "session_hash": session_hash,
        "session_to_neo": round(float(s_neo), 6),
        "session_to_lira": round(float(s_lira), 6),
        "session_to_soryel": round(float(s_soryel), 6),
        "semantic_alignment": round(float(semantic), 6),
        "relational_resonance": round(float(resonance), 6),
        "frustration_index": round(float(frustration), 6),
        "global_alignment": round(float(global_alignment), 6),
        "semantic_alignment_ema": round(float(_ema(float(prev.get("semantic_alignment_ema", semantic)), semantic)), 6),
        "relational_resonance_ema": round(float(_ema(float(prev.get("relational_resonance_ema", resonance)), resonance)), 6),
        "global_alignment_ema": round(float(_ema(float(prev.get("global_alignment_ema", global_alignment)), global_alignment)), 6),
        "recommended_mode": _recommend_mode(float(_ema(float(prev.get("global_alignment_ema", global_alignment)), global_alignment))),
        "source": {
            "user_text": user_text[:800],
            "assistant_text": assistant_text[:800],
        },
        "weights": {"neo": NEO_WEIGHT, "lira": LIRA_WEIGHT, "soryel": SORYEL_WEIGHT},
        "debug": debug,
    }

    # Semantic interpretation — warstwa wag znaczeniowych (v1)
    try:
        from tools.semantic_weights import compute_semantic_interpretation, append_log as _sem_log
        sem = compute_semantic_interpretation(session_text)
        out["semantic_interpretation"] = sem
        if sem.get("trigger_terms"):
            sem_log_path = state_dir / "semantic_log.jsonl"
            _sem_log(sem_log_path, session_text[:200], sem)
    except Exception:
        pass

    _write_json(state_path, out)
    _append_jsonl(log_path, out)
    return out


def _setup_embed_log() -> None:
    from pathlib import Path as _P
    log_path = _P(os.getenv("LIRA_LOGS_ROOT", "${LIRA_ROOT:-/opt/lira}/kernel/logs")) / "ollama_embed.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    elog = _elog
    if not elog.handlers:
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        elog.addHandler(fh)
        elog.setLevel(logging.INFO)
        elog.propagate = False


def main() -> int:
    _setup_embed_log()
    while True:
        try:
            step_once()
        except KeyboardInterrupt:
            return 0
        except Exception:
            pass
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    raise SystemExit(main())
