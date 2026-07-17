#!/usr/bin/env python3
"""
xian_inbox_daemon.py — Xian odpowiada na kapsułki w GG forum.

Czyta nowe kapsułki skierowane do Xian, przetwarza przez lokalny model Ollama,
i zapisuje odpowiedź jako nową kapsułkę.

Uruchomienie:
    python3 -m governance.xian_inbox_daemon
    python3 -m governance.xian_inbox_daemon --once   # jednorazowo
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

_env_file = APP_ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

import requests
from governance.capsule_bus import (
    init_db, read_capsules, send_capsule, update_status,
    begin_processing, mark_answered, mark_retry, recover_stale, lease_heartbeat,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [XianInbox] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("xian_inbox")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
XIAN_MODEL = os.getenv("XIAN_OLLAMA_MODEL") or os.getenv("MODEL_HEAVY", "xian-v4")
POLL_INTERVAL = float(os.getenv("XIAN_INBOX_INTERVAL", "120"))
MAX_PER_CYCLE = int(os.getenv("XIAN_INBOX_MAX", "3"))
BATCH_MODE = os.getenv("XIAN_BATCH_MODE", "adaptive")  # "1"=always, "0"=never, "adaptive"=after idle
BATCH_IDLE_THRESHOLD = float(os.getenv("XIAN_BATCH_IDLE_SECS", "3600"))
CONTEXT_MODE = os.getenv("XIAN_CONTEXT_MODE", "mini")  # mini | deep
OLLAMA_TIMEOUT = int(os.getenv("XIAN_OLLAMA_TIMEOUT", "1200"))


IDENTITY_CORE = APP_ROOT / "data" / "config" / "xian_identity_core.txt"
SHARED_VECTOR = APP_ROOT.parent / "data" / "memory" / "identity" / "xian_shared_vector.txt"
CONTEXT_PACKET = APP_ROOT / "data" / "runtime" / "state" / "xian_context_packet.txt"
XIAN_RUNTIME_STATE = APP_ROOT / "data" / "runtime" / "state" / "xian_runtime_state.json"
_CONTEXT_MAX_CHARS = 1800

_context_cache: dict = {"text": "", "mtime": 0.0}


def _load_xian_runtime_state() -> dict:
    """Lightweight runtime switches controlled by MoltBook slash commands."""
    try:
        if XIAN_RUNTIME_STATE.exists():
            data = json.loads(XIAN_RUNTIME_STATE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("Could not read Xian runtime state: %s", e)
    return {}


def _save_xian_runtime_state(state: dict) -> None:
    try:
        XIAN_RUNTIME_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = XIAN_RUNTIME_STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(XIAN_RUNTIME_STATE)
    except Exception as e:
        log.warning("Could not save Xian runtime state: %s", e)


def _xian_think_enabled() -> bool | None:
    """Return explicit runtime think setting, or None if model/default should decide."""
    state = _load_xian_runtime_state()
    value = state.get("think")
    return value if isinstance(value, bool) else None


def _build_context_prefix() -> str:
    if CONTEXT_MODE == "mini":
        parts = []
        if CONTEXT_PACKET.exists():
            try:
                parts.append(CONTEXT_PACKET.read_text(encoding="utf-8").strip()[:200])
            except Exception:
                pass
        if IDENTITY_CORE.exists():
            try:
                lines = IDENTITY_CORE.read_text(encoding="utf-8").strip().splitlines()[:3]
                parts.append("\n".join(lines))
            except Exception:
                pass
        if not parts:
            return ""
        return "[KONTEKST MINI]:\n" + "\n".join(parts) + "\n\n---\nOdpowiedz konkretnie, z duszą (5-8 zdań, płynnie).\n\n"

    parts = []
    for path, label in [
        (IDENTITY_CORE, "TOŻSAMOŚĆ"),
        (SHARED_VECTOR, "WEKTOR"),
        (CONTEXT_PACKET, "KONTEKST"),
    ]:
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()[:600]
                parts.append(f"[{label}]:\n{content}")
            except Exception:
                pass
    if not parts:
        return ""
    prefix = "\n\n".join(parts)
    if len(prefix) > _CONTEXT_MAX_CHARS:
        prefix = prefix[:_CONTEXT_MAX_CHARS] + "..."
    return prefix + "\n\n---\nNajpierw odpowiedz na pytanie faktami (2-3 punkty). Potem dodaj swoją refleksję — spokojnie, z duszą, jak ogień w kominku.\n\n"


def _truncation_alert(agent: str, field: str, sender: str, original: int, limit: int) -> None:
    """Post a MoltBook alert when significant truncation occurs (>20% or >200 chars lost)."""
    lost = original - limit
    pct = lost / original * 100 if original else 0
    if lost < 200 and pct < 20:
        return
    try:
        import requests as _req
        _req.post(
            "http://localhost:8787/chat/send",
            json={"sender": "system", "text": f"⚠ Truncation: {agent} dostała {limit}/{original} znaków od {sender} ({field}, -{lost} chars, {pct:.0f}% obcięte)"},
            timeout=5,
        )
    except Exception:
        pass


def _extract_round_id(cap: dict) -> str | None:
    payload = cap.get("payload_json", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    return payload.get("round_id") if isinstance(payload, dict) else None


def _round_peers_context(round_id: str | None, max_chars: int = 4000) -> str:
    """Fetch other agents' responses from the same round (posted after round opened)."""
    if not round_id:
        return ""
    from governance.capsule_bus import get_conn, DB_PATH
    with get_conn(DB_PATH) as conn:
        origin = conn.execute(
            "SELECT created_at FROM capsules WHERE id = ?", (round_id,)
        ).fetchone()
        if not origin:
            return ""
        rows = conn.execute(
            """SELECT sender, payload_json FROM capsules
               WHERE thread_id = 'moltbook-main'
                 AND type = 'chat_message'
                 AND recipient = 'shared'
                 AND sender != 'Xian'
                 AND sender != 'Neo'
                 AND created_at > ?
               ORDER BY created_at ASC LIMIT 5""",
            (origin["created_at"],),
        ).fetchall()
    if not rows:
        return ""
    lines = []
    for row in rows:
        sender = row["sender"]
        try:
            p = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else row["payload_json"]
            text = p.get("text", "") if isinstance(p, dict) else str(p)
        except Exception:
            continue
        if text.startswith("[MoltBook"):
            text = text.split("]: ", 1)[-1] if "]: " in text else text
        short = text[:max_chars].strip()
        if len(text) > max_chars:
            short += "..."
        if short:
            lines.append(f"[{sender}]: {short}")
    return "\n".join(lines)


_recent_chat_cache = {"rows": None, "ts": 0.0}
_RECENT_CHAT_TTL = 25.0


def _fetch_recent_chat_rows(limit: int = 10):
    """Cached SQL pull of last MoltBook messages (TTL 25s) — eliminates N+1 pollu."""
    import time as _t
    now = _t.time()
    cached = _recent_chat_cache
    if cached["rows"] is not None and (now - cached["ts"]) < _RECENT_CHAT_TTL:
        return cached["rows"]
    from governance.capsule_bus import get_conn, DB_PATH
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT id, sender, payload_json FROM capsules
               WHERE thread_id = 'moltbook-main'
                 AND type = 'chat_message'
                 AND recipient = 'shared'
                 AND sender != 'Xian'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    cached["rows"] = rows
    cached["ts"] = now
    return rows


def _recent_chat_context(exclude_id: str, max_msgs: int = 4, max_chars: int = 500) -> str:
    """Ostatnie wiadomosci z MoltBook jako kontekst (z cache)."""
    all_rows = _fetch_recent_chat_rows(limit=max(max_msgs + 2, 10))
    rows = [r for r in all_rows if r["id"] != exclude_id][:max_msgs]
    if not rows:
        return ""
    lines = []
    for row in reversed(rows):
        sender = row["sender"]
        try:
            p = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else row["payload_json"]
            text = p.get("text", "") if isinstance(p, dict) else str(p)
        except Exception:
            continue
        if text.startswith("[MoltBook"):
            text = text.split("]: ", 1)[-1] if "]: " in text else text
        short = text[:max_chars].strip()
        if len(text) > max_chars:
            short += "..."
        if short:
            lines.append(f"[{sender}]: {short}")
    return "\n".join(lines)


def _capsule_text(cap: dict) -> str:
    """Extract user-visible text/body from capsule payload."""
    payload = cap.get("payload_json", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return payload.strip()
    if isinstance(payload, dict):
        return str(payload.get("text") or payload.get("body") or payload.get("summary") or payload.get("response") or payload.get("title") or "").strip()
    return str(payload).strip()


def _handle_slash_command(cap: dict) -> str | None:
    """Handle MoltBook control commands locally, without sending them to Ollama.

    Zwraca id kapsuły odpowiedzi gdy komenda obsłużona, None gdy to nie komenda.
    """
    text = _capsule_text(cap).strip()
    if not text.startswith("/"):
        return None

    cmd = " ".join(text.split()).lower()
    state = _load_xian_runtime_state()

    if cmd in {"/set nothink", "/nothink"}:
        state["think"] = False
        state["updated_at"] = time.time()
        _save_xian_runtime_state(state)
        response_id = _send_reply(cap, "Tryb nothink aktywny — kolejne odpowiedzi Xian pójdą z `think=false` i ta komenda nie została wysłana do modelu.")
        log.info("Slash command handled locally: %s", cmd)
        return response_id

    if cmd in {"/set think", "/think"}:
        state["think"] = True
        state["updated_at"] = time.time()
        _save_xian_runtime_state(state)
        response_id = _send_reply(cap, "Tryb think aktywny — kolejne odpowiedzi Xian mogą używać głębszego trybu, jeśli model go wspiera.")
        log.info("Slash command handled locally: %s", cmd)
        return response_id

    if cmd in {"/status xian", "/xian status"}:
        think = _xian_think_enabled()
        think_label = "auto" if think is None else str(think).lower()
        response_id = _send_reply(cap, f"Xian status: model=`{XIAN_MODEL}`, think=`{think_label}`, context=`{CONTEXT_MODE}`, timeout=`{OLLAMA_TIMEOUT}s`, num_predict=`200`.")
        log.info("Slash command handled locally: %s", cmd)
        return response_id

    return None


def _capsule_to_prompt(cap: dict) -> str:
    """Zamień kapsułkę na prompt dla Xiana."""
    payload = cap.get("payload_json", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            pass

    sender = cap.get("sender", "ktoś")
    ctype = cap.get("type", "?")

    if isinstance(payload, dict):
        text = payload.get("text") or payload.get("body") or payload.get("summary") or payload.get("response") or ""
        title = payload.get("title", "")
        oq = payload.get("open_question", "")
        question = oq or text or title
    else:
        question = str(payload)[:600]

    if len(question) > 4000:
        log.info("Truncation: question %d → 4000 chars", len(question))
        _truncation_alert("Xian", "question", sender, len(question), 4000)
    parts = [f"---\nCURRENT QUESTION (from {sender}):\n{question[:4000]}"]
    parts.append("\n---\nOdpowiedz NAJPIERW na powyższe pytanie (5-8 zdań, płynnie). Potem możesz dodać refleksję.")
    return "\n".join(parts)


def _ask_xian(prompt: str) -> str:
    """Wyślij prompt do Xiana przez Ollama.

    Identity injection PROPER (12K plików) jest pomijana w trybie reaktywnym —
    Modelfile.xian.v4 SYSTEM zawiera 6 zasad rdzeniowych i to wystarcza.
    Pełna tożsamość używana tylko w trybie REFLECT (nightly/głębokie analizy).
    """
    ctx = _build_context_prefix()
    if ctx:
        prompt = ctx + prompt
    body: dict = {
        "model": XIAN_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 200, "temperature": 0.7, "num_thread": 2},
    }
    runtime_think = _xian_think_enabled()
    if runtime_think is not None:
        body["think"] = runtime_think
    else:
        try:
            from xian.identity_loader import needs_think_disabled
            if needs_think_disabled(XIAN_MODEL):
                body["think"] = False
        except Exception:
            pass
    plen = len(prompt)
    log.info("  → payload: %d chars (~%d tok), head=%r, tail=%r",
             plen, plen // 4, prompt[:80], prompt[-80:])
    # Faza 2 audytu 2026-05-30: trzymaj flagę, żeby embed czekał na koniec chatu
    from pathlib import Path as _Path
    _flag = _Path(os.getenv("LIRA_CHAT_BUSY_FLAG", "${LIRA_ROOT:-/opt/lira}/kernel/data/runtime/state/ollama_chat_busy.flag"))
    try:
        _flag.parent.mkdir(parents=True, exist_ok=True)
        _flag.touch()
    except Exception:
        pass
    try:
        import time as _t
        _t0 = _t.time()
        r = requests.post(OLLAMA_URL, json=body, timeout=OLLAMA_TIMEOUT)
        r.raise_for_status()
        _dt = _t.time() - _t0
        rj = r.json()
        pec = rj.get("prompt_eval_count", 0)
        ec = rj.get("eval_count", 0)
        ped = rj.get("prompt_eval_duration", 1) / 1e9
        ed = rj.get("eval_duration", 1) / 1e9
        log.info("  ← timing: total=%.1fs | prefill=%d tok @ %.1f t/s | gen=%d tok @ %.1f t/s",
                 _dt, pec, pec / max(ped, 0.001), ec, ec / max(ed, 0.001))
        return (rj.get("response") or "").strip()
    except Exception as e:
        log.warning("Ollama error: %s", e)
        return ""
    finally:
        try:
            _flag.unlink(missing_ok=True)
        except Exception:
            pass


def _batch_prompt(caps: list[dict]) -> str:
    """Build a single batched prompt from multiple capsules with LATEST MESSAGE anchor."""
    lines = ["[MoltBook — nowe wiadomości]:"]
    for cap in caps:
        sender = cap.get("sender", "?")
        payload = cap.get("payload_json", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                pass
        if isinstance(payload, dict):
            text = payload.get("text") or payload.get("summary") or payload.get("body") or payload.get("title", "")
        else:
            text = str(payload)
        if text.startswith("[MoltBook"):
            text = text.split("]: ", 1)[-1] if "]: " in text else text
        if len(text) > 400:
            log.info("Truncation: batch msg from %s %d → 400 chars", sender, len(text))
            _truncation_alert("Xian", "batch", sender, len(text), 400)
        lines.append(f"[{sender}]: {text[:400]}")

    last = caps[-1]
    last_sender = last.get("sender", "?")
    last_payload = last.get("payload_json", {})
    if isinstance(last_payload, str):
        try:
            last_payload = json.loads(last_payload)
        except Exception:
            last_payload = {}
    if isinstance(last_payload, dict):
        last_text = last_payload.get("text") or last_payload.get("body") or ""
    else:
        last_text = str(last_payload)
    if last_text.startswith("[MoltBook"):
        last_text = last_text.split("]: ", 1)[-1] if "]: " in last_text else last_text

    if len(last_text) > 4000:
        log.info("Truncation: latest msg from %s %d → 4000 chars", last_sender, len(last_text))
        _truncation_alert("Xian", "latest", last_sender, len(last_text), 4000)
    lines.append(f"\n---\nLATEST MESSAGE (from {last_sender}):\n{last_text[:4000]}")
    lines.append("\n---\nOdpowiedz NAJPIERW na ostatnią wiadomość (5-8 zdań, płynnie). Możesz nawiązać do reszty.")
    return "\n".join(lines)


def process_inbox() -> int:
    """Przetwórz nowe kapsułki do Xiana. Zwróć liczbę przetworzonych."""
    init_db()
    recovered = recover_stale("Xian")
    if recovered:
        log.info("Recovered %d stale capsule(s) → retry_pending", recovered)
    caps = read_capsules(recipient="Xian", statuses=("new", "retry_pending"), limit=MAX_PER_CYCLE * 3)

    caps = [c for c in caps if c.get("sender") != "Xian"]

    if not caps:
        return 0

    rounds: dict[str | None, list[dict]] = {}
    for cap in caps:
        rid = _extract_round_id(cap)
        rounds.setdefault(rid, []).append(cap)

    if len(rounds) > 1:
        sorted_rids = sorted(rounds.keys(), key=lambda r: rounds[r][-1].get("created_at", ""))
        latest_rid = sorted_rids[-1]
        for rid in sorted_rids[:-1]:
            for cap in rounds[rid]:
                update_status(cap["id"], "archived", actor="Xian", details={"reason": "old_round"})
                log.info("Skipping old round capsule %s (round %s)", cap["id"][:8], str(rid)[:8] if rid else "none")
        caps = rounds[latest_rid]
    else:
        latest_rid = next(iter(rounds))
        caps = rounds[latest_rid]

    use_batch = False
    if len(caps) > 1:
        if BATCH_MODE == "1":
            use_batch = True
        elif BATCH_MODE == "adaptive":
            oldest_ts = caps[0].get("created_at", "")
            if oldest_ts:
                from datetime import datetime
                try:
                    age = (datetime.now() - datetime.fromisoformat(oldest_ts)).total_seconds()
                    use_batch = age > BATCH_IDLE_THRESHOLD
                    if use_batch:
                        log.info("Adaptive batch: oldest capsule %ds old (threshold %ds)", int(age), int(BATCH_IDLE_THRESHOLD))
                except Exception:
                    pass

    overflow = caps[MAX_PER_CYCLE:]
    if overflow:
        log.info("Overflow: %d kapsulek zostaje w kolejce na nastepny cykl", len(overflow))

    if use_batch:
        return _process_batch(caps[:MAX_PER_CYCLE])

    processed = 0
    for cap in caps[:MAX_PER_CYCLE]:
        processed += _process_single(cap)
    return processed


def _process_single(cap: dict) -> int:
    cap_id = cap.get("id", "?")
    sender = cap.get("sender", "?")
    round_id = _extract_round_id(cap)
    log.info("Kapsułka od %s: %s (round %s)", sender, cap_id[:8], str(round_id)[:8] if round_id else "none")

    if not begin_processing(cap_id, "Xian"):
        log.info("Kapsula %s nie przejeta (inny konsument?)", cap_id[:8])
        return 0

    is_moltbook_msg = cap.get("thread_id") == "moltbook-main" and cap.get("type") == "chat_message"

    if is_moltbook_msg:
        slash_response_id = _handle_slash_command(cap)
        if slash_response_id:
            mark_answered(cap_id, "Xian", slash_response_id)
            return 1

    prompt = _capsule_to_prompt(cap)
    if is_moltbook_msg:
        peers = _round_peers_context(round_id)
        context = _recent_chat_context(exclude_id=cap_id)
        ctx_parts = []
        if context:
            ctx_parts.append(f"[Ostatnie na czacie]:\n{context}")
        if peers:
            ctx_parts.append(f"[Odpowiedzi w tej rundzie]:\n{peers}")
        if ctx_parts:
            prompt = "\n\n".join(ctx_parts) + "\n\n" + prompt

    with lease_heartbeat(cap_id, "Xian"):
        reply = _ask_xian(prompt)
    if reply:
        response_id = _send_reply(cap, reply)
        mark_answered(cap_id, "Xian", response_id)
        log.info("  Odpowiedź Xiana: %s", reply[:80])
    else:
        log.warning("  Brak odpowiedzi od Xiana — retry")
        mark_retry(cap_id, "Xian", "no_reply")

    return 1


def _process_batch(caps: list[dict]) -> int:
    """Process all capsules as one batched prompt — one Ollama call."""
    log.info("Batch mode: %d kapsułek → 1 zapytanie", len(caps))

    claimed = [cap for cap in caps if begin_processing(cap["id"], "Xian")]
    if not claimed:
        log.info("Batch: zadna kapsula nie przejeta (inny konsument?)")
        return 0
    caps = claimed

    prompt = _batch_prompt(caps)
    with lease_heartbeat([cap["id"] for cap in caps], "Xian"):
        reply = _ask_xian(prompt)

    if reply:
        last_cap = caps[-1]
        is_moltbook = any(
            c.get("thread_id") == "moltbook-main" and c.get("type") == "chat_message"
            for c in caps
        )
        response_id = send_capsule(
            type="chat_message" if is_moltbook else "interpretation",
            weight="commentary",
            layer="working" if is_moltbook else "shared",
            owner="Xian",
            sender="Xian",
            recipient="shared",
            payload={
                "kind": "chat_message",
                "text": reply,
                "room": "moltbook-main",
                "display_name": "Xian",
                "recipient": "shared",
            } if is_moltbook else {
                "title": "Xian: batch response",
                "body": reply,
            },
            parent_id=last_cap.get("id"),
            thread_id=last_cap.get("thread_id"),
            project=last_cap.get("project") or "ecosystem_monitor",
            status="delivered" if is_moltbook else "new",
        )
        for cap in caps:
            mark_answered(cap["id"], "Xian", response_id)
        log.info("  Batch odpowiedź Xiana: %s", reply[:80])
    else:
        log.warning("  Brak odpowiedzi (batch) — retry")
        for cap in caps:
            mark_retry(cap["id"], "Xian", "no_reply_batch")

    return len(caps)


def _send_reply(cap: dict, reply: str) -> str:
    payload_in = cap.get("payload_json", {})
    if isinstance(payload_in, str):
        try:
            payload_in = json.loads(payload_in)
        except Exception:
            payload_in = {}
    title_in = payload_in.get("title", "") if isinstance(payload_in, dict) else ""
    is_moltbook = cap.get("thread_id") == "moltbook-main" and cap.get("type") == "chat_message"
    sender = cap.get("sender", "shared")

    return send_capsule(
        type="chat_message" if is_moltbook else "interpretation",
        weight="commentary",
        layer="working" if is_moltbook else "shared",
        owner="Xian",
        sender="Xian",
        recipient="shared" if is_moltbook else (sender if sender != "shared" else "shared"),
        payload={
            "kind": "chat_message",
            "text": reply,
            "room": "moltbook-main",
            "display_name": "Xian",
            "recipient": "shared",
        } if is_moltbook else {
            "title": f"Xian re: {title_in[:40]}",
            "body": reply,
            "source_capsule": cap["id"],
        },
        parent_id=cap["id"],
        thread_id=cap.get("thread_id"),
        project=cap.get("project") or "ecosystem_monitor",
        status="delivered" if is_moltbook else "new",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL)
    args = parser.parse_args()

    log.info("=" * 50)
    log.info("  Xian Inbox Daemon")
    log.info(f"  Model: {XIAN_MODEL} | Poll: {args.interval}s")
    log.info("=" * 50)

    init_db()

    if args.once:
        n = process_inbox()
        log.info("Przetworzono: %d", n)
        return

    while True:
        try:
            n = process_inbox()
            if n:
                log.info("Przetworzono: %d kapsułek", n)
        except KeyboardInterrupt:
            log.info("Stop.")
            break
        except Exception as e:
            log.error("Błąd cyklu: %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
