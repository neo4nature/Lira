from __future__ import annotations
import os
import subprocess
from pathlib import Path
from typing import Dict, Any

from agents.core.runtime_stream import append_event
from agents.core.snapshot_context import load_snapshot
from agents.core.idle_gate import allows_background

def run_external_cognitive(script_path: Path) -> int:
    snap = load_snapshot()
    ok, reason = allows_background(require_veto_none=True)
    if not ok:
        append_event("cognitive_skipped", "cognitive_bridge", {"reason": reason, **snap})
        return 0

    append_event("cognitive_start", "cognitive_bridge", {"script": str(script_path), **snap})

    env = os.environ.copy()
    # expose roots explicitly for external cognitive module
    env.setdefault("LIRA_DATA_ROOT", os.getenv("LIRA_DATA_ROOT", ""))
    env.setdefault("LIRA_ARCHIVE_ROOT", os.getenv("LIRA_ARCHIVE_ROOT", ""))
    env.setdefault("LIRA_SECURE_ROOT", os.getenv("LIRA_SECURE_ROOT", ""))

    try:
        proc = subprocess.run(["python3", str(script_path)], env=env, check=False)
        rc = proc.returncode
    except Exception as e:
        append_event("cognitive_error", "cognitive_bridge", {"error": str(e), "script": str(script_path), **snap})
        return 1

    append_event("cognitive_end", "cognitive_bridge", {"script": str(script_path), "returncode": rc, **snap})
    return rc

def main():
    # Prefer an absolute, CWD-independent default.
    # This file lives at: <repo>/kernel/app/agents/cognition/cognitive_bridge.py
    # repo_root == parents[4] -> <repo>
    repo_root = Path(__file__).resolve().parents[4]
    default_script = repo_root / "cognitive" / "night_cycle" / "cognitive_night_cycle.py"
    script = Path(os.getenv("LIRA_COGNITIVE_SCRIPT", str(default_script)))
    if not script.exists():
        append_event("cognitive_missing", "cognitive_bridge", {"script": str(script)})
        return
    run_external_cognitive(script)

if __name__ == "__main__":
    main()
