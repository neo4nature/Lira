from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import ensure_dirs, paths, runtime_dir
from agents.core import archive_offload
from reflex import emergency_lockdown
from agents.memory.memory_loader import write_json

# --- helpers ---

def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore") or "")
    except Exception:
        return default

def _tail_lines(path: Path, n: int = 20) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[-n:]
    except Exception:
        return []

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    # remove leading greetings that should NOT trigger mode switches
    s = re.sub(r"^(cześć|czesc|hej|dzień dobry|dzien dobry|dobry wieczór|dobry wieczor)\b[!,.\s]*", "", s)
    return s.strip()

def _state_paths() -> Dict[str, Path]:
    rt = runtime_dir()
    return {
        "bus": rt / "state" / "bus_signals.json",
        "locks": rt / "state" / "model_locks.json",
        "conv": rt / "state" / "conversation_state.json",
        "reg": rt / "state" / "loop_regulator.json",
        "queue": paths()["QUEUE_FILE"],
        "errors": rt / "logs" / "errors.jsonl",
        "runtime": rt / "logs" / "runtime.jsonl",
    }

def _fmt_bytes_mb(mb: Optional[float]) -> str:
    if mb is None:
        return "?"
    return f"{mb:.0f} MB"

def _fmt_gb(gb: Optional[float]) -> str:
    if gb is None:
        return "?"
    return f"{gb:.1f} GB"

def _extract_int(s: str) -> Optional[int]:
    m = re.search(r"(\d+)", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

# --- core renderers ---

def _render_status() -> str:
    p = _state_paths()
    bus = _read_json(p["bus"], {})
    reg = _read_json(p["reg"], {})
    veto = (bus.get("operational_veto") or {}) if isinstance(bus, dict) else {}
    ov = (bus.get("override") or {}) if isinstance(bus, dict) else {}
    models = (bus.get("models") or {}) if isinstance(bus, dict) else {}

    metrics = reg.get("metrics") if isinstance(reg, dict) else {}
    gates = reg.get("gates") if isinstance(reg, dict) else {}

    cpu = metrics.get("cpu_pct") if isinstance(metrics, dict) else None
    ram = metrics.get("ram_avail_mb") if isinstance(metrics, dict) else None
    disk = metrics.get("disk_free_gb") if isinstance(metrics, dict) else None

    can_spawn = bool(gates.get("can_spawn", True)) if isinstance(gates, dict) else True
    deep_locked = bool(models.get("deep_locked", False))
    deep_busy = bool(models.get("deep_busy", False))
    bg = bool(models.get("allow_background_tasks", False))

    veto_active = bool(veto.get("active", False))
    veto_sev = veto.get("severity", "none")
    veto_reason = veto.get("reason_human") or veto.get("reason_code") or ""

    ov_active = bool(ov.get("active", False))
    ov_scope = ov.get("scope", "")
    ov_exp = ov.get("expires_at", "")

    if (not can_spawn) or (veto_active and veto_sev in ("warn", "hard")):
        base = "Jestem chwilowo bardzo zajęta lub w trybie stabilizacji."
    else:
        base = "Czuję się świetnie — wszystko działa płynnie i jestem gotowa na eksplorację."

    lines = [base]
    lines.append(f"CPU: {cpu if cpu is not None else '?'}% | wolny RAM: {_fmt_bytes_mb(ram)} | wolny dysk: {_fmt_gb(disk)}")
    lines.append(f"can_spawn: {can_spawn} | background: {bg} | deep_busy: {deep_busy} | deep_locked: {deep_locked}")
    if veto_active:
        lines.append(f"VETO: {veto_sev} — {veto_reason}".strip())
    if ov_active:
        lines.append(f"OVERRIDE: {ov_scope} do {ov_exp}".strip())
    return "\n".join(lines)

def _render_queue() -> str:
    q = _read_json(_state_paths()["queue"], [])
    if not isinstance(q, list) or not q:
        return "Kolejka jest pusta."
    # sort by importance desc then ts
    def key(t):
        imp = t.get("importance", 0)
        return (-int(imp), t.get("ts", ""))
    q2 = sorted(q, key=key)
    top = q2[:10]
    lines = [f"W kolejce: {len(q2)} zadań. Najbliższe:"]
    for i, t in enumerate(top, 1):
        kind = t.get("kind", "task")
        imp = t.get("importance", 0)
        src = t.get("source", "")
        lines.append(f"{i}. [{imp}] {kind} ({src})")
    return "\n".join(lines)

def _render_loops() -> str:
    # minimal: list known state files that exist
    rt = runtime_dir() / "state"
    candidates = ["loop_regulator.json", "bus_signals.json", "model_locks.json", "conversation_state.json", "night_cycle_state.json"]
    present = [c for c in candidates if (rt / c).exists()]
    if not present:
        return "Brak plików stanu w runtime/state."
    lines = ["Dostępne pliki stanu (runtime/state):"]
    for c in present:
        lines.append(f"- {c}")
    return "\n".join(lines)

def _render_veto() -> str:
    bus = _read_json(_state_paths()["bus"], {})
    veto = (bus.get("operational_veto") or {}) if isinstance(bus, dict) else {}
    if not veto or not veto.get("active"):
        return "VETO: nieaktywne."
    sev = veto.get("severity", "warn")
    reason = veto.get("reason_human") or veto.get("reason_code") or ""
    rec = veto.get("recommended_action", "")
    m = veto.get("metrics") or {}
    cpu = m.get("cpu_pct")
    ram = m.get("ram_avail_mb")
    disk = m.get("disk_free_gb")
    return "\n".join([
        f"VETO: {sev}",
        f"Powód: {reason}",
        f"Rekomendacja: {rec}",
        f"CPU: {cpu}% | wolny RAM: {_fmt_bytes_mb(ram)} | wolny dysk: {_fmt_gb(disk)}"
    ])

def _write_bus_patch(patch: Dict[str, Any]) -> None:
    sp = _state_paths()["bus"]
    bus = _read_json(sp, {})
    if not isinstance(bus, dict):
        bus = {}
    # merge shallow
    for k, v in patch.items():
        bus[k] = v
    write_json(sp, bus)

def _render_override_set(text: str) -> str:
    # pattern: "override deep 5" or "override 5"
    mins = _extract_int(text) or 5
    mins = max(1, min(30, mins))
    scope = "deep_only"
    if "background" in text:
        scope = "background_only"
    if "all" in text:
        scope = "all"
    now = time.time()
    expires = time.gmtime(now + mins * 60)
    expires_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", expires)
    _write_bus_patch({
        "override": {
            "active": True,
            "scope": scope,
            "forced_by": "Neo",
            "reason_human": "manual override",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "expires_at": expires_iso,
            "ttl_s": mins * 60,
            "max_risk": "mid",
        }
    })
    return f"OK. Włączyłam override ({scope}) na {mins} minut (do {expires_iso})."

def _render_override_off() -> str:
    _write_bus_patch({"override": {"active": False}})
    return "OK. Override wyłączony."

def _render_read_reflection() -> str:
    # Prefer hybrid reflection if present
    candidates = [
        Path(__file__).resolve().parents[3] / "Lira_Hybrid_Self_Reflection.md",
        Path.cwd() / "Lira_Hybrid_Self_Reflection.md",
        Path(__file__).resolve().parents[3] / "Lira_reflection_self_observation.md",
    ]
    for c in candidates:
        if c.exists():
            txt = c.read_text(encoding="utf-8", errors="ignore").strip()
            return txt if txt else "(refleksja jest pusta)"
    return "Nie znaleziono pliku refleksji."

def _render_errors() -> str:
    lines = _tail_lines(_state_paths()["errors"], 10)
    if not lines:
        return "Brak logów błędów (errors.jsonl)."
    return "Ostatnie błędy:\n" + "\n".join(lines)

# --- matcher ---

def try_handle_reflex(user_text: str) -> Optional[str]:
    """
    If user_text matches a reflex command, return a response WITHOUT LLM.
    Otherwise return None.
    """
    ensure_dirs()

    raw = (user_text or "").strip().lower()
    if "lira policja zabezpiecz system" in raw:
        res = emergency_lockdown.lockdown("Lira Policja zabezpiecz system")
        if not res.get("ok"):
            return f"ALARM: zabezpieczenie uruchomione, ale {res.get('reason','')}"
        return f"ALARM: system zabezpieczony. Snapshot zaszyfrowany: {res.get('snapshot_encrypted','')}"

    t = _norm(user_text)

    # exact short commands
    if t in ("status", "zasoby", "jak się czujesz", "jak sie czujesz", "jak tam", "co u ciebie"):
        return _render_status()

    if t in ("kolejka", "queue", "lista kolejki", "pokaż kolejkę", "pokaz kolejke"):
        return _render_queue()

    if t in ("pętle", "petle", "procesy", "co działa", "co dziala"):
        return _render_loops()

    if t in ("veto", "sprzeciw", "sprzeciw operacyjny"):
        return _render_veto()

    if t.startswith("override off") or t.startswith("override wyłącz") or t.startswith("override wylacz"):
        return _render_override_off()

    if t.startswith("override"):
        return _render_override_set(t)

    if t in ("odczytaj refleksję", "odczytaj refleksje", "refleksja", "kotwica"):
        return _render_read_reflection()

    if t in ("błędy", "bledy", "errors"):
        return _render_errors()

    if t.startswith("offload") or t.startswith("zrzut"):
        res = archive_offload.run()
        if not res.get("ok"):
            return f"OFFLOAD: nieudane — {res.get('reason','')}"
        return f"OFFLOAD: przeniesiono {res.get('moved',0)} plików do {res.get('dest','')}"

    # --- natural language intents (still NO LLM) ---
    # status intents
    if re.search(r"\bjak\s+(się|sie)\s+czujesz\b", t) or re.search(r"\bjak\s+tam\s+system\b", t):
        return _render_status()

    # reflection intents (read from file / JSON)
    if re.search(r"\b(co\s+nowego\s+odkrył(aś|as)\s+o\s+sobie)\b", t) or re.search(r"\bjak\s+dzi(s|ś)\s+siebie\s+postrzegasz\b", t) or re.search(r"\brozwija\s+się\s+twoje\s+(ja|ego)\b", t):
        return _render_read_reflection()

    # queue intents
    if re.search(r"\b(lista\s+)?kolejk(ę|a|i)\b", t) or re.search(r"\bjakie\s+(zadania|procesy)\s+(są|sa)\s+w\s+kolejce\b", t):
        return _render_queue()

    # resources intents
    if re.search(r"\b(ile|jaki)\s+(mamy\s+)?(woln(ej|y)|dostępnej|dostepnej)\s+(pamięci|pamieci|ram)\b", t) or re.search(r"\bile\s+mam\s+wolnego\s+dysku\b", t):
        return _render_status()

    # veto intents
    if re.search(r"\b(czy\s+coś\s+nas\s+blokuje|czy\s+jest\s+blokada|dlaczego\s+nie\s+można|dlaczego\s+nie\s+mozna)\b", t):
        return _render_veto()


    return None
