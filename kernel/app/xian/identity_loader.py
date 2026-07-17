"""xian/identity_loader.py — wczytuje pełną tożsamość Xiana z plików.

Architektura: Modelfile.xian.v4 trzyma tylko 6 zasad rdzeniowych (kręgosłup).
Reszta tożsamości (IDENTITY, SOUL, STYLE, RULES, MAP, STATE + core)
żyje w kernel/data/memory/identity/xian/ i jest wstrzykiwana jako
preamble do każdego promptu kierowanego do xian-v4.

Cache: pierwszy load przy starcie procesu, potem reuse.
Reload przy zmianie któregoś z plików (mtime check).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("xian.identity_loader")

_IDENTITY_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "memory" / "identity" / "xian"
)

# Kolejność ładowania = kolejność dostępu modelu do warstw tożsamości
_LOAD_ORDER = (
    "xian_core.txt",
    "XIAN_IDENTITY.md",
    "XIAN_SOUL.md",
    "XIAN_STYLE.md",
    "XIAN_RULES.md",
    "XIAN_MAP.md",
    "XIAN_STATE.md",
)

_cache: Optional[str] = None
_cache_mtime: float = 0.0


def _latest_mtime() -> float:
    latest = 0.0
    for name in _LOAD_ORDER:
        p = _IDENTITY_DIR / name
        if p.exists():
            latest = max(latest, p.stat().st_mtime)
    return latest


def _load_files() -> str:
    chunks = []
    for name in _LOAD_ORDER:
        p = _IDENTITY_DIR / name
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                chunks.append(f"### {name}\n{text}")
        except Exception as exc:
            log.warning("[identity_loader] cannot read %s: %s", name, exc)
    return "\n\n".join(chunks)


def get_xian_identity(force_reload: bool = False) -> str:
    """Zwróć pełną tożsamość Xiana jako preamble do promptu.

    Cache invalidates automatycznie gdy któryś plik się zmieni (mtime).
    """
    global _cache, _cache_mtime
    current_mtime = _latest_mtime()
    if force_reload or _cache is None or current_mtime > _cache_mtime:
        _cache = _load_files()
        _cache_mtime = current_mtime
        log.info("[identity_loader] loaded %d chars from %d files", len(_cache), len(_LOAD_ORDER))
    return _cache or ""


def is_xian_model(model_name: str) -> bool:
    """Czy ten model wymaga injection tożsamości Xiana?"""
    if not model_name:
        return False
    m = model_name.lower()
    return m.startswith("xian") or "gemma4" in m


def needs_think_disabled(model_name: str) -> bool:
    """Czy ten model ma wbudowany thinking trace który trzeba wyłączyć?"""
    if not model_name:
        return False
    m = model_name.lower()
    # gemma4 ma thinking, xian-v4 zbudowany z gemma4 ma to samo
    return "gemma4" in m or m.startswith("xian-v") or m == "xian-v4"


__all__ = ("get_xian_identity", "is_xian_model", "needs_think_disabled")
