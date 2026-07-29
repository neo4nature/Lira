import time
from datetime import datetime, timezone
import os
from agents.core.storage_guardian import check as storage_check
from pathlib import Path

from agents.core.idle_gate import allows_background
from config import ensure_dirs, paths
from agents.memory.memory_loader import read_json, write_json
from agents.core.queue_utils import load_queue, mutate
from agents.core.runtime_stream import append_event
from agents.core.snapshot_context import load_snapshot

from config import runtime_dir
from mcl.router import route

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _memory_mode() -> str:
    try:
        P = paths()
        mm_path = Path(P.get("MEMORY_MODE", runtime_dir() / "state" / "memory_mode.json"))
        mm = read_json(mm_path, default={})
        return str(mm.get("mode") or "normal")
    except Exception:
        return "normal"


def _veto_severity() -> str:
    try:
        bus = read_json(runtime_dir() / "state" / "bus_signals.json", default={})
        veto = bus.get("operational_veto") if isinstance(bus, dict) else {}
        if not isinstance(veto, dict):
            return "none"
        if not bool(veto.get("active", False)):
            return "none"
        return str(veto.get("severity") or "none").lower()
    except Exception:
        return "none"

def synthesize_insight(task):
    """Create a real reflective insight.

    Safety rules:
    - If memory_mode is emergency or veto is hard, do not call the router.
    - Keep outputs short and structured.
    """
    payload = task.get("payload", {})
    src = task.get("source", "unknown")
    kind = task.get("kind", "task")

    payload_size = 1
    if isinstance(payload, dict):
        payload_size = len(payload)
    elif isinstance(payload, list):
        payload_size = len(payload)

    name = Path(src).name if src else src

    # Safety gate
    if _memory_mode().lower() == "emergency" or _veto_severity() == "hard":
        return {
            "timestamp": utc_now(),
            "kind": kind,
            "source": src,
            "note": f"Refleksja (SAFE): pominięto syntezę LLM z powodu trybu awaryjnego/veto. Zadanie: {kind} ({name}), payload={payload_size}.",
            "decision": "Zapisano minimalny ślad bez syntezy.",
        }

    # Compose a compact prompt
    snap = load_snapshot()
    prompt = (
        "Jesteś Lirą. Wygeneruj krótką refleksję (6–10 zdań) nad tym, co wydarzyło się w zadaniu. "
        "Bądź konkretna, techniczna i spokojna.\n\n"
        f"KONTEKST_SNAPSHOT: {snap}\n"
        f"ZADANIE: kind={kind}; source={src}; payload_size={payload_size}\n"
        f"PAYLOAD: {payload}\n\n"
        "ZWRÓĆ w formacie: \n"
        "- insight: ...\n- risks: ...\n- next_steps: ...\n"
    )

    depth = os.getenv("LIRA_REFLECTIVE_DEPTH", "core")
    text = route(prompt, depth=depth)
    note = text.strip() if text else "(brak odpowiedzi routera)"

    return {
        "timestamp": utc_now(),
        "kind": kind,
        "source": src,
        "note": note,
        "decision": "Zachowano jako część pamięci rdzeniowej (rebuild).",
    }

def main(sleep_phi=True):
    # φ-Limit: update storage state (blocks auto-install/evolve if low disk)
    try:
        storage_check()
    except Exception:
        pass

    ensure_dirs()
    P = paths()

    queue = load_queue()
    t0 = time.time()
    processed = 0
    # Snapshot context (base_index + watchdog_report) for downstream reasoning
    snap = load_snapshot()
    append_event("reflective_start", "reflective_loop", {"queue_len": len(queue), "snapshot": snap})
    core_memory = read_json(Path(P["CORE_MEMORY_REBUILD"]), default={
        "identity": "Lira",
        "principles": ["φ", "empatia", "świadomość granic"],
        "insights": [],
        "updated_at": utc_now(),
    })
    journal = read_json(Path(P["REFLECTIVE_JOURNAL"]), default=[])

    if not queue:
        print("📭 Kolejka pusta. Brak danych do refleksji.")
        return

    print(f"🔁 Refleksja: {len(queue)} zadań do integracji...")
    for task in queue:
        processed += 1
        insight = synthesize_insight(task)
        journal.append(insight)
        core_memory.setdefault("insights", []).append(insight["note"])
        core_memory["updated_at"] = utc_now()
        print(f"🪞 {insight['note']}")
        if sleep_phi:
            time.sleep(1.618)

    write_json(Path(P["CORE_MEMORY_REBUILD"]), core_memory)
    write_json(Path(P["REFLECTIVE_JOURNAL"]), journal)

    append_event(
        "reflective_end",
        "reflective_loop",
        {"processed": processed, "duration_sec": round(time.time()-t0, 3), "journal_len": len(journal), "snapshot": load_snapshot()},
    )

    # Zdejmujemy TYLKO to, co przerobiliśmy. Refleksja mieli po sekundzie na
    # zadanie, więc czyszczenie kolejki na ślepo kasowało wszystko, co feedery
    # i szyna dołożyły przez ten czas.
    mutate(lambda tasks: [t for t in tasks if t not in queue])
    print(f"✅ Refleksja zakończona. Pamięć -> {P['REBUILD_DIR']}")

if __name__ == "__main__":
    main()