import json
import os
import time
from pathlib import Path
from datetime import datetime
from config import ensure_dirs, paths
from channeling.channel_router import ChannelRouter
from agents.core.queue_utils import enqueue, make_task

def _maybe_tts(text: str):
    """Optional TTS in text-mode. Enable with TEXT_MODE_TTS=1."""
    if os.getenv("TEXT_MODE_TTS", "0").strip() not in {"1", "true", "True", "yes", "YES"}:
        return
    try:
        from voice.tts import speak
        speak(text)
    except Exception as _:
        # don't break text loop if TTS is missing
        return

def now():
    return datetime.now().isoformat(timespec="seconds")

def read_text(p: Path, limit=1400):
    try:
        return (p.read_text(encoding="utf-8", errors="ignore") or "")[:limit]
    except Exception:
        return ""

def load_memory_snippet(max_files=5):
    p = paths()
    idx_path = p["MEMORY_INDEX"]
    if not idx_path.exists():
        return ""
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8", errors="ignore") or "{}")
    except Exception:
        return ""

    files = idx.get("files", [])[:max_files]
    chunks = []
    for f in files:
        rel = f.get("path")
        if not rel:
            continue
        fp = Path(p["MEMORY_DIR"]) / rel
        if fp.exists():
            chunks.append(f"### {rel}\n{read_text(fp)}")
    return "\n\n".join(chunks).strip()


def load_identity_snippet(max_lines: int = 8) -> str:
    """
    Lightweight Identity-RAG: load static identity_pack.jsonl and format it.
    (Full retrieval lives in kernel/scripts/identity_retrieval.py.)
    """
    p = paths()
    root = Path(p["PROJECT_ROOT"]).resolve().parent
    pack = root / "data" / "memory" / "ledger" / "identity_pack.jsonl"
    if not pack.exists():
        return ""
    lines = []
    try:
        for line in pack.read_text(encoding="utf-8", errors="ignore").splitlines()[:max_lines]:
            try:
                obj = json.loads(line)
                txt = obj.get("text")
                if isinstance(txt, str) and txt.strip():
                    lines.append(f"- {txt.strip()}")
            except Exception:
                continue
    except Exception:
        return ""
    return "\n".join(lines).strip()

def log_event(kind: str, payload: dict):
    p = paths()
    events_dir = p["EVENTS_DIR"]
    events_dir.mkdir(parents=True, exist_ok=True)
    out = events_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{kind}.json"
    out.write_text(json.dumps({"ts": now(), "kind": kind, "payload": payload}, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

def build_prompt(user_text: str):
    ident = load_identity_snippet()
    mem = load_memory_snippet()
    if mem:
        system = (
            "IDENTITY (stałe zasady):\n"
            f"{ident}\n\n" if ident else ""
        ) + (
            "KONTEKST PAMIĘCI (skrócony):\n"
            f"{mem}\n\n"
            "Zasada: używaj pamięci tylko gdy pomaga; nie zmyślaj faktów."
        )
        return system + "\n\nUŻYTKOWNIK:\n" + user_text
    return user_text

def main():
    ensure_dirs()
    print("Lira Heartbeat (text) — wpisz 'exit' aby wyjść.\n")
    while True:
        user_text = input("NEO> ").strip()
        if user_text.lower() in {"exit", "quit"}:
            break

        prompt = build_prompt(user_text)
        ev_in = log_event("user_input", {"text": user_text})

        t0 = time.time()
        try:
            result = ChannelRouter().handle(user_text, local_text=prompt)
            answer = result.reply
        except Exception as e:
            answer = f"[ERROR] Router/Ollama: {e}"
        dt = round(time.time() - t0, 2)

        ev_out = log_event("assistant_output", {"text": answer, "latency_s": dt})

        # Deduplikacja: max 3 conversation_turn w kolejce
        from agents.core.queue_utils import load_queue
        queue = load_queue()
        pending = [t for t in queue if t.get("kind") == "conversation_turn"
                   and t.get("status", "pending") == "pending"]
        if len(pending) < 3:
            enqueue(make_task(
                kind="conversation_turn",
                source="heartbeat",
                payload={
                    "user": user_text,
                    "assistant": answer,
                    "event_in": str(ev_in),
                    "event_out": str(ev_out),
                    "latency_s": dt,
                },
                importance=3,
                tags=["conversation"]
            ))
        else:
            print(f"  [queue saturated: {len(pending)} pending, skipping]")

        print(f"\nLIRA> {answer}\n")
        _maybe_tts(answer)

if __name__ == "__main__":
    main()
