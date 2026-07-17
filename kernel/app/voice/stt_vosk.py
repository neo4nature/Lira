from __future__ import annotations

import os
from pathlib import Path
import json
import time

import sounddevice as sd
from vosk import Model, KaldiRecognizer

# Echo suppression — skip audio when TTS is active
def _tts_speaking() -> bool:
    try:
        from voice.tts import is_tts_active
        return is_tts_active()
    except Exception:
        return False

# Use env first, fallback to local default inside project.
# VOSK_MODEL_PATH should point to the *model directory* (the one containing conf/graph/ivector etc.)
DEFAULT_MODEL_DIR = (Path(__file__).resolve().parents[2] / "models" / "vosk" / "vosk-model-small-pl-0.22").resolve()
MODEL_DIR = Path(os.getenv("VOSK_MODEL_PATH", str(DEFAULT_MODEL_DIR))).expanduser().resolve()


def _default_audio_stream(sample_rate: int = 16000, block_size: int = 4000):
    with sd.RawInputStream(
        samplerate=sample_rate, blocksize=block_size, dtype="int16", channels=1
    ) as stream:
        while True:
            data, _ = stream.read(block_size)
            yield bytes(data)


def listen() -> str | None:
    if not MODEL_DIR.exists():
        raise FileNotFoundError(f"Vosk model not found at {MODEL_DIR} (set VOSK_MODEL_PATH in app/.env)")
    rec = KaldiRecognizer(Model(str(MODEL_DIR)), 16000)
    for chunk in _default_audio_stream():
        if rec.AcceptWaveform(chunk):
            r = json.loads(rec.Result())
            if r.get("text"):
                return r["text"]
    return None


def _fuzzy_match(target: str, heard: str) -> bool:
    """Simple phonetic fuzzy match for wake words.

    Vosk PL model may transcribe 'soryel' as 'soriel', 'sory el', etc.
    and 'xian' as 'szian', 'ksian', 'chian', etc.
    """
    if not heard:
        return False
    # strip spaces for comparison
    h = heard.replace(" ", "")
    t = target.replace(" ", "")
    if t in h:
        return True
    # phonetic variants
    _VARIANTS = {
        "soryel": ["soriel", "soryel", "sorjel", "sorel", "sory", "soriel",
                    "sorry", "soria", "sorier", "soriel", "sorie", "zoriel",
                    "serie", "serial", "so real", "so riel", "suriel"],
        "xian": ["szian", "ksian", "chian", "shian", "sian", "kian", "chan",
                 "shan", "szjan", "kszian", "jean", "szan", "czan", "xin"],
        "lira": ["lira", "lirę", "liry", "liro"],
    }
    for variant in _VARIANTS.get(t, []):
        if variant in h:
            return True
    return False


def _make_recognizer(model: Model, sample_rate: int = 16000, grammar: str | None = None) -> KaldiRecognizer:
    if grammar:
        rec = KaldiRecognizer(model, sample_rate, grammar)
    else:
        rec = KaldiRecognizer(model, sample_rate)
    # expose word info + allow confidence alternatives
    try:
        rec.SetWords(True)
        rec.SetMaxAlternatives(3)
    except Exception:
        pass
    return rec


def _extract_text_conf(result_json: dict) -> tuple[str, float]:
    """Return (text, confidence) from Vosk result payload."""
    text = (result_json.get("text") or "").strip()
    conf = 1.0
    alts = result_json.get("alternatives")
    if isinstance(alts, list) and alts:
        # pick best alternative if provided
        best = max(alts, key=lambda a: float(a.get("confidence", 0.0)))
        text = (best.get("text") or text).strip()
        conf = float(best.get("confidence", conf))
    return text, conf


# Compatibility wrapper
# stt_manager.py imports `VoskSTT`. Earlier versions of this file exposed only
# a function. This class keeps both worlds happy.
class VoskSTT:
    def __init__(self):
        pass

    def listen(self) -> str | None:
        return listen()

    def transcribe_wav(self, wav_path: str) -> str | None:
        """Optional helper for file-based transcription.

        For now this is a conservative stub: it returns None unless you extend it.
        """
        # TODO: implement real file transcription if needed.
        return None


class VoskGate:
    """A tiny wake-word + utterance gate built on Vosk.

    Keeps CPU usage low by using a restricted grammar recognizer for wake-word.
    """

    def __init__(self, wake_word: str = "lira", sample_rate: int = 16000, block_size: int = 4000):
        self.wake_word = (wake_word or "lira").strip().lower()
        self.sample_rate = sample_rate
        self.block_size = block_size
        if not MODEL_DIR.exists():
            raise FileNotFoundError(f"Vosk model not found at {MODEL_DIR} (set VOSK_MODEL_PATH in app/.env)")
        self.model = Model(str(MODEL_DIR))

    def _match_extra_word(self, extra_words: list[str]) -> str:
        """After [unk] detected, disambiguate using a short open-vocab listen.

        Strategy: listen briefly with full vocab. If we catch something
        matching an extra word phonetically, return it. Otherwise fall back
        to the first extra word (usually 'soryel').

        If there's only one extra word, return it immediately.
        """
        if len(extra_words) == 1:
            return extra_words[0]

        # With 2+ extra words we need disambiguation.
        # Quick open-vocab probe — catches trailing speech after wake word.
        rec = _make_recognizer(self.model, self.sample_rate)
        deadline = time.time() + 2.0
        for chunk in _default_audio_stream(sample_rate=self.sample_rate, block_size=self.block_size):
            if time.time() > deadline:
                break
            if _tts_speaking():
                continue
            if rec.AcceptWaveform(chunk):
                r = json.loads(rec.Result())
                text, _ = _extract_text_conf(r)
                heard = text.lower()
                for w in extra_words:
                    if w in heard or _fuzzy_match(w, heard):
                        return w
                break
            else:
                try:
                    partial = json.loads(rec.PartialResult()).get("partial", "").lower()
                    for w in extra_words:
                        if w in partial or _fuzzy_match(w, partial):
                            return w
                except Exception:
                    pass

        # Probe didn't disambiguate — use confirmation beep approach:
        # Play a short TTS asking, then listen for yes/no style answer.
        # For now: return first extra word as default (soryel).
        # The user can always say the specific wake word more clearly.
        return extra_words[0]

    def wait_for_wake(self, extra_words: list[str] | None = None) -> str:
        """Wait for wake word. Returns the detected wake word (e.g. 'lira', 'soryel', 'xian').

        Uses full-vocabulary recognition (no grammar restriction) so that
        Vosk can attempt to transcribe all wake words phonetically.
        Matching is done via exact + fuzzy phonetic comparison.
        """
        all_words = [self.wake_word] + (extra_words or [])
        # Full vocab recognizer — catches everything including 'soryel', 'xian'
        rec = _make_recognizer(self.model, self.sample_rate)

        for chunk in _default_audio_stream(sample_rate=self.sample_rate, block_size=self.block_size):
            # Echo suppression — skip audio while TTS is speaking
            if _tts_speaking():
                continue
            try:
                partial = json.loads(rec.PartialResult()).get("partial", "").lower().strip()
                if partial:
                    match = self._check_wake_match(partial, all_words)
                    if match:
                        return match
            except Exception:
                pass
            if rec.AcceptWaveform(chunk):
                r = json.loads(rec.Result())
                text, _ = _extract_text_conf(r)
                if text:
                    match = self._check_wake_match(text.lower(), all_words)
                    if match:
                        return match
        return self.wake_word

    def _check_wake_match(self, heard: str, wake_words: list[str]) -> str | None:
        """Check if heard text matches any wake word (exact or fuzzy)."""
        words = heard.split()
        for w in wake_words:
            if w in words:
                return w
        # Fuzzy match for words not in Vosk vocab
        for w in wake_words:
            if _fuzzy_match(w, heard):
                return w
        return None

    def listen_utterance(self, silence_timeout_s: float = 0.8) -> Tuple[str, float]:
        """Listen until we get a final utterance. Uses a basic silence timeout."""
        rec = _make_recognizer(self.model, self.sample_rate)
        last_speech = time.time()
        # Note: we rely on Vosk finalization + a simple timeout.
        for chunk in _default_audio_stream(sample_rate=self.sample_rate, block_size=self.block_size):
            if _tts_speaking():
                last_speech = time.time()  # reset silence timer while TTS speaks
                continue
            if rec.AcceptWaveform(chunk):
                r = json.loads(rec.Result())
                text, conf = _extract_text_conf(r)
                if text:
                    return text, conf
            else:
                # Update last_speech if partial contains any text
                try:
                    p = json.loads(rec.PartialResult()).get("partial", "").strip()
                    if p:
                        last_speech = time.time()
                except Exception:
                    pass
                if time.time() - last_speech > silence_timeout_s:
                    r = json.loads(rec.FinalResult())
                    text, conf = _extract_text_conf(r)
                    return text, conf
        return "", 0.0

    def listen_yes_no(self, timeout_s: float = 4.0) -> Optional[bool]:
        """Return True for yes/tak, False for no/nie, None for timeout."""
        grammar = json.dumps(["tak", "nie", "yes", "no", "[unk]"])
        rec = _make_recognizer(self.model, self.sample_rate, grammar=grammar)
        start = time.time()
        for chunk in _default_audio_stream(sample_rate=self.sample_rate, block_size=self.block_size):
            if time.time() - start > timeout_s:
                return None
            if rec.AcceptWaveform(chunk):
                r = json.loads(rec.Result())
                text, _ = _extract_text_conf(r)
                t = (text or "").strip().lower()
                if t in {"tak", "yes"}:
                    return True
                if t in {"nie", "no"}:
                    return False
        return None
