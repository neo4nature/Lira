
#!/usr/bin/env python3



from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import argparse
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from governance.capsule_bus import (
    DB_PATH,
    init_db,
    log_event,
    read_capsules,
    send_capsule,
    update_status,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("play_capsule")

DEFAULT_TIMEOUT_MS = int(os.getenv("PLAY_CAPSULE_TIMEOUT_MS", "600000"))   # 10 min default
POLL_INTERVAL_MS   = int(os.getenv("PLAY_CAPSULE_POLL_MS",    "3000"))
STABLE_POLLS_REQUIRED = int(os.getenv("PLAY_CAPSULE_STABLE_POLLS", "4"))
SELECTOR_RETRIES = 3
TYPING_DELAY_MS = 10
TRANSPORT_ACTOR = "PlayCapsule"
FORMAT_VERSION = "capsule-reply/v1"

AGENT_CONFIG: dict[str, dict[str, Any]] = {
    "Lira": {
        "mode": "cdp_attach",
        "cdp_url": "http://127.0.0.1:9222",
        "url": None,
        "site": "chatgpt",
        "input_selectors": [
            'div#prompt-textarea',
            'textarea[data-testid="prompt-textarea"]',
            'textarea',
            '[contenteditable="true"]',
        ],
        "send_selectors": [
            'button[data-testid="send-button"]',
            'button[aria-label="Send prompt"]',
            'button[type="submit"]',
        ],
        "response_selectors": [
            '[data-message-author-role="assistant"] .markdown',
            '[data-message-author-role="assistant"]',
            'article .markdown',
            'main [data-message-author-role="assistant"]',
        ],
    },
    "Soryel": {
        "mode": "cdp_attach",
        "cdp_url": "http://127.0.0.1:9222",
        "url": None,
        "site": "claude",
        "input_selectors": [
            'div[contenteditable="true"]',
            'div.ProseMirror[contenteditable="true"]',
            'textarea',
            '[contenteditable="plaintext-only"]',
        ],
        "send_selectors": [
            'button[aria-label="Send message"]',
            'button[type="submit"]',
        ],
        "response_selectors": [
            '[data-testid="assistant-message"]',
            '[data-testid="assistant-message"] .prose',
            'div[data-is-streaming="false"]',
            'main .font-claude-message',
            'main [class*="font-claude"]',
            'main .prose',
        ],
    },
}


def safe_strip(text: Optional[str]) -> str:
    return text.strip() if isinstance(text, str) else ""


def json_preview(value: Any, max_len: int = 300) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def classify_reply_type(response_text: str, incoming_capsule: dict[str, Any]) -> str:
    source_type = incoming_capsule.get("type", "fact")
    text = response_text.lower()

    if source_type == "decision":
        if any(k in text for k in ["decyzja", "wybieram", "rekomenduję", "proponuję", "należy"]):
            return "decision"
        return "interpretation"

    if source_type == "hypothesis":
        if any(k in text for k in ["hipoteza", "możliwe", "prawdopodobne", "wydaje się"]):
            return "hypothesis"
        return "interpretation"

    if source_type == "doubt":
        if any(k in text for k in ["nie wiem", "wątpliwość", "niepewne", "trzeba sprawdzić"]):
            return "doubt"
        return "interpretation"

    return "interpretation"


def classify_reply_weight(response_text: str, incoming_capsule: dict[str, Any]) -> str:
    source_weight = incoming_capsule.get("weight", "commentary")
    source_type = incoming_capsule.get("type", "fact")
    text = response_text.lower()

    strong_markers = [
        "zmień",
        "nowy kierunek",
        "decyzja",
        "priorytet",
        "następny krok",
        "wdrożyć",
        "implementować",
        "zatrzymać",
        "usunąć",
        "przenieść",
        "architektura",
        "standardyz",
        "sformaliz",
        "router",
        "decision core",
        "decision_core",
    ]

    if any(marker in text for marker in strong_markers):
        return "changes_trajectory"

    if source_weight == "changes_trajectory" and source_type in {"decision", "hypothesis"}:
        return "changes_trajectory"

    if len(response_text.strip()) < 20:
        return "archive"

    return "commentary"


def build_reply_payload(
    response_text: str,
    incoming_capsule: dict[str, Any],
    responder: str,
) -> dict[str, Any]:
    incoming_payload = incoming_capsule.get("payload_json", {})
    if isinstance(incoming_payload, str):
        try:
            incoming_payload = json.loads(incoming_payload)
        except json.JSONDecodeError:
            incoming_payload = {"raw": incoming_payload}

    source_title = incoming_payload.get("title") or incoming_payload.get("summary") or "Kapsuła"
    summary = response_text.strip()
    if len(summary) > 1200:
        summary = summary[:1200].rstrip() + "..."

    return {
        "format_version": FORMAT_VERSION,
        "title": f"Re: {source_title}",
        "summary": summary,
        "raw_response": response_text,
        "in_reply_to": incoming_capsule.get("id"),
        "source_sender": incoming_capsule.get("sender"),
        "source_type": incoming_capsule.get("type"),
        "source_weight": incoming_capsule.get("weight"),
        "receiver": responder,
    }


def capsule_to_prompt(capsule: dict[str, Any]) -> str:
    payload = capsule.get("payload_json", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            pass

    lines = [
        f"[KAPSUŁA od: {capsule.get('sender', '?')} → do: {capsule.get('recipient', '?')}]",
        f"TYP: {capsule.get('type', '?')}",
        f"WAGA: {capsule.get('weight', '?')}",
        f"WARSTWA: {capsule.get('layer', '?')}",
        f"WŁAŚCICIEL: {capsule.get('owner', '?')}",
        f"PROJEKT: {capsule.get('project', '—')}",
        "",
        "TREŚĆ:",
    ]

    if isinstance(payload, dict):
        if "title" in payload:
            lines.append(f"  TYTUŁ: {payload['title']}")
        if "summary" in payload:
            lines.append(f"  OPIS: {payload['summary']}")
        if "notes" in payload and isinstance(payload["notes"], list):
            lines.append("  NOTATKI:")
            for note in payload["notes"]:
                lines.append(f"    • {note}")

        for k, v in payload.items():
            if k not in {"title", "summary", "notes"}:
                lines.append(f"  {k}: {v}")
    else:
        lines.append(f"  {payload}")

    oq = safe_strip(capsule.get("open_question", ""))
    if oq:
        lines += ["", f"OTWARTE PYTANIE: {oq}"]

    lines += [
        "",
        "Odpowiedz zwięźle, ale merytorycznie.",
        "Jeśli coś zmienia trajektorię projektu, zaznacz to wyraźnie.",
        "Odpowiedź ma być w zwykłym języku naturalnym — system zapisze ją do wspólnego standardu kapsuły.",
    ]
    return "\n".join(lines)


def log_transport_event(capsule_id: str, event_type: str, details: Optional[dict[str, Any]] = None) -> None:
    try:
        log_event(
            capsule_id=capsule_id,
            event_type=event_type,
            actor=TRANSPORT_ACTOR,
            details=details or {},
            db_path=DB_PATH,
        )
    except Exception as e:
        log.warning("Nie udało się zapisać eventu %s dla %s: %s", event_type, capsule_id, e)


def first_working_locator(page: Page, selectors: list[str], timeout_ms: int = 10_000):
    last_error: Optional[Exception] = None
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=timeout_ms)
            locator = page.locator(selector).last
            locator.count()
            return locator, selector
        except Exception as e:
            last_error = e
    if last_error:
        raise last_error
    raise RuntimeError("Nie znaleziono działającego selektora input.")


def first_clickable_selector(page: Page, selectors: list[str], timeout_ms: int = 10_000) -> str:
    last_error: Optional[Exception] = None
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=timeout_ms)
            return selector
        except Exception as e:
            last_error = e
    if last_error:
        raise last_error
    raise RuntimeError("Nie znaleziono działającego selektora send.")


def robust_fill_or_type(locator, text: str) -> None:
    locator.click()

    # Atomowe wstawienie przez execCommand (bez char-by-char)
    try:
        locator.evaluate(
            "(el, t) => { el.focus(); document.execCommand('selectAll',false,null);"
            " document.execCommand('insertText',false,t); return true; }",
            text,
        )
        return
    except Exception:
        pass

    try:
        locator.fill("")
    except Exception:
        pass

    try:
        locator.press("Control+A")
        locator.press("Backspace")
    except Exception:
        pass

    try:
        locator.fill(text)
    except Exception:
        locator.type(text, delay=TYPING_DELAY_MS)


def assert_not_logged_out_chatgpt(page: Page) -> None:
    content = page.content()
    if (
        "Zaloguj się" in content
        or "Log in" in content
        or "Zarejestruj się za darmo" in content
        or "Sign up for free" in content
    ):
        raise RuntimeError(
            "Strona ChatGPT wygląda na niezalogowaną. "
            "PlayCapsule trafił do złej karty albo sesja wygasła."
        )


def assert_not_logged_out_claude(page: Page) -> None:
    url = page.url or ""
    if "claude.ai" not in url:
        raise RuntimeError("To nie wygląda na kartę Claude.")
    return


def wait_for_response(page: Page, response_selectors: list[str], timeout_ms: int) -> str:
    log.info("Czekam na odpowiedź agenta...")
    deadline = time.time() + timeout_ms / 1000

    # Zapamiętaj baseline — ile elementów było PRZED wysłaniem
    baseline_count = 0
    baseline_selector: Optional[str] = None
    for selector in response_selectors:
        try:
            elements = page.query_selector_all(selector)
            if elements:
                baseline_count = len(elements)
                baseline_selector = selector
                log.info("Baseline: selector=%s count=%d", selector, baseline_count)
                break
        except Exception:
            continue

    last_text = ""
    stable_count = 0

    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_MS / 1000)
        current_text = ""

        for selector in response_selectors:
            try:
                elements = page.query_selector_all(selector)
                if not elements:
                    continue

                # Weź tylko nowe elementy (te powyżej baseline)
                if selector == baseline_selector:
                    if len(elements) <= baseline_count:
                        # Jeszcze nic nowego — czekaj
                        continue
                    new_elements = elements[baseline_count:]
                else:
                    new_elements = elements

                # Połącz WSZYSTKIE nowe elementy — tu był bug split-message
                texts = [e.inner_text().strip() for e in new_elements]
                texts = [t for t in texts if t]
                if texts:
                    current_text = "\n\n".join(texts)
                    break
            except Exception:
                continue

        if not current_text:
            continue

        if current_text == last_text:
            stable_count += 1
            if stable_count >= STABLE_POLLS_REQUIRED:
                log.info("Odpowiedź stabilna — odebrana (%d znaków).", len(current_text))
                return current_text
        else:
            last_text = current_text
            stable_count = 0

    raise PlaywrightTimeout(f"Timeout {timeout_ms} ms — odpowiedź nie nadeszła.")


def ensure_logged_chatgpt_page(context, preferred_url: str | None = None) -> Page:
    candidate_pages = []

    for page in context.pages:
        try:
            url = page.url or ""
            if "chatgpt.com" in url or "chat.openai.com" in url:
                candidate_pages.append(page)
        except Exception:
            continue

    if candidate_pages:
        page = candidate_pages[-1]
        page.bring_to_front()
        page.wait_for_timeout(1000)
        assert_not_logged_out_chatgpt(page)
        return page

    if preferred_url:
        page = context.new_page()
        page.goto(preferred_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        assert_not_logged_out_chatgpt(page)
        return page

    raise RuntimeError(
        "Nie znaleziono żadnej otwartej zalogowanej karty ChatGPT. "
        "Otwórz ręcznie ChatGPT w Brave i spróbuj ponownie."
    )


def debug_claude_page(page: Page) -> None:
    print("[DEBUG] Claude URL:", page.url)
    html = page.content()
    print("[DEBUG] Claude HTML preview:")
    print(html[:1500])


def ensure_logged_claude_page(context, preferred_url: str | None = None) -> Page:
    candidate_pages = []

    for page in context.pages:
        try:
            url = page.url or ""
            if "claude.ai" in url:
                candidate_pages.append(page)
        except Exception:
            continue

    if candidate_pages:
        page = candidate_pages[-1]
        page.bring_to_front()
        page.wait_for_timeout(1000)
        debug_claude_page(page)
        assert_not_logged_out_claude(page)
        return page

    if preferred_url:
        page = context.new_page()
        page.goto(preferred_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        debug_claude_page(page)
        assert_not_logged_out_claude(page)
        return page

    raise RuntimeError(
        "Nie znaleziono żadnej otwartej zalogowanej karty Claude. "
        "Otwórz ręcznie Claude w Brave i spróbuj ponownie."
    )


def open_agent_page(play, config: dict[str, Any]):
    cdp_url = config["cdp_url"]
    site = config.get("site", "")

    log.info("Podłączam do istniejącego Brave przez CDP: %s", cdp_url)
    browser = play.chromium.connect_over_cdp(cdp_url)

    if not browser.contexts:
        raise RuntimeError(
            f"Brak contextów w podłączonym Brave dla {site}. "
            "Upewnij się, że Brave działa z właściwym --remote-debugging-port."
        )

    context = browser.contexts[0]

    if site == "chatgpt":
        page = ensure_logged_chatgpt_page(context, preferred_url=config.get("url"))
    elif site == "claude":
        page = ensure_logged_claude_page(context, preferred_url=config.get("url"))
    else:
        raise RuntimeError(f"Nieznany site={site!r} w configu.")

    return browser, context, page


def reply_already_exists(parent_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM capsules WHERE parent_id = ? LIMIT 1",
            (parent_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def send_to_agent(page: Page, config: dict[str, Any], prompt: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
    last_error: Optional[Exception] = None

    site = config.get("site")
    if site == "chatgpt":
        assert_not_logged_out_chatgpt(page)
    elif site == "claude":
        assert_not_logged_out_claude(page)

    for attempt in range(1, SELECTOR_RETRIES + 1):
        try:
            log.info("Próba %d/%d", attempt, SELECTOR_RETRIES)

            input_locator, input_selector = first_working_locator(
                page, config["input_selectors"], timeout_ms=15_000
            )
            log.info("Input selector: %s", input_selector)

            robust_fill_or_type(input_locator, prompt)
            page.wait_for_timeout(500)

            send_selector = first_clickable_selector(
                page, config["send_selectors"], timeout_ms=10_000
            )
            log.info("Send selector: %s", send_selector)
            page.click(send_selector)

            return wait_for_response(page, config["response_selectors"], timeout_ms)

        except Exception as e:
            last_error = e
            log.warning("Próba %d nieudana: %s", attempt, e)
            page.wait_for_timeout(1500)

    if last_error:
        raise last_error
    raise RuntimeError("Nie udało się wysłać promptu do agenta.")


def run_transport(
    target_agent: str,
    project: Optional[str] = None,
    limit: int = 5,
    dry_run: bool = False,
) -> None:
    if target_agent not in AGENT_CONFIG:
        raise ValueError(f"Nieznany agent: {target_agent!r}. Dostępni: {list(AGENT_CONFIG.keys())}")

    config = AGENT_CONFIG[target_agent]
    init_db()

    capsules = read_capsules(
        recipient=[target_agent, "shared"],
        statuses=("new",),
        project=project,
        limit=limit,
    )

    if not capsules:
        log.info("Brak nowych kapsuł dla %s.", target_agent)
        return

    log.info("Znaleziono %d kapsuł dla %s.", len(capsules), target_agent)

    if dry_run:
        for c in capsules:
            log.info("[DRY RUN] Kapsuła %s", c["id"])
            print(capsule_to_prompt(c))
            print("─" * 80)
        return

    with sync_playwright() as p:
        browser = None
        context = None
        page = None

        try:
            browser, context, page = open_agent_page(p, config)

            for capsule in capsules:
                capsule_id = capsule["id"]
                log.info("Przetwarzam kapsułę: %s", capsule_id)

                if reply_already_exists(capsule_id):
                    log.info("Reply dla kapsuły %s już istnieje — pomijam ponowne wysłanie.", capsule_id)
                    update_status(capsule_id, "answered", actor=TRANSPORT_ACTOR, db_path=DB_PATH)
                    continue

                try:
                    update_status(capsule_id, "in_progress", actor=TRANSPORT_ACTOR, db_path=DB_PATH)
                    log_transport_event(
                        capsule_id,
                        "transport_started",
                        {"target_agent": target_agent, "project": capsule.get("project")},
                    )

                    prompt = capsule_to_prompt(capsule)
                    log_transport_event(
                        capsule_id,
                        "prompt_sent",
                        {"prompt_preview": prompt[:700], "prompt_len": len(prompt)},
                    )

                    response_text = send_to_agent(page, config, prompt)
                    print("\n[DEBUG] RAW RESPONSE START\n")
                    print(response_text)
                    print("\n[DEBUG] RAW RESPONSE END\n")

                    log_transport_event(
                        capsule_id,
                        "response_received",
                        {"response_preview": response_text[:700], "response_len": len(response_text)},
                    )

                    reply_type = classify_reply_type(response_text, capsule)
                    reply_weight = classify_reply_weight(response_text, capsule)
                    reply_payload = build_reply_payload(response_text, capsule, responder=target_agent)

                    print("\n[DEBUG] REPLY PAYLOAD\n")
                    print(json.dumps(reply_payload, ensure_ascii=False, indent=2))

                    _orig = capsule.get("sender", "Neo")
                    _reply_to = "shared" if _orig in ("Ekosystem", "shared") else _orig
                    reply_id = send_capsule(
                        type=reply_type,
                        weight=reply_weight,
                        layer=capsule.get("layer", "working"),
                        owner=target_agent,
                        sender=target_agent,
                        recipient=_reply_to,
                        project=capsule.get("project"),
                        thread_id=capsule.get("thread_id"),
                        parent_id=capsule_id,
                        open_question="",
                        payload=reply_payload,
                        status="new",
                        db_path=DB_PATH,
                    )

                    log_transport_event(
                        capsule_id,
                        "reply_capsule_created",
                        {
                            "reply_id": reply_id,
                            "reply_type": reply_type,
                            "reply_weight": reply_weight,
                            "reply_payload_preview": json_preview(reply_payload),
                        },
                    )

                    update_status(capsule_id, "answered", actor=target_agent, db_path=DB_PATH)
                    log.info("✓ Kapsuła %s → odpowiedź zapisana jako %s", capsule_id, reply_id)

                except PlaywrightTimeout as e:
                    log.error("Timeout dla kapsuły %s: %s", capsule_id, e)
                    log_transport_event(
                        capsule_id,
                        "transport_failed",
                        {"reason": "timeout", "error": str(e)},
                    )
                    update_status(capsule_id, "blocked", actor=TRANSPORT_ACTOR, db_path=DB_PATH)

                except Exception as e:
                    log.error("Błąd dla kapsuły %s: %s", capsule_id, e)
                    log_transport_event(
                        capsule_id,
                        "transport_failed",
                        {"reason": "exception", "error": str(e)},
                    )
                    update_status(capsule_id, "blocked", actor=TRANSPORT_ACTOR, db_path=DB_PATH)

                finally:
                    page.wait_for_timeout(1500)

        finally:
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass

    log.info("Transport zakończony.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PlayCapsule — dual-CDP transport kapsuł do Liry i Soryel"
    )
    parser.add_argument("agent", choices=list(AGENT_CONFIG.keys()))
    parser.add_argument("--project", default=None)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_transport(
        target_agent=args.agent,
        project=args.project,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()