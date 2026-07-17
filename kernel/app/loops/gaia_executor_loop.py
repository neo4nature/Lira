"""Gaia executor daemon.

Reads queue_tasks.json and delegates tasks marked as delegable=true to GaiaAgent.spawn().

Safety:
  - Disabled by default. Enable with LIRA_ENABLE_GAIA=1
  - Respects idle gate + veto + memory_mode.
  - Processes at most one delegable task per cycle.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List

from agents.core.idle_gate import allows_background
from agents.core.runtime_stream import append_event
from agents.core.queue_utils import load_queue, save_queue
from agents.memory.memory_loader import read_json
from agents.gaia.gaia_agent import GaiaAgent, SpawnRequest
from config import runtime_dir, paths, ensure_dirs


def _memory_mode() -> str:
    try:
        P = paths()
        mm_path = Path(P.get("MEMORY_MODE", runtime_dir() / "state" / "memory_mode.json"))
        mm = read_json(mm_path, default={})
        return str(mm.get("mode") or "normal").lower()
    except Exception:
        return "normal"


def _task_is_delegable(task: Dict[str, Any]) -> bool:
    if bool(task.get("delegable", False)):
        return True
    # Backward compatible: allow delegable flag inside payload
    payload = task.get("payload")
    if isinstance(payload, dict) and bool(payload.get("delegable", False)):
        return True
    return False


def main() -> None:
    ensure_dirs()
    poll_s = float(os.getenv("LIRA_GAIA_POLL_SECONDS", "10"))
    max_tries = int(os.getenv("LIRA_GAIA_MAX_TRIES", "3"))

    agent = GaiaAgent()
    append_event("gaia_executor_start", "gaia_executor", {"poll_s": poll_s})

    while True:
        try:
            if os.getenv("LIRA_ENABLE_GAIA", "0") != "1":
                time.sleep(poll_s)
                continue

            if _memory_mode() == "emergency":
                time.sleep(poll_s)
                continue

            ok, reason = allows_background(require_veto_none=False)
            if not ok:
                time.sleep(poll_s)
                continue

            q = load_queue()
            if not q:
                time.sleep(poll_s)
                continue

            processed = False
            new_q: List[Dict[str, Any]] = []

            for t in q:
                if processed:
                    new_q.append(t)
                    continue

                if not isinstance(t, dict):
                    continue

                if t.get("status") in {"done", "failed"}:
                    new_q.append(t)
                    continue

                if not _task_is_delegable(t):
                    new_q.append(t)
                    continue

                tries = int(t.get("tries", 0))
                if tries >= max_tries:
                    t["status"] = "failed"
                    t["error"] = "max_tries_exceeded"
                    new_q.append(t)
                    continue

                # Build a spawn request
                name = str(t.get("agent_name") or f"gaia_helper_{int(time.time())}")
                purpose = str(t.get("purpose") or f"Delegated task: {t.get('kind','task')}")
                code = str(t.get("code") or "# TODO: implement agent behavior\n")
                overwrite = bool(t.get("overwrite", False))

                append_event("gaia_spawn_start", "gaia_executor", {"task": t, "agent_name": name})
                try:
                    res = agent.spawn(SpawnRequest(name=name, purpose=purpose, code=code, overwrite=overwrite))
                    t["status"] = "done" if res.get("spawned") else "failed"
                    t["result"] = res
                except Exception as e:
                    t["tries"] = tries + 1
                    t["status"] = "failed" if (tries + 1) >= max_tries else "pending"
                    t["error"] = str(e)
                append_event("gaia_spawn_end", "gaia_executor", {"task": t})

                new_q.append(t)
                processed = True

            save_queue(new_q)

        except Exception as e:
            append_event("gaia_executor_error", "gaia_executor", {"error": str(e)})

        time.sleep(poll_s)


if __name__ == "__main__":
    main()
