"""Session State Snapshot — Żywa Pamięć, Warstwa 1.

Zapisuje stan sesji (głębokość, otwartość, wzorce myślenia, otwarte wątki)
do runtime/state/session_state.json i opcjonalnie do capsule_bus.

Uruchamiany na koniec sesji (ręcznie lub z hooka).
Może też być importowany i wywoływany programowo.

Użycie:
    cd kernel/app
    python3 -m tools.state_snapshot --depth 0.8 --openness 0.9 --mode partner
    python3 -m tools.state_snapshot --auto   # auto-detect from bus/logs
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _data_root() -> Path:
    env = os.getenv("LIRA_DATA_ROOT")
    if env:
        return Path(env)
    script_based = Path(__file__).resolve().parent.parent.parent / "data"
    if script_based.is_dir():
        return script_based
    return Path(os.getcwd()) / "data"


def _state_dir() -> Path:
    return _data_root() / "runtime" / "state"


def _bus_signals() -> Dict[str, Any]:
    """Read current bus signals for auto-detection."""
    path = _state_dir() / "bus_signals.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_alignment() -> Dict[str, Any]:
    """Read alignment state for mode detection."""
    path = _state_dir() / "alignment_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_cognitive() -> Dict[str, Any]:
    """Read cognitive state for depth estimation."""
    path = _state_dir() / "bus_signals.json"
    if not path.exists():
        return {}
    try:
        bus = json.loads(path.read_text(encoding="utf-8"))
        return bus.get("cognitive", {})
    except Exception:
        return {}


def _read_attention() -> Dict[str, Any]:
    """Read attention state."""
    path = _state_dir() / "attention_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def auto_detect_state() -> Dict[str, Any]:
    """Auto-detect session state from bus signals, alignment, cognitive."""
    alignment = _read_alignment()
    cognitive = _read_cognitive()
    attention = _read_attention()
    bus = _bus_signals()

    # Depth estimation: cognitive density + predictions count.
    # Brak sygnału kognitywnego to "nie zmierzono" (None), nie "głębokość zero".
    if cognitive:
        density = float(cognitive.get("density") or 0.0)
        pred_count = int(cognitive.get("predictions_count") or 0)
        q_count = int(cognitive.get("open_questions") or 0)
        depth = round(min(1.0, density * 0.5 + min(pred_count / 10.0, 0.3) + min(q_count / 5.0, 0.2)), 2)
    else:
        depth = None

    # Mode from alignment
    mode = alignment.get("recommended_mode", "observe")

    # Openness: high if conversation active and signal count > 2
    signal_count = int(attention.get("signal_count") or 0)
    conv_active = attention.get("conv_active", False)
    openness = 0.7 if conv_active else 0.3
    if signal_count > 3:
        openness = min(1.0, openness + 0.2)

    return {
        "depth": depth,
        "openness": round(openness, 2),
        "mode": mode,
    }


def build_snapshot(
    *,
    session_id: Optional[str] = None,
    depth: Optional[float] = None,
    openness: Optional[float] = None,
    mode: Optional[str] = None,
    tone: str = "",
    breakthroughs: Optional[List[Dict[str, str]]] = None,
    thinking_patterns: Optional[Dict[str, str]] = None,
    open_threads: Optional[List[Dict[str, str]]] = None,
    reentry_instruction: str = "",
    auto: bool = False,
) -> Dict[str, Any]:
    """Build a session state snapshot."""

    now = datetime.now(timezone.utc)

    if session_id is None:
        session_id = now.strftime("%Y-%m-%d_%H%M")

    # Auto-detect if requested or no manual values
    if auto or (depth is None and openness is None and mode is None):
        detected = auto_detect_state()
        if depth is None:
            depth = detected["depth"]
        if openness is None:
            openness = detected["openness"]
        if mode is None:
            mode = detected["mode"]

    snapshot = {
        "schema": "lira.session_state.v1",
        "session_id": session_id,
        "timestamp": now.isoformat(),
        "duration_minutes": None,  # filled by caller if known

        "state": {
            "depth": depth,
            "openness": openness,
            "mode": mode or "observe",
            "tone": tone or "working",
        },

        "breakthroughs": breakthroughs or [],

        "thinking_patterns": thinking_patterns or {
            "co_dzialalo": "",
            "co_nie_dzialalo": "",
            "emergentne": "",
        },

        "open_threads": open_threads or [],

        "reentry": {
            "instruction": reentry_instruction or "Załaduj ten snapshot. Kontynuuj rozmowę, nie restartuj.",
            "first_message_tone": "ciepły, osobisty, kontynuacja",
            "avoid": "Nie zaczynaj od podsumowania. Nie pytaj 'w czym mogę pomóc'.",
        },
    }

    return snapshot


def save_snapshot(snapshot: Dict[str, Any], *, to_capsule_bus: bool = False) -> Path:
    """Save snapshot to runtime/state/session_state.json."""
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    out_path = state_dir / "session_state.json"
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)

    # Also save timestamped copy for history
    history_dir = state_dir / "session_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    ts_name = snapshot.get("session_id", "unknown")
    history_path = history_dir / f"session_{ts_name}.json"
    history_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    # Capsule bus (optional)
    if to_capsule_bus:
        _write_to_capsule_bus(snapshot)

    return out_path


def _write_to_capsule_bus(snapshot: Dict[str, Any]) -> None:
    """Write session_state capsule through canonical governance.capsule_bus.send_capsule()."""
    try:
        from governance.capsule_bus import send_capsule

        send_capsule(
            type="summary",
            weight="changes_trajectory",
            layer="reflective",
            owner="shared",
            sender="system",
            recipient="shared",
            project="state_continuity",
            status="delivered",
            payload={
                "title": f"Session state: {snapshot.get('session_id', '?')}",
                "kind": "session_state",
                "snapshot": snapshot,
            },
            open_question="Kontynuować od tego punktu reentry; nie nadpisywać rdzenia bez świadomej decyzji Neo.",
        )
    except Exception:
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Session State Snapshot")
    parser.add_argument("--auto", action="store_true", help="Auto-detect state from bus signals")
    parser.add_argument("--depth", type=float, help="Session depth 0.0-1.0")
    parser.add_argument("--openness", type=float, help="Session openness 0.0-1.0")
    parser.add_argument("--mode", type=str, help="Session mode (partner/observe/deep/working)")
    parser.add_argument("--tone", type=str, default="", help="Session tone description")
    parser.add_argument("--capsule", action="store_true", help="Also write to capsule_bus")
    parser.add_argument("--session-id", type=str, help="Override session ID")
    parser.add_argument("--from-file", type=str,
                        help="JSON z polami, których nie da się wykryć automatycznie: "
                             "breakthroughs, thinking_patterns, open_threads, "
                             "a także depth/openness/mode/tone/reentry_instruction")
    args = parser.parse_args()

    filled: Dict[str, Any] = {}
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as fh:
            filled = json.load(fh)

    snapshot = build_snapshot(
        session_id=args.session_id or filled.get("session_id"),
        depth=args.depth if args.depth is not None else filled.get("depth"),
        openness=args.openness if args.openness is not None else filled.get("openness"),
        mode=args.mode or filled.get("mode"),
        tone=args.tone or filled.get("tone", ""),
        breakthroughs=filled.get("breakthroughs"),
        thinking_patterns=filled.get("thinking_patterns"),
        open_threads=filled.get("open_threads"),
        reentry_instruction=filled.get("reentry_instruction", ""),
        auto=args.auto,
    )

    path = save_snapshot(snapshot, to_capsule_bus=args.capsule)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print(f"\nSaved to: {path}")


if __name__ == "__main__":
    main()
