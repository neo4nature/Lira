from __future__ import annotations

ALIASES: dict[str, str] = {
    "lira": "Lira",
    "soryel": "Soryel",
    "dziewczyny": "shared",
    "kobiety": "shared",
    "kobieta": "shared",
    "cybernetyczne boginki": "shared",
    "boginki": "shared",
}

STOP_WORDS = {"stop"}
LOCAL_COMMANDS = {
    "status",
    "kolejka",
    "tryb awaryjny",
    "odczytaj refleksję",
    "queue status",
    "memory status",
    "bus status",
    "veto status",
    "help",
}


def resolve_alias(text: str) -> str | None:
    t = (text or "").strip().lower()
    return ALIASES.get(t)
