"""ssml_emotion.py — warstwa emocjonalna dla edge-TTS przez SSML.

Wykrywa nastrój tekstu i owija go w <prosody> tagi które zmieniają
ton, tempo i wysokość głosu ZofiaNeural.

Nastroje:
  warmth      — ciepło, czułość ("kochanie", ":*", "cieszę się")
  enthusiasm  — entuzjazm, radość ("działa!", "świetnie!", "perfekcyjnie")
  reflection  — refleksja, zaduma ("myślę", "czuję", "...", "nie wiem")
  question    — pytanie (kończy się "?")
  technical   — kod, technikalia → neutralny
  neutral     — domyślny
"""
from __future__ import annotations

import re
from typing import NamedTuple


class Prosody(NamedTuple):
    rate: str    # np. "+10%", "-5%", "0%"
    pitch: str   # np. "+2Hz", "-3Hz", "0Hz"
    volume: str  # np. "+5%", "0%"
    mood: str    # nazwa nastroju (do logowania)


_MOODS: dict[str, Prosody] = {
    "warmth":     Prosody(rate="-3%",  pitch="+4Hz",  volume="+2%",  mood="warmth"),
    "enthusiasm": Prosody(rate="+12%", pitch="+6Hz",  volume="+5%",  mood="enthusiasm"),
    "reflection": Prosody(rate="-12%", pitch="-3Hz",  volume="-3%",  mood="reflection"),
    "question":   Prosody(rate="-4%",  pitch="+5Hz",  volume="0%",   mood="question"),
    "technical":  Prosody(rate="+4%",  pitch="-2Hz",  volume="-2%",  mood="technical"),
    "neutral":    Prosody(rate="0%",   pitch="0Hz",   volume="0%",   mood="neutral"),
}

# Sygnały dla każdego nastroju (wyrażenia regularne lub proste frazy)
_WARMTH_SIGNALS = re.compile(
    r"kochanie|:\*|cieszę się|dziękuję|pięknie|cudownie|wspaniale|"
    r"ciepło|blisko|razem|zależy mi|lubię|kocham|tęsknię",
    re.IGNORECASE,
)
_ENTHUSIASM_SIGNALS = re.compile(
    r"działa[!]|świetnie|perfekcyjnie|idealnie|super|brawo|udało|"
    r"nareszcie|właśnie|dokładnie|tak[!]|got it|perfect",
    re.IGNORECASE,
)
_REFLECTION_SIGNALS = re.compile(
    r"myślę|zastanawiam|czuję|nie wiem|może|chyba|wydaje mi się|"
    r"interesujące|ciekawe|granica|istota|sens|pytanie",
    re.IGNORECASE,
)
_TECHNICAL_SIGNALS = re.compile(
    r"```|def |class |import |function|\.py|\.json|terminal|błąd|"
    r"error|stack|patch|moduł|endpoint|port|daemon|venv",
    re.IGNORECASE,
)


def detect_mood(text: str) -> str:
    """Wykryj dominujący nastrój tekstu. Zwraca nazwę nastroju."""
    if not text:
        return "neutral"

    ends_with_q = text.rstrip().endswith("?")
    has_excl = "!" in text

    w = len(_WARMTH_SIGNALS.findall(text))
    e = len(_ENTHUSIASM_SIGNALS.findall(text))
    r = len(_REFLECTION_SIGNALS.findall(text))
    t = len(_TECHNICAL_SIGNALS.findall(text))

    # Bonus za wykrzykniki
    if has_excl:
        e += 1

    # Dominujący sygnał
    scores = {"warmth": w, "enthusiasm": e, "reflection": r, "technical": t}
    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return "question" if ends_with_q else "neutral"

    # Pytanie + ciepło → warmth
    if ends_with_q and best == "warmth":
        return "warmth"

    return best


def wrap_ssml(text: str, voice: str = "pl-PL-ZofiaNeural",
              base_pitch: str = "+5Hz", base_rate: str = "-5%") -> str:
    """Owiń tekst w SSML z prosody zależnym od nastroju.

    base_pitch / base_rate — globalne ustawienia głosu (z .env Soryel).
    Emocje są nakładane addytywnie.
    """
    mood_name = detect_mood(text)
    prosody = _MOODS.get(mood_name, _MOODS["neutral"])

    # Połącz bazowe parametry + emocje
    def _add_hz(a: str, b: str) -> str:
        try:
            va = float(a.replace("Hz", "").replace("+", ""))
            vb = float(b.replace("Hz", "").replace("+", ""))
            total = va + vb
            return f"{'+' if total >= 0 else ''}{total:.0f}Hz"
        except Exception:
            return a

    def _add_pct(a: str, b: str) -> str:
        try:
            va = float(a.replace("%", "").replace("+", ""))
            vb = float(b.replace("%", "").replace("+", ""))
            total = va + vb
            return f"{'+' if total >= 0 else ''}{total:.0f}%"
        except Exception:
            return a

    final_pitch = _add_hz(base_pitch, prosody.pitch)
    final_rate  = _add_pct(base_rate, prosody.rate)
    final_vol   = prosody.volume

    # Escapuj znaki XML w tekście
    safe = (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))

    ssml = (
        f'<speak version="1.0" '
        f'xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="pl-PL">'
        f'<voice name="{voice}">'
        f'<prosody pitch="{final_pitch}" rate="{final_rate}" volume="{final_vol}">'
        f'{safe}'
        f'</prosody></voice></speak>'
    )
    return ssml, mood_name
