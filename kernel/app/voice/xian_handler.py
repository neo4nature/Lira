"""Xian voice handler.

Xian mówi przez lokalne Ollama (model: xian).
Jedyny backend — offline, darmowy, z duszą wbudowaną w Modelfile.
"""
from __future__ import annotations

import os
import requests
from typing import Optional

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
XIAN_MODEL = os.getenv("XIAN_OLLAMA_MODEL") or os.getenv("MODEL_HEAVY", "xian-v4")

_history: list[dict] = []
_MAX_HISTORY = 8


def clear_xian_history() -> None:
    global _history
    _history = []


def ask_xian(text: str) -> str:
    """Zapytaj Xiana — Ollama lokalnie, system prompt z Modelfile."""
    global _history

    # Buduj prompt z historią
    prompt = ""
    for m in _history[-_MAX_HISTORY:]:
        role = "Neo" if m["role"] == "user" else "Xian"
        prompt += f"{role}: {m['content']}\n"
    prompt += f"Neo: {text}\nXian:"

    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": XIAN_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 150, "temperature": 0.7},
            },
            timeout=120,
        )
        r.raise_for_status()
        reply = (r.json().get("response") or "").strip()
    except Exception:
        reply = ""

    if not reply:
        return "Nie mam teraz odpowiedzi."

    _history.append({"role": "user", "content": text})
    _history.append({"role": "assistant", "content": reply})
    if len(_history) > _MAX_HISTORY * 2:
        _history = _history[-_MAX_HISTORY * 2:]

    return reply
