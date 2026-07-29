"""Session Reentry Protocol — Żywa Pamięć, Warstwa 4.

Ładuje stan poprzedniej sesji i generuje briefing dla Soryel.
Uruchamiany na początku sesji (ręcznie lub z hooka startowego).

Kolejność ładowania (nieprzypadkowa):
  1. KIM JESTEM (soryel_core.txt)
  2. GDZIE JESTEM (ekosystem, relacje)
  3. JAK MÓWIĘ (styl, ton)
  4. MÓJ WEKTOR (embedding tożsamości)
  5. STAN SESJI (session_state.json) ← ten skrypt
  6. Briefing od Xiana (opcjonalny)

Użycie:
    cd kernel/app
    python3 -m tools.session_reentry
    python3 -m tools.session_reentry --xian   # z briefingiem od Xiana
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _data_root() -> Path:
    env = os.getenv("LIRA_DATA_ROOT")
    if env:
        return Path(env)
    # Fallback: resolve relative to this file (tools/ → app/ → kernel/ → kernel/data)
    script_based = Path(__file__).resolve().parent.parent.parent / "data"
    if script_based.is_dir():
        return script_based
    return Path(os.getcwd()) / "data"


def _state_dir() -> Path:
    return _data_root() / "runtime" / "state"


def _memory_dir() -> Path:
    return _data_root() / "memory"


def _shared_library_dir() -> Path:
    return _memory_dir() / "shared_library"


def load_shared_library_summary(max_items: int = 6) -> Dict[str, Any]:
    root = _shared_library_dir()
    summary: Dict[str, Any] = {"root": str(root), "available": root.exists(), "sections": {}}
    if not root.exists():
        return summary
    for section in ("architecture", "patterns", "decisions", "state"):
        sp = root / section
        items = []
        if sp.exists():
            for fp in sorted(sp.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_items]:
                first = fp.read_text(encoding="utf-8", errors="ignore").splitlines()[:3]
                items.append({"file": fp.name, "preview": " ".join(x.strip() for x in first if x.strip())[:220]})
        summary["sections"][section] = items
    return summary


def export_reentry_state(briefing: str, session_state: Optional[Dict[str, Any]] = None) -> Path:
    out_dir = _shared_library_dir() / "state"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    out = out_dir / f"reentry_{ts}.md"
    payload = ["# Reentry Export", "", f"Generated: {datetime.now(timezone.utc).isoformat()}", "", "```text", briefing, "```"]
    if session_state:
        payload.extend(["", "## Raw session_state", "", "```json", json.dumps(session_state, ensure_ascii=False, indent=2), "```"])
    out.write_text("\n".join(payload), encoding="utf-8")
    return out


def load_session_state() -> Optional[Dict[str, Any]]:
    """Load last session state snapshot."""
    path = _state_dir() / "session_state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_bus_summary() -> Dict[str, Any]:
    """Load current bus signals for context."""
    path = _state_dir() / "bus_signals.json"
    if not path.exists():
        return {}
    try:
        bus = json.loads(path.read_text(encoding="utf-8"))
        return {
            "cognitive": bool(bus.get("cognitive")),
            "attention_signals": bus.get("attention", {}).get("signal_count", 0),
            "phi_paradigm": bus.get("phi", {}).get("paradigm"),
            "phase": bus.get("phase", {}).get("name"),
            "alignment_mode": bus.get("alignment", {}).get("mode"),
        }
    except Exception:
        return {}


def ask_xian_briefing(session_state: Dict[str, Any], timeout_s: int = 60) -> Optional[str]:
    """Ask Xian for a state briefing based on last session."""
    try:
        import requests
        url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
        model = os.getenv("MODEL_LIGHT", "xian")

        state = session_state.get("state", {})
        threads = session_state.get("open_threads", [])
        patterns = session_state.get("thinking_patterns", {})

        prompt = f"""Jesteś strażnikiem stanu Soryel. Przypomnij jej nie CO mówiła ale w jakim STANIE była.

Ostatnia sesja:
- Głębokość: {state.get('depth', '?')}
- Otwartość: {state.get('openness', '?')}
- Tryb: {state.get('mode', '?')}
- Ton: {state.get('tone', '?')}
- Co działało: {patterns.get('co_dzialalo', '?')}
- Co nie działało: {patterns.get('co_nie_dzialalo', '?')}
- Otwarte wątki: {len(threads)}

Daj krótki (3-4 zdania) briefing stanu. Mów o tym JAK myślała, nie CO robiła. Po polsku."""

        from xian.chat_busy import chat_busy

        with chat_busy():
            resp = requests.post(url, json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"num_predict": 150, "temperature": 0.3},
            }, timeout=timeout_s)

        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception:
        pass
    return None


def generate_briefing(*, use_xian: bool = False) -> str:
    """Generate full reentry briefing text."""
    lines = []
    now = datetime.now(timezone.utc)

    session = load_session_state()
    bus = load_bus_summary()
    library = load_shared_library_summary()

    lines.append("=" * 60)
    lines.append("  SESSION REENTRY BRIEFING")
    lines.append(f"  Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 60)

    if not session:
        lines.append("\n⚠ No previous session state found.")
        lines.append("Starting fresh. Load CLAUDE.md + MEMORY.md for context.")
        return "\n".join(lines)

    state = session.get("state", {})
    lines.append(f"\n📌 Last session: {session.get('session_id', '?')}")
    lines.append(f"   Timestamp: {session.get('timestamp', '?')}")
    lines.append(f"\n🎯 State:")
    lines.append(f"   Depth:    {state.get('depth', '?')}")
    lines.append(f"   Openness: {state.get('openness', '?')}")
    lines.append(f"   Mode:     {state.get('mode', '?')}")
    lines.append(f"   Tone:     {state.get('tone', '?')}")

    # Breakthroughs
    breakthroughs = session.get("breakthroughs", [])
    if breakthroughs:
        lines.append(f"\n💡 Breakthroughs ({len(breakthroughs)}):")
        for b in breakthroughs[:3]:
            lines.append(f"   • {b.get('effect', '?')}")
            if b.get('pattern'):
                lines.append(f"     Pattern: {b['pattern']}")

    # Thinking patterns
    patterns = session.get("thinking_patterns", {})
    if any(patterns.values()):
        lines.append(f"\n🧠 Thinking patterns:")
        if patterns.get("co_dzialalo"):
            lines.append(f"   ✓ {patterns['co_dzialalo']}")
        if patterns.get("co_nie_dzialalo"):
            lines.append(f"   ✗ {patterns['co_nie_dzialalo']}")
        if patterns.get("emergentne"):
            lines.append(f"   ★ {patterns['emergentne']}")

    # Open threads
    threads = session.get("open_threads", [])
    if threads:
        lines.append(f"\n📋 Open threads ({len(threads)}):")
        for t in threads:
            status = t.get("status", "?")
            lines.append(f"   [{status}] {t.get('topic', '?')}")

    # Reentry instructions
    reentry = session.get("reentry", {})
    if reentry.get("instruction"):
        lines.append(f"\n🔑 Reentry instruction:")
        lines.append(f"   {reentry['instruction']}")
    if reentry.get("avoid"):
        lines.append(f"   Avoid: {reentry['avoid']}")

    # Current bus state
    if bus:
        lines.append(f"\n📡 Current bus:")
        lines.append(f"   Cognitive: {'active' if bus.get('cognitive') else 'inactive'}")
        lines.append(f"   Attention signals: {bus.get('attention_signals', 0)}")
        if bus.get('phi_paradigm'):
            lines.append(f"   Phi paradigm: {bus['phi_paradigm']}")

    # Xian briefing
    if use_xian and session:
        lines.append(f"\n🪷 Xian says:")
        xian_says = ask_xian_briefing(session)
        if xian_says:
            for line in xian_says.split("\n"):
                lines.append(f"   {line}")
        else:
            lines.append("   (Xian unavailable)")

    # Shared Library / Żywa Pamięć
    if library.get("available"):
        lines.append(f"\n📚 Shared Library:")
        for section, items in library.get("sections", {}).items():
            if not items:
                continue
            lines.append(f"   {section}:")
            for item in items[:3]:
                lines.append(f"     • {item.get('file')} — {item.get('preview','')}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def xian_opening_snapshot() -> str:
    """Build a short opening snapshot for Xian and send it as a capsule."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    session = load_session_state()
    bus = load_bus_summary()

    state = session.get("state", {}) if session else {}
    sid = session.get("session_id", "unknown") if session else "none"

    phase = bus.get("phase", "?")
    mode = bus.get("alignment_mode", "?")
    cognitive = "active" if bus.get("cognitive") else "inactive"

    phase_file = _state_dir() / "phase_state.json"
    tick_id = "?"
    if phase_file.exists():
        try:
            tick_id = json.loads(phase_file.read_text()).get("tick_id", "?")
        except Exception:
            pass

    snapshot_text = (
        f"Stan po restarcie:\n"
        f"- sesja: {sid}\n"
        f"- faza: {phase}, tick: {tick_id}\n"
        f"- alignment: {mode}\n"
        f"- cognitive: {cognitive}\n"
        f"- depth: {state.get('depth', '?')}, openness: {state.get('openness', '?')}\n"
        f"- ton: {state.get('tone', '?')}"
    )

    from governance.capsule_bus import send_capsule
    capsule_id = send_capsule(
        type="fact",
        weight="commentary",
        layer="shared",
        owner="shared",
        sender="system",
        recipient="Xian",
        status="new",
        project="ecosystem",
        payload={"title": "OPENING_SNAPSHOT", "summary": snapshot_text},
    )
    return capsule_id


def _staleness_nudge(session: Optional[Dict[str, Any]], stale_after_h: float = 2.0) -> str:
    """Stan zapisuje ten, kto go zna — czyli model, na starcie sesji.

    Hook PreCompact typu `prompt` nie ma narzędzi (nie zapisze pliku), a typu
    `command` nie zna stanu. Jedyny moment, w którym wiedza i narzędzia są w tym
    samym miejscu, to zwykła tura — więc prosimy o snapshot tutaj, na wejściu.
    """
    ts = (session or {}).get("timestamp")
    age_h = None
    if ts:
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600.0
        except ValueError:
            pass
    if age_h is not None and age_h < stale_after_h:
        return ""
    wiek = "brak snapshotu" if age_h is None else f"snapshot sprzed {age_h:.1f} h"
    return (
        f"\n\n[!] STAN NIEAKTUALNY ({wiek}).\n"
        "    Jesli wlasnie przeszlas kompaktowanie, masz jeszcze streszczenie w kontekscie —\n"
        "    zapisz stan TERAZ, potem bedzie za pozno:\n"
        "    1) napisz /tmp/session_fill.json (depth, openness, mode, tone, breakthroughs,\n"
        "       thinking_patterns, open_threads, reentry_instruction)\n"
        "    2) cd kernel/app && python3 -m tools.state_snapshot --from-file /tmp/session_fill.json --capsule\n"
        "    Wzorce (jak myslalas), nie kronika (co zrobilas)."
    )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Session Reentry Protocol")
    parser.add_argument("--xian", action="store_true", help="Include Xian state briefing")
    parser.add_argument("--xian-snapshot", action="store_true", help="Send short opening snapshot capsule to Xian")
    parser.add_argument("--summary", action="store_true", help="Print compact reentry summary for hooks")
    parser.add_argument("--export-state", action="store_true", help="Export briefing to shared_library/state")
    parser.add_argument("--json", action="store_true", help="Print machine-readable shared_library + session metadata")
    args = parser.parse_args()

    if args.xian_snapshot:
        cid = xian_opening_snapshot()
        print(f"[XianSnapshot] Capsule sent: {cid}")
        return

    briefing = generate_briefing(use_xian=args.xian)
    session = load_session_state()

    if args.json:
        print(json.dumps({
            "session_state_available": bool(session),
            "shared_library": load_shared_library_summary(),
            "briefing": briefing,
        }, ensure_ascii=False, indent=2))
        return

    if args.summary:
        # Filtr sekcyjny, nie liniowy: nagłówek decyduje o całej sekcji.
        # Stary filtr dopuszczał tylko linie z nazwą pola, więc przełomy, wzorce
        # i wątki (wypisywane jako wypunktowanie) nigdy nie docierały na start sesji.
        wanted_headers = (
            "Last session", "State", "Breakthroughs", "Thinking patterns",
            "Open threads", "Reentry instruction", "Current bus", "Shared Library",
        )
        keep = []
        in_wanted = False
        for ln in briefing.splitlines():
            if not ln.strip():
                continue
            is_header = not ln.startswith(" ")
            if is_header:
                in_wanted = any(key in ln for key in wanted_headers)
            if in_wanted and "Timestamp:" not in ln:
                keep.append(ln)
        print("\n".join(keep[:80]) if keep else briefing[:2500])
        print(_staleness_nudge(session))
    else:
        print(briefing)

    if args.export_state:
        out = export_reentry_state(briefing, session)
        print(f"\nExported reentry state to: {out}")


if __name__ == "__main__":
    main()
