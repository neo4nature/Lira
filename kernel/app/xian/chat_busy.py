"""Single source of truth for the "Xian is generating" flag.

Every path that calls the generative model holds this flag for the duration of the
request; every embedder checks it and defers. Before 2026-07-26 only
xian_inbox_daemon and night_cycle raised it, so voice/router traffic collided with
embedding runs on the same four cores.

Flaga jest LICZONA, nie binarna. Nadawców jest siedmiu i potrafią mówić naraz —
przy zwykłym pliku pierwszy, który skończył, kasował flagę spod wciąż mielącego
sąsiada i embeddery ruszały mu na te same cztery rdzenie. Dlatego w pliku leży
lista trzymających, a znika on dopiero, gdy wyjdzie ostatni.

O tym, czy trzymający żyje, rozstrzyga jego PID, nie wiek pliku. Wiek był
domysłem: proces ubity w trakcie generacji zostawiał plik, a wszyscy odbiorcy
poza routerem pytali o samo `exists()`, więc embeddingi stały do następnej
rozmowy — nie trzy godziny, tylko bez końca. Martwy PID widać od razu.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[3]
FLAG_PATH = Path(os.getenv(
    "LIRA_CHAT_BUSY_FLAG",
    str(_REPO_ROOT / "kernel/data/runtime/state/ollama_chat_busy.flag"),
))
DEFER_ON_CHAT = os.getenv("LIRA_DEFER_EMBEDS_ON_CHAT", "1") == "1"
_LOCK_TIMEOUT_S = float(os.getenv("LIRA_CHAT_BUSY_LOCK_S", "5"))
_LOCK_POLL_S = 0.02


def _proc_start(pid: int) -> str:
    """Znacznik startu procesu — ten sam PID po ponownym użyciu ma inny."""
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as fh:
            # nazwa polecenia bywa w nawiasach i ze spacjami, więc tniemy po ")"
            return fh.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def _alive(entry: Dict[str, Any]) -> bool:
    pid = entry.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    start = _proc_start(pid)
    if not start:
        return False
    # Brak zapamiętanego startu = wpis sprzed tej wersji; sam żywy PID wystarczy.
    return start == entry.get("start", start)


@contextmanager
def _locked():
    lock_path = FLAG_PATH.with_suffix(".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+", encoding="utf-8")
    except OSError:
        yield False
        return
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    held = False
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_LOCK_POLL_S)
        yield held
    finally:
        if held:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def _read_holders() -> List[Dict[str, Any]]:
    try:
        data = json.loads(FLAG_PATH.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        # Plik sprzed tej wersji jest pusty (touch). Istnieje, więc ktoś go
        # postawił, ale nie wiadomo kto — traktujemy jak jednego nieznanego.
        return [{"pid": 0, "who": "legacy"}] if FLAG_PATH.exists() else []
    return [h for h in data if isinstance(h, dict)] if isinstance(data, list) else []


def _write_holders(holders: List[Dict[str, Any]]) -> None:
    if not holders:
        # Odbiorcy sprzed tej wersji pytają o samo exists() — pusta lista musi
        # znaczyć brak pliku, nie plik z pustą tablicą.
        try:
            FLAG_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return
    try:
        FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        FLAG_PATH.write_text(json.dumps(holders, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def is_chat_busy() -> bool:
    """Czy ktokolwiek żywy generuje. Martwe wpisy sprząta po drodze."""
    if not DEFER_ON_CHAT:
        return False
    if not FLAG_PATH.exists():
        return False
    with _locked() as held:
        holders = _read_holders()
        alive = [h for h in holders if _alive(h)]
        # Wpisu "legacy" nie umiemy zweryfikować, więc zostaje — ale tylko on.
        alive += [h for h in holders if h.get("who") == "legacy" and h not in alive]
        if held and len(alive) != len(holders):
            _write_holders(alive)
        return bool(alive)


def raise_flag(who: str = "") -> None:
    with _locked():
        holders = [h for h in _read_holders() if _alive(h)]
        holders.append({
            "pid": os.getpid(),
            "start": _proc_start(os.getpid()),
            "who": who or "unknown",
            "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        _write_holders(holders)


def clear_flag(who: str = "") -> None:
    """Zdejmuje TYLKO własny wpis — sąsiad może wciąż mielić."""
    me = os.getpid()
    with _locked():
        holders = [h for h in _read_holders() if _alive(h)]
        for i, h in enumerate(holders):
            if h.get("pid") == me and (not who or h.get("who") == who):
                holders.pop(i)
                break
        _write_holders(holders)


@contextmanager
def chat_busy(who: str = ""):
    """Hold the flag while generating. Always releases, including on exception."""
    raise_flag(who)
    try:
        yield
    finally:
        clear_flag(who)
