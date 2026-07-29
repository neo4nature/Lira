"""xian/executor.py — Execution bridge dla Xian.

Gdy Xian jest w fazie DECIDE z rekomendacją route → pobiera zadanie
z kolejki lub capsule_bus i wysyła do właściwego modelu przez mcl.router.
Wynik zapisuje jako kapsułę do capsule_bus.

Mapa celów:
  xian        → depth="core"  (gemma3:4b z duszą)
  local_fast  → depth="fast"  (xian)
  lira        → kapsułka do Liry (przez capsule_bus)
  soryel      → skip (Soryel czyta własne kapsuły)
  status_only → skip
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("xian.executor")

_ROUTE_TO_DEPTH: Dict[str, str] = {
    "xian":        "core",
    "local_fast":  "fast",
}

# Tiered token budget per task type
_TASK_TIERS: Dict[str, Dict[str, Any]] = {
    "status":     {"num_predict": 80,  "suffix": "\nOdpowiedz: [stan] | [odchylenie] | [akcja]."},
    "analysis":   {"num_predict": 200, "suffix": "\nOdpowiedz w formacie: stan → wniosek → rekomendacja."},
    "reflection": {"num_predict": 350, "suffix": "\nOdpowiedz zwięźle: obserwacja → znaczenie → propozycja."},
}


def _detect_task_tier(target: str, phase: str) -> str:
    """Wykryj typ zadania z route.target i phase."""
    if target == "status_only":
        return "status"
    if target == "qwen_coder":
        return "reflection"
    if phase == "REFLECT":
        return "status"
    if phase == "DECIDE":
        return "analysis"
    return "analysis"

_last_executed_ts: float = 0.0
_last_status_prompt: str = ""
EXECUTOR_COOLDOWN = float(os.getenv("XIAN_EXECUTOR_COOLDOWN_S", "120"))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _pick_task(queue_file: Path) -> Optional[Dict[str, Any]]:
    """Pobierz najważniejsze zadanie z kolejki (nie event:*) lub None."""
    tasks = _read_json(queue_file, [])
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("status") in {"done", "failed"}:
            continue
        kind = str(task.get("kind") or "")
        if kind.startswith("event:"):
            continue
        payload = task.get("payload", {})
        if isinstance(payload, dict) and payload.get("text"):
            return task
    return None


def _mark_task_done(text: str, cap_id: str) -> None:
    """Odhacz wykonane zadanie — bez tego kolejka-pierwsza mieliłaby je w kółko."""
    from agents.core.queue_utils import mutate

    def _mark(tasks):
        for task in tasks:
            if not isinstance(task, dict) or task.get("status") in {"done", "failed"}:
                continue
            if str((task.get("payload") or {}).get("text") or "") != text:
                continue
            task["status"] = "done"
            task["result_capsule_id"] = cap_id
            task["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            return tasks
        return None

    try:
        if mutate(_mark) is None:
            log.warning("[executor] nie udało się odhaczyć zadania — kolejka zajęta")
    except Exception as exc:
        log.warning("[executor] nie udało się odhaczyć zadania: %s", exc)


def _pick_capsule_task(state_dir: Path) -> Optional[str]:
    """Sprawdź czy jest pending zadanie z xian_report — jako prompt."""
    report_path = state_dir / "xian_report.json"
    report = _read_json(report_path, {})
    if not isinstance(report, dict):
        return None
    summary = report.get("summary") or report.get("status_line") or ""
    notes = report.get("notes") or []
    if not summary:
        return None
    notes_text = "\n".join(f"- {n}" for n in notes[:3]) if notes else ""
    return f"Stan systemu Xian:\n{summary}\n{notes_text}"


def step(xian_state: Dict[str, Any], paths: Dict[str, Path]) -> Optional[str]:
    """
    Wykonaj jeden krok execution bridge.
    Zwraca ID nowej kapsuły lub None.
    """
    global _last_executed_ts

    # Sprawdź cooldown
    now = time.time()
    if now - _last_executed_ts < EXECUTOR_COOLDOWN:
        return None

    route = xian_state.get("route", {})
    target = str(route.get("target") or "")
    phase = str((xian_state.get("snapshot") or {}).get("phase") or "")
    confidence = float(route.get("confidence") or 0.0)

    # Faza zostaje twarda — poza ACT router i tak odmówi (gate allow_llm_calls).
    if phase not in ("DECIDE", "ACT"):
        return None

    state_dir = paths.get("STATE_DIR", Path("."))
    # Ścieżka z config, nie sklejana z RUNTIME_DIR — odhaczanie idzie przez
    # queue_utils i musi trafiać w ten sam plik, który czyta _pick_task.
    queue_file = Path(paths.get("QUEUE_FILE") or paths.get("RUNTIME_DIR", Path(".")) / "queue" / "queue_tasks.json")

    # Najpierw prawdziwa robota z kolejki. Ping statusowy jest tylko wypełniaczem
    # ciszy — dopóki szedł pierwszy, xian_report.json (zawsze ma summary) zjadał
    # każdy przebieg i gałąź kolejki nie wykonała się ani razu: 435 kapsuł,
    # 8 unikalnych pytań, wszystkie ten sam szablon.
    global _last_status_prompt
    queue_text = ""
    task = _pick_task(queue_file)
    if task:
        queue_text = str((task.get("payload") or {}).get("text") or "")

    # Route rozstrzyga CZYM wykonać, nie CZY. Bramka celu i pewności obowiązuje
    # tylko wypełniacz ciszy; zadanie od feedera jest samo w sobie powodem.
    # Inaczej wykonanie zależało od `pressure >= 0.88` albo 20 zadań w kolejce —
    # czyli budziło Xiana dokładnie wtedy, gdy maszyna była najbardziej zajęta,
    # a przy spokojnym systemie kolejka stała bezterminowo (2026-07-28).
    if not queue_text:
        if target not in _ROUTE_TO_DEPTH:
            return None
        if confidence < 0.6:
            return None

    depth = _ROUTE_TO_DEPTH.get(target, "fast")

    # Zadanie z kolejki to analiza, nie meldunek — status_only dałby mu 80 tokenów.
    tier = "analysis" if queue_text else _detect_task_tier(target, phase)
    tier_cfg = _TASK_TIERS[tier]

    prompt = queue_text

    if not prompt:
        status_prompt = _pick_capsule_task(state_dir) or ""
        # Dedup po TREŚCI, nie po czasie — to samo pytanie nie zasługuje na drugie
        # mielenie. Ta sama lekcja co przy embeddingach rosnących plików.
        if status_prompt and status_prompt == _last_status_prompt:
            log.info("[executor] ping statusowy bez zmian — pomijam wywołanie modelu")
            return None
        prompt = status_prompt
        _last_status_prompt = status_prompt

    if not prompt or len(prompt.strip()) < 10:
        return None

    # Dodaj kontrakt odpowiedzi do promptu
    prompt_with_contract = prompt + tier_cfg["suffix"]

    # Wywołaj router
    try:
        from mcl.router import route as mcl_route
        log.info("[executor] DECIDE phase=%s target=%s depth=%s tier=%s num_predict=%d prompt=%d chars",
                 phase, target, depth, tier, tier_cfg["num_predict"], len(prompt_with_contract))
        result = mcl_route(prompt_with_contract, depth=depth, num_predict=tier_cfg["num_predict"])
        if not result or result.startswith("[router"):
            log.info("[executor] Router zwrócił pusty/błędny wynik")
            return None
    except Exception as exc:
        log.warning("[executor] mcl.route błąd: %s", exc)
        return None

    # Zapisz jako kapsułę
    try:
        from governance.capsule_bus import init_db, send_capsule
        init_db()
        cap_id = send_capsule(
            type="result",
            weight="commentary",
            layer="shared",
            owner="shared",
            sender="Xian",
            recipient="Neo",
            payload={
                "title": f"Xian [{target}] → wynik",
                "body": result[:1200],
                "route_target": target,
                "route_confidence": confidence,
                "phase": phase,
                "prompt_preview": prompt[:200],
            },
            project="xian_execution",
            status="new",
        )
        _last_executed_ts = now
        if queue_text:
            _mark_task_done(queue_text, cap_id)
        log.info("[executor] Wynik zapisany: %s (%d znaków, źródło=%s)",
                 cap_id[:8], len(result), "kolejka" if queue_text else "ping")
        return cap_id
    except Exception as exc:
        log.warning("[executor] Błąd zapisu kapsuły: %s", exc)
        return None
