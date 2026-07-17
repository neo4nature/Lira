from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional


class JsonlLogger:
    """Append-only JSONL logger (one JSON object per line)."""
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def log(self, event: Dict[str, Any]) -> None:
        event = dict(event)
        event.setdefault("ts", time.time())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
