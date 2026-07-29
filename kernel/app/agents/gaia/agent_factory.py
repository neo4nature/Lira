"""
Agent factory: create small helper agents (Python) inside app/agents/gaia/generated/
Conservative: does not overwrite existing agents unless explicitly requested.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

GENERATED_DIR = Path(__file__).resolve().parent / "generated"
REGISTRY_PATH = Path(__file__).resolve().parent / "registry.json"

def _ts() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

def load_registry() -> Dict[str, Any]:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {"agents": []}

def save_registry(reg: Dict[str, Any]) -> None:
    REGISTRY_PATH.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")

def create_agent(name: str, purpose: str, code: str, overwrite: bool = False) -> Path:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = GENERATED_DIR / f"{name}.py"
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists (overwrite=False)")
    path.write_text(code, encoding="utf-8")

    reg = load_registry()
    reg["agents"].append({
        "name": name,
        "purpose": purpose,
        "path": str(path.relative_to(Path(__file__).resolve().parents[3])),
        "created_at": _ts(),
    })
    save_registry(reg)
    return path
