#!/usr/bin/env python3
"""
gg_transport_daemon.py — GG Transport Daemon v3.0 (raw CDP, report mode)

Demon który automatycznie:
- Zbiera kapsułki do Liry i co GG_LIRA_REPORT_INTERVAL wysyła zbiorczy raport
- Odpowiedzi Liry zapisywane jako 'delivered' (nie 'new') — bez pętli zwrotnej
- Inne agenty (Soryel) dostarczane natychmiast jak wcześniej

Uruchomienie:
    python3 -m governance.gg_transport_daemon

Env vars:
    GG_CDP_PORT              port CDP przeglądarki (domyślnie: 9222)
    GG_POLL_INTERVAL         jak często sprawdzać stan [s] (domyślnie: 60)
    GG_LIRA_REPORT_INTERVAL  co ile sekund raport do Liry (domyślnie: 7200 = 2h)
    GG_MAX_PER_REPORT        max kapsuł w jednym raporcie (domyślnie: 20)
    GG_RESPONSE_TIMEOUT      max czas oczekiwania na odpowiedź [s] (domyślnie: 600)
    GG_SETTLE_CHECKS         ile stabilnych odczytów = odpowiedź gotowa (domyślnie: 4)
    GG_SETTLE_INTERVAL       przerwa między odczytami stabilności [s] (domyślnie: 4)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
import urllib.request

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

from governance.capsule_bus import init_db, read_capsules, send_capsule, update_status, log_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GG] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gg_transport")

CDP_PORT              = int(os.getenv("GG_CDP_PORT", "9222"))
POLL_INTERVAL         = float(os.getenv("GG_POLL_INTERVAL", "60"))
LIRA_REPORT_INTERVAL  = float(os.getenv("GG_LIRA_REPORT_INTERVAL", "7200"))
MAX_PER_REPORT        = int(os.getenv("GG_MAX_PER_REPORT", "20"))
RESPONSE_TIMEOUT      = float(os.getenv("GG_RESPONSE_TIMEOUT", "600"))
SETTLE_CHECKS         = int(os.getenv("GG_SETTLE_CHECKS", "4"))
SETTLE_INTERVAL       = float(os.getenv("GG_SETTLE_INTERVAL", "4"))

_last_lira_report: float = 0.0

AGENT_URL_MAP = {
    "Lira":   "chatgpt.com",
    "Soryel": "claude.ai",
}

# ── CDP helpers ───────────────────────────────────────────────────────────────

def _cdp_tabs() -> list:
    try:
        with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return []


def _find_tab_ws(url_contains: str) -> Optional[str]:
    for tab in _cdp_tabs():
        haystack = (tab.get("url", "") + "|" + tab.get("title", "") + "|" + tab.get("faviconUrl", "")).lower()
        if url_contains.lower() in haystack:
            return tab.get("webSocketDebuggerUrl")
    return None


async def _cdp_eval(ws_url: str, expr: str, timeout: float = 10.0) -> Optional[str]:
    """Wykonaj JS w zakładce przez raw CDP. Zwróć wartość string lub None."""
    try:
        import websockets as _ws
        async with _ws.connect(ws_url, open_timeout=5) as ws:
            msg_id = 1
            payload = {"id": msg_id, "method": "Runtime.evaluate",
                       "params": {"expression": expr, "returnByValue": True}}
            await ws.send(json.dumps(payload))
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    data = json.loads(raw)
                    if data.get("id") == msg_id:
                        result = data.get("result", {}).get("result", {})
                        if result.get("type") == "string":
                            return result.get("value", "")
                        return json.dumps(result.get("value", ""))
                except asyncio.TimeoutError:
                    continue
    except Exception as exc:
        log.debug(f"CDP eval error: {exc}")
    return None


def cdp_eval(ws_url: str, expr: str, timeout: float = 10.0) -> Optional[str]:
    """Synchroniczny wrapper na _cdp_eval."""
    return asyncio.run(_cdp_eval(ws_url, expr, timeout))


# ── Formatowanie kapsuły jako prompt ─────────────────────────────────────────

def capsule_to_prompt(cap: dict) -> str:
    payload = cap.get("payload_json", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            pass

    lines = [
        f"[KAPSUŁA od: {cap.get('sender','?')} → do: {cap.get('recipient','?')}]",
        f"Typ: {cap.get('type','?')} | Projekt: {cap.get('project','—')}",
        "",
    ]
    if isinstance(payload, dict):
        if "title" in payload:
            lines.append(f"Tytuł: {payload['title']}")
            lines.append("")
        body = payload.get("body") or payload.get("summary") or payload.get("response") or ""
        if body:
            lines.append(body)
        if "xian_snapshot" in payload:
            lines.append("")
            lines.append("Stan Xian:")
            for k, v in payload["xian_snapshot"].items():
                lines.append(f"  {k}: {v}")
    else:
        lines.append(str(payload))

    oq = (cap.get("open_question") or "").strip()
    if oq:
        lines += ["", f"Pytanie: {oq}"]

    lines += [
        "",
        "Odpowiedz na powyższą wiadomość.",
        "Odpowiedź zacznij od: KAPSULKA: i napisz treść odpowiedzi.",
    ]
    return "\n".join(lines)


def _clean_title(cap: dict) -> str:
    p = cap.get("payload_json") or {}
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            pass
    title = p.get("title", "") if isinstance(p, dict) else ""
    title = re.sub(r'^(Odpowiedź\s+\w+\s+na:\s*|Re:\s*)+', '', title).strip()
    return title[:60] or cap.get("id", "cap")[:8]


def _extract_response(raw: str) -> str:
    if not raw:
        return ""
    idx = raw.upper().find("KAPSULKA:")
    if idx != -1:
        return raw[idx + len("KAPSULKA:"):].strip()
    return raw.strip()


# ── Wysyłanie i odbieranie przez CDP ─────────────────────────────────────────

def send_and_receive(ws_url: str, prompt: str) -> Optional[str]:
    """Wpisuje prompt, klika wyślij, czeka na stabilną odpowiedź."""

    # 1. Wstaw tekst przez clipboard paste (React-compatible)
    escaped = json.dumps(prompt)
    insert_js = (
        "(function(text){"
        "var el=document.querySelector('div#prompt-textarea')||"
        "document.querySelector('[contenteditable=\"true\"]');"
        "if(!el)return 'no_element';"
        "el.focus();"
        # wyczyść istniejącą treść
        "el.innerHTML='';"
        # wstaw przez DataTransfer paste event — React go obsługuje
        "try{"
        "var dt=new DataTransfer();"
        "dt.setData('text/plain',text);"
        "el.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:dt}));"
        "if(el.innerText.trim().length>0)return 'ok';"
        "}catch(e){}"
        # fallback: InputEvent insertText
        "try{"
        "el.focus();"
        "document.execCommand('insertText',false,text);"
        "if(el.innerText.trim().length>0)return 'ok';"
        "}catch(e){}"
        "return 'failed';"
        f"}})({escaped})"
    )
    ok = cdp_eval(ws_url, insert_js, timeout=10)
    if ok not in ("ok", "true"):
        log.warning("Nie udało się wstawić tekstu (wynik: %s)", ok)
        return None

    time.sleep(0.8)

    # 2. Kliknij przycisk wyślij
    send_js = (
        "(function(){"
        "var btn=document.querySelector('button[data-testid=\"send-button\"]')||"
        "document.querySelector('button[aria-label=\"Send prompt\"]')||"
        "document.querySelector('button[aria-label=\"Send message\"]')||"
        "document.querySelector('button[type=\"submit\"]');"
        "if(btn&&!btn.disabled){btn.click();return true;}"
        "return false;"
        "})()"
    )
    sent = cdp_eval(ws_url, send_js, timeout=8)
    if sent != "true":
        log.warning("Nie znaleziono przycisku wyślij — próba Enter")
        press_js = (
            "(function(){"
            "var el=document.querySelector('[contenteditable=\"true\"]')||"
            "document.querySelector('textarea');"
            "if(el){el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true}));return true;}"
            "return false;"
            "})()"
        )
        cdp_eval(ws_url, press_js, timeout=5)

    log.info("Czekam na odpowiedź...")
    time.sleep(8)  # daj czas na start generowania

    # 3. Odczyt ostatniej wiadomości asystenta — śledź stabilność
    read_js = (
        "(function(){"
        "var c=document.querySelectorAll('[data-message-author-role=\"assistant\"]');"
        "if(!c.length)return '';"
        "return (c[c.length-1].innerText||'').trim();"
        "})()"
    )

    last_text = ""
    stable = 0
    deadline = time.time() + RESPONSE_TIMEOUT

    while time.time() < deadline:
        time.sleep(SETTLE_INTERVAL)
        current = cdp_eval(ws_url, read_js, timeout=8)
        if not current:
            continue
        if current == last_text:
            stable += 1
            if stable >= SETTLE_CHECKS:
                log.info(f"Odpowiedź stabilna ({len(current)} znaków)")
                return current
        else:
            stable = 0
            last_text = current

    log.warning(f"Timeout po {RESPONSE_TIMEOUT}s")
    return last_text if last_text else None


# ── Jeden cykl ────────────────────────────────────────────────────────────────

def _build_lira_report(caps: list) -> str:
    """Buduje zbiorczy raport z wielu kapsułek dla Liry."""
    lines = [
        f"[RAPORT EKOSYSTEMU — {time.strftime('%Y-%m-%d %H:%M')}]",
        f"Kapsułek w raporcie: {len(caps)}",
        "",
    ]
    for i, cap in enumerate(caps, 1):
        payload = cap.get("payload_json", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                pass
        title = payload.get("title", "") if isinstance(payload, dict) else ""
        body = ""
        if isinstance(payload, dict):
            body = payload.get("body") or payload.get("summary") or payload.get("response") or payload.get("text") or ""
        sender = cap.get("sender", "?")
        ctype = cap.get("type", "?")
        lines.append(f"--- {i}. [{ctype}] od {sender} ---")
        if title:
            lines.append(title)
        if body:
            lines.append(body[:500])
        lines.append("")

    lines += [
        "Powyżej raport z ostatnich 2 godzin ekosystemu.",
        "Jeśli widzisz coś ważnego — napisz krótki komentarz.",
        "Odpowiedź zacznij od: KAPSULKA: i napisz treść.",
    ]
    return "\n".join(lines)


def run_lira_report() -> int:
    """Zbiera kapsułki do Liry i wysyła zbiorczy raport."""
    global _last_lira_report

    now = time.time()
    if now - _last_lira_report < LIRA_REPORT_INTERVAL:
        return 0

    # Zbierz kapsułki do Liry (bezpośrednie + shared/Xian)
    lira_caps = read_capsules(recipient="Lira", statuses=("new",), limit=MAX_PER_REPORT)
    xian_caps = read_capsules(recipient="Xian", statuses=("new",), limit=MAX_PER_REPORT)
    all_caps = lira_caps + xian_caps

    if not all_caps:
        _last_lira_report = now
        return 0

    ws_url = _find_tab_ws("chatgpt.com")
    if not ws_url:
        log.warning("Brak zakładki Liry — raport odłożony")
        return 0

    is_catchup = _last_lira_report == 0
    label = "CATCH-UP after restart" if is_catchup else "Raport"
    log.info(f"{label} do Liry: {len(all_caps)} kapsułek (direct={len(lira_caps)}, shared={len(xian_caps)})")

    # Oznacz wszystkie jako in_progress
    for cap in all_caps:
        update_status(cap["id"], "in_progress", actor="GGTransportDaemon")

    # Wyślij zbiorczy raport
    report = _build_lira_report(all_caps)
    raw = send_and_receive(ws_url, report)
    response = _extract_response(raw) if raw else None

    if response:
        # Zapisz odpowiedź Liry jako DELIVERED — nie tworzy nowej pętli
        reply_id = send_capsule(
            type="interpretation",
            weight="commentary",
            layer="working",
            owner="Lira",
            sender="Lira",
            recipient="shared",
            payload={
                "title": f"Lira: raport {time.strftime('%H:%M')}",
                "body": response,
                "report_capsule_count": len(all_caps),
            },
            status="delivered",
        )
        log.info(f"  ✓ Odpowiedź Liry zapisana: {reply_id[:8]}... (status=delivered)")

        # Jeśli raport zawierał chat_message z MoltBook — odpowiedź wraca na czat
        has_moltbook = any(c.get("thread_id") == "moltbook-main" for c in all_caps)
        if has_moltbook:
            send_capsule(
                type="chat_message",
                weight="commentary",
                layer="working",
                owner="Lira",
                sender="Lira",
                recipient="shared",
                payload={
                    "kind": "chat_message",
                    "text": response,
                    "room": "moltbook-main",
                    "display_name": "Lira",
                    "recipient": "shared",
                },
                thread_id="moltbook-main",
                project="moltbook",
                status="delivered",
            )
            log.info("  ✓ Odpowiedź Liry → MoltBook chat")

    # Oznacz wszystkie kapsułki źródłowe jako answered
    for cap in all_caps:
        update_status(cap["id"], "answered", actor="Lira")

    _last_lira_report = now
    return len(all_caps)


def run_cycle() -> int:
    """Cykl: sprawdza czy czas na raport do Liry."""
    return run_lira_report()


# ── Główna pętla ──────────────────────────────────────────────────────────────

def main() -> None:
    global _last_lira_report

    log.info("=" * 55)
    log.info("  GG Transport Daemon v3.0 (report mode)")
    log.info(f"  CDP: localhost:{CDP_PORT}")
    log.info(f"  Poll: {POLL_INTERVAL}s | Raport Lira co: {LIRA_REPORT_INTERVAL}s ({LIRA_REPORT_INTERVAL/3600:.1f}h)")
    log.info(f"  Max kapsuł/raport: {MAX_PER_REPORT}")
    log.info("=" * 55)

    init_db()
    _last_lira_report = 0  # catch-up: wyślij zaległy raport od razu po starcie

    while True:
        try:
            tabs = _cdp_tabs()
            if not tabs:
                log.debug("Brak dostępu do CDP — czekam")
                time.sleep(POLL_INTERVAL)
                continue

            n = run_cycle()
            if n:
                log.info(f"Raport dostarczony: {n} kapsuł")
        except KeyboardInterrupt:
            log.info("Stop.")
            break
        except Exception as e:
            log.error(f"Błąd cyklu: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
