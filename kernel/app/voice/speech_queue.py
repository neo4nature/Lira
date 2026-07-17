"""speech_queue.py — plik-kolejka dla TTS między Stop hookiem a run_voice.

Mechanizm:
  - Stop hook (speak_claude_response.py) wpisuje tekst do kolejki
  - run_voice.py (background thread) drenuje kolejkę i mówi przez _speak_soryel()
  - Gdy run_voice NIE działa → hook mówi bezpośrednio (fallback)

Plik kolejki: data/runtime/voice/speech_queue.jsonl
PID file:     data/runtime/voice/voice_agent.pid
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from typing import Optional

DATA_ROOT  = os.getenv("LIRA_DATA_ROOT", os.path.join(
    os.path.dirname(__file__), "..", "data"
))
VOICE_DIR  = os.path.join(DATA_ROOT, "runtime", "voice")
QUEUE_PATH = os.path.join(VOICE_DIR, "speech_queue.jsonl")
PID_PATH   = os.path.join(VOICE_DIR, "voice_agent.pid")

os.makedirs(VOICE_DIR, exist_ok=True)


# ── PID helpers ───────────────────────────────────────────────────────────────

def register_pid() -> None:
    """Zapisz PID bieżącego procesu (run_voice.py powinien to wywołać przy starcie)."""
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))


def unregister_pid() -> None:
    try:
        os.unlink(PID_PATH)
    except FileNotFoundError:
        pass


def voice_agent_running() -> bool:
    """Sprawdź czy run_voice.py aktualnie działa."""
    if not os.path.exists(PID_PATH):
        return False
    try:
        pid = int(open(PID_PATH).read().strip())
        # Sprawdź czy proces żyje
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, ValueError, OSError):
        # Martwy PID — wyczyść
        unregister_pid()
        return False


# ── Queue helpers ─────────────────────────────────────────────────────────────

def push(text: str, voice: Optional[str] = None, priority: int = 5) -> None:
    """Dodaj tekst do kolejki TTS."""
    entry = {
        "ts": time.time(),
        "text": text,
        "voice": voice,
        "priority": priority,
    }
    with open(QUEUE_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def pop_all(max_age_s: float = 30.0) -> list[dict]:
    """Wyciągnij i usuń wszystkie wpisy z kolejki (pomijaj starsze niż max_age_s)."""
    if not os.path.exists(QUEUE_PATH):
        return []

    now = time.time()
    fresh: list[dict] = []

    try:
        with open(QUEUE_PATH, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                lines = f.readlines()
                f.seek(0)
                f.truncate()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                age = now - float(entry.get("ts", 0))
                if age <= max_age_s:
                    fresh.append(entry)
            except Exception:
                pass

        # Sortuj po priorytecie (niższy = ważniejszy), potem po czasie
        fresh.sort(key=lambda e: (e.get("priority", 5), e.get("ts", 0)))
    except Exception:
        pass

    return fresh
