# app/agents/memory/memory_orchestrator.py
# Minimalny generator context_packet.json (v1) pod system Lira / MA
# Wersja: wpięta w config.py (DATA_ROOT/RUNTIME_DIR/MEMORY_DIR)

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import runtime_dir, memory_dir, shared_context_dir
from agents.memory.memory_loader import read_json, write_json
from agents.memory.semantic_retrieval import search as semantic_search

from config import paths

def _embeddings_ready() -> bool:
    """Return True if semantic embeddings index is available and declared ready by the system."""
    try:
        p = paths()
        # 1) Prefer bus flag (DecisionLoop publishes it)
        bus = read_json(Path(p["RUNTIME_DIR"]) / "state" / "bus_signals.json", default={})
        if isinstance(bus, dict):
            mem = bus.get("memory") if isinstance(bus.get("memory"), dict) else {}
            if mem.get("embeddings_ready") is True:
                return True
        # 2) Fallback: check index file exists and is non-trivial
        idx = Path(p.get("EMBEDDINGS_INDEX", Path(p["MEMORY_DIR"]) / "embeddings" / "embeddings_index.jsonl"))
        return idx.exists() and idx.stat().st_size > 0
    except Exception:
        return False


def _phase_allows_embeddings() -> bool:
    """Gate semantic retrieval by phase + energy hints.

    v0.2 rule:
      - embeddings allowed in ACT and INTEGRATE phases
      - but still blocked when DecisionLoop explicitly disables them
    """
    try:
        p = paths()
        st = Path(p["RUNTIME_DIR"]) / "state"
        phase = read_json(st / "phase_state.json", default={})
        if isinstance(phase, dict):
            ph = str(phase.get("phase") or "").upper()
            forced = str(os.getenv("LIRA_EMBEDDINGS_ALLOWED_PHASES", "ACT,INTEGRATE")).upper()
            allowed = {x.strip() for x in forced.split(",") if x.strip()} or {"ACT", "INTEGRATE"}
            if ph not in allowed:
                return False
        bus = read_json(st / "bus_signals.json", default={})
        if isinstance(bus, dict):
            en = bus.get("energy") if isinstance(bus.get("energy"), dict) else {}
            hints = en.get("policy_hints") if isinstance(en.get("policy_hints"), dict) else {}
            if hints.get("allow_embeddings") is False:
                return False
        return True
    except Exception:
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> Dict[str, Path]:
    rt = runtime_dir()
    st = rt / "state"
    rq = rt / "requests"
    st.mkdir(parents=True, exist_ok=True)
    rq.mkdir(parents=True, exist_ok=True)

    mem = memory_dir()
    led = mem / "ledger"
    led.mkdir(parents=True, exist_ok=True)

    ev = shared_context_dir() / "events"
    ev.mkdir(parents=True, exist_ok=True)

    return {"runtime": rt, "state": st, "requests": rq, "ledger": led, "events": ev}


def save_request(text: str, intent: str = "unknown", priority: str = "normal") -> Path:
    """
    Zapisuje request do data/runtime/requests/*.json i zwraca ścieżkę.
    """
    d = _ensure_dirs()
    trace_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    req = {
        "schema": "lira.request.v1",
        "trace_id": trace_id,
        "created_at": now_iso(),
        "text": text,
        "intent": intent,
        "priority": priority,
    }
    out = d["requests"] / f"{trace_id}.json"
    write_json(out, req)
    return out


def load_latest_request() -> Optional[Dict[str, Any]]:
    d = _ensure_dirs()
    files = sorted(d["requests"].glob("*.json"), reverse=True)
    if not files:
        return None
    try:
        return read_json(files[0], default=None)
    except Exception:
        return None


def simple_retrieval(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Retrieval (v2):
    - prefer semantic search via local embeddings index (Ollama embeddings) when enabled
    - fallback to keyword scan over ledger/events
    """
    d = _ensure_dirs()
    results: List[Dict[str, Any]] = []
    q = (query or "").strip().lower()
    if not q:
        return []

    # 1) Semantic retrieval (fast path)
    if os.getenv("LIRA_ENABLE_EMBEDDINGS", "1") == "1" and _embeddings_ready() and _phase_allows_embeddings():
        try:
            sem = semantic_search(query, top_k=top_k)
            if sem:
                return sem
        except Exception:
            pass

    def scan(folder: Path, source: str):
        for file in folder.glob("*.json"):
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
                if q in text.lower():
                    results.append({
                        "id": file.stem,
                        "score": 0.6,
                        "source": source,
                        "path": str(file),
                    })
            except Exception:
                pass

    scan(d["ledger"], "ledger")
    scan(d["events"], "event")

    return results[:top_k]


def build_context_packet(request: Dict[str, Any]) -> Dict[str, Any]:
    trace_id = request.get("trace_id") or f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    text = request.get("text", "")

    retrieval = simple_retrieval(text, top_k=5)

    packet = {
        "schema": "lira.context_packet.v1",
        "trace_id": trace_id,
        "created_at": now_iso(),
        "ttl_seconds": 240,
        "owner": "DecisionLoop",
        "producer": "memory_orchestrator",
        "request": {
            "text": text,
            "intent": request.get("intent", "unknown"),
            "priority": request.get("priority", "normal"),
        },
        "retrieval": {
            "embeddings_ready": _embeddings_ready(),
            "top_k": len(retrieval),
            "results": retrieval,
            "scoring": "relevance*recency",
        },
        "context": {"snippets": [], "assumptions": []},
        "risk": {"error_prediction": 0.2, "cause": "context_drift"},
    }
    return packet


def save_packet(packet: Dict[str, Any]) -> Path:
    d = _ensure_dirs()
    out = d["state"] / "context_packet.json"
    write_json(out, packet)
    return out


def run_once(text: str, intent: str = "unknown", priority: str = "normal") -> Path:
    """
    Jedno wywołanie: zapis requestu + zbudowanie i zapis context_packet.json.
    Zwraca ścieżkę do context_packet.json.
    """
    req_path = save_request(text=text, intent=intent, priority=priority)
    request = read_json(req_path, default={})
    packet = build_context_packet(request)
    return save_packet(packet)


if __name__ == "__main__":
    req = load_latest_request()
    if not req:
        print("Brak requestów.")
        raise SystemExit(0)
    out = save_packet(build_context_packet(req))
    print("Context packet zapisany:", out)