"""
GaiaAgent: detects delegable tasks and can create helper agents via agent_factory.
Minimal v1: exposes a 'plan' method and a 'spawn' method.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any

from .agent_factory import create_agent

@dataclass
class SpawnRequest:
    name: str
    purpose: str
    code: str
    overwrite: bool = False

class GaiaAgent:
    def plan(self, problem: str) -> Dict[str, Any]:
        # Minimal heuristic; can be expanded by OLAMA later.
        delegable = any(k in problem.lower() for k in ["monitor", "check", "clean", "download", "install", "summarize", "transcribe"])
        return {
            "delegable": delegable,
            "suggestion": "Create a helper agent to offload this recurring task." if delegable else "Handle inline (not worth spawning an agent yet)."
        }

    def spawn(self, req: SpawnRequest) -> Dict[str, Any]:
        path = create_agent(req.name, req.purpose, req.code, overwrite=req.overwrite)
        return {"spawned": True, "path": str(path)}
