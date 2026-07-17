from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import ensure_dirs, paths
from agents.memory.memory_loader import read_json, write_json
from agents.core.runtime_stream import append_event, runtime_stream_path

def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def _read_last_lines(fp: Path, max_lines: int = 2000) -> List[Dict[str, Any]]:
    if not fp.exists():
        return []
    # read from end in a simple way (file is typically small enough); keep robust
    try:
        lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()[-max_lines:]
    except Exception:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out

def summarize(window_minutes: int = 60, max_lines: int = 2000) -> Dict[str, Any]:
    p = paths()
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=window_minutes)
    events = _read_last_lines(runtime_stream_path(), max_lines=max_lines)

    filtered = []
    for e in events:
        ts = e.get("ts", "")
        try:
            t = datetime.fromisoformat(ts.replace("Z",""))
        except Exception:
            continue
        if t >= cutoff:
            filtered.append(e)

    kinds = Counter(e.get("kind","unknown") for e in filtered)
    levels = Counter(e.get("level","info") for e in filtered)

    # Detect veto / mode signals
    veto_counts = Counter()
    mode_counts = Counter()
    for e in filtered:
        if e.get("kind") == "policy_update":
            pol = e.get("payload", {})
            veto = (pol.get("operational_veto") or pol.get("veto") or {}).get("severity") if isinstance(pol.get("operational_veto"), dict) else pol.get("operational_veto")
            if veto:
                veto_counts[str(veto)] += 1
            mode = pol.get("mode") or pol.get("system_mode")
            if mode:
                mode_counts[str(mode)] += 1

    insights: List[str] = []
    if veto_counts.get("hard", 0) > 0:
        insights.append("W oknie analizy pojawił się veto=hard — system powinien trzymać tło wyłączone i preferować tryb stabilizacji.")
    if veto_counts.get("warn", 0) >= 3:
        insights.append("Częste veto=warn — rozważ zwiększenie progów idle-gate dla night/reflective lub skrócenie keep-alive ciężkich modeli.")
    if kinds.get("queue_overflow", 0) > 0:
        insights.append("Wykryto overflow kolejki — Turmalin działa, ale warto sprawdzić producentów tasków i TTL.")
    if kinds.get("emergency_snapshot", 0) > 0:
        insights.append("Wykonano snapshot awaryjny — sprawdź wolne miejsce na dm-0 i rotację archiwów.")
    if not insights:
        insights.append("Brak krytycznych anomalii w oknie analizy; system wygląda stabilnie.")

    return {
        "ts": utc_now(),
        "window_minutes": window_minutes,
        "counts": {
            "kinds": dict(kinds),
            "levels": dict(levels),
            "veto": dict(veto_counts),
            "modes": dict(mode_counts),
        },
        "insights": insights,
    }

def write_outcome(summary: Dict[str, Any]) -> Path:
    ensure_dirs()
    p = paths()
    out_file = p["REBUILD_DIR"] / "outcomes_log.json"
    data = read_json(out_file, default={"schema":"lira.outcomes.v1","items":[]})
    data.setdefault("items", []).append(summary)
    # keep last 200
    data["items"] = data["items"][-200:]
    write_json(out_file, data)
    return out_file

def run(window_minutes: int = 60) -> Dict[str, Any]:
    s = summarize(window_minutes=window_minutes)
    write_outcome(s)
    append_event("cognition_outcome", "agents.cognition.outcome_insights", {"summary": s})
    return s

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=60)
    args = ap.parse_args()
    print(json.dumps(run(args.window), ensure_ascii=False, indent=2))
