from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from config import paths
from xian.observer import build_snapshot
from xian.reporter import build_report
from xian.router import recommend_route
from xian.vector_contract import ensure_xian_vector, session_to_xian

log = logging.getLogger("xian.core")

INTERVAL_S = float(os.getenv("LIRA_XIAN_INTERVAL_S", "6"))
XIAN_REPORT_INTERVAL = float(os.getenv("XIAN_REPORT_INTERVAL", "7200"))  # 2h
XIAN_ANOMALY_COOLDOWN = float(os.getenv("XIAN_ANOMALY_COOLDOWN", "1800"))  # anomalia max co 30 min

_last_report_ts: float = 0.0
_last_anomaly_ts: float = 0.0


def _setup_embed_log() -> None:
    """Kieruj logger ollama.embed do dedykowanego pliku."""
    log_path = Path(os.getenv("LIRA_LOGS_ROOT", "${LIRA_ROOT:-/opt/lira}/kernel/logs")) / "ollama_embed.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    elog = logging.getLogger("ollama.embed")
    if not elog.handlers:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        elog.addHandler(fh)
        elog.setLevel(logging.INFO)
        elog.propagate = False


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _session_text(p: Dict[str, Path]) -> str:
    align = _read_json(p["STATE_DIR"] / "alignment_state.json", {})
    source = align.get("source") if isinstance(align, dict) else {}
    user = (source.get("user_text") or "").strip() if isinstance(source, dict) else ""
    assistant = (source.get("assistant_text") or "").strip() if isinstance(source, dict) else ""
    return (user + "\n" + assistant).strip()


def _read_lira_feedback() -> Dict[str, Any]:
    """Czytaj nowe kapsuły od Liry skierowane do Xiana. Oznacz jako 'read'."""
    try:
        from governance.capsule_bus import init_db, read_capsules, update_status
        init_db()
        caps = read_capsules(recipient="Xian", statuses=("new",), limit=5)
        if not caps:
            return {}
        # Weź najnowszą sensowną wiadomość od Liry
        lira_caps = [c for c in caps if c.get("sender") == "Lira"]
        if not lira_caps:
            return {}
        latest = lira_caps[0]
        payload = latest.get("payload_json") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        text = (
            payload.get("body")
            or payload.get("summary")
            or payload.get("raw_response")
            or ""
        )
        # Oznacz wszystkie jako przeczytane
        for c in lira_caps:
            try:
                update_status(c["id"], "read", actor="Xian")
            except Exception as exc:
                log.warning("[xian_core] update_status failed for cap %s: %s", c.get("id", "?")[:8], exc)
        return {
            "sender": "Lira",
            "capsule_id": latest["id"],
            "text": str(text)[:800],
        }
    except Exception as exc:
        log.warning("[xian_core] _read_lira_feedback failed: %s", exc)
        return {}


def _maybe_report_to_lira(xian_state: Dict[str, Any]) -> None:
    """Send status capsule to Lira — on schedule or on anomaly (throttled)."""
    global _last_report_ts, _last_anomaly_ts

    snapshot = xian_state.get("snapshot", {})
    route = xian_state.get("route", {})

    hard_veto    = bool(snapshot.get("hard_veto"))
    watchdog     = str(snapshot.get("watchdog_status") or "").lower()
    memory_mode  = str(snapshot.get("memory_mode") or "").lower()
    pressure     = float(snapshot.get("regulator_pressure", 0.0) or 0.0)
    criticality  = float(snapshot.get("criticality", 0.0) or 0.0)
    phase        = snapshot.get("phase", "UNKNOWN")
    policy       = snapshot.get("policy_mode", "UNKNOWN")
    drift_count  = int(snapshot.get("drift_count", 0) or 0)
    alignment    = float((xian_state.get("xian_alignment") or {}).get("score", 0.0) or 0.0)

    anomaly = (
        hard_veto
        or watchdog == "alert"
        or memory_mode == "emergency"
        or pressure >= 0.88
    )

    now = time.time()
    scheduled = (now - _last_report_ts) >= XIAN_REPORT_INTERVAL
    anomaly_allowed = anomaly and (now - _last_anomaly_ts) >= XIAN_ANOMALY_COOLDOWN

    if not anomaly_allowed and not scheduled:
        return

    if anomaly_allowed:
        _last_anomaly_ts = now
    reason = "anomaly" if anomaly else "scheduled"
    summary_text = (
        f"[Xian → Lira | {reason}] "
        f"phase={phase}, policy={policy}, route={route.get('target','?')}, "
        f"pressure={pressure:.2f}, drift={drift_count}, alignment={alignment:.2f}, "
        f"criticality={criticality:.2f}"
    )
    if hard_veto:
        summary_text += " | ⚠ HARD VETO"
    if watchdog == "alert":
        summary_text += " | ⚠ WATCHDOG ALERT"
    if memory_mode == "emergency":
        summary_text += " | ⚠ MEMORY EMERGENCY"

    try:
        from governance.capsule_bus import init_db, send_capsule
        init_db()
        send_capsule(
            type="summary",
            weight="changes_trajectory" if anomaly else "commentary",
            layer="shared",
            owner="shared",
            sender="Xian",
            recipient="Lira",
            payload={
                "title": f"Raport Xian [{reason}] — {_now_iso()[:16]}",
                "body": summary_text,
                "xian_snapshot": {
                    "phase": phase,
                    "policy": policy,
                    "route": route.get("target"),
                    "pressure": round(pressure, 3),
                    "drift_count": drift_count,
                    "alignment": round(alignment, 3),
                    "criticality": round(criticality, 3),
                    "hard_veto": hard_veto,
                    "watchdog_status": snapshot.get("watchdog_status"),
                    "memory_mode": memory_mode,
                },
            },
            project="ecosystem_monitor",
            status="new",
        )
        _last_report_ts = now
    except Exception as exc:
        # nie przerywaj głównej pętli z powodu błędu raportowania
        _ = exc


def _read_status_only_since(state_dir: Path) -> float:
    """Odczytaj czas wejścia w status_only z poprzedniego cyklu."""
    try:
        prev = _read_json(state_dir / "xian_router_state.json", {})
        if prev.get("target") == "status_only":
            ts = float(prev.get("status_only_since_ts", 0.0) or 0.0)
            return ts if ts > 0 else time.time()
        return 0.0
    except Exception:
        return 0.0


def step_once() -> Dict[str, Any]:
    p = paths()
    state_dir = p["STATE_DIR"]
    ensure_xian_vector()
    snapshot = build_snapshot()
    session_text = _session_text(p)
    xian_alignment = session_to_xian(session_text)
    status_only_since_ts = _read_status_only_since(state_dir)
    route = recommend_route(snapshot, status_only_since_ts=status_only_since_ts)
    report = build_report(snapshot, route, float(xian_alignment.get("score", 0.0) or 0.0))

    lira_feedback = _read_lira_feedback()

    xian_state = {
        "schema": "lira.xian_state.v1",
        "updated_at": _now_iso(),
        "role": "observer-router-reporter",
        "snapshot": snapshot,
        "xian_alignment": xian_alignment,
        "route": route,
        "lira_feedback": lira_feedback,
    }
    xian_report = {
        "schema": "lira.xian_report.v1",
        "updated_at": _now_iso(),
        **report,
    }
    _write_json(state_dir / "xian_state.json", xian_state)
    _write_json(state_dir / "xian_router_state.json", route)
    _write_json(state_dir / "xian_report.json", xian_report)
    _maybe_report_to_lira(xian_state)

    # Execution bridge — domyka pętlę DECIDE → wykonanie
    try:
        from xian.executor import step as executor_step
        executor_step(xian_state, p)
    except Exception as exc:
        log.warning("[xian_core] executor_step failed: %s", exc)

    return xian_state


def main() -> int:
    _setup_embed_log()
    while True:
        try:
            step_once()
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            log.warning("[xian_core] step_once failed: %s", exc)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    raise SystemExit(main())
