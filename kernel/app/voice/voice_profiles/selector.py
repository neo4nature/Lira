"""
Voice profile selector.
Stores last selected profile to data/shared_context/state/voice.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

def _profiles_path() -> Path:
    return Path(__file__).resolve().parent / "profiles.json"

def _state_path() -> Path:
    return Path(__file__).resolve().parents[4] / "data" / "shared_context" / "state" / "voice.json"

def _ts() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

def load_profiles() -> Dict[str, Any]:
    return json.loads(_profiles_path().read_text(encoding="utf-8"))

def select(profile_key: str) -> Dict[str, Any]:
    profiles = load_profiles()
    prof = profiles.get(profile_key) or profiles.get("default") or {}
    state = {"timestamp": _ts(), "profile": profile_key, "resolved": prof}
    sp = _state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state

def auto_pick(lang: str = "pl") -> Dict[str, Any]:
    key = "default"
    if lang.lower().startswith("en"):
        key = "en"
    elif lang.lower().startswith("de"):
        key = "de"
    return select(key)
