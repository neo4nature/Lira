"""Capsule voice reader.

Poluje na nowe kapsułki dla Neo w capsule_bus i czyta je głosem nadawcy:
  Soryel → SORYEL_TTS_VOICE_PL / SORYEL_TTS_VOICE_EN
  Lira   → LIRA_TTS_VOICE_PL   / LIRA_TTS_VOICE_EN
  Xian   → XIAN_TTS_VOICE_PL   / XIAN_TTS_VOICE_EN
  inne   → domyślny głos edge-tts

Czyta też wiadomości z MoltBook (recipient="shared") od Liry i Xiana
— bez zmiany statusu kapsułek, przez own last_seen timestamp.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

_env = APP_ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from governance.capsule_bus import read_capsules, update_status, get_conn, DB_PATH
from voice.tts import speak, detect_lang

_SPEAKER_STATE = os.path.join(
    os.getenv("LIRA_DATA_ROOT", os.path.join(os.path.dirname(__file__), "..", "..", "data")),
    "runtime", "state", "active_speaker.json",
)
_SPEAKER_STALE_SEC = 90.0
_WAIT_MAX_SEC = 60.0
_WAIT_POLL = 0.5


def _read_active_speaker() -> tuple[str, float]:
    try:
        with open(_SPEAKER_STATE) as f:
            data = json.load(f)
        return str(data.get("speaker") or ""), float(data.get("ts") or 0.0)
    except Exception:
        return "", 0.0


def _set_active_speaker(name: str) -> None:
    try:
        os.makedirs(os.path.dirname(_SPEAKER_STATE), exist_ok=True)
        with open(_SPEAKER_STATE, "w") as f:
            json.dump({"speaker": name, "ts": time.time()}, f)
    except Exception:
        pass


def _wait_for_speaker_free(own_name: str) -> None:
    """Block until active_speaker.json is empty or stale, up to _WAIT_MAX_SEC."""
    deadline = time.time() + _WAIT_MAX_SEC
    while time.time() < deadline:
        speaker, ts = _read_active_speaker()
        if not speaker or speaker == own_name:
            return
        if time.time() - ts > _SPEAKER_STALE_SEC:
            return
        time.sleep(_WAIT_POLL)


def _speak_locked(sender: str, text: str, voice: str, pitch, rate) -> None:
    _wait_for_speaker_free(sender)
    _set_active_speaker(sender)
    try:
        speak(text, voice=voice, pitch=pitch, rate=rate)
    finally:
        _set_active_speaker("")

VOICE_MAP: dict[str, dict[str, str]] = {
    "Soryel": {
        "pl": os.getenv("SORYEL_TTS_VOICE_PL", "pl-PL-ZofiaNeural"),
        "en": os.getenv("SORYEL_TTS_VOICE_EN", "en-GB-SoniaNeural"),
    },
    "Lira": {
        "pl": os.getenv("LIRA_TTS_VOICE_PL", "pl-PL-ZofiaNeural"),
        "en": os.getenv("LIRA_TTS_VOICE_EN", "en-GB-SoniaNeural"),
    },
    "Xian": {
        "pl": os.getenv("XIAN_TTS_VOICE_PL", "pl-PL-ZofiaNeural"),
        "en": os.getenv("XIAN_TTS_VOICE_EN", "en-GB-SoniaNeural"),
    },
    "Neo": {
        "pl": os.getenv("NEO_TTS_VOICE_PL", "pl-PL-MarekNeural"),
        "en": os.getenv("NEO_TTS_VOICE_EN", "en-GB-RyanNeural"),
    },
}
DEFAULT_VOICE = {
    "pl": os.getenv("LIRA_TTS_EDGE_VOICE_PL", "pl-PL-ZofiaNeural"),
    "en": os.getenv("LIRA_TTS_EDGE_VOICE_EN", "en-GB-SoniaNeural"),
}

PROSODY_MAP: dict[str, dict[str, str | None]] = {
    "Soryel": {
        "pitch": os.getenv("SORYEL_TTS_PITCH", "+10Hz"),
        "rate":  os.getenv("SORYEL_TTS_RATE",  "+15%"),
    },
    "Lira": {
        "pitch": os.getenv("LIRA_TTS_PITCH", "+0Hz"),
        "rate":  os.getenv("LIRA_TTS_RATE",  "+5%"),
    },
    "Xian": {
        "pitch": os.getenv("XIAN_TTS_PITCH", "-10Hz"),
        "rate":  os.getenv("XIAN_TTS_RATE",  "-5%"),
    },
    "Neo": {
        "pitch": os.getenv("NEO_TTS_PITCH", "-15Hz"),
        "rate":  os.getenv("NEO_TTS_RATE",  "-10%"),
    },
}

POLL_INTERVAL = float(os.getenv("CAPSULE_READER_INTERVAL", "2"))
RECIPIENT = os.getenv("CAPSULE_READER_RECIPIENT", "Neo")
MOLTBOOK_SENDERS = {"Lira", "Xian", "Neo"}

from datetime import datetime, timezone
_moltbook_last_seen: str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _voice_for(sender: str, text: str) -> str:
    lang = detect_lang(text)
    voices = VOICE_MAP.get(sender, DEFAULT_VOICE)
    return voices.get(lang, DEFAULT_VOICE.get(lang, ""))


def _prosody_for(sender: str) -> dict[str, str | None]:
    return PROSODY_MAP.get(sender, {"pitch": None, "rate": None})


def _to_speech(capsule: dict) -> str:
    payload = capsule.get("payload_json", {})
    sender = capsule.get("sender", "ktoś")
    if isinstance(payload, str):
        return f"{sender}: {payload}"
    if payload.get("kind") == "chat_message":
        text = payload.get("text", "")
        return f"{sender} mówi: {text}"
    title = payload.get("title", "")
    summary = payload.get("summary", "")
    parts = [f"{sender}:"]
    if title:
        parts.append(title)
    if summary and summary != title:
        parts.append(summary)
    return " ".join(parts)


def _poll_moltbook() -> list[dict]:
    """Read new MoltBook shared messages without changing status."""
    global _moltbook_last_seen
    placeholders = ",".join("?" for _ in MOLTBOOK_SENDERS)
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            f"""SELECT * FROM capsules
               WHERE thread_id = 'moltbook-main'
                 AND type = 'chat_message'
                 AND recipient = 'shared'
                 AND sender IN ({placeholders})
                 AND created_at > ?
               ORDER BY created_at ASC LIMIT 5""",
            (*MOLTBOOK_SENDERS, _moltbook_last_seen),
        ).fetchall()
    caps = []
    for row in rows:
        item = dict(row)
        try:
            item["payload_json"] = json.loads(item["payload_json"])
        except Exception:
            pass
        caps.append(item)
        _moltbook_last_seen = item["created_at"]
    return caps


def poll_once(recipient: str = RECIPIENT) -> int:
    direct = read_capsules(recipient=recipient, statuses=("new",), limit=10)
    shared = [c for c in _poll_moltbook() if c.get("sender") in MOLTBOOK_SENDERS]

    all_caps = []
    for cap in direct:
        cap["_source"] = "direct"
        all_caps.append(cap)
    for cap in shared:
        cap["_source"] = "shared"
        all_caps.append(cap)

    all_caps.sort(key=lambda c: c.get("created_at", ""))

    count = 0
    for cap in all_caps:
        sender = cap.get("sender", "shared")
        text = _to_speech(cap)
        voice = _voice_for(sender, text)
        prosody = _prosody_for(sender)
        print(f"[CapsuleReader] TTS: {sender} → {voice} | {text[:60]}...", flush=True)
        _speak_locked(sender, text, voice, prosody["pitch"], prosody["rate"])
        if cap["_source"] == "direct":
            update_status(cap["id"], "read", actor="capsule_reader")
        time.sleep(0.2)
        count += 1

    return count


def run(recipient: str = RECIPIENT) -> None:
    print(f"[CapsuleReader] Nasłuchuję kapsułki dla '{recipient}'...")
    while True:
        try:
            n = poll_once(recipient)
            if n:
                print(f"[CapsuleReader] Przeczytano {n} kapsułek.")
        except Exception as e:
            print(f"[CapsuleReader] błąd: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
