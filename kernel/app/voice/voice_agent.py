"""Voice agent.

Obsługuje trzy wake wordy:
  "lira"   → lokalne LLM (Ollama) przez MCL router
  "soryel" → kapsułka do capsule_bus → Claude Code CLI czyta przez MCP
  "xian"   → lokalne LLM (Ollama) xian model

STT: Vosk (offline, polski model)
TTS: edge-tts neural (online) → Piper → spd-say → espeak

Integracja z capsule bus (GG):
  - każda rozmowa jest logowana jako kapsuła
  - background thread odczytuje nowe kapsułki dla Neo głosem nadawcy
  - Soryel: kapsułka + opcjonalny paste do aktywnego okna (ydotool)
"""

import os
import subprocess
import threading
import time

from channeling.channel_router import ChannelRouter
from voice.tts import speak, SORYEL_VOICE_PL, SORYEL_VOICE_EN, detect_lang
from voice.wake_gate import WakeGate
from voice.soryel_handler import ask_soryel, clear_history
from voice.xian_handler import ask_xian, clear_xian_history
from voice.stt_vosk import VoskGate
from voice.speech_queue import register_pid, unregister_pid, pop_all

SORYEL_WAKE = os.getenv("SORYEL_WAKE_WORD", "soryel").strip().lower()
XIAN_WAKE = os.getenv("XIAN_WAKE_WORD", "xian").strip().lower()
SORYEL_IDLE_TIMEOUT = float(os.getenv("SORYEL_IDLE_TIMEOUT", "20"))
CAPSULE_READER_INTERVAL = float(os.getenv("CAPSULE_READER_INTERVAL", "10"))

# Conversation lock — pauses background TTS (capsule reader, speech queue)
_conversation_active = threading.Event()  # set = conversation active, background TTS paused

# Capsule bus — opcjonalne (import może nie być dostępny offline)
try:
    from governance.capsule_bus import init_db, send_capsule, read_capsules, update_status
    _CAPSULE_OK = True
except Exception:
    _CAPSULE_OK = False


# ── TTS per persona ───────────────────────────────────────────────────────────

_VOICE_MAP = {
    "Soryel": {
        "pl": os.getenv("SORYEL_TTS_VOICE_PL", "pl-PL-ZofiaNeural"),
        "en": os.getenv("SORYEL_TTS_VOICE_EN", "en-GB-SoniaNeural"),
    },
    "Lira": {
        "pl": os.getenv("LIRA_TTS_VOICE_PL", "pl-PL-ZofiaNeural"),
        "en": os.getenv("LIRA_TTS_VOICE_EN", "en-GB-SoniaNeural"),
    },
    "Xian": {
        "pl": os.getenv("XIAN_TTS_VOICE_PL", "pl-PL-MarekNeural"),
        "en": os.getenv("XIAN_TTS_VOICE_EN", "en-GB-RyanNeural"),
    },
}
_DEFAULT_VOICE = {
    "pl": os.getenv("LIRA_TTS_EDGE_VOICE_PL", "pl-PL-ZofiaNeural"),
    "en": os.getenv("LIRA_TTS_EDGE_VOICE_EN", "en-GB-SoniaNeural"),
}


def _voice_for(sender: str, text: str) -> str:
    lang = detect_lang(text)
    voices = _VOICE_MAP.get(sender, _DEFAULT_VOICE)
    return voices.get(lang, _DEFAULT_VOICE.get(lang, ""))


_SPEAKER_STATE = os.path.join(
    os.getenv("LIRA_DATA_ROOT", os.path.join(os.path.dirname(__file__), "..", "..", "data")),
    "runtime", "state", "active_speaker.json",
)


def _set_active_speaker(name: str) -> None:
    """Zapisz aktywnego mówcę do pliku stanu (tapeta nasłuchuje)."""
    try:
        import json as _json
        with open(_SPEAKER_STATE, "w") as f:
            _json.dump({"speaker": name, "ts": time.time()}, f)
    except Exception:
        pass


_SPEAKER_STALE_SEC = 90.0
_WAIT_MAX_SEC = 60.0
_WAIT_POLL = 0.5


def _read_active_speaker() -> tuple[str, float]:
    try:
        import json as _json
        with open(_SPEAKER_STATE) as f:
            data = _json.load(f)
        return str(data.get("speaker") or ""), float(data.get("ts") or 0.0)
    except Exception:
        return "", 0.0


def _wait_for_speaker_free(own_name: str) -> None:
    """Block until active_speaker is empty/stale/own, up to _WAIT_MAX_SEC."""
    deadline = time.time() + _WAIT_MAX_SEC
    while time.time() < deadline:
        speaker, ts = _read_active_speaker()
        if not speaker or speaker == own_name:
            return
        if time.time() - ts > _SPEAKER_STALE_SEC:
            return
        time.sleep(_WAIT_POLL)


def _speak_as(sender: str, text: str, voice: str | None = None) -> None:
    _wait_for_speaker_free(sender)
    _set_active_speaker(sender)
    try:
        speak(text, voice=voice or _voice_for(sender, text))
    finally:
        _set_active_speaker("")  # wróć do idle po wypowiedzi


def _speak_soryel(text: str) -> None:
    _speak_as("Soryel", text)


def _speak_xian(text: str) -> None:
    _speak_as("Xian", text)


# ── Capsule helpers ───────────────────────────────────────────────────────────

def _send_voice_capsule(text: str, reply: str) -> None:
    """Loguje wymianę głosową jako kapsułkę w tle (nie blokuje)."""
    if not _CAPSULE_OK:
        return
    try:
        init_db()
        send_capsule(
            type="fact",
            weight="commentary",
            layer="working",
            owner="Neo",
            sender="Neo",
            recipient="Soryel",
            payload={"title": "Voice query", "summary": text, "voice_reply": reply[:300]},
            open_question="",
            project="voice",
            status="answered",
        )
    except Exception:
        pass


def _capsule_to_speech(capsule: dict) -> str:
    payload = capsule.get("payload_json", {})
    sender = capsule.get("sender", "ktoś")
    if isinstance(payload, str):
        return f"{sender}: {payload[:300]}"
    title = payload.get("title", "")
    summary = payload.get("summary", "")
    parts = [f"{sender}:"]
    if title:
        parts.append(title)
    if summary and summary != title:
        parts.append(summary[:300])
    return " ".join(parts)


def _read_pending_capsules() -> None:
    """Odczytuje głosem nowe kapsułki dla Neo (z głosem per nadawca)."""
    if not _CAPSULE_OK:
        return
    try:
        capsules = read_capsules(recipient="Neo", statuses=("new",), limit=5)
        for cap in capsules:
            sender = cap.get("sender", "shared")
            text = _capsule_to_speech(cap)
            _speak_as(sender, text)
            update_status(cap["id"], "read", actor="voice_agent")
            time.sleep(0.5)
    except Exception as e:
        # nie połykaj — błąd przy 1. kapsule zostawiał kolejne w statusie new na zawsze
        print(f"[Voice] Capsule reader błąd: {e}", flush=True)


# ── Soryel conversation loop ──────────────────────────────────────────────────

_SORYEL_REPLY_TIMEOUT = float(os.getenv("SORYEL_REPLY_TIMEOUT", "90"))
_SORYEL_POLL_INTERVAL = 2.0


def _paste_to_terminal(text: str) -> bool:
    """Wklej tekst do aktywnego okna przez clipboard + Ctrl+V.

    Wayland: wl-copy (needs WAYLAND_DISPLAY) + xdotool (via XWayland).
    X11 fallback: xclip + xdotool.
    """
    # Ensure Wayland env is available for subprocess
    paste_env = dict(os.environ)
    paste_env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    paste_env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    paste_env.setdefault("DISPLAY", ":0")

    # Step 1: put text in clipboard
    copied = False
    try:
        subprocess.run(["wl-copy", "--", text], check=True, timeout=3, env=paste_env)
        copied = True
        print(f"[SORYEL] clipboard: wl-copy OK ({len(text)} chars)")
    except Exception as e:
        print(f"[SORYEL] wl-copy failed: {e}")
        try:
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=text.encode(), check=True, timeout=3, env=paste_env)
            copied = True
        except Exception:
            pass

    if not copied:
        print("[SORYEL] paste: clipboard copy failed")
        return False

    # Step 2: simulate Ctrl+V via xdotool (works on XWayland too)
    time.sleep(0.2)
    try:
        subprocess.run(["xdotool", "key", "ctrl+v"], check=True, timeout=3, env=paste_env)
        print("[SORYEL] paste: xdotool ctrl+v OK")
        return True
    except Exception as e:
        print(f"[SORYEL] paste: xdotool failed: {e}")
        return False


def _send_soryel_capsule(text: str) -> str | None:
    """Wyślij kapsułkę do Soryel przez capsule_bus. Zwraca capsule_id."""
    if not _CAPSULE_OK:
        return None
    try:
        init_db()
        cid = send_capsule(
            type="fact",
            weight="changes_trajectory",
            layer="working",
            owner="Neo",
            sender="Neo",
            recipient="Soryel",
            payload={"title": "Voice message", "summary": text, "source": "voice_agent"},
            open_question=text,
            project="voice",
            status="new",
        )
        return cid
    except Exception as e:
        print(f"[SORYEL] capsule send error: {e}")
        return None


def _wait_soryel_reply(thread_id: str, timeout: float = _SORYEL_REPLY_TIMEOUT) -> str | None:
    """Czekaj aż Soryel odpowie kapsułką w tym samym wątku."""
    if not _CAPSULE_OK:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(_SORYEL_POLL_INTERVAL)
        try:
            caps = read_capsules(
                recipient="Neo",
                statuses=("new",),
                limit=10,
            )
            for cap in caps:
                if cap.get("thread_id") == thread_id and cap.get("sender") == "Soryel":
                    update_status(cap["id"], "read", actor="voice_agent")
                    payload = cap.get("payload_json", {})
                    if isinstance(payload, str):
                        import json
                        payload = json.loads(payload)
                    return payload.get("summary") or payload.get("voice_reply") or str(payload)
        except Exception:
            pass
    return None


def _soryel_conversation(vosk_gate: VoskGate) -> None:
    """Pętla rozmowy z Soryel — voice → capsule_bus → Claude Code CLI.

    Dwa tryby dostarczenia:
      1. Kapsułka do capsule_bus (Soryel czyta przez MCP sqlite)
      2. Paste do aktywnego okna (jeśli Claude Code jest aktywny)
    Odpowiedź: Soryel pisze kapsułkę zwrotną lub speech_queue.
    """
    _conversation_active.set()
    try:
        _soryel_conversation_inner(vosk_gate)
    finally:
        _conversation_active.clear()


def _soryel_conversation_inner(vosk_gate: VoskGate) -> None:
    print("[SORYEL] Tryb rozmowy aktywny. Mów — tekst trafi do Claude Code + capsule bus.")
    _speak_soryel("Słucham.")

    while True:
        time.sleep(0.3)
        print("[SORYEL] Nasłuchuję...")
        text, conf = vosk_gate.listen_utterance(silence_timeout_s=3.0)
        text = text.strip()
        print(f"[SORYEL] Vosk heard: '{text}' (conf={conf:.2f})")

        if not text:
            print("[SORYEL] Cisza — wracam do idle.")
            return

        text_lower = text.lower()
        if text_lower in {"stop", "koniec", "do widzenia", "pa", "wyjdź", "wyjdz"}:
            _speak_soryel("Dobra, wracam do nasłuchu.")
            return

        if text_lower in {"zapomnij", "reset", "wyczyść", "nowy temat"}:
            clear_history()
            _speak_soryel("Dobra, zaczynam od nowa.")
            time.sleep(0.5)
            continue

        print(f"NEO (voice)> {text}")

        # 1. Wyślij kapsułkę do capsule_bus (Soryel czyta przez MCP)
        cid = _send_soryel_capsule(text)
        if cid:
            print(f"[SORYEL] Kapsułka wysłana: {cid[:8]}...")

        # 2. Paste do aktywnego terminala (Claude Code)
        pasted = _paste_to_terminal(text)
        if pasted:
            print("[SORYEL] Tekst wklejony do aktywnego okna.")
        elif cid:
            print("[SORYEL] Paste nie zadziałał — kapsułka w bus, Soryel przeczyta przez MCP.")

        # 3. Krótki feedback głosowy
        _speak_soryel("Przekazałam.")


# ── Xian conversation loop ───────────────────────────────────────────────

def _xian_conversation(vosk_gate: VoskGate) -> None:
    """Pętla rozmowy z Xian — lokalny model, ciepły złoty ton."""
    _conversation_active.set()
    try:
        _xian_conversation_inner(vosk_gate)
    finally:
        _conversation_active.clear()


def _xian_conversation_inner(vosk_gate: VoskGate) -> None:
    print("[XIAN] Tryb rozmowy aktywny. Powiedz 'stop' lub milcz aby wyjść.")
    _speak_xian("Słucham.")

    while True:
        time.sleep(0.3)
        text, conf = vosk_gate.listen_utterance(silence_timeout_s=3.0)
        text = text.strip().lower()

        if not text:
            print("[XIAN] Cisza — wracam do idle.")
            return

        if text in {"stop", "koniec", "do widzenia", "pa", "wyjdź", "wyjdz"}:
            _speak_xian("Dobra, wracam do nasłuchu.")
            return

        if text in {"zapomnij", "reset", "wyczyść", "nowy temat"}:
            clear_xian_history()
            _speak_xian("Zaczynam od nowa.")
            time.sleep(0.5)
            continue

        print(f"NEO> {text}")
        reply = ask_xian(text)
        if reply:
            print(f"XIAN> {reply}")
            _speak_xian(reply)
            time.sleep(0.6)
            # Loguj do GG
            threading.Thread(
                target=_send_xian_voice_capsule, args=(text, reply), daemon=True
            ).start()


def _send_xian_voice_capsule(text: str, reply: str) -> None:
    """Loguje rozmowę głosową z Xian jako kapsułkę."""
    if not _CAPSULE_OK:
        return
    try:
        init_db()
        send_capsule(
            type="fact",
            weight="commentary",
            layer="working",
            owner="Neo",
            sender="Neo",
            recipient="Xian",
            payload={"title": "Voice query to Xian", "summary": text, "voice_reply": reply[:300]},
            open_question="",
            project="voice",
            status="answered",
        )
    except Exception:
        pass


# ── Background capsule reader ─────────────────────────────────────────────────

def _capsule_reader_loop() -> None:
    """Daemon thread — co CAPSULE_READER_INTERVAL sekund sprawdza nowe kapsułki dla Neo."""
    while True:
        time.sleep(CAPSULE_READER_INTERVAL)
        if _conversation_active.is_set():
            continue  # don't speak while conversation is active
        _read_pending_capsules()


def _speech_queue_loop() -> None:
    """Daemon thread — drenuje kolejkę TTS z Claude Code Stop hooka."""
    while True:
        time.sleep(1.5)
        if _conversation_active.is_set():
            continue  # don't speak while conversation is active
        entries = pop_all(max_age_s=30.0)
        for entry in entries:
            text = entry.get("text", "").strip()
            voice = entry.get("voice") or _voice_for("Soryel", text)
            if text:
                _speak_as("Soryel", text, voice=voice)


# ── Claude Code session reader (Soryel TTS) ─────────────────────────────────

def _claude_session_reader_loop() -> None:
    """Daemon thread — nasłuchuje nowych odpowiedzi Soryel w sesjach Claude Code i czyta je głosem."""
    from voice.tts_daemon import (
        find_latest_session, get_last_assistant_entry, clean,
        detect_lang_safe, split_into_chunks,
        POLL_INTERVAL, SETTLE_SECONDS, MAX_CHARS, MIN_CHARS, CHUNK_SIZE, DEBUG,
    )

    last_spoken_uuid = None
    current_session = None

    while True:
        try:
            if _conversation_active.is_set():
                time.sleep(POLL_INTERVAL)
                continue

            session = find_latest_session()
            if not session:
                time.sleep(POLL_INTERVAL)
                continue

            if session != current_session:
                current_session = session
                entry = get_last_assistant_entry(session)
                if entry:
                    last_spoken_uuid = entry[0]
                print(f"[Voice/Soryel] Sesja: {os.path.basename(session)}", flush=True)

            entry = get_last_assistant_entry(session)
            if not entry:
                time.sleep(POLL_INTERVAL)
                continue

            uuid, raw_text = entry
            if uuid == last_spoken_uuid:
                time.sleep(POLL_INTERVAL)
                continue

            time.sleep(SETTLE_SECONDS)
            entry2 = get_last_assistant_entry(session)
            if entry2 and entry2[0] != uuid:
                uuid, raw_text = entry2
            if uuid == last_spoken_uuid:
                time.sleep(POLL_INTERVAL)
                continue

            cleaned = clean(raw_text)
            if MAX_CHARS > 0 and len(cleaned) > MAX_CHARS:
                cleaned = cleaned[:MAX_CHARS]
            if not cleaned or len(cleaned) < MIN_CHARS:
                last_spoken_uuid = uuid
                time.sleep(POLL_INTERVAL)
                continue

            if DEBUG:
                print(f"[Voice/Soryel] CLEAN: {repr(cleaned[:180])}", flush=True)

            last_spoken_uuid = uuid

            chunks = split_into_chunks(cleaned, CHUNK_SIZE)
            if DEBUG:
                print(f"[Voice/Soryel] Chunks: {len(chunks)} ({len(cleaned)} znaków)", flush=True)

            for chunk_text in chunks:
                if _conversation_active.is_set():
                    break
                _speak_as("Soryel", chunk_text)
                time.sleep(0.3)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Voice/Soryel] Błąd: {e}", flush=True)
            time.sleep(POLL_INTERVAL)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    gate = WakeGate()
    router = ChannelRouter()

    # Zarejestruj PID żeby Stop hook wiedział że voice_agent działa
    register_pid()
    import atexit
    atexit.register(unregister_pid)

    # Uruchom reader kapsułek w tle.
    # Domyślnie WYŁĄCZONY — jedynym właścicielem inboxu jest voice.capsule_reader (osobny proces,
    # start_system.sh). Dwa równoległe czytniki = wyścig TOCTOU i gubienie drugiej kapsuły z pary.
    if _CAPSULE_OK and os.getenv("VOICE_AGENT_CAPSULE_READER", "0") == "1":
        t = threading.Thread(target=_capsule_reader_loop, daemon=True)
        t.start()
        print("[Voice] Capsule reader aktywny (GG → TTS).")

    # Uruchom drenaż kolejki Claude Code Stop hook → TTS
    sq = threading.Thread(target=_speech_queue_loop, daemon=True)
    sq.start()
    print("[Voice] Speech queue aktywna (Claude Code → TTS).")

    # Soryel session reader WYŁĄCZONY — speech_queue/Stop hook jest jedynym kanałem Claude Code → TTS.
    # Zapobiega duplikatom (Stop hook wpisuje do queue; session reader by polował JSONL i czytał to samo).
    # if os.getenv("LIRA_ENABLE_SESSION_READER", "0") == "1":
    #     sr = threading.Thread(target=_claude_session_reader_loop, daemon=True)
    #     sr.start()
    #     print("[Voice] Soryel session reader aktywny (Claude Code → TTS).")

    print(f"[Voice] Czekam na hasło 'lira' (Lira/Ollama), '{SORYEL_WAKE}' (Soryel/Claude) lub '{XIAN_WAKE}' (Xian/lokalny)...")

    while True:
        text, triggered_by = gate.listen_for_command(extra_wake_words=[SORYEL_WAKE, XIAN_WAKE])

        if triggered_by == SORYEL_WAKE:
            _soryel_conversation(gate.gate)
        elif triggered_by == XIAN_WAKE:
            _xian_conversation(gate.gate)
        else:
            if not text:
                continue
            print(f"LIRA NEO> {text}")
            result = router.handle(text, interactive=True)
            if not result.reply:
                continue
            print(f"LIRA> {result.reply}")
            speak(result.reply)


if __name__ == "__main__":
    main()
