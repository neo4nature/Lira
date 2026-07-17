from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from config import paths
from governance.capsule_bus import send_capsule
from channeling.channel_aliases import ALIASES, STOP_WORDS, LOCAL_COMMANDS
from mcl.router import route
from utils.state_io import now_ts, read_json, write_json_atomic

LISTEN_STATE_NAME = "listen_state.json"
DEFAULT_PROJECT = "ecosystem_monitor"


def _state_path() -> Path:
    return paths()["STATE_DIR"] / LISTEN_STATE_NAME


def read_listen_state() -> dict[str, Any]:
    return read_json(str(_state_path()), default={
        "listening": False,
        "active_channel": None,
        "last_wake_word": None,
        "updated_at": None,
    })


def write_listen_state(state: dict[str, Any]) -> None:
    state = dict(state)
    state["updated_at"] = now_ts()
    write_json_atomic(str(_state_path()), state)


@dataclass
class RouteResult:
    mode: str
    channel: Optional[str]
    text: str
    reply: str
    capsule_id: Optional[str] = None


class ChannelRouter:
    def __init__(self, project: str = DEFAULT_PROJECT):
        self.project = project

    def _match_prefix_alias(self, text: str) -> tuple[Optional[str], str]:
        raw = (text or "").strip()
        low = raw.lower()
        for alias, channel in sorted(ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
            if low == alias:
                return channel, ""
            if low.startswith(alias + " "):
                return channel, raw[len(alias):].strip()
        return None, raw

    def _set_channel(self, channel: str, wake_word: str) -> None:
        write_listen_state({
            "listening": True,
            "active_channel": channel,
            "last_wake_word": wake_word,
        })

    def _stop(self) -> RouteResult:
        write_listen_state({
            "listening": False,
            "active_channel": None,
            "last_wake_word": "stop",
        })
        return RouteResult(mode="stop", channel=None, text="stop", reply="Zatrzymuję nasłuch kanału zewnętrznego.")

    def _handle_local(self, raw_text: str, local_text: str | None = None, *, interactive: bool = False) -> RouteResult:
        routed_text = local_text if local_text is not None else raw_text
        reply = route(routed_text, interactive=interactive)
        return RouteResult(mode="local", channel="local", text=raw_text, reply=reply)

    def _send_external(self, channel: str, text: str) -> RouteResult:
        payload = {
            "title": f"Wejście {channel}",
            "summary": text,
            "source": "channel_router",
            "requested_channel": channel,
        }
        capsule_id = send_capsule(
            type="task",
            weight="commentary",
            layer="working",
            owner="shared" if channel == "shared" else channel,
            sender="Neo",
            recipient=channel,
            payload=payload,
            open_question="Odpowiedz lub przejdź do walidacji governance, zależnie od kanału.",
            project=self.project,
        )
        return RouteResult(
            mode="external",
            channel=channel,
            text=text,
            reply=f"Przekazuję do kanału {channel} przez GG.",
            capsule_id=capsule_id,
        )

    def handle(self, text: str, local_text: str | None = None, *, interactive: bool = False) -> RouteResult:
        raw = (text or "").strip()
        if not raw:
            return RouteResult(mode="ignore", channel=None, text="", reply="")

        if raw.lower() in STOP_WORDS:
            return self._stop()

        explicit_channel, remainder = self._match_prefix_alias(raw)
        if explicit_channel:
            self._set_channel(explicit_channel, raw.split()[0].lower())
            if not remainder:
                return RouteResult(mode="channel_switch", channel=explicit_channel, text=raw, reply=f"Kanał {explicit_channel} aktywny.")
            return self._send_external(explicit_channel, remainder)

        state = read_listen_state()
        active_channel = state.get("active_channel") if state.get("listening") else None

        if raw.lower() in LOCAL_COMMANDS:
            return self._handle_local(raw, local_text=local_text, interactive=interactive)

        if active_channel in {"Lira", "Soryel", "shared"}:
            return self._send_external(active_channel, raw)

        return self._handle_local(raw, local_text=local_text, interactive=interactive)
