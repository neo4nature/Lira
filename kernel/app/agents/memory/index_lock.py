"""Shared POSIX append lock for cooperating embedding-index writers.

Extracted from the reviewed workshop implementation. The released batch writer,
capsule ingester and reviewed importer all use <index>.lock. Hold it for append,
flush and fsync, never for embedding inference. Existing producer locks remain
separate. A process exit releases flock; uncertain record contents are not
repaired by a lock. Slice/callback options exist for deterministic process tests.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Optional, TextIO, Union


def plik_blokady(index_path: Union[str, Path]) -> Path:
    p = Path(index_path)
    return p.with_name(p.name + ".lock")


@contextmanager
def wylacznie(index_path: Union[str, Path]) -> Iterator[None]:
    """Sekcja krytyczna wspólna dla wszystkich pisarzy tego indeksu."""
    lock = plik_blokady(index_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o664)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # zamknięcie zwalnia flock


def dopisz_linie(f: Union[TextIO, BinaryIO], linia: Union[str, bytes], index_path: Union[str, Path], *,
                 kawalek: Optional[int] = None,
                 po_kawalku: Optional[Callable[[int], None]] = None) -> None:
    """Dopisz JEDEN kompletny rekord (z końcowym \\n) do otwartego w trybie dopisywania pliku.

    `kawalek` i `po_kawalku` istnieją dla testu wieloprocesowego: porcjują zapis i dają punkt
    zatrzymania w środku rekordu, żeby przeplot dało się wymusić barierą, nie szczęściem.
    """
    if (linia[-1:] if isinstance(linia, bytes) else linia[-1:]) not in ("\n", b"\n"):
        raise ValueError("rekord indeksu musi kończyć się znakiem nowej linii")
    with wylacznie(index_path):
        if kawalek:
            for i in range(0, len(linia), kawalek):
                f.write(linia[i:i + kawalek])
                f.flush()
                if po_kawalku:
                    po_kawalku(i // kawalek)
        else:
            f.write(linia)
            f.flush()
        os.fsync(f.fileno())
