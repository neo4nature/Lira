from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

from config import paths

SRC_DEFAULT = "Lira_Embedding_Records_v0_2_2026-02-23.jsonl"
MODEL = os.getenv("LIRA_BOOTSTRAP_EMBED_MODEL", os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest"))
OLLAMA_EMBED_URL = os.getenv("LIRA_OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embeddings")
LIMIT = int(os.getenv("LIRA_BOOTSTRAP_EMBED_LIMIT", "64"))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _ollama_embed(text: str, timeout_s: int = int(os.getenv("LIRA_OLLAMA_EMBED_TIMEOUT", "1800"))) -> List[float] | None:
    try:
        r = requests.post(OLLAMA_EMBED_URL, json={"model": MODEL, "input": text}, timeout=timeout_s)
        r.raise_for_status()
        data = r.json()
        emb = (data.get("embeddings") or [[]])[0] if isinstance(data.get("embeddings"), list) else data.get("embedding")
        if isinstance(emb, list) and emb:
            return emb
    except Exception:
        return None
    return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def main() -> int:
    p = paths()
    src = p["MEMORY_DIR"] / "ledger" / "bootstrap" / SRC_DEFAULT
    if not src.exists():
        return 0
    idx = p["EMBEDDINGS_INDEX"]
    idx.parent.mkdir(parents=True, exist_ok=True)

    existing = set()
    if idx.exists():
        try:
            for line in idx.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]:
                row = json.loads(line)
                existing.add(row.get("id"))
        except Exception:
            pass

    inserted = 0
    with src.open("r", encoding="utf-8") as f, idx.open("a", encoding="utf-8") as out:
        for n, line in enumerate(f):
            if n >= LIMIT:
                break
            try:
                row = json.loads(line)
            except Exception:
                continue
            text = row.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            rid = f"bootstrap:{row.get('id') or n}:{_sha256(text)[:10]}"
            if rid in existing:
                continue
            vec = _ollama_embed(text[:6000])
            if not vec:
                continue
            rec = {
                "schema": "lira.embedding_record.v1",
                "id": rid,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": MODEL,
                "source": {"kind": "bootstrap_record", "path": str(src), "offset": n, "len": len(text)},
                "text": text[:2000],
                "vector": vec,
                "meta": {"sha256": _sha256(text), "legacy_meta": row.get("meta", {})},
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            inserted += 1
    report = {
        "schema": "lira.bootstrap_embeddings_report.v1",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": str(src),
        "model": MODEL,
        "inserted": inserted,
    }
    (p["STATE_DIR"] / "bootstrap_embeddings_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
