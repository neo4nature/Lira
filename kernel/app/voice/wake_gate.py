from __future__ import annotations

import os
import re
import time
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from voice.stt_vosk import VoskGate
from voice.tts import speak


def _norm(text: str) -> str:
    text = (text or "").strip().lower()
    # keep letters/numbers/spaces only
    text = re.sub(r"[^a-z0-9ąćęłńóśżź\s]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


DEFAULT_COMMANDS = {
    "status",
    "help",
    "stop",
    "restart",
    "veto status",
    "queue status",
    "memory status",
    "bus status",
}


@dataclass
class GateConfig:
    wake_word: str = os.getenv("LIRA_WAKE_WORD", "lira").strip().lower() or "lira"
    confirm_threshold: float = float(os.getenv("LIRA_STT_CONFIRM_THRESHOLD", "0.65"))
    active_timeout_s: float = float(os.getenv("LIRA_STT_ACTIVE_TIMEOUT", "6.0"))
    silence_timeout_s: float = float(os.getenv("LIRA_STT_SILENCE_TIMEOUT", "0.8"))
    prompt_listen: str = os.getenv("LIRA_WAKE_PROMPT", "Słucham.")
    prompt_confirm: str = os.getenv("LIRA_CONFIRM_PROMPT", "Czy powiedziałeś: {text}?")
    prompt_repeat: str = os.getenv("LIRA_REPEAT_PROMPT", "Powtórz proszę.")


class WakeGate:
    """Wake-word gate for local (offline) voice control.

    Flow:
      IDLE: listen for wake-word only
      ACTIVE: capture next utterance
      CONFIRM: if uncertain, ask yes/no
    """

    def __init__(self, config: Optional[GateConfig] = None):
        self.cfg = config or GateConfig()
        self.gate = VoskGate(wake_word=self.cfg.wake_word)
        self._listen_popup = None

    def _has(self, cmd: str) -> bool:
        return subprocess.call(["bash", "-lc", f"command -v {cmd} >/dev/null 2>&1"]) == 0

    def _popup_listening_start(self) -> None:
        if os.getenv("LIRA_LISTEN_POPUP", "0") != "1":
            return
        if os.getenv("LIRA_NO_GUI", "0") == "1":
            return
        # Use zenity if available for an obvious visual cue; best-effort only.
        if self._has("zenity"):
            try:
                self._listen_popup = subprocess.Popen(
                    ["zenity", "--info", "--title", "Lira", "--text", "Listening…", "--no-wrap"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                self._listen_popup = None
        elif self._has("notify-send"):
            subprocess.Popen(["notify-send", "Lira", "Listening…"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _popup_listening_stop(self) -> None:
        p = self._listen_popup
        self._listen_popup = None
        if not p:
            return
        try:
            p.terminate()
        except Exception:
            pass

    def _is_clean_command(self, text: str) -> bool:
        t = _norm(text)
        return t in DEFAULT_COMMANDS

    def _strip_wake(self, text: str, wake: str | None = None) -> str:
        t = _norm(text)
        w = wake or self.cfg.wake_word
        if t.startswith(w + " "):
            t = t[len(w) + 1:]
        elif t == w:
            t = ""
        return t.strip()

    def _needs_confirm(self, text: str, confidence: float) -> bool:
        t = _norm(text)
        if not t:
            return True
        if self._is_clean_command(t):
            return False
        # Heuristics: low confidence OR text looks garbled.
        if confidence < self.cfg.confirm_threshold:
            return True

        tokens = t.split()
        # For very short phrases, confirm only when confidence isn't clearly high.
        if len(tokens) <= 2:
            if confidence < min(0.85, self.cfg.confirm_threshold + 0.15):
                return True

        # Garbled signal: too many 1-letter tokens (common with STT glitches)
        one_letter = sum(1 for tok in tokens if len(tok) == 1)
        if one_letter >= 2:
            return True

        return False

    def listen_for_command(self, extra_wake_words: list[str] | None = None) -> tuple[Optional[str], str]:
        """Wait for wake word, then capture command.

        Returns (text, triggered_by) where triggered_by is the wake word detected.
        Returns (None, '') on timeout.
        """
        triggered_by = self.gate.wait_for_wake(extra_words=extra_wake_words)
        if self.cfg.prompt_listen:
            speak(self.cfg.prompt_listen)
            time.sleep(0.5)  # odczekaj aż echo z głośnika opadnie

        self._popup_listening_start()

        # ACTIVE: capture utterance
        start = time.time()
        while True:
            if time.time() - start > self.cfg.active_timeout_s:
                self._popup_listening_stop()
                return None, triggered_by

            text, conf = self.gate.listen_utterance(silence_timeout_s=self.cfg.silence_timeout_s)
            text = self._strip_wake(text, wake=triggered_by)
            if not text:
                continue

            if self._is_clean_command(text):
                self._popup_listening_stop()
                return _norm(text), triggered_by

            if self._needs_confirm(text, conf):
                speak(self.cfg.prompt_confirm.format(text=text))
                time.sleep(0.5)
                yn = self.gate.listen_yes_no(timeout_s=4.0)
                if yn is True:
                    self._popup_listening_stop()
                    return _norm(text), triggered_by
                speak(self.cfg.prompt_repeat)
                time.sleep(0.5)
                start = time.time()
                continue

            self._popup_listening_stop()
            return _norm(text), triggered_by
