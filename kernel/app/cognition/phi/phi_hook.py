from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .transduction_phi_layer import TransductionPhiLayer
from .cognitive_phi_layer import CognitivePhiLayer


@dataclass
class PhiAnalysis:
    paradigm: str
    tension: bool
    bridge: Optional[str] = None


_layer = TransductionPhiLayer()
_layer_v2 = CognitivePhiLayer()


def analyze_text(text: str) -> Dict[str, Any]:
    """Return simple paradigm tag for a single text."""
    paradigm = _layer.detect_paradigms(text)
    return {"paradigm": paradigm}


def analyze_pair(prev_text: str, new_text: str) -> PhiAnalysis:
    """Detect tension and (optionally) generate a short bridge."""
    paradigm_a = _layer.detect_paradigms(prev_text)
    paradigm_b = _layer.detect_paradigms(new_text)
    tension = _layer.recognize_tension(paradigm_a, paradigm_b)
    bridge = None
    if tension:
        out = _layer.generate_response(prev_text, new_text)
        # generate_response returns dict with 'message'
        bridge = out.get("response") or out.get("message")
    return PhiAnalysis(
        paradigm=paradigm_b,
        tension=tension,
        bridge=bridge,
    )


def analyze_text_v2(text: str) -> Dict[str, Any]:
    return _layer_v2.classify(text)


def analyze_pair_v2(prev_text: str, new_text: str) -> Dict[str, Any]:
    a = _layer_v2.classify(prev_text)
    b = _layer_v2.classify(new_text)
    shift = a.get("paradigm") != b.get("paradigm") and "unknown" not in {a.get("paradigm"), b.get("paradigm")}
    confidence = max(float(a.get("confidence", 0.0)), float(b.get("confidence", 0.0)))
    bridge = None
    if shift:
        bridge = f"Bridge {a.get('paradigm')} → {b.get('paradigm')}: przełącz tryb zgodnie z dowodami semantycznymi, nie tylko słowami-kluczami."
    return {
        "schema": "lira.phi_2b2c.v1",
        "paradigm_a": a.get("paradigm"),
        "paradigm_b": b.get("paradigm"),
        "shift_detected": shift,
        "confidence": round(confidence, 6),
        "analysis_a": a,
        "analysis_b": b,
        "bridge": bridge,
    }
