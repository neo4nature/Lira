"""Jedyne wejście do queue_tasks.json — wielu producentów, jeden plik.

Zamek leży w OSOBNYM pliku obok kolejki: zapis podmienia i-węzeł przez
os.replace, więc flock trzymany na samej kolejce zniknąłby razem ze starym
plikiem i drugi proces wszedłby do środka.

Czytelnicy nie biorą zamka i nie muszą: os.replace jest atomowy, więc odczyt
trafia zawsze na którąś pełną wersję pliku, nigdy na połowę zapisu.
"""

import fcntl
import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import ensure_dirs, paths
from agents.memory.memory_loader import write_json

log = logging.getLogger("queue_utils")

# Kto nie doczeka się zamka, ma się poddać i zameldować. Nocny cykl nie może
# stanąć na kolejce dlatego, że jakiś proces padł z otwartym zamkiem.
LOCK_TIMEOUT_S = float(os.getenv("LIRA_QUEUE_LOCK_TIMEOUT_S", "10"))
_LOCK_POLL_S = 0.05


def now():
    return datetime.now().isoformat(timespec="seconds")


def _queue_path() -> Path:
    return Path(paths()["QUEUE_FILE"])


@contextmanager
def _locked(qf: Path, timeout_s: float):
    """Zamek na `<kolejka>.lock`. Oddaje False, gdy nie doczekał w limicie."""
    lock_path = qf.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+", encoding="utf-8")
    deadline = time.monotonic() + max(0.0, timeout_s)
    held = False
    try:
        while True:
            try:
                # flock nie ma timeoutu — stąd nieblokujące próby w pętli.
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


def load() -> List[Dict[str, Any]]:
    """Kolejka albo pusta lista — uszkodzony i pusty plik wyglądają tak samo."""
    try:
        data = json.loads(_queue_path().read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def mutate(fn: Callable[[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]],
           timeout_s: float = LOCK_TIMEOUT_S) -> Optional[List[Dict[str, Any]]]:
    """Zamek → odczyt → fn(zadania) → zapis → zwolnienie.

    Odczyt i zapis MUSZĄ być pod tym samym zamkiem — inaczej dwa procesy czytają
    ten sam stan i drugi zapis wymazuje pierwszy.

    `fn` zwraca nową listę albo None, gdy zapis jest niepotrzebny (np. warunek
    wstępny nie przeszedł) — decyzja zapada już pod zamkiem, więc nie zdezaktualizuje
    się między odczytem a zapisem.

    Zwraca listę po zmianie; None oznacza zajęty zamek — wtedy nic nie zapisano.
    """
    ensure_dirs()
    qf = _queue_path()
    qf.parent.mkdir(parents=True, exist_ok=True)
    with _locked(qf, timeout_s) as held:
        if not held:
            log.warning("[queue] zamek zajęty dłużej niż %.1fs — pomijam zapis do %s", timeout_s, qf)
            return None
        current = load()
        updated = fn(current)
        if updated is None:
            return current
        write_json(qf, updated)
        return updated


def load_queue() -> List[Dict[str, Any]]:
    return load()


def save_queue(queue: List[Dict[str, Any]]):
    mutate(lambda _: queue)


def enqueue(task: Dict[str, Any]):
    def _add(data):
        data.append(task)
        return data
    mutate(_add)
    return _queue_path()


def make_task(kind: str, source: str, payload: Any, importance: int = 3, tags=None) -> Dict[str, Any]:
    return {
        "ts": now(),
        "kind": kind,
        "source": source,
        "importance": int(importance),
        "tags": tags or [],
        "payload": payload,
    }
