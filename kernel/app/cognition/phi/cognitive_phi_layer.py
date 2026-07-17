from __future__ import annotations

import math
from typing import Any, Dict, List

from agents.memory.semantic_retrieval import search as semantic_search
from config import paths
import json


class CognitivePhiLayer:
    """Phase 2A scaffold.

    Uses semantic retrieval plus lightweight evidence features to classify a text
    into a small set of operational paradigms. This is intentionally modest:
    the goal is to make Phi depend on stable runtime data rather than keyword magic.
    """

    LABELS = {
        "system_architecture": ["decisionloop", "bus", "routing", "policy", "queue", "ollama", "mcl", "loop"],
        "reflective_relational": ["neo", "lira", "relacja", "spójność", "prawda", "zaufanie", "kotwica"],
        "execution_repair": ["błąd", "patch", "napraw", "test", "import", "moduł", "endpoint"],
        "field_symbolic": ["świadomość", "pole", "phi", "paradygmat", "transdukcja", "symbol"],
    }

    def _keyword_score(self, text: str, words: List[str]) -> float:
        tx = (text or "").lower()
        if not tx:
            return 0.0
        hits = sum(1 for w in words if w in tx)
        return hits / max(1, len(words))

    def _retrieval_votes(self, text: str, top_k: int = 6) -> Dict[str, float]:
        votes = {k: 0.0 for k in self.LABELS}
        try:
            rows = semantic_search(text, top_k=top_k)
        except Exception:
            rows = []
        for row in rows:
            chunk = str(row.get("text") or "")
            score = float(row.get("score") or 0.0)
            for label, words in self.LABELS.items():
                votes[label] += score * self._keyword_score(chunk, words)
        return votes

    def classify(self, text: str) -> Dict[str, Any]:
        kw = {label: self._keyword_score(text, words) for label, words in self.LABELS.items()}
        rv = self._retrieval_votes(text)
        combined = {label: round(0.55 * kw[label] + 0.45 * rv[label], 6) for label in self.LABELS}
        label = max(combined, key=combined.get) if combined else "unknown"
        confidence = float(combined.get(label, 0.0))
        if confidence < 0.12:
            label = "unknown"
        return {
            "schema": "lira.phi_2a.v1",
            "paradigm": label,
            "confidence": round(confidence, 6),
            "evidence": {
                "keyword": kw,
                "retrieval": rv,
                "combined": combined,
            },
            "retrieval_used": True,
        }
