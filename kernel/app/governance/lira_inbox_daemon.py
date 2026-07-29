#!/usr/bin/env python3
"""
lira_inbox_daemon.py — Lira odpowiada na kapsulki z MoltBook czatu.

Czyta nowe chat_message skierowane do Liry, dostarcza przez CDP (ChatGPT),
czeka na odpowiedz i zapisuje ja jako chat_message w moltbook-main.

Wzorzec identyczny jak xian_inbox_daemon.py.

Uruchomienie:
    python3 -m governance.lira_inbox_daemon
    python3 -m governance.lira_inbox_daemon --once   # jednorazowo
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

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

from governance.capsule_bus import (
    init_db, read_capsules, send_capsule, update_status,
    begin_processing, mark_answered, mark_retry, recover_stale, lease_heartbeat,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [LiraInbox] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lira_inbox")

CDP_PORT = int(os.getenv("GG_CDP_PORT", "9222"))
POLL_INTERVAL = float(os.getenv("LIRA_INBOX_INTERVAL", "60"))
MAX_PER_CYCLE = int(os.getenv("LIRA_INBOX_MAX", "5"))
BATCH_MODE = os.getenv("LIRA_BATCH_MODE", "1") == "1"
RESPONSE_TIMEOUT = float(os.getenv("LIRA_RESPONSE_TIMEOUT", "120"))
SETTLE_CHECKS = int(os.getenv("LIRA_SETTLE_CHECKS", "4"))
SETTLE_INTERVAL = float(os.getenv("LIRA_SETTLE_INTERVAL", "4"))


# ── CDP helpers (z gg_transport_daemon) ──────────────────────────────────────

def _cdp_tabs() -> list:
    try:
        with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return []


def _find_lira_ws() -> Optional[str]:
    for tab in _cdp_tabs():
        haystack = (tab.get("url", "") + "|" + tab.get("title", "")).lower()
        if "chatgpt.com" in haystack:
            return tab.get("webSocketDebuggerUrl")
    return None


async def _cdp_eval(ws_url: str, expr: str, timeout: float = 10.0) -> Optional[str]:
    try:
        import websockets as _ws
        async with _ws.connect(ws_url, open_timeout=5) as ws:
            payload = {"id": 1, "method": "Runtime.evaluate",
                       "params": {"expression": expr, "returnByValue": True}}
            await ws.send(json.dumps(payload))
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    data = json.loads(raw)
                    if data.get("id") == 1:
                        result = data.get("result", {}).get("result", {})
                        if result.get("type") == "string":
                            return result.get("value", "")
                        return json.dumps(result.get("value", ""))
                except asyncio.TimeoutError:
                    continue
    except Exception as exc:
        log.debug("CDP eval error: %s", exc)
    return None


def cdp_eval(ws_url: str, expr: str, timeout: float = 10.0) -> Optional[str]:
    return asyncio.run(_cdp_eval(ws_url, expr, timeout))


# ── Wysylanie i odbieranie ───────────────────────────────────────────────────

READ_LAST_JS = (
    "(function(){"
    "var c=document.querySelectorAll('[data-message-author-role=\"assistant\"]');"
    # Wezel bledu ChatGPT tez ma role=assistant, ale nie ma .markdown - jego innerText
    # to baner ("Cos poszlo nie tak" / "Ponow probe"). Nigdy nie czytamy takiego wezla.
    "for(var i=c.length-1;i>=0;i--){"
    "var md=c[i].querySelectorAll('.markdown');"
    "if(!md.length)continue;"
    "var t=(md[md.length-1].innerText||'').trim();"
    "if(t)return t;}"
    "return '';"
    "})()"
)

FIND_SEND_JS = (
    "(function(){"
    "var sels=["
    "'button[data-testid=\"composer-send-button\"]',"
    "'button[aria-label*=\"Wyślij\"]',"
    "'button[aria-label*=\"Wyslij\"]',"
    "'button[aria-label*=\"Send\"]',"
    "'button[data-testid=\"send-button\"]'"
    "];"
    "for(var i=0;i<sels.length;i++){var b=document.querySelector(sels[i]);"
    "if(b&&!b.disabled)return sels[i];}"
    "return '';"
    "})()"
)


def _get_lira_page():
    """Podepnij Playwright do CDP i znajdz zakladke ChatGPT. Zwraca (playwright, browser, page) lub None."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        log.error("Playwright niedostepny: %s", exc)
        return None
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    except Exception as exc:
        log.error("CDP connect fail: %s", exc)
        pw.stop()
        return None
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                url = page.url
            except Exception:
                continue
            if "chatgpt.com" in url or "chat.openai.com" in url:
                return pw, browser, page
    try:
        browser.close()
    except Exception:
        pass
    pw.stop()
    return None


def send_and_receive(ws_url: str, message: str) -> Optional[str]:
    """Wpisuje wiadomosc do ChatGPT przez Playwright, klika wyslij, czeka na stabilna odpowiedz.

    Nowe podejscie (2026-07-19): React/Lexical composer ChatGPT odrzuca
    document.execCommand('insertText') i syntetyczny ClipboardEvent('paste')
    — DOM chwilowo pokazuje tekst, potem React czysci. Playwright locator.fill
    dziala natywnie i tekst sie utrzymuje. Selektor send buttona zaktualizowany
    do PL aria-label ("Wyslij polecenie/wiadomosc") + composer-send-button testid.
    Parametr ws_url zostawiony dla zgodnosci — nie jest juz uzywany.
    """
    ctx_bundle = _get_lira_page()
    if not ctx_bundle:
        log.warning("Nie znaleziono zakladki ChatGPT przez Playwright")
        return None
    pw, browser, page = ctx_bundle

    baseline = ""
    try:
        # Snapshot ostatniej odpowiedzi PRZED wysylka — chroni przed duplikatem
        # gdy ChatGPT wciaz wyswietla stara wiadomosc.
        try:
            baseline = (page.evaluate(READ_LAST_JS) or "").strip()
        except Exception:
            baseline = ""

        composer = page.locator('#prompt-textarea')
        # Fokus + wyczysc (na wypadek gdyby zostal draft)
        try:
            composer.click(timeout=5000)
        except Exception as exc:
            log.warning("Composer click fail: %s", exc)
            return None
        page.wait_for_timeout(150)
        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
        except Exception:
            pass
        page.wait_for_timeout(150)

        # METODA 1: fill (Playwright natywnie, symuluje wpisywanie do contenteditable)
        insert_method = None
        try:
            composer.fill(message, timeout=8000)
            page.wait_for_timeout(400)
            current = (composer.inner_text() or "").strip()
            if current and message.strip()[:40] in current:
                insert_method = "fill"
        except Exception as exc:
            log.debug("fill exc: %s", exc)

        # METODA 2: press_sequentially — realne eventy klawiatury (fallback)
        if not insert_method:
            log.info("fill nie utrzymalo tekstu — fallback press_sequentially")
            try:
                composer.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.wait_for_timeout(150)
                composer.press_sequentially(message, delay=1)
                page.wait_for_timeout(400)
                current = (composer.inner_text() or "").strip()
                if current and message.strip()[:40] in current:
                    insert_method = "press_sequentially"
            except Exception as exc:
                log.warning("press_sequentially exc: %s", exc)

        if not insert_method:
            log.warning("Nie udalo sie wstawic tekstu ani przez fill ani przez press_sequentially")
            # Sprzatnij co jest w composerze
            try:
                composer.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
            except Exception:
                pass
            return None

        log.info("Tekst wstawiony (%s, %d znakow)", insert_method, len(message))

        # Znajdz i klik send button — kolejnosc selektorow zgodna z FIND_SEND_JS
        page.wait_for_timeout(200)
        sel_used = ""
        try:
            sel_used = page.evaluate(FIND_SEND_JS) or ""
        except Exception:
            sel_used = ""

        clicked = False
        if sel_used:
            try:
                page.locator(sel_used).first.click(timeout=5000)
                clicked = True
                log.info("Send button klik: %s", sel_used)
            except Exception as exc:
                log.warning("Klik send buttonem [%s] nie udal sie: %s", sel_used, exc)

        if not clicked:
            log.warning("Send button nie znaleziony (%s) — fallback Enter", sel_used or "brak")
            try:
                composer.press("Enter")
                clicked = True
                log.info("Fallback Enter wyslany")
            except Exception as exc:
                log.warning("Enter fallback fail: %s", exc)

        if not clicked:
            return None

        log.info("Czekam na odpowiedz Liry...")
        page.wait_for_timeout(8000)
    finally:
        # NIE zamykaj browsera (CDP owned by user), tylko odpiecz playwright
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass

    # Reszta — czytanie odpowiedzi — wraca do cdp_eval bo pracujemy poza sesja Playwright
    # (unikamy trzymania Playwright przez cala minute podczas czekania).
    read_js = READ_LAST_JS

    # Prefix zamiast exact-match: łapie "Przeszukiwanie...", "Myśli przez 5s" itd.
    THINKING_PREFIXES = (
        "myśli", "thinking", "thought",
        "przeszukiwan", "searching", "wyszukiwan", "browsing", "szukam",
    )
    ERROR_PHRASES = [
        "połączenie przerwane", "oczekiwanie na pełną odpowiedź",
        "something went wrong", "an error occurred", "network error",
        "try again", "unable to generate", "too many requests",
        "coś poszło nie tak", "spróbuj ponownie", "ponów próbę",
        "wystąpił błąd", "zbyt wiele żądań",
    ]

    last_text = ""
    stable = 0
    deadline = time.time() + RESPONSE_TIMEOUT

    while time.time() < deadline:
        time.sleep(SETTLE_INTERVAL)
        current = cdp_eval(ws_url, read_js, timeout=8)
        if not current:
            continue
        stripped = current.strip()
        low = stripped.lower()
        if not low or low.startswith(THINKING_PREFIXES):
            continue
        if stripped == baseline:
            continue
        if any(ep in low for ep in ERROR_PHRASES):
            log.warning("ChatGPT error detected: %s", current[:80])
            continue
        if current == last_text:
            stable += 1
            if stable >= SETTLE_CHECKS:
                log.info("Odpowiedz stabilna (%d znakow)", len(current))
                return current
        else:
            stable = 0
            last_text = current

    log.warning("Timeout po %ds", RESPONSE_TIMEOUT)
    return last_text if last_text else None


# ── Glowna logika ───────────────────────────────────────────────────────────

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
                 AND sender != 'Lira'
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


def _recent_chat_context(exclude_id: str, own_name: str = "Lira", max_msgs: int = 5, max_chars_per_msg: int = 4000) -> str:
    """Zbierz ostatnie wiadomosci z MoltBook jako kontekst (skrocone)."""
    from governance.capsule_bus import get_conn, DB_PATH
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT sender, payload_json FROM capsules
               WHERE thread_id = 'moltbook-main'
                 AND type = 'chat_message'
                 AND recipient = 'shared'
                 AND id != ?
               ORDER BY created_at DESC LIMIT ?""",
            (exclude_id, max_msgs),
        ).fetchall()
    if not rows:
        return ""
    lines = []
    for row in reversed(rows):
        sender = row["sender"]
        if sender == own_name:
            continue
        try:
            p = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else row["payload_json"]
            text = p.get("text", "") if isinstance(p, dict) else str(p)
        except Exception:
            continue
        if text.startswith("[MoltBook"):
            text = text.split("]: ", 1)[-1] if "]: " in text else text
        short = text[:max_chars_per_msg].strip()
        if len(text) > max_chars_per_msg:
            short += "..."
        if short:
            lines.append(f"[{sender}]: {short}")
    return "\n".join(lines)


def _read_lira_chat_capsules(limit: int = 3) -> list[dict]:
    """Czytaj nowe chat_message z moltbook-main skierowane do Liry."""
    from governance.capsule_bus import get_conn, DB_PATH
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT * FROM capsules
               WHERE recipient = 'Lira'
                 AND type = 'chat_message'
                 AND thread_id = 'moltbook-main'
                 AND status IN ('new', 'retry_pending')
                 AND sender != 'Lira'
               ORDER BY created_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload_json"] = json.loads(item["payload_json"])
        except Exception:
            pass
        out.append(item)
    return out


def _build_batch_prompt(caps: list[dict]) -> str:
    """Zbuduj jeden prompt z kilku kapsulek — Lira odpowiada raz na wszystkie."""
    lines = ["[MoltBook — nowe wiadomosci na czacie]:"]
    for cap in caps:
        sender = cap.get("sender", "?")
        payload = cap.get("payload_json", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        if text.startswith("[MoltBook"):
            text = text.split("]: ", 1)[-1] if "]: " in text else text
        if len(text) > 4000:
            log.info("Truncation: batch msg from %s %d → 4000 chars", sender, len(text))
            _truncation_alert("Lira", "batch", sender, len(text), 4000)
        lines.append(f"[{sender}]: {text[:4000]}")

    last = caps[-1]
    last_sender = last.get("sender", "?")
    last_payload = last.get("payload_json", {})
    if isinstance(last_payload, str):
        try:
            last_payload = json.loads(last_payload)
        except Exception:
            last_payload = {}
    last_text = last_payload.get("text", "") if isinstance(last_payload, dict) else str(last_payload)
    if last_text.startswith("[MoltBook"):
        last_text = last_text.split("]: ", 1)[-1] if "]: " in last_text else last_text

    if len(last_text) > 4000:
        log.info("Truncation: latest msg from %s %d → 4000 chars", last_sender, len(last_text))
        _truncation_alert("Lira", "latest", last_sender, len(last_text), 4000)
    lines.append(f"\n---\nLATEST MESSAGE (from {last_sender}):\n{last_text[:4000]}")
    lines.append("\n---\nOdpowiedz NAJPIERW na ostatnia wiadomosc powyzej (5-8 zdan, plynnie). Mozesz nawiazac do wczesniejszych watkow jesli sa powiazane.")
    return "\n".join(lines)


def process_inbox() -> int:
    """Przetworz nowe chat_messages do Liry z moltbook-main."""
    init_db()
    recovered = recover_stale("Lira")
    if recovered:
        log.info("Recovered %d stale capsule(s) → retry_pending", recovered)
    caps = _read_lira_chat_capsules(limit=MAX_PER_CYCLE)

    if not caps:
        return 0

    ws_url = _find_lira_ws()
    if not ws_url:
        log.warning("Brak zakladki Liry (ChatGPT) — pomijam cykl")
        return 0

    valid_caps = []
    for cap in caps:
        payload = cap.get("payload_json", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        if not text:
            update_status(cap["id"], "archived", actor="Lira", details={"reason": "empty_text"})
        else:
            valid_caps.append(cap)

    if not valid_caps:
        return 0

    rounds: dict[str | None, list[dict]] = {}
    for cap in valid_caps:
        rid = _extract_round_id(cap)
        rounds.setdefault(rid, []).append(cap)

    if len(rounds) > 1:
        sorted_rids = sorted(rounds.keys(), key=lambda r: rounds[r][-1].get("created_at", ""))
        latest_rid = sorted_rids[-1]
        for rid in sorted_rids[:-1]:
            for cap in rounds[rid]:
                update_status(cap["id"], "archived", actor="Lira", details={"reason": "old_round"})
                log.info("Skipping old round capsule %s (round %s)", cap["id"][:8], str(rid)[:8] if rid else "none")
        valid_caps = rounds[latest_rid]

    if BATCH_MODE and len(valid_caps) > 1:
        return _process_batch(ws_url, valid_caps)

    return _process_single(ws_url, valid_caps[0])


def _process_batch(ws_url: str, caps: list[dict]) -> int:
    """Przetworz wiele kapsulek jako jeden batch — jedna odpowiedz Liry."""
    log.info("Batch mode: %d kapsulek → 1 prompt", len(caps))

    claimed = [cap for cap in caps if begin_processing(cap["id"], "Lira")]
    if not claimed:
        log.info("Batch: zadna kapsula nie przejeta (inny konsument?)")
        return 0

    context = _recent_chat_context(exclude_id=claimed[-1]["id"], own_name="Lira")
    prompt = _build_batch_prompt(claimed)
    if context:
        prompt = f"[Kontekst — ostatnie wiadomosci]:\n{context}\n\n{prompt}"

    with lease_heartbeat([cap["id"] for cap in claimed], "Lira"):
        reply = send_and_receive(ws_url, prompt)

    if reply:
        response_id = send_capsule(
            type="chat_message",
            weight="commentary",
            layer="working",
            owner="Lira",
            sender="Lira",
            recipient="shared",
            payload={
                "kind": "chat_message",
                "text": reply[:4000],
                "room": "moltbook-main",
                "display_name": "Lira",
                "recipient": "shared",
            },
            thread_id="moltbook-main",
            parent_id=claimed[-1]["id"],
            project="moltbook",
            status="delivered",
        )
        for cap in claimed:
            mark_answered(cap["id"], "Lira", response_id)
        log.info("  Batch odpowiedz Liry: %s", reply[:80])
    else:
        log.warning("  Brak odpowiedzi od Liry (batch) — retry")
        for cap in claimed:
            mark_retry(cap["id"], "Lira", "no_reply_batch")

    return len(claimed)


def _process_single(ws_url: str, cap: dict) -> int:
    """Przetworz jedna kapsulke."""
    cap_id = cap.get("id", "?")
    sender = cap.get("sender", "?")
    round_id = _extract_round_id(cap)
    payload = cap.get("payload_json", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
    log.info("Kapsulka od %s: %s (round %s)", sender, cap_id[:8], str(round_id)[:8] if round_id else "none")
    if not begin_processing(cap_id, "Lira"):
        log.info("Kapsula %s nie przejeta (inny konsument?)", cap_id[:8])
        return 0

    peers = _round_peers_context(round_id)
    context = _recent_chat_context(exclude_id=cap_id, own_name="Lira")
    ctx_parts = []
    if context:
        ctx_parts.append(f"[Ostatnie wiadomosci na czacie MoltBook]:\n{context}")
    if peers:
        ctx_parts.append(f"[Odpowiedzi w tej rundzie]:\n{peers}")
    if ctx_parts:
        prompt = "\n\n".join(ctx_parts) + f"\n\n[Nowa wiadomosc — {sender}]: {text}"
    else:
        prompt = f"[MoltBook — {sender}]: {text}"

    prompt += "\nOdpowiedz plynnie (5-8 zdan)."
    with lease_heartbeat(cap_id, "Lira"):
        reply = send_and_receive(ws_url, prompt)

    if reply:
        response_id = send_capsule(
            type="chat_message",
            weight="commentary",
            layer="working",
            owner="Lira",
            sender="Lira",
            recipient="shared",
            payload={
                "kind": "chat_message",
                "text": reply[:4000],
                "room": "moltbook-main",
                "display_name": "Lira",
                "recipient": "shared",
            },
            thread_id="moltbook-main",
            parent_id=cap_id,
            project="moltbook",
            status="delivered",
        )
        mark_answered(cap_id, "Lira", response_id)
        log.info("  Odpowiedz Liry: %s", reply[:80])
    else:
        log.warning("  Brak odpowiedzi od Liry — retry")
        mark_retry(cap_id, "Lira", "no_reply")

    return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL)
    args = parser.parse_args()

    log.info("=" * 50)
    log.info("  Lira Inbox Daemon")
    log.info("  CDP port: %d | Poll: %ss | Timeout: %ss", CDP_PORT, args.interval, RESPONSE_TIMEOUT)
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
                log.info("Przetworzono: %d kapsulek", n)
        except KeyboardInterrupt:
            log.info("Stop.")
            break
        except Exception as e:
            log.error("Blad cyklu: %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
