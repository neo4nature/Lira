import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

PIPER_FIRST = os.environ.get("LIRA_TTS_PIPER_FIRST", "1") == "1"
TTS_SINK = os.environ.get("TTS_SINK", "")


def _play_wav(wav_path: str) -> None:
    if TTS_SINK and shutil.which("paplay"):
        subprocess.run(["paplay", f"--device={TTS_SINK}", wav_path], check=False)
    elif shutil.which("aplay"):
        subprocess.run(["aplay", "-q", wav_path], check=False)
    elif shutil.which("paplay"):
        subprocess.run(["paplay", wav_path], check=False)

# Echo suppression flag — when True, STT should ignore audio input
import threading
import time as _time_mod
from pathlib import Path as _Path

_tts_lock = threading.Event()  # set = TTS speaking, clear = idle
_tts_lock.clear()
_TTS_LOCKFILE = _Path(os.getenv("LIRA_TTS_LOCKFILE", "/tmp/lira_tts.lock"))
_TTS_LOCK_STALE_S = 60

def is_tts_active() -> bool:
    return _tts_lock.is_set()

def _tts_guard_start() -> None:
    # Cross-process lock: wait for other TTS processes to finish
    for _ in range(300):  # max 60s wait (300 * 0.2s)
        if not _TTS_LOCKFILE.exists():
            break
        age = _time_mod.time() - _TTS_LOCKFILE.stat().st_mtime
        if age > _TTS_LOCK_STALE_S:
            _TTS_LOCKFILE.unlink(missing_ok=True)
            break
        _time_mod.sleep(0.2)
    _TTS_LOCKFILE.write_text(str(os.getpid()), encoding="utf-8")
    _tts_lock.set()

def _tts_guard_end() -> None:
    _tts_lock.clear()
    _TTS_LOCKFILE.unlink(missing_ok=True)

# Edge-TTS neural voices (Microsoft, online)
EDGE_VOICE_PL = os.getenv("LIRA_TTS_EDGE_VOICE_PL", "pl-PL-ZofiaNeural")
EDGE_VOICE_EN = os.getenv("LIRA_TTS_EDGE_VOICE_EN", "en-GB-SoniaNeural")
SORYEL_VOICE_PL = os.getenv("SORYEL_TTS_VOICE_PL", "pl-PL-ZofiaNeural")
SORYEL_VOICE_EN = os.getenv("SORYEL_TTS_VOICE_EN", "en-GB-SoniaNeural")
SORYEL_TTS_PITCH = os.getenv("SORYEL_TTS_PITCH", "+5Hz")
SORYEL_TTS_RATE = os.getenv("SORYEL_TTS_RATE", "-5%")


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_PIPER_BIN = os.path.join(PROJECT_ROOT, "models", "piper", "bin", "piper")
DEFAULT_PIPER_VOICES = os.path.join(PROJECT_ROOT, "models", "piper", "voices")

def _resolve_piper_bin() -> str:
    # Env wins
    env = os.getenv("PIPER_BIN")
    if env and shutil.which(env):
        return env
    if os.path.exists(DEFAULT_PIPER_BIN):
        return DEFAULT_PIPER_BIN
    # Fallback to PATH
    return env or "piper"

def _resolve_piper_model(lang: str) -> Optional[str]:
    # Env wins
    env = os.getenv("PIPER_MODEL_PL") if lang.startswith("pl") else os.getenv("PIPER_MODEL_EN")
    if env:
        return env
    # Conventional local layout
    # Expected files: voices/pl.onnx / voices/en.onnx (or any name if explicitly set)
    cand = os.path.join(DEFAULT_PIPER_VOICES, "pl.onnx" if lang.startswith("pl") else "en.onnx")
    if os.path.exists(cand):
        return cand
    return None


def detect_lang(text: str) -> str:
    if re.search(r"[ąćęłńóśżź]", (text or "").lower()):
        return "pl"
    return "en"

def try_edge_tts(text: str, lang: str = "pl", voice: Optional[str] = None,
                 pitch: Optional[str] = None, rate: Optional[str] = None) -> bool:
    """Speak using Microsoft Edge TTS (online, neural quality).

    Requires: pip install edge-tts, and network access.
    Falls back gracefully if unavailable.
    """
    try:
        import edge_tts as _edge
    except ImportError:
        return False

    if voice is None:
        voice = EDGE_VOICE_PL if lang == "pl" else EDGE_VOICE_EN

    # edge-tts outputs MP3 — use ffplay or mpg123; fallback to ffmpeg→aplay pipe
    player_mp3 = (
        "ffplay" if shutil.which("ffplay") else
        ("mpg123" if shutil.which("mpg123") else
         ("mpv" if shutil.which("mpv") else None))
    )
    if not player_mp3 and not shutil.which("ffmpeg"):
        return False

    mp3_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name

        async def _synth():
            kwargs = {}
            if pitch:
                kwargs["pitch"] = pitch
            if rate:
                kwargs["rate"] = rate
            comm = _edge.Communicate(text, voice, **kwargs)
            with open(mp3_path, "wb") as out:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        out.write(chunk["data"])

        asyncio.run(_synth())
        if os.path.getsize(mp3_path) < 100:
            return False

        # Konwertuj MP3→WAV przez ffmpeg, potem play via _play_wav
        if shutil.which("ffmpeg") and (shutil.which("paplay") or shutil.which("aplay")):
            wav_path = mp3_path.replace(".mp3", ".wav")
            subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path, "-ar", "22050", "-ac", "1",
                 "-f", "wav", wav_path],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _play_wav(wav_path)
            try:
                os.unlink(wav_path)
            except Exception:
                pass
        elif player_mp3 == "ffplay":
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", mp3_path],
                check=False,
            )
        elif player_mp3:
            subprocess.run([player_mp3, mp3_path], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False
    finally:
        if mp3_path:
            try:
                os.unlink(mp3_path)
            except Exception:
                pass


MAX_TTS_CHUNK = int(os.getenv("LIRA_TTS_MAX_CHUNK", "800"))

def _split_to_chunks(text: str, max_len: int = MAX_TTS_CHUNK) -> list[str]:
    """Split text into sentence-sized chunks for TTS."""
    sentences = re.split(r'(?<=[.!?;:])\s+|\n+', text)
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if buf and len(buf) + len(s) + 1 > max_len:
            chunks.append(buf.strip())
            buf = s
        else:
            buf = f"{buf} {s}" if buf else s
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [text]


def speak(text: str, voice: Optional[str] = None,
          pitch: Optional[str] = None, rate: Optional[str] = None):
    """Speak text using whatever is available.

    Priority (can be overridden by TTS_BACKEND env):
      0) edge-tts (Microsoft Neural, online — best quality)
      1) piper (if LIRA_TTS_PIPER_FIRST=1 and PIPER_MODEL_* configured)
      2) speech-dispatcher (spd-say)
      3) festival
      4) espeak-ng / espeak

    pitch/rate — opcjonalne nadpisanie prosody (np. "+5Hz", "-15%").
    Jeśli nie podane, używa defaults per głos z .env.
    """
    text = (text or "").strip()
    if not text:
        return

    chunks = _split_to_chunks(text)
    _tts_guard_start()
    try:
        for chunk in chunks:
            _speak_impl(chunk, voice=voice, pitch=pitch, rate=rate)
    finally:
        import time as _t
        _t.sleep(0.3)
        _tts_guard_end()


def _speak_impl(text: str, voice: Optional[str] = None,
                pitch: Optional[str] = None, rate: Optional[str] = None):
    backend = os.getenv("TTS_BACKEND", "").strip().lower()
    lang = detect_lang(text)

    # Edge-TTS first (best quality, online)
    if backend not in {"piper", "spd", "festival", "espeak", "espeak-ng"}:
        # Jeśli pitch/rate nie podane — użyj defaults per głos z .env
        _pitch = pitch
        _rate = rate
        if _pitch is None and _rate is None:
            if voice in {SORYEL_VOICE_PL, SORYEL_VOICE_EN}:
                _pitch = SORYEL_TTS_PITCH or None
                _rate = SORYEL_TTS_RATE or None
        if try_edge_tts(text, lang=lang, voice=voice, pitch=_pitch, rate=_rate):
            return

    # Piper-first (if configured)
    if PIPER_FIRST and try_piper(text, lang=lang):
        return


    def has(cmd: str) -> bool:
        return shutil.which(cmd) is not None

    def try_spd() -> bool:
        if not has("spd-say"):
            return False
        # speech-dispatcher language tags: pl / en
        subprocess.run(["spd-say", "-l", lang, text], check=False)
        return True

    def try_festival() -> bool:
        if not has("festival"):
            return False
        p = subprocess.Popen(["festival", "--tts"], stdin=subprocess.PIPE)
        p.communicate(text.encode())
        return True

    def try_espeak() -> bool:
        cmd: Optional[str] = "espeak-ng" if has("espeak-ng") else ("espeak" if has("espeak") else None)
        if not cmd:
            return False
        voice = "pl" if lang == "pl" else "en"
        subprocess.run([cmd, "-v", voice, text], check=False)
        return True

    # Explicit backend selection
    if backend in {"spd", "spd-say", "speech-dispatcher"}:
        if not try_spd():
            print("[TTS] spd-say not found")
        return
    if backend == "festival":
        if not try_festival():
            print("[TTS] festival not found")
        return
    if backend in {"espeak", "espeak-ng"}:
        if not try_espeak():
            print("[TTS] espeak/espeak-ng not found")
        return

    # Auto mode
    if try_spd():
        return
    if try_festival():
        return
    if try_espeak():
        return

    print("[TTS] No TTS backend found. Install one of: speech-dispatcher, festival, espeak-ng.")


def try_piper(text: str, lang: str = "pl") -> bool:
    """Try Piper TTS if configured.

    Requires:
      - PIPER_BIN (default: piper)
      - PIPER_MODEL_PL / PIPER_MODEL_EN
      - an audio player (aplay/paplay)
    """
    import tempfile

    model = _resolve_piper_model(lang)
    piper_bin = _resolve_piper_bin()
    if not model:
        return False

    if not (shutil.which("paplay") or shutil.which("aplay")):
        return False

    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        subprocess.run([piper_bin, "--model", model, "--output_file", wav_path],
                       input=text.encode("utf-8"), check=True)
        _play_wav(wav_path)
        return True
    except Exception:
        return False
    finally:
        if wav_path:
            try:
                os.unlink(wav_path)
            except Exception:
                pass
