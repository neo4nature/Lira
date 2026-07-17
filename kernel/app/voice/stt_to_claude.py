#!/usr/bin/env python3
"""STT → aktywne okno bridge (ciągły nasłuch + xdotool).

Nasłuchuje mikrofonu. Gdy wykryje głos (VAD), nagrywa do ciszy,
transkrybuje przez faster-whisper i wpisuje tekst do aktywnego okna
przez xdotool.

Uruchom w osobnym terminalu:
    cd kernel/app && python3 -m voice.stt_to_claude
"""
import os
import sys
import time
import subprocess
from pathlib import Path

# Załaduj .env
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import sounddevice as sd

SAMPLE_RATE   = 16000
BLOCK_SIZE    = 4000
VAD_THRESH    = float(os.getenv("STT_VAD_THRESH", "300"))
SILENCE_S     = float(os.getenv("STT_SILENCE_S", "1.2"))
MAX_RECORD_S  = float(os.getenv("STT_MAX_RECORD_S", "15"))
WHISPER_MODEL = os.getenv("STT_WHISPER_MODEL", "small")
POST_TYPE_PAUSE = float(os.getenv("STT_POST_TYPE_PAUSE", "4.0"))  # pauza po wpisaniu żeby nie łapać TTS


def _log(msg: str) -> None:
    print(msg, flush=True)


def _type_text(text: str) -> None:
    """Wpisz tekst do aktywnego okna: wl-copy → Ctrl+V (polskie znaki OK)."""
    try:
        subprocess.run(["wl-copy", "--", text], check=True)
        time.sleep(0.1)
        subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
        _log(f"[paste] wpisano: '{text}'")
    except Exception as e:
        _log(f"[paste ERROR] {e}")


def _record_utterance() -> np.ndarray:
    """Nagraj jedną wypowiedź (VAD). Zwraca numpy array float32."""
    chunks = []
    recording = False
    silence_start = None
    record_start = None

    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
                           dtype="int16", channels=1) as stream:
        while True:
            data, _ = stream.read(BLOCK_SIZE)
            arr = np.frombuffer(bytes(data), dtype=np.int16)
            rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))

            if not recording:
                if rms > VAD_THRESH:
                    _log(f"[STT] Głos wykryty (RMS={rms:.0f})")
                    recording = True
                    record_start = time.time()
                    chunks.append(arr)
                    silence_start = None
            else:
                chunks.append(arr)
                if rms < VAD_THRESH:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start >= SILENCE_S:
                        _log("[STT] Cisza — koniec")
                        break
                else:
                    silence_start = None
                if time.time() - record_start > MAX_RECORD_S:
                    _log("[STT] Max długość — przerywam")
                    break

    if not chunks:
        return np.array([], dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def _transcribe(whisper, audio: np.ndarray) -> tuple[str, float]:
    """Transkrybuj audio przez faster-whisper."""
    if len(audio) == 0:
        return "", 0.0
    segments, _ = whisper.transcribe(
        audio,
        language="pl",
        beam_size=5,
        vad_filter=True,
    )
    parts, confs = [], []
    for seg in segments:
        t = seg.text.strip()
        if t:
            parts.append(t)
        if hasattr(seg, "avg_logprob"):
            confs.append(min(1.0, max(0.0, 1.0 + seg.avg_logprob)))
    text = " ".join(parts).strip()
    conf = float(np.mean(confs)) if confs else 0.5
    return text, conf


def main() -> None:
    _log("=" * 55)
    _log("  STT → xdotool (ciągły nasłuch)")
    _log(f"  Model: faster-whisper {WHISPER_MODEL} | język: pl")
    _log(f"  VAD próg: {VAD_THRESH} | cisza: {SILENCE_S}s")
    _log(f"  Wyjście: ydotool type → aktywne okno (Wayland)")
    _log("=" * 55)

    _log(f"[STT] Ładuję faster-whisper '{WHISPER_MODEL}'...")
    from faster_whisper import WhisperModel
    whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    _log("[STT] Gotowe — słucham! Mów naturalnie, tekst trafi do aktywnego okna.")
    _log("[STT] Ctrl+C żeby zatrzymać.\n")

    while True:
        try:
            audio = _record_utterance()
            if len(audio) == 0:
                continue

            _log("[STT] Transkrybuję...")
            text, conf = _transcribe(whisper, audio)

            if not text:
                _log("[STT] Nie rozpoznałam nic\n")
                continue

            _log(f"[STT] '{text}' (pewność: {conf:.2f})")

            if conf < 0.15:
                _log("[STT] Zbyt niska pewność — pomijam\n")
                continue

            if text.strip().lower() in {"stop", "koniec", "wyjdź", "wyjdz"}:
                _log("[STT] Kończę.")
                break

            _type_text(text)
            _log(f"[STT] Pauza {POST_TYPE_PAUSE}s (czekam aż TTS skończy)...")
            time.sleep(POST_TYPE_PAUSE)
            _log("")

        except KeyboardInterrupt:
            _log("\n[STT] Stop.")
            break
        except Exception as e:
            _log(f"[STT] Błąd: {e}")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
