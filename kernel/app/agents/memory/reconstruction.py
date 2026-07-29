import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import ensure_dirs, env, paths
from agents.memory.memory_loader import read_json, write_json
from agents.core.runtime_stream import append_event


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _tail_jsonl(p: Path, max_lines: int = 200) -> List[Dict[str, Any]]:
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    # Read from end efficiently enough for small files; for huge files prefer a real tail.
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-max_lines:]
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    except Exception:
        return []
    return out


def _latest_snapshot(P: Dict[str, Any]) -> Tuple[Optional[str], Optional[Path], Optional[Path]]:
    snap_dir = Path(P["SNAPSHOTS_DIR"]).resolve()
    md_dir = Path(P["SNAPSHOTS_MD_DIR"]).resolve()
    if not snap_dir.exists():
        return None, None, None

    snaps = sorted(snap_dir.rglob("*.snapshot.json"))
    if not snaps:
        return None, None, None

    latest = max(snaps, key=lambda p: p.stat().st_mtime)
    # Date from filename if possible
    date = latest.name.replace(".snapshot.json", "")
    md_path = md_dir / latest.parent.name / f"{date}.md"
    if not md_path.exists():
        # Fallback: search by date
        cands = list(md_dir.rglob(f"{date}.md"))
        md_path = cands[0] if cands else None
    return date, latest, md_path


def _latest_diary(P: Dict[str, Any], days_back: int = 3) -> List[Dict[str, Any]]:
    diary_dir = Path(P["DIARY_DIR"]).resolve()
    if not diary_dir.exists():
        return []
    files = sorted(diary_dir.rglob("*.diary.jsonl"))
    if not files:
        return []
    latest = max(files, key=lambda p: p.stat().st_mtime)
    return _tail_jsonl(latest, max_lines=400)


def _read_bus_and_modes(P: Dict[str, Any]) -> Dict[str, Any]:
    state_dir = Path(P["STATE_DIR"]).resolve()
    bus_path = state_dir / "bus_signals.json"
    mm_path = Path(P["MEMORY_MODE"]).resolve()

    bus = read_json(bus_path, default={}) if bus_path.exists() else {}
    mm = read_json(mm_path, default={}) if mm_path.exists() else {}

    veto = bus.get("operational_veto") or {}
    mem = bus.get("memory") or {}
    return {
        "bus_owner": bus.get("owner"),
        "operational_veto": {
            "active": bool(veto.get("active", False)),
            "severity": (veto.get("severity") or "none"),
            "reason": veto.get("reason"),
        },
        "memory_mode": {
            "mode": mm.get("mode") or mm.get("memory_mode") or "unknown",
            "reasons": mm.get("reasons") or [],
            "policy": mm.get("policy") or {},
        },
        "memory_bus": mem,
    }


def _summarize_events(runtime_events: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Pick last occurrence of key event types.
    wanted = {
        "snapshot_written",
        "reflective_end",
        "reflective_skipped",
        "cognitive_end",
        "cognitive_skipped",
        "gaia_spawn_end",
        "gaia_spawn_error",
        "memory_promoted",
        "core_memory_rebuild_end",
    }
    last: Dict[str, Dict[str, Any]] = {}
    for ev in runtime_events:
        et = ev.get("event_type") or ev.get("type")
        if et in wanted:
            last[et] = ev
    return last


def rebuild_core_memory(light: bool = True) -> Dict[str, Any]:
    """Build a useful core memory ledger from canonical artifacts.

    - Deterministic by default (no LLM calls)
    - Safe in emergency/veto states (still writes ledger)
    """
    ensure_dirs()
    P = paths()

    append_event("core_memory_rebuild_start", "reconstruction", {"light": light})

    snap_date, snap_path, snap_md = _latest_snapshot(P)
    diary_tail = _latest_diary(P)
    turmalin_tail = _tail_jsonl(Path(P["TURMALIN_CACHE"]).resolve(), max_lines=200)
    runtime_tail = _tail_jsonl(Path(P["RUNTIME_STREAM"]).resolve(), max_lines=250)

    modes = _read_bus_and_modes(P)
    last_events = _summarize_events(runtime_tail)

    # Pull a short human-readable reflection (if present in MD)
    reflection_excerpt = None
    if snap_md and isinstance(snap_md, Path) and snap_md.exists():
        try:
            txt = snap_md.read_text(encoding="utf-8", errors="ignore")
            # Try to extract a small section under "Refleksja" if it exists.
            idx = txt.lower().find("refleks")
            reflection_excerpt = txt[idx: idx + 1200] if idx >= 0 else txt[:800]
        except Exception:
            reflection_excerpt = None

    # Summarize diary (task trail)
    tasks = []
    for e in diary_tail[-200:]:
        if (e.get("event_type") or "") in {"task", "task_end", "task_done", "task_failed"}:
            tasks.append(e)
        # allow generic task records
        if e.get("task_id"):
            tasks.append(e)
    tasks = tasks[-40:]

    # Compact Turmalin notes
    tur_tags: Dict[str, int] = {}
    for r in turmalin_tail:
        meta = r.get("meta") or {}
        for t in meta.get("tags") or []:
            tur_tags[str(t)] = tur_tags.get(str(t), 0) + 1

    ledger = {
        "schema": "core_memory_ledger.v1",
        "generated_at": utc_now(),
        "phi": 1.618,
        "snapshot": {
            "latest_date": snap_date,
            "snapshot_path": str(snap_path) if snap_path else None,
            "snapshot_md_path": str(snap_md) if isinstance(snap_md, Path) else None,
        },
        "system": {
            "identity": "Lira",
            "bus_owner": modes.get("bus_owner"),
            "operational_veto": modes.get("operational_veto"),
            "memory_mode": modes.get("memory_mode"),
        },
        "highlights": {
            "reflection_excerpt": reflection_excerpt,
            "recent_tasks": tasks,
            "turmalin_tags_top": sorted(tur_tags.items(), key=lambda x: x[1], reverse=True)[:12],
            "last_events": last_events,
        },
        "refs": {
            "runtime_stream": str(Path(P["RUNTIME_STREAM"]).resolve()),
            "turmalin_cache": str(Path(P["TURMALIN_CACHE"]).resolve()),
            "diary_dir": str(Path(P["DIARY_DIR"]).resolve()),
        },
    }

    # Write outputs
    out_json = Path(P["CORE_MEMORY_REBUILD"]).resolve()
    out_md = Path(P["CORE_MEMORY_REBUILD_MD"]).resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)

    write_json(out_json, ledger)

    md_lines = []
    md_lines.append(f"# Core Memory Ledger — {snap_date or 'no-snapshot'}")
    md_lines.append("")
    md_lines.append("## Rdzeń (kanon)")
    md_lines.append(f"- Snapshot: `{ledger['snapshot']['snapshot_path']}`")
    md_lines.append(f"- MD: `{ledger['snapshot']['snapshot_md_path']}`")
    md_lines.append(f"- Veto: `{ledger['system']['operational_veto']}`")
    md_lines.append(f"- Memory mode: `{ledger['system']['memory_mode']['mode']}`")
    md_lines.append("")
    md_lines.append("## Oś dnia — skrót")
    if reflection_excerpt:
        md_lines.append("### Refleksja (fragment)")
        md_lines.append("```")
        md_lines.append(reflection_excerpt.strip())
        md_lines.append("```")
    md_lines.append("")
    md_lines.append("### Ostatnie eventy")
    for k, ev in ledger["highlights"]["last_events"].items():
        md_lines.append(f"- **{k}**: {ev.get('ts') or ev.get('timestamp') or ''} {ev.get('payload') or ''}")
    md_lines.append("")
    md_lines.append("## Ślad zadań (ostatnie)")
    if tasks:
        for t in tasks[-20:]:
            md_lines.append(f"- {t.get('ts') or t.get('timestamp') or ''} | {t.get('task_id') or ''} | {t.get('status') or t.get('event_type') or ''}")
    else:
        md_lines.append("- (brak)")
    md_lines.append("")
    md_lines.append("## Turmalin — najczęstsze tagi")
    if ledger["highlights"]["turmalin_tags_top"]:
        for tag, cnt in ledger["highlights"]["turmalin_tags_top"]:
            md_lines.append(f"- `{tag}` × {cnt}")
    else:
        md_lines.append("- (brak)")

    out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    append_event(
        "core_memory_rebuild_end",
        "reconstruction",
        {"ok": True, "out_json": str(out_json), "out_md": str(out_md), "snapshot_date": snap_date},
    )

    return ledger
