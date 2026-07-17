"""
STT Manager: chooses an available STT backend (prefer local, already-present).
Backends:
- VOSK (existing, offline)
- Whisper (optional)
This manager is designed to be safe: if optional deps missing, it falls back to VOSK.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal, Dict, Any

from ..agents.core.installer import ensure_importable

BackendName = Literal["vosk", "whisper"]

@dataclass
class STTResult:
    text: str
    meta: Dict[str, Any]

class STTManager:
    def __init__(self, preferred: Optional[BackendName] = None):
        self.preferred = preferred

    def available(self) -> Dict[BackendName, bool]:
        avail: Dict[BackendName, bool] = {"vosk": True, "whisper": False}
        # Whisper optional
        if ensure_importable("whisper", "openai-whisper"):
            avail["whisper"] = True
        return avail

    def choose(self) -> BackendName:
        avail = self.available()
        if self.preferred and avail.get(self.preferred):
            return self.preferred
        # Prefer whisper if available (higher quality), else vosk
        return "whisper" if avail.get("whisper") else "vosk"

    def transcribe_file(self, audio_path: str, lang: str = "pl") -> STTResult:
        backend = self.choose()
        if backend == "whisper":
            import whisper  # type: ignore
            model_name = "base"
            model = whisper.load_model(model_name)
            result = model.transcribe(audio_path, language=lang)
            return STTResult(text=result.get("text", "").strip(), meta={"backend": "whisper", "model": model_name})
        # fallback: VOSK
        from .stt_vosk import transcribe_wav  # expects wav
        text = transcribe_wav(audio_path, lang=lang)
        return STTResult(text=text.strip(), meta={"backend": "vosk"})
