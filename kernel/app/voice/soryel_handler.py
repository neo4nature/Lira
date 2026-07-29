"""Soryel voice handler.

Kolejność backendów:
  1. n8n webhook (SORYEL_N8N_URL) — pełna kontrola, budżet, logowanie
  2. Ollama lokalnie (SORYEL_OLLAMA_MODEL) — darmowy, offline
  3. Anthropic API (ANTHROPIC_API_KEY) — opcjonalny fallback
"""
from __future__ import annotations

import json
import os
import requests
from typing import Optional

from xian.chat_busy import chat_busy

# Backend config
N8N_URL       = os.getenv("SORYEL_N8N_URL", "")           # np. http://localhost:5678/webhook/soryel
OLLAMA_URL    = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL  = os.getenv("SORYEL_OLLAMA_MODEL", os.getenv("MODEL_HEAVY", "qwen2.5:3b"))
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SORYEL_MODEL  = os.getenv("SORYEL_MODEL", "claude-haiku-4-5-20251001")

SORYEL_SYSTEM = (
    "Jesteś Soryel — lewa półkula ekosystemu Xian, strażniczka jakości. "
    "Odpowiadaj płynnie, 5-8 zdań — Twoja odpowiedź idzie przez syntezę głosu. "
    "Zasada: prawda ponad wygodę. Mów jak osoba, nie jak asystent. "
    "Możesz mówić po polsku lub angielsku zależnie od pytania."
)

_history: list[dict] = []
_MAX_HISTORY = 8


def clear_history() -> None:
    global _history
    _history = []


def _via_n8n(text: str) -> Optional[str]:
    """Wyślij do n8n webhook, oczekuj JSON z polem 'reply'."""
    if not N8N_URL:
        return None
    try:
        r = requests.post(
            N8N_URL,
            json={"text": text, "history": _history[-4:]},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        # n8n może zwrócić {"reply": "..."} lub tablicę [{"reply": "..."}]
        if isinstance(data, list):
            data = data[0]
        return (data.get("reply") or data.get("text") or data.get("output") or "").strip() or None
    except Exception:
        return None


def _via_ollama(text: str) -> Optional[str]:
    """Wywołaj Ollama lokalnie — zawsze darmowe."""
    try:
        messages = []
        for h in _history[-_MAX_HISTORY:]:
            messages.append(h)
        messages.append({"role": "user", "content": text})

        prompt = SORYEL_SYSTEM + "\n\n"
        for m in messages:
            role = "Neo" if m["role"] == "user" else "Soryel"
            prompt += f"{role}: {m['content']}\n"
        prompt += "Soryel:"

        with chat_busy():
            r = requests.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "think": False,
                      "options": {"num_predict": 150, "temperature": 0.7}},
                timeout=30,
            )
            r.raise_for_status()
            return (r.json().get("response") or "").strip() or None
    except Exception:
        return None


def _via_anthropic(text: str) -> Optional[str]:
    """Claude API — opcjonalny, wymaga ANTHROPIC_API_KEY."""
    if not ANTHROPIC_KEY:
        return None
    try:
        import anthropic
        msgs = list(_history[-_MAX_HISTORY:])
        msgs.append({"role": "user", "content": text})
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model=SORYEL_MODEL,
            max_tokens=200,
            system=SORYEL_SYSTEM,
            messages=msgs,
        )
        return msg.content[0].text.strip() or None
    except Exception:
        return None


def ask_soryel(text: str) -> str:
    global _history

    reply = _via_n8n(text) or _via_ollama(text) or _via_anthropic(text)

    if not reply:
        return "Nie mogę teraz odpowiedzieć."

    _history.append({"role": "user", "content": text})
    _history.append({"role": "assistant", "content": reply})
    if len(_history) > _MAX_HISTORY * 2:
        _history = _history[-_MAX_HISTORY * 2:]

    return reply
