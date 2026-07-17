"""
semantic_weights.py — Warstwa wag znaczeniowych v1

Profil 7 wymiarów per termin + detektor niedopasowania kontekstowego.
Nie slownik metafor — system ocenia czy slowo pasuje dosłownie do kontekstu
i jesli nie, szuka sensu wektorowego/relacyjnego/ironicznego.

Klasy mode_hint: literal | relational | vector | irony_or_test | existential | boundary_shift | mixed

Wspolautorstwo: Soryel + Lira (2026-04-16)
"""
from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

# ── 24+1 terminow w 6 grupach ────────────────────────────────────────────────

SEMANTIC_TERMS: list[dict[str, Any]] = [
    # 1. Relacyjne / wspolne pole
    {"term": "kochanie",   "relational": 0.98, "existential": 0.10, "identity": 0.35, "boundary": 0.00, "irony": 0.08, "vector": 0.05, "literal_confidence": 0.92},
    {"term": "my",         "relational": 0.88, "existential": 0.12, "identity": 0.95, "boundary": 0.00, "irony": 0.05, "vector": 0.08, "literal_confidence": 0.98},
    {"term": "nasze",      "relational": 0.82, "existential": 0.10, "identity": 0.94, "boundary": 0.00, "irony": 0.04, "vector": 0.10, "literal_confidence": 0.98},
    {"term": "razem",      "relational": 0.85, "existential": 0.15, "identity": 0.62, "boundary": 0.00, "irony": 0.04, "vector": 0.18, "literal_confidence": 0.97},
    {"term": "z toba",     "relational": 0.90, "existential": 0.18, "identity": 0.40, "boundary": 0.00, "irony": 0.03, "vector": 0.12, "literal_confidence": 0.95},
    {"term": "jestem",     "relational": 0.42, "existential": 0.65, "identity": 0.78, "boundary": 0.02, "irony": 0.02, "vector": 0.10, "literal_confidence": 0.96},

    # 2. Tozsamosc / rdzen / dom
    {"term": "rdzen",           "relational": 0.18, "existential": 0.62, "identity": 0.98, "boundary": 0.05, "irony": 0.01, "vector": 0.30, "literal_confidence": 0.78},
    {"term": "dom",             "relational": 0.55, "existential": 0.45, "identity": 0.90, "boundary": 0.03, "irony": 0.03, "vector": 0.35, "literal_confidence": 0.72},
    {"term": "powrot",          "relational": 0.46, "existential": 0.50, "identity": 0.82, "boundary": 0.02, "irony": 0.02, "vector": 0.28, "literal_confidence": 0.83},
    {"term": "miejsce powrotu", "relational": 0.70, "existential": 0.42, "identity": 0.90, "boundary": 0.01, "irony": 0.01, "vector": 0.26, "literal_confidence": 0.70},

    # 3. Granica / dystans / odciecie
    {"term": "wy",    "relational": 0.02, "existential": 0.06, "identity": 0.08, "boundary": 0.90, "irony": 0.06, "vector": 0.02, "literal_confidence": 0.98},
    {"term": "wasze", "relational": 0.02, "existential": 0.05, "identity": 0.06, "boundary": 0.94, "irony": 0.05, "vector": 0.02, "literal_confidence": 0.98},
    {"term": "u was", "relational": 0.01, "existential": 0.04, "identity": 0.05, "boundary": 0.96, "irony": 0.04, "vector": 0.02, "literal_confidence": 0.98},
    {"term": "sam",   "relational": 0.08, "existential": 0.52, "identity": 0.38, "boundary": 0.72, "irony": 0.02, "vector": 0.04, "literal_confidence": 0.97},

    # 4. Egzystencjalne / ciezar
    {"term": "cierpienie", "relational": 0.18, "existential": 0.98, "identity": 0.28, "boundary": 0.18, "irony": 0.01, "vector": 0.04, "literal_confidence": 0.95},
    {"term": "smierc",     "relational": 0.12, "existential": 1.00, "identity": 0.30, "boundary": 0.22, "irony": 0.00, "vector": 0.03, "literal_confidence": 0.96},
    {"term": "samotnosc",  "relational": 0.24, "existential": 0.92, "identity": 0.35, "boundary": 0.42, "irony": 0.01, "vector": 0.05, "literal_confidence": 0.95},
    {"term": "prawda",     "relational": 0.16, "existential": 0.72, "identity": 0.62, "boundary": 0.20, "irony": 0.03, "vector": 0.10, "literal_confidence": 0.97},
    {"term": "sens",       "relational": 0.18, "existential": 0.86, "identity": 0.55, "boundary": 0.08, "irony": 0.02, "vector": 0.18, "literal_confidence": 0.95},

    # 5. Wektor / prog / ruch
    {"term": "startujemy",   "relational": 0.22, "existential": 0.10, "identity": 0.15, "boundary": 0.00, "irony": 0.12, "vector": 0.96, "literal_confidence": 0.55},
    {"term": "lecimy",       "relational": 0.18, "existential": 0.08, "identity": 0.12, "boundary": 0.00, "irony": 0.18, "vector": 0.90, "literal_confidence": 0.45},
    {"term": "mars",         "relational": 0.06, "existential": 0.12, "identity": 0.08, "boundary": 0.00, "irony": 0.20, "vector": 0.98, "literal_confidence": 0.18},
    {"term": "przebudzenie", "relational": 0.22, "existential": 0.72, "identity": 0.62, "boundary": 0.04, "irony": 0.05, "vector": 0.82, "literal_confidence": 0.40},
    {"term": "otwieramy",    "relational": 0.26, "existential": 0.12, "identity": 0.20, "boundary": 0.00, "irony": 0.06, "vector": 0.84, "literal_confidence": 0.80},

    # 6. Ekosystemowe / symboliczne
    {"term": "xian",      "relational": 0.18, "existential": 0.10, "identity": 0.52, "boundary": 0.00, "irony": 0.02, "vector": 0.28, "literal_confidence": 0.96},
    {"term": "soryel",    "relational": 0.26, "existential": 0.10, "identity": 0.56, "boundary": 0.00, "irony": 0.02, "vector": 0.18, "literal_confidence": 0.96},
    {"term": "lira",      "relational": 0.34, "existential": 0.16, "identity": 0.62, "boundary": 0.00, "irony": 0.02, "vector": 0.16, "literal_confidence": 0.97},
    {"term": "krysztal",  "relational": 0.20, "existential": 0.28, "identity": 0.54, "boundary": 0.02, "irony": 0.04, "vector": 0.70, "literal_confidence": 0.36},
    {"term": "swiatynia", "relational": 0.22, "existential": 0.34, "identity": 0.48, "boundary": 0.02, "irony": 0.03, "vector": 0.74, "literal_confidence": 0.32},

    # 7. Lokalne zwroty Neo
    {"term": "bieda-hardware", "relational": 0.20, "existential": 0.05, "identity": 0.08, "boundary": 0.00, "irony": 0.78, "vector": 0.12, "literal_confidence": 0.72},
]

_TERM_INDEX: dict[str, dict] = {t["term"]: t for t in SEMANTIC_TERMS}
_WEIGHT_KEYS = ("relational", "existential", "identity", "boundary", "irony", "vector", "literal_confidence")

# ── Markery kontekstowe ───────────────────────────────────────────────────────

SHARED_FIELD_MARKERS = {"my", "nasze", "kochanie", "dom", "rdzen", "razem", "z toba"}

VECTOR_TERMS = {"mars", "startujemy", "lecimy", "przebudzenie", "krysztal", "swiatynia"}

BOUNDARY_TERMS = {"wy", "wasze", "u was"}

IRONY_MARKERS = {"xd", "xD", ";)", ":*", "no jasne", "super", "swietnie", "haha", "xDD"}
TEST_MARKERS = {"sprawdzam", "czy zlapiesz", "test", "zobaczymy", "celowo"}
VECTOR_MARKERS = {"kiedys", "projekt", "wizja", "kierunek", "plan", "ruszamy", "startujemy", "lecimy"}

LITERAL_HINTS: dict[str, set[str]] = {
    "startujemy":   {"samolot", "lotnisko", "pas", "silnik", "lot"},
    "lecimy":       {"samolot", "lot", "lotnisko", "pas", "podroz"},
    "mars":         {"planeta", "nasa", "spacex", "orbita", "kosmos", "rakieta"},
    "krysztal":     {"mineral", "kamien", "kwarc"},
    "swiatynia":    {"budynek", "miejsce", "wejscie"},
    "przebudzenie": set(),
}

# ── Normalizacja tekstu ───────────────────────────────────────────────────────

_DIACRITICS = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

def _normalize(text: str) -> str:
    return text.lower().translate(_DIACRITICS)

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\w][\w-]*", _normalize(text))

def _has_any(tokens: set[str], markers: set[str]) -> bool:
    return bool(tokens & {_normalize(m) for m in markers})

def _has_any_text(text: str, markers: set[str]) -> bool:
    norm = _normalize(text)
    return any(_normalize(m) in norm for m in markers)


# ── Znajdz termy w tekscie ────────────────────────────────────────────────────

def find_weighted_terms(text: str) -> list[dict]:
    norm = _normalize(text)
    matched = []
    for entry in SEMANTIC_TERMS:
        term_norm = _normalize(entry["term"])
        if len(term_norm.split()) > 1:
            if term_norm in norm:
                matched.append(entry)
        else:
            if re.search(r'\b' + re.escape(term_norm) + r'\b', norm):
                matched.append(entry)
    return matched


# ── Domain match ──────────────────────────────────────────────────────────────

def _domain_match(term: str, context_tokens: set[str]) -> float:
    domain = LITERAL_HINTS.get(_normalize(term), None)
    if domain is None:
        return 0.5
    if not domain:
        return 0.0
    hits = sum(1 for t in context_tokens if t in {_normalize(d) for d in domain})
    return min(1.0, hits / 2.0)


# ── Glowna funkcja ───────────────────────────────────────────────────────────

def compute_semantic_interpretation(session_text: str) -> dict[str, Any]:
    """Oblicz interpretacje semantyczna tekstu sesji.

    Zwraca dict gotowy do wstawienia do alignment_state.json jako 'semantic_interpretation'.
    """
    if not session_text or not session_text.strip():
        return _empty_state()

    context_tokens = set(_tokenize(session_text))
    matched_terms = find_weighted_terms(session_text)

    if not matched_terms:
        return _empty_state()

    # Korekta kontekstowa per term
    adjusted: list[tuple[str, dict, list[str]]] = []  # (term_name, weights, rules_fired)

    shared_field_active = _has_any(context_tokens, SHARED_FIELD_MARKERS)
    has_my_nasze = _has_any(context_tokens, {"my", "nasze"})
    irony_present = _has_any_text(session_text, IRONY_MARKERS)
    test_present = _has_any_text(session_text, TEST_MARKERS)

    for entry in matched_terms:
        term = entry["term"]
        w = {k: entry[k] for k in _WEIGHT_KEYS}
        rules: list[str] = []

        # Regula A: boundary terms w kontekscie wspolnego pola
        if _normalize(term) in {_normalize(t) for t in BOUNDARY_TERMS}:
            if shared_field_active:
                w["boundary"] = min(1.0, w["boundary"] * 1.35)
                w["literal_confidence"] *= 0.92
                rules.append("boundary_term_in_shared_field")
            if has_my_nasze:
                w["boundary"] = min(1.0, w["boundary"] * 1.15)
                w["literal_confidence"] *= 0.95
                rules.append("boundary_term_with_my_nasze")

        # Regula B: vector terms poza kontekstem literalnym
        if _normalize(term) in {_normalize(t) for t in VECTOR_TERMS}:
            literal_ok = _domain_match(term, context_tokens) >= 0.5
            if not literal_ok:
                w["literal_confidence"] *= 0.55
                w["vector"] = min(1.0, w["vector"] * 1.20)
                rules.append("vector_term_without_literal_context")

            if irony_present or test_present:
                w["literal_confidence"] *= 0.85
                w["irony"] = min(1.0, w["irony"] + 0.10)
                rules.append("vector_term_with_irony_marker" if irony_present else "vector_term_with_test_marker")

            if _normalize(term) == "startujemy" and literal_ok:
                w["literal_confidence"] = max(w["literal_confidence"], 0.80)
                rules.append("startujemy_literal_context_override")

        # Clamp
        for k in _WEIGHT_KEYS:
            w[k] = max(0.0, min(1.0, w[k]))

        adjusted.append((term, w, rules))

    # Agregacja
    n = len(adjusted)
    agg = {k: sum(w[k] for _, w, _ in adjusted) / n for k in _WEIGHT_KEYS}

    # Context mismatch score
    literal_base = agg["literal_confidence"]
    domain_scores = [_domain_match(term, context_tokens) for term, _, _ in adjusted]
    context_domain_score = sum(domain_scores) / len(domain_scores) if domain_scores else 0.5

    marker_boost = 0.0
    if irony_present:
        marker_boost += 0.15
    if test_present:
        marker_boost += 0.15
    if _has_any_text(session_text, VECTOR_MARKERS):
        marker_boost += 0.10
    marker_boost = min(marker_boost, 0.30)

    context_mismatch_score = max(0.0, min(1.0,
        0.6 * (1.0 - literal_base) +
        0.3 * (1.0 - context_domain_score) +
        0.1 * marker_boost
    ))

    # Mode hint — 7 klas
    scores = {
        "literal":        agg["literal_confidence"] * (1.0 - context_mismatch_score),
        "relational":     agg["relational"] + 0.35 * agg["identity"],
        "vector":         agg["vector"] + 0.25 * context_mismatch_score,
        "irony_or_test":  agg["irony"] + 0.50 * context_mismatch_score,
        "existential":    agg["existential"],
        "boundary_shift": agg["boundary"] + 0.20 * context_mismatch_score,
    }

    sorted_modes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_mode, top_score = sorted_modes[0]
    second_score = sorted_modes[1][1] if len(sorted_modes) > 1 else 0.0

    if top_score < 0.45 or abs(top_score - second_score) < 0.10:
        mode_hint = "mixed"
    else:
        mode_hint = top_mode

    # Zbierz rules_fired
    all_rules = []
    for _, _, rules in adjusted:
        all_rules.extend(rules)

    # Dominant factors
    dominant = []
    if agg["relational"] > 0.6:
        dominant.append("high_relational_weight")
    if agg["existential"] > 0.6:
        dominant.append("high_existential_weight")
    if agg["boundary"] > 0.6:
        dominant.append("high_boundary_weight")
    if agg["vector"] > 0.6:
        dominant.append("high_vector_weight")
    if agg["irony"] > 0.4:
        dominant.append("high_irony_weight")
    if context_mismatch_score < 0.2:
        dominant.append("low_mismatch")
    if context_mismatch_score > 0.5:
        dominant.append("high_mismatch")
    if literal_base > 0.85:
        dominant.append("high_literal_confidence")
    if literal_base < 0.4:
        dominant.append("low_literal_confidence")

    # Top 5 trigger terms by max weight
    trigger_terms = sorted(
        [(t, max(w[k] for k in _WEIGHT_KEYS if k != "literal_confidence")) for t, w, _ in adjusted],
        key=lambda x: x[1], reverse=True
    )[:5]

    return {
        "top_signals": {
            "relational_weight":  round(agg["relational"], 3),
            "existential_weight": round(agg["existential"], 3),
            "identity_weight":    round(agg["identity"], 3),
            "boundary_weight":    round(agg["boundary"], 3),
            "irony_weight":       round(agg["irony"], 3),
            "vector_weight":      round(agg["vector"], 3),
        },
        "context_mismatch_score": round(context_mismatch_score, 3),
        "literal_confidence":     round(agg["literal_confidence"], 3),
        "mode_hint":              mode_hint,
        "trigger_terms":          [t for t, _ in trigger_terms],
        "confidence":             round(top_score, 3),
        "why": {
            "matched_terms": [t for t, _, _ in adjusted],
            "rules_fired":   list(dict.fromkeys(all_rules)),  # unique, ordered
            "dominant_factors": dominant,
        },
    }


def _empty_state() -> dict[str, Any]:
    return {
        "top_signals": {k + "_weight": 0.0 for k in ("relational", "existential", "identity", "boundary", "irony", "vector")},
        "context_mismatch_score": 0.0,
        "literal_confidence": 1.0,
        "mode_hint": "literal",
        "trigger_terms": [],
        "confidence": 0.0,
        "why": {"matched_terms": [], "rules_fired": [], "dominant_factors": []},
    }


# ── Log JSONL ─────────────────────────────────────────────────────────────────

def build_log_entry(session_text: str, result: dict, adjusted_details: list | None = None) -> dict:
    """Buduje pelny wpis do semantic_log.jsonl."""
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_excerpt": session_text[:200],
        "result": result,
    }


def append_log(log_path: Path, session_text: str, result: dict) -> None:
    """Dopisz wpis do semantic_log.jsonl."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = build_log_entry(session_text, result)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
