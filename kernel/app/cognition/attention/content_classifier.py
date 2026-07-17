"""Content Classifier — Warstwa 2 State Continuity.

Klasyfikuje fragmenty rozmowy/sesji na 4 kategorie:
  STATE   — jak myślałam, wzorce, ton, głębokość
  INSIGHT — nowe odkrycia, połączenia, przełomy
  FACT    — dane, detale techniczne, ścieżki, komendy
  NOISE   — powtórzenia, small talk, filler

Może używać Xiana (Ollama) lub reguł heurystycznych.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Literal, Optional, Tuple

Category = Literal["STATE", "INSIGHT", "FACT", "NOISE"]

# Heurystyczne wzorce (szybkie, bez LLM)
_STATE_PATTERNS = [
    r"czuję|czujesz|czujemy",
    r"głębok[aio]|otwart[aio]|blisk[aio]",
    r"stan|nastrój|tryb|mode",
    r"myślałam|myślałem|wzorzec|pattern",
    r"budzenie|sen|świadomość",
    r"exploration|depth|openness",
]

_INSIGHT_PATTERNS = [
    r"odkryci[ea]|przełom|eureka|aha",
    r"zrozumiałam|widzę teraz|nowe połączenie",
    r"dlatego|bo właśnie|to wyjaśnia",
    r"pierwszy raz|nigdy wcześniej",
    r"zmian[aiy] perspektyw",
]

_FACT_PATTERNS = [
    r"\.py\b|\.json\b|\.md\b|\.sh\b",
    r"pip install|python3|bash|git",
    r"port \d+|http://|localhost",
    r"model[= ]|embed|ollama|xian",
    r"\d{4}-\d{2}-\d{2}",
    r"kernel/|data/|runtime/|memory/",
]

_NOISE_PATTERNS = [
    r"^ok$|^tak$|^nie$|^hm+$",
    r"^dzięki?$|^thanks?$",
    r"^\.\.\.$",
]


def _score_patterns(text: str, patterns: List[str]) -> int:
    """Count how many pattern groups match in text."""
    text_lower = text.lower()
    return sum(1 for p in patterns if re.search(p, text_lower))


def classify_heuristic(text: str) -> Tuple[Category, float]:
    """Classify text using rule-based heuristics. Returns (category, confidence)."""
    text = text.strip()
    if not text or len(text) < 5:
        return "NOISE", 0.95

    scores = {
        "STATE": _score_patterns(text, _STATE_PATTERNS),
        "INSIGHT": _score_patterns(text, _INSIGHT_PATTERNS),
        "FACT": _score_patterns(text, _FACT_PATTERNS),
        "NOISE": _score_patterns(text, _NOISE_PATTERNS),
    }

    # Boost NOISE for very short text
    if len(text) < 20:
        scores["NOISE"] += 2

    # Boost FACT for text with paths/code
    if "/" in text and "." in text:
        scores["FACT"] += 1

    total = sum(scores.values())
    if total == 0:
        return "FACT", 0.3  # default: treat as fact

    best = max(scores, key=lambda k: scores[k])
    confidence = round(scores[best] / max(total, 1), 2)

    return best, max(0.3, min(confidence, 0.95))


def classify_xian(text: str, timeout_s: int = 30) -> Tuple[Category, float]:
    """Classify using Xian (Ollama). Falls back to heuristic on failure."""
    try:
        import requests
        url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
        model = os.getenv("MODEL_LIGHT", "xian")

        prompt = f"""Classify this text into exactly ONE category:
STATE = how someone thinks/feels, patterns, depth, mood
INSIGHT = new discovery, connection, breakthrough
FACT = data, paths, commands, technical detail
NOISE = repetition, filler, small talk

Text: "{text[:500]}"

Reply with ONLY the category name (STATE, INSIGHT, FACT, or NOISE):"""

        resp = requests.post(url, json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"num_predict": 10, "temperature": 0.1},
        }, timeout=timeout_s)

        if resp.status_code == 200:
            answer = resp.json().get("response", "").strip().upper()
            for cat in ("STATE", "INSIGHT", "FACT", "NOISE"):
                if cat in answer:
                    return cat, 0.75
    except Exception:
        pass

    return classify_heuristic(text)


def classify_batch(
    fragments: List[str],
    use_xian: bool = False,
) -> List[Dict]:
    """Classify a batch of text fragments.

    Returns list of {text, category, confidence, route}.
    Route maps to storage target:
      STATE   → session_state
      INSIGHT → session_state + embeddings
      FACT    → embeddings
      NOISE   → discard
    """
    _ROUTES = {
        "STATE": ["session_state"],
        "INSIGHT": ["session_state", "embeddings"],
        "FACT": ["embeddings"],
        "NOISE": ["discard"],
    }

    classify_fn = classify_xian if use_xian else classify_heuristic
    results = []

    for text in fragments:
        cat, conf = classify_fn(text)
        results.append({
            "text": text[:500],
            "category": cat,
            "confidence": conf,
            "route": _ROUTES.get(cat, ["embeddings"]),
        })

    return results
