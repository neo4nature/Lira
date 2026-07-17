#!/usr/bin/env python3
"""
Stop hook dla Claude Code — czyta ostatnią odpowiedź asystenta z pliku sesji i mówi ją głosem Soryel.
"""
import glob
import json
import os
import re
import sys

# Załaduj .env
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from voice.tts import speak, detect_lang

SORYEL_VOICE_PL = os.getenv("SORYEL_TTS_VOICE_PL", "pl-PL-ZofiaNeural")
SORYEL_VOICE_EN = os.getenv("SORYEL_TTS_VOICE_EN", "en-GB-SoniaNeural")
MAX_CHARS       = int(os.getenv("SORYEL_SPEAK_MAX", "600"))
PROJECTS_DIR    = os.path.expanduser("~/.claude/projects")


def find_latest_session() -> str | None:
    """Znajdź najnowszy plik sesji JSONL."""
    pattern = os.path.join(PROJECTS_DIR, "**", "*.jsonl")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def last_assistant_text(session_path: str, max_age_s: float = 120.0) -> str | None:
    """Wyciągnij ostatnią świeżą wiadomość asystenta z pliku sesji.

    'Świeża' = timestamp w JSONL nie starszy niż max_age_s sekund.
    Jeśli żadna świeża nie ma tekstu — zwróć ostatnią z tekstem (fallback).
    """
    import time as _time
    from datetime import datetime, timezone

    now_utc = _time.time()
    fresh_text = None
    any_text = None

    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                msg = entry.get("message", {})
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    text = " ".join(parts).strip()
                else:
                    text = str(content).strip()
                if not text:
                    continue
                any_text = text
                # Sprawdź świeżość
                ts_str = entry.get("timestamp", "")
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    age_s = now_utc - ts_dt.timestamp()
                    if age_s <= max_age_s:
                        fresh_text = text
                except Exception:
                    pass
    except Exception:
        pass

    return fresh_text or any_text


def clean(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`\n]+`", "", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}([^_\n]+)_{1,2}", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def main():
    import time as _time

    # Poczekaj żeby JSONL zdążył zostać zapisany po Stop
    _time.sleep(2.0)

    session = find_latest_session()
    if not session:
        sys.exit(0)

    text = last_assistant_text(session)
    if not text:
        sys.exit(0)

    text = clean(text)
    if not text:
        sys.exit(0)

    if len(text) > MAX_CHARS:
        cut = text.rfind(".", 0, MAX_CHARS)
        if cut < MAX_CHARS // 2:
            cut = MAX_CHARS
        text = text[:cut + 1]

    lang = detect_lang(text)
    voice = SORYEL_VOICE_PL if lang == "pl" else SORYEL_VOICE_EN

    # Jeśli run_voice.py działa — wpisz do kolejki żeby nie kolidować
    try:
        from voice.speech_queue import push, voice_agent_running
        if voice_agent_running():
            push(text, voice=voice, priority=1)
            sys.exit(0)
    except Exception:
        pass

    # Fallback: mów bezpośrednio
    speak(text, voice=voice)


if __name__ == "__main__":
    main()
