"""Lira Night Cycle (v2)

This file is intentionally **standalone** so you can run it safely without touching
run_text (which may append memory and slow the response).

What it does:
  1) Loads NEW events from: data/shared_context/events
  2) Produces a strict JSON consolidation with 3 lists:
       {"daily":[], "strategic":[], "open_questions":[]}
  3) Writes outputs into data/runtime/rebuild/ and logs a success/failure event.
  4) Updates state using processed *filenames* (robust even if you delete old files).

Dependencies:
  - Ollama running locally (default http://127.0.0.1:11434)
  - requests

Usage:
  python3 night_cycle.py
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

from config import ensure_dirs, paths
from agents.core.runtime_stream import append_event
from agents.memory.turmalin_cache import append_record as turmalin_append
from agents.memory.base_index_sqlite import upsert_record as base_upsert


# === Chat busy gate ===
from xian.chat_busy import chat_busy, is_chat_busy as _is_chat_busy

# === Ollama endpoints ===
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_EMBED_URL = os.getenv("LIRA_OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed")

# === Models (override via env if you want) ===
LLM_MODEL = os.getenv("LIRA_NIGHT_MODEL", os.getenv("LIRA_FAST_MODEL", "qwen2.5:3b"))
EMBED_MODEL = os.getenv("LIRA_EMBED_MODEL", "mxbai-embed-large:latest")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class NightState:
    processed_files: List[str]
    last_consolidation: str | None

    @staticmethod
    def load(state_path: Path) -> "NightState":
        if not state_path.exists():
            return NightState(processed_files=[], last_consolidation=None)
        try:
            data = json.loads(state_path.read_text(encoding="utf-8", errors="ignore") or "{}")
            return NightState(
                processed_files=list(data.get("processed_files") or []),
                last_consolidation=data.get("last_consolidation"),
            )
        except Exception:
            return NightState(processed_files=[], last_consolidation=None)

    def save(self, state_path: Path) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "processed_files": self.processed_files,
                    "last_consolidation": self.last_consolidation,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _log_event(kind: str, payload: Dict[str, Any]) -> Path:
    p = paths()
    events_dir = p["EVENTS_DIR"]
    events_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{kind}.json"
    fp = events_dir / fname
    fp.write_text(
        json.dumps({"ts": _now(), "kind": kind, "payload": payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return fp


def _list_event_files(events_dir: Path) -> List[Path]:
    return sorted([p for p in events_dir.glob("*.json") if p.is_file()])


def _load_new_events(events_dir: Path, state: NightState) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return (events, new_processed_filenames)."""
    files = _list_event_files(events_dir)
    new_files = [f for f in files if f.name not in set(state.processed_files)]
    events: List[Dict[str, Any]] = []
    for f in new_files:
        try:
            events.append(json.loads(f.read_text(encoding="utf-8", errors="ignore") or "{}"))
        except Exception:
            events.append({"kind": "invalid_json", "payload": {"file": f.name}})
    return events, [f.name for f in new_files]




def _load_governance_feed(pths: Dict[str, Any], limit: int = 50) -> Tuple[List[Dict[str, Any]], List[int]]:
    ledger = Path(pths["MEMORY_DIR"]) / "ledger" / "night_cycle_governance_feed.jsonl"
    records: List[Dict[str, Any]] = []
    selected_indices: List[int] = []
    if not ledger.exists():
        return records, selected_indices
    try:
        lines = ledger.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return [], []

    parsed: List[Tuple[int, Dict[str, Any]]] = []
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("night_cycle_ingested") is True:
            continue
        parsed.append((idx, rec if isinstance(rec, dict) else {}))

    for idx, rec in parsed[-limit:]:
        records.append(rec)
        selected_indices.append(idx)
    return records, selected_indices


def _mark_governance_feed_ingested(pths: Dict[str, Any], indices: List[int]) -> None:
    if not indices:
        return
    ledger = Path(pths["MEMORY_DIR"]) / "ledger" / "night_cycle_governance_feed.jsonl"
    if not ledger.exists():
        return
    try:
        lines = ledger.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return

    changed = False
    index_set = set(indices)
    for idx in index_set:
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx].strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("night_cycle_ingested") is True:
            continue
        rec["night_cycle_ingested"] = True
        rec["ingested_at"] = _now()
        lines[idx] = json.dumps(rec, ensure_ascii=False)
        changed = True

    if changed:
        tmp = ledger.with_suffix(ledger.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        tmp.replace(ledger)


def _build_governance_block(records: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for rec in records:
        verdict = rec.get("verdict", "?")
        summary = str(rec.get("summary", "")).strip()
        if not summary:
            continue
        lines.append(f"[governance:{verdict}] {summary}")
    return "\n".join(lines).strip()

def _build_event_block(events: List[Dict[str, Any]]) -> str:
    """Turn events into a compact text block for the consolidator model."""
    lines: List[str] = []
    for e in events:
        kind = (e.get("kind") or "").strip()
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}

        # Skip Lira's raw outputs to avoid feeding the model its own noise.
        if kind == "assistant_output":
            continue

        text = payload.get("text") or payload.get("user") or payload.get("message")
        if isinstance(text, str) and text.strip():
            lines.append(f"[{kind}] {text.strip()}")
    return "\n".join(lines).strip()


def _call_ollama_generate(prompt: str) -> str:
    with chat_busy():
        r = requests.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0,
                    "num_predict": int(os.getenv("LIRA_NIGHT_MAX_TOKENS", "600")),
                },
            },
            timeout=(10, int(os.getenv("LIRA_OLLAMA_GENERATE_TIMEOUT", "10800"))),
        )
    r.raise_for_status()
    return (r.json().get("response") or "").strip()


def _call_ollama_embed(text: str) -> List[float] | None:
    timeout_s = int(os.getenv("LIRA_OLLAMA_EMBED_TIMEOUT", "1800"))
    r = requests.post(
        OLLAMA_EMBED_URL,
        json={"model": EMBED_MODEL, "input": text},
        timeout=(10, timeout_s),
    )
    r.raise_for_status()
    data = r.json()
    emb = (data.get("embeddings") or [[]])[0] if isinstance(data.get("embeddings"), list) else data.get("embedding")
    return emb if isinstance(emb, list) else None


def _extract_json_object(text: str) -> Dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def _validate_consolidation(obj: Any) -> bool:
    required = {"daily", "strategic", "open_questions"}
    if not isinstance(obj, dict) or set(obj.keys()) != required:
        return False
    for k in required:
        v = obj.get(k)
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            return False
    return True


# === Daily snapshot + human-readable MD ===

def _read_memory_mode(pths: Dict[str, Any]) -> Dict[str, Any]:
    """Read memory_mode.json written by RegulatorLoop (best-effort).

    Used to skip heavy work under resource pressure.
    """
    try:
        state_dir = Path(pths.get('STATE_DIR', Path(pths['RUNTIME_DIR']) / 'state'))
        mm_path = state_dir / 'memory_mode.json'
        if mm_path.exists():
            return json.loads(mm_path.read_text(encoding='utf-8', errors='ignore') or '{}')
    except Exception:
        return {}
    return {}


def _safe_read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass
    return default


def _write_daily_snapshot(pths: Dict[str, Any], consolidation: Dict[str, Any], new_files: List[str]) -> Tuple[Path, Path]:
    """Write one canonical snapshot per day (JSON) and a human-readable MD report."""
    today = datetime.now().strftime('%Y-%m-%d')
    year = datetime.now().strftime('%Y')

    snapshots_dir = Path(pths["MEMORY_DIR"]) / 'snapshots' / year
    md_dir = Path(pths["MEMORY_DIR"]) / 'snapshots_md' / year
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)

    state_dir = Path(pths.get('STATE_DIR', Path(pths['RUNTIME_DIR']) / 'state'))
    bus = _safe_read_json(state_dir / 'bus_signals.json', {})
    # Veto is owned/published on the bus (DecisionLoop owns bus_signals.json).
    # Do not read phantom state files that may never be written.
    veto = bus.get('operational_veto', {}) if isinstance(bus, dict) else {}
    locks = _safe_read_json(state_dir / 'model_locks.json', {})

    snapshot = {
        'schema': 'lira.daily_snapshot.v1',
        'date': today,
        'closed_at': datetime.now().isoformat(timespec='seconds'),
        'version': {
            'git': os.getenv('LIRA_GIT_COMMIT', ''),
            'tag': os.getenv('LIRA_VERSION', ''),
            'llm_model': LLM_MODEL,
            'embed_model': EMBED_MODEL,
        },
        'inputs': {
            'new_event_files': new_files,
        },
        'state': {
            'bus_signals': bus,
            'operational_veto': veto,
            'model_locks': locks,
        },
        'consolidation': consolidation,
    }

    snap_path = snapshots_dir / f'{today}.snapshot.json'
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')

    # MD report (human)
    md_path = md_dir / f'{today}.md'
    md = []
    md.append(f'# Lira — Dzienny Obraz: {today}')
    md.append('')
    md.append('## 1) Stan kanoniczny')
    md.append(f'- Zamknięcie: {snapshot["closed_at"]}')
    md.append(f'- Model LLM: {LLM_MODEL}')
    md.append(f'- Model embed: {EMBED_MODEL}')
    if isinstance(veto, dict) and veto.get('active'):
        md.append(f'- Veto: ACTIVE — {veto.get("reason", "")}'.strip())
    else:
        md.append('- Veto: NONE')
    md.append('')
    md.append('## 2) Konsolidacja (Night Cycle)')
    for k, title in [('daily','Daily'), ('strategic','Strategic'), ('open_questions','Open Questions')]:
        md.append(f'### {title}')
        items = consolidation.get(k, []) if isinstance(consolidation, dict) else []
        if not items:
            md.append('- (brak)')
        else:
            for it in items:
                md.append(f'- {it}')
        md.append('')
    md.append('## 3) Bus signals (skrót)')
    if isinstance(bus, dict):
        md.append('```json')
        md.append(json.dumps(bus, ensure_ascii=False, indent=2)[:4000])
        md.append('```')
    else:
        md.append('- (brak)')
    md.append('')
    md.append('## 4) Nowe eventy')
    for fn in new_files[:50]:
        md.append(f'- {fn}')
    if len(new_files) > 50:
        md.append(f'- ... +{len(new_files)-50} więcej')
    md.append('')

    # Task trail from diary.jsonl (if available)
    diary_path = Path(pths["MEMORY_DIR"]) / "diary" / year / f"{today}.diary.jsonl"
    md.append('## 5) Task Trail (Diary)')
    if diary_path.exists() and diary_path.stat().st_size > 0:
        md.append('| Czas | Kind | Trace ID | Intent | Wynik |')
        md.append('|------|------|----------|--------|-------|')
        try:
            with diary_path.open('r', encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f):
                    if i > 200:
                        md.append('| ... | ... | ... | ... | ... |')
                        break
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    ts = obj.get('ts')
                    kind = obj.get('kind', '')
                    trace = obj.get('trace_id', '')
                    intent = obj.get('intent', '')
                    ok = 'OK'
                    md.append(f'| {ts} | {kind} | {trace} | {intent} | {ok} |')
        except Exception:
            md.append('- (błąd odczytu diary)')
    else:
        md.append('- (brak diary dla dnia)')
    md.append('')
    md.append('## 6) Plan na jutro')
    md.append('- [ ] Napraw / usprawnij: ...')
    md.append('- [ ] ...')

    md_path.write_text('\n'.join(md), encoding='utf-8')
    return snap_path, md_path


def _write_memory_state(pths: Dict[str, Any], *, snapshot_date: str, snapshot_path: Path, md_path: Path, capsules_written: int = 0) -> None:
    """Write lightweight memory_state.json for DecisionLoop (single source of truth for memory readiness flags)."""
    state_dir = Path(pths.get('STATE_DIR', Path(pths['RUNTIME_DIR']) / 'state'))
    ms_path = state_dir / 'memory_state.json'
    base_db = Path(pths.get('BASE_INDEX_DB', Path(pths['MEMORY_DIR']) / 'root' / 'base_index.sqlite3'))
    ms = {
        "schema": "lira.memory_state.v1",
        "updated_at": _now(),
        "snapshot_latest_date": snapshot_date,
        "snapshot_ready": True,
        "snapshot_path": str(snapshot_path),
        "snapshot_md_path": str(md_path),
        "snapshot_capsules_written": int(capsules_written),
        "base_index_mtime": base_db.stat().st_mtime if base_db.exists() else None,
        "base_index_bytes": base_db.stat().st_size if base_db.exists() else 0,
    }
    # Best-effort write
    ms_path.parent.mkdir(parents=True, exist_ok=True)
    ms_path.write_text(json.dumps(ms, ensure_ascii=False, indent=2), encoding='utf-8')


def _write_snapshot_capsules(snapshot_date: str, consolidation: Dict[str, Any]) -> int:
    """Embed and write daily snapshot capsules into TurmalinCache; promote a stable capsule into BaseIndex.

    We keep capsules small and bounded to avoid RAM bloat.
    """
    written = 0
    try:
        # Small capsule per item
        for bucket in ("daily", "strategic", "open_questions"):
            items = consolidation.get(bucket, []) if isinstance(consolidation, dict) else []
            if not isinstance(items, list):
                continue
            for it in items[:60]:
                text = (it or "").strip()
                if not text:
                    continue
                vec = _call_ollama_embed(text)
                if not vec:
                    continue
                turmalin_append(
                    kind="daily_snapshot_capsule",
                    text=text,
                    vector=vec,
                    meta={"snapshot_date": snapshot_date, "bucket": bucket, "pin": False},
                )
                written += 1

        # Promote ONE stable capsule to BaseIndex (strategic rollup) to keep the base small.
        strategic = consolidation.get("strategic", []) if isinstance(consolidation, dict) else []
        if isinstance(strategic, list) and strategic:
            rollup = " ".join([str(x).strip() for x in strategic if str(x).strip()])[:4000]
            if rollup:
                vec = _call_ollama_embed(rollup)
                if vec:
                    rid = f"snapshot:{snapshot_date}:strategic"
                    base_upsert(
                        rid,
                        kind="daily_snapshot_capsule",
                        text=f"[{snapshot_date}] strategic_rollup: {rollup}",
                        vector=vec,
                        meta={"snapshot_date": snapshot_date, "bucket": "strategic", "from": "night_cycle", "stable_rollup": True},
                        stable=1,
                    )
    except Exception:
        # never crash night cycle because of capsule writing
        return written
    return written


def run() -> int:
    if _is_chat_busy():
        print("[night_cycle] defer: Xian chat busy, exiting gracefully")
        return 0

    print("=== NIGHT CYCLE START ===")

    ensure_dirs()
    p = paths()

    mm = _read_memory_mode(p)
    policy = mm.get('policy') if isinstance(mm, dict) else {}
    if not isinstance(policy, dict):
        policy = {}
    embed_enabled = bool(policy.get('embed_enabled', True))

    events_dir = p["EVENTS_DIR"]
    state_path = p["STATE_DIR"] / "night_cycle_state.json"
    out_dir = p["REBUILD_DIR"]
    out_dir.mkdir(parents=True, exist_ok=True)

    state = NightState.load(state_path)
    events, new_files = _load_new_events(events_dir, state)
    governance_records, governance_indices = _load_governance_feed(p)

    if not events and not governance_records:
        print("Brak nowych eventów i brak nowych decyzji governance.")
        return 0

    event_block = _build_event_block(events)
    governance_block = _build_governance_block(governance_records)
    if not event_block and not governance_block:
        _log_event("night_cycle_no_text", {"new_files": new_files, "note": "No usable text in events or governance feed."})
        # Still mark as processed to avoid looping forever.
        state.processed_files.extend(new_files)
        state.last_consolidation = _now()
        state.save(state_path)
        print("=== NIGHT CYCLE END ===")
        return 0

    system_prompt = (
        "Jesteś modułem konsolidacji pamięci systemu AI.\n"
        "Zwróć WYŁĄCZNIE poprawny JSON w strukturze:\n"
        "{\n  \"daily\": [],\n  \"strategic\": [],\n  \"open_questions\": []\n}\n"
        "Bez komentarzy, bez markdown, bez dodatkowego tekstu.\n"
        "Każdy element listy ma być krótkim zdaniem.\n"
    )

    full_prompt = system_prompt + "\n=== NOWE EVENTY ===\n" + (event_block or "[brak nowych eventów tekstowych]") + "\n\n=== DECYZJE GOVERNANCE ===\n" + (governance_block or "[brak nowych decyzji governance]")

    try:
        llm_output = _call_ollama_generate(full_prompt)
    except Exception as e:
        _log_event("consolidation_failed", {"reason": "ollama_generate_error", "error": repr(e)})
        return 2

    obj = _extract_json_object(llm_output)
    if not obj:
        _log_event("consolidation_failed", {"reason": "no_json_detected", "excerpt": llm_output[:400]})
        return 2

    if not _validate_consolidation(obj):
        _log_event("consolidation_failed", {"reason": "invalid_structure", "excerpt": json.dumps(obj)[:400]})
        return 2

    # Save consolidation
    out_path = out_dir / f"night_consolidation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")    # Optional: embeddings (store only as raw vectors for now)
    if embed_enabled:
        vectors_path = out_dir / f"night_embeddings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        vectors: Dict[str, Any] = {"model": EMBED_MODEL, "items": []}
        try:
            daily_text = " ".join(obj["daily"]).strip()
            if daily_text:
                vectors["items"].append({"kind": "daily", "text": daily_text, "embedding": _call_ollama_embed(daily_text)})
            for s in obj["strategic"]:
                s = (s or "").strip()
                if s:
                    vectors["items"].append({"kind": "strategic", "text": s, "embedding": _call_ollama_embed(s)})
            vectors_path.write_text(json.dumps(vectors, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            # Embeddings are optional; do not fail the night cycle.
            _log_event("embedding_failed", {"error": repr(e), "note": "Embeddings are optional; consolidation saved."})
    else:
        _log_event('embedding_skipped', {'note': 'embed_enabled=false (memory_mode)'})

    # Mark processed
    state.processed_files.extend(new_files)
    state.last_consolidation = _now()
    # Keep the state file from growing forever
    state.processed_files = state.processed_files[-5000:]
    state.save(state_path)
    _mark_governance_feed_ingested(p, governance_indices)

    _log_event(
        "consolidation_success",
        {
            "new_files": new_files,
            "daily_count": len(obj["daily"]),
            "strategic_count": len(obj["strategic"]),
            "open_questions_count": len(obj["open_questions"]),
            "consolidation_file": str(out_path),
        },
    )

    # Write canonical daily snapshot + MD report
    try:
        snap_path, md_path = _write_daily_snapshot(p, obj, new_files)
        today = datetime.now().strftime('%Y-%m-%d')

        # Write snapshot capsules into Turmalin + promote strategic rollup
        capsules_written = 0
        try:
            if embed_enabled:
                capsules_written = _write_snapshot_capsules(today, obj)
            else:
                capsules_written = 0
        except Exception:
            capsules_written = 0

        # Emit structured runtime events (JSONL)
        append_event(
            kind="snapshot_written",
            source="night_cycle",
            payload={
                "date": today,
                "snapshot_path": str(snap_path),
                "md_path": str(md_path),
                "capsules_written": int(capsules_written),
            },
        )

        # Write memory_state for DecisionLoop
        _write_memory_state(p, snapshot_date=today, snapshot_path=snap_path, md_path=md_path, capsules_written=capsules_written)
    except Exception as e:
        _log_event("snapshot_failed", {"error": repr(e)})

    print(f"Saved: {out_path}")
    print("=== NIGHT CYCLE END ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
