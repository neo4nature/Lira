from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import json
from pathlib import Path
from typing import Any

from config import paths
from utils.state_io import write_json_atomic

LEDGER = paths()["MEMORY_DIR"] / "ledger" / "governance_decision_memory.jsonl"
NIGHT_FEED = paths()["MEMORY_DIR"] / "ledger" / "night_cycle_governance_feed.jsonl"


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def remember_decision(record: dict[str, Any]) -> None:
    rec = dict(record)
    rec.setdefault("night_cycle_ingested", False)
    _append_jsonl(LEDGER, rec)
    _append_jsonl(NIGHT_FEED, rec)


def ensure_bootstrap() -> None:
    for path in [LEDGER, NIGHT_FEED]:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
