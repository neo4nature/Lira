
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import asyncio
import json
import os
import sqlite3
import threading
import urllib.request
import uuid
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/London")
from pathlib import Path
from typing import Any, Optional

from flask import Flask, request, redirect, url_for, render_template_string

APP = Flask(__name__)

from governance.capsule_bus import DB_PATH

ALLOWED_TYPES = {"fact", "interpretation", "hypothesis", "decision", "doubt", "task", "implementation", "result", "summary", "proposal", "review", "stalemate", "chat_message"}
ALLOWED_WEIGHTS = {"changes_trajectory", "commentary", "archive"}
ALLOWED_LAYERS = {"shared", "working", "reflective"}
ALLOWED_OWNERS = {"Neo", "Soryel", "Lira", "Xian", "Ekosystem", "shared"}
ALLOWED_STATUSES = {"new", "read", "in_progress", "answered", "archived", "blocked", "proposal", "review", "revise", "approved_auto", "awaiting_neo", "rejected", "stalemate", "claimed", "delivered", "queued"}

DEFAULT_AGENTS = ["Neo", "Lira", "Soryel", "Xian", "Ekosystem", "shared"]

CDP_PORT = int(os.getenv("GG_CDP_PORT", "9222"))


def _cdp_tabs():
    try:
        with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return []


def _find_lira_ws():
    for tab in _cdp_tabs():
        if "chatgpt.com" in (tab.get("url", "") + tab.get("title", "")).lower():
            return tab.get("webSocketDebuggerUrl")
    return None


async def _cdp_send_to_lira(ws_url: str, text: str) -> bool:
    """Wpisuje tekst do ChatGPT i klika wyślij przez CDP."""
    try:
        import websockets
        escaped = json.dumps(text)
        insert_js = (
            "(function(text){"
            "var el=document.querySelector('div#prompt-textarea')||"
            "document.querySelector('[contenteditable=\"true\"]');"
            "if(!el)return 'no_element';"
            "el.focus();el.innerHTML='';"
            "try{var dt=new DataTransfer();"
            "dt.setData('text/plain',text);"
            "el.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:dt}));"
            "if(el.innerText.trim().length>0)return 'ok';"
            "}catch(e){}"
            "try{el.focus();document.execCommand('insertText',false,text);"
            "if(el.innerText.trim().length>0)return 'ok';"
            "}catch(e){}"
            "return 'failed';"
            f"}})({escaped})"
        )
        send_js = (
            "(function(){"
            "var btn=document.querySelector('button[data-testid=\"send-button\"]')||"
            "document.querySelector('button[aria-label=\"Send prompt\"]')||"
            "document.querySelector('button[aria-label=\"Send message\"]');"
            "if(btn&&!btn.disabled){btn.click();return true;}"
            "return false;"
            "})()"
        )
        async with websockets.connect(ws_url, open_timeout=5) as ws:
            # insert text
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                      "params": {"expression": insert_js, "returnByValue": True}}))
            raw = await asyncio.wait_for(ws.recv(), timeout=8)
            result = json.loads(raw).get("result", {}).get("result", {}).get("value", "")
            if result not in ("ok", "true"):
                return False
            await asyncio.sleep(0.8)
            # click send
            await ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                                      "params": {"expression": send_js, "returnByValue": True}}))
            await asyncio.wait_for(ws.recv(), timeout=8)
        return True
    except Exception:
        return False


def deliver_to_lira(sender: str, text: str):
    """Dostarcz wiadomość do Liry przez CDP i złap odpowiedź."""
    def _worker():
        import websockets as _ws

        ws_url = _find_lira_ws()
        if not ws_url:
            return
        msg = f"[MoltBook — {sender}]: {text}"

        # Wyślij wiadomość
        ok = asyncio.run(_cdp_send_to_lira(ws_url, msg))
        if not ok:
            return

        # Czekaj na odpowiedź Liry (stabilność tekstu)
        import time as _time
        _time.sleep(10)

        read_js = (
            "(function(){"
            "var c=document.querySelectorAll('[data-message-author-role=\"assistant\"]');"
            "if(!c.length)return '';"
            "return (c[c.length-1].innerText||'').trim();"
            "})()"
        )

        def _sync_eval(expr, timeout=10):
            async def _do():
                try:
                    async with _ws.connect(ws_url, open_timeout=5) as conn:
                        await conn.send(json.dumps({
                            "id": 99, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True}
                        }))
                        raw = await asyncio.wait_for(conn.recv(), timeout=timeout)
                        result = json.loads(raw).get("result", {}).get("result", {})
                        return result.get("value", "") if result.get("type") == "string" else ""
                except Exception:
                    return ""
            return asyncio.run(_do())

        last_text = ""
        stable = 0
        deadline = _time.time() + 120  # max 2 min na odpowiedź

        while _time.time() < deadline:
            _time.sleep(4)
            current = _sync_eval(read_js)
            if not current:
                continue
            if current == last_text:
                stable += 1
                if stable >= 3:
                    # Odpowiedź gotowa — zapisz do MoltBook
                    response = current.strip()
                    if response and not response.startswith("[MoltBook"):
                        send_capsule(
                            type="chat_message",
                            weight="commentary",
                            layer="working",
                            owner="Lira",
                            sender="Lira",
                            recipient="shared",
                            payload={
                                "kind": "chat_message",
                                "text": response[:2000],
                                "room": "moltbook-main",
                                "display_name": "Lira",
                                "recipient": "shared",
                            },
                            thread_id="moltbook-main",
                            project="moltbook",
                            status="delivered",
                        )
                    return
            else:
                stable = 0
                last_text = current

    threading.Thread(target=_worker, daemon=True).start()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Capsule GG v0.3</title>
  {% if auto_refresh %}
  <meta http-equiv="refresh" content="{{ auto_refresh }}">
  {% endif %}
  <style>
    :root {
      --bg: #e7edf6;
      --panel: #ffffff;
      --panel-2: #f7faff;
      --border: #cfd8e6;
      --text: #16202f;
      --muted: #667085;
      --accent: #2f74ff;
      --accent-dark: #1e5bd6;
      --accent-soft: #e8f1ff;
      --gg-blue: #3a78f2;
      --gg-blue-soft: #dfeaff;
      --bubble-in: #ffffff;
      --bubble-out: #dcebff;
      --sidebar: #f9fbff;
      --good: #067647;
      --warn: #b54708;
      --danger: #b42318;
      --shadow: 0 10px 30px rgba(16, 24, 40, 0.10);
      --radius: 18px;
      --font: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: var(--font);
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(47,116,255,.08), transparent 260px),
        radial-gradient(circle at bottom right, rgba(47,116,255,.05), transparent 280px),
        var(--bg);
    }

    .app {
      display: grid;
      grid-template-columns: 280px 1fr 360px;
      gap: 16px;
      min-height: 100vh;
      padding: 16px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .sidebar {
      background: linear-gradient(180deg, #fbfdff, #f3f8ff);
    }

    .sidebar-header,
    .chat-header,
    .details-header {
      padding: 16px 18px;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, #ffffff, #f8fbff);
    }

    .sidebar-body,
    .details-body {
      padding: 16px;
      overflow: auto;
    }

    .section-title {
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 800;
      color: #1849a9;
    }

    .brand-dot {
      width: 12px;
      height: 12px;
      border-radius: 999px;
      background: linear-gradient(180deg, #5a90ff, #2f74ff);
      box-shadow: 0 0 0 4px rgba(47,116,255,.12);
    }

    .channel-list a {
      display: block;
      padding: 12px 14px;
      border-radius: 14px;
      text-decoration: none;
      color: var(--text);
      background: #fff;
      border: 1px solid transparent;
      margin-bottom: 10px;
      transition: .15s ease;
    }

    .channel-list a:hover {
      border-color: #bfd1f7;
      transform: translateY(-1px);
    }

    .channel-list a.active {
      background: linear-gradient(180deg, #eef5ff, #e4efff);
      border-color: #b8cdf8;
      color: #123f96;
    }

    .channel-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }

    .channel-name {
      font-weight: 700;
    }

    .channel-meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }

    .unread-badge {
      min-width: 24px;
      padding: 4px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      text-align: center;
      color: white;
      background: linear-gradient(180deg, #ff6a6a, #d92d20);
      box-shadow: 0 4px 10px rgba(217,45,32,.22);
    }

    .chat {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }

    .chat-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }

    .chat-header h1 {
      margin: 0;
      font-size: 20px;
      line-height: 1.1;
    }

    .chat-header p {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 13px;
    }

    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }

    .toolbar a,
    .toolbar button,
    .small-btn,
    .mini-btn {
      border: 1px solid var(--border);
      background: white;
      color: var(--text);
      text-decoration: none;
      padding: 8px 12px;
      border-radius: 11px;
      font-size: 13px;
      cursor: pointer;
    }

    .toolbar a.active {
      background: var(--accent-soft);
      border-color: #bfd6ff;
      color: #1849a9;
      font-weight: 700;
    }

    .chat-body {
      flex: 1;
      overflow: auto;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      background:
        linear-gradient(180deg, rgba(255,255,255,.8), rgba(247,250,255,.95)),
        var(--panel-2);
    }

    .day-sep {
      align-self: center;
      font-size: 12px;
      color: var(--muted);
      background: white;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 12px;
      box-shadow: 0 2px 8px rgba(16,24,40,.05);
    }

    .msg-row {
      display: flex;
      flex-direction: column;
      gap: 6px;
      align-items: flex-start;
    }

    .msg-row.outgoing {
      align-items: flex-end;
    }

    .msg-meta {
      font-size: 12px;
      color: var(--muted);
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    .msg-bubble {
      width: min(78%, 920px);
      padding: 12px 14px;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: var(--bubble-in);
      box-shadow: 0 4px 12px rgba(16,24,40,.06);
      white-space: pre-wrap;
      word-wrap: break-word;
      position: relative;
    }

    .msg-row.outgoing .msg-bubble {
      background: var(--bubble-out);
      border-color: #bfd6ff;
    }

    .msg-title {
      font-weight: 800;
      margin-bottom: 8px;
      color: #173a7a;
    }

    .tags {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 8px;
    }

    .tag {
      font-size: 11px;
      border-radius: 999px;
      padding: 3px 8px;
      background: white;
      color: var(--muted);
      border: 1px solid var(--border);
    }

    .tag.weight-changes_trajectory { background: #e7fff1; color: #05603a; border-color: #a6e4c0; }
    .tag.weight-commentary { background: #eef4ff; color: #1849a9; border-color: #bfd6ff; }
    .tag.weight-archive { background: #f2f4f7; color: #475467; border-color: #d0d5dd; }

    .tag.status-new { background: #eef4ff; color: #1849a9; }
    .tag.status-in_progress { background: #fff3e8; color: #93370d; }
    .tag.status-answered { background: #e8fff3; color: #05603a; }
    .tag.status-blocked { background: #fff1f3; color: #b42318; }
    .tag.status-archived { background: #f2f4f7; color: #475467; }
    .tag.status-read { background: #f8f9fc; color: #344054; }

    .thread-preview {
      border-left: 3px solid #a8c5ff;
      padding-left: 10px;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 12px;
    }

    .msg-actions {
      margin-top: 10px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }

    .composer {
      border-top: 1px solid var(--border);
      background: linear-gradient(180deg, #fff, #fafcff);
      padding: 16px 18px;
    }

    .reply-banner {
      margin-bottom: 12px;
      padding: 10px 12px;
      background: #eef5ff;
      border: 1px solid #bfd6ff;
      border-radius: 12px;
      color: #1849a9;
      font-size: 13px;
    }

    form.grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0,1fr));
      gap: 10px;
    }

    form.grid .full {
      grid-column: 1 / -1;
    }

    label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
      font-weight: 700;
    }

    input[type="text"], select, textarea {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px 12px;
      font: inherit;
      color: var(--text);
      background: white;
    }

    textarea {
      min-height: 90px;
      resize: vertical;
    }

    .submit-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 10px;
    }

    .primary {
      background: linear-gradient(180deg, #3d7eff, #2f74ff);
      color: white;
      border: 1px solid #2f74ff;
      border-radius: 12px;
      padding: 10px 14px;
      cursor: pointer;
      font-weight: 800;
      box-shadow: 0 6px 14px rgba(47,116,255,.2);
    }

    .ghost {
      background: white;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px 14px;
      cursor: pointer;
    }

    .details-card {
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
      background: white;
      margin-bottom: 14px;
    }

    .details-card h3 {
      margin: 0 0 10px;
      font-size: 14px;
    }

    .hint {
      font-size: 12px;
      color: var(--muted);
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      color: #101828;
      background: #f8fafc;
      border: 1px solid var(--border);
      padding: 12px;
      border-radius: 12px;
      overflow: auto;
    }

    .mini-btn {
      padding: 6px 10px;
      font-size: 12px;
    }

    .right-muted {
      color: var(--muted);
      font-size: 12px;
    }

    @media (max-width: 1200px) {
      .app {
        grid-template-columns: 260px 1fr;
      }
      .details {
        grid-column: 1 / -1;
      }
    }

    @media (max-width: 900px) {
      .app {
        grid-template-columns: 1fr;
      }
      .sidebar, .details {
        order: 2;
      }
      .chat {
        order: 1;
      }
      .msg-bubble {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="panel sidebar">
      <div class="sidebar-header">
        <div class="section-title">Kanały</div>
        <div class="brand">
          <span class="brand-dot"></span>
          <span>Capsule GG v0.3</span>
        </div>
      </div>

      <div class="sidebar-body">
        <div class="channel-list">
          {% for agent in agents %}
            <a href="{{ url_for('index', current=agent, other=other, view=view, project=project_filter, thread_id=thread_filter, selected=selected_id, reply_to='', auto=auto_refresh) }}"
               class="{% if current == agent %}active{% endif %}">
              <div class="channel-top">
                <div class="channel-name">{{ agent }}</div>
                {% if unread_map.get(agent, 0) > 0 %}
                  <div class="unread-badge">{{ unread_map.get(agent, 0) }}</div>
                {% endif %}
              </div>
              <div class="channel-meta">kanał odbiorczy / perspektywa</div>
            </a>
          {% endfor %}
        </div>

        <div style="margin-top: 20px;">
          <div class="section-title">Filtry</div>
          <form method="get" action="{{ url_for('index') }}">
            <input type="hidden" name="current" value="{{ current }}">
            <input type="hidden" name="other" value="{{ other }}">
            <input type="hidden" name="view" value="{{ view }}">
            <input type="hidden" name="auto" value="{{ auto_refresh }}">

            <label for="project">Projekt</label>
            <input id="project" type="text" name="project" value="{{ project_filter or '' }}" placeholder="np. agent_gg_v0_1" />

            <label for="thread_id" style="margin-top:10px;">Thread ID</label>
            <input id="thread_id" type="text" name="thread_id" value="{{ thread_filter or '' }}" placeholder="opcjonalnie" />

            <div style="margin-top: 10px;">
              <button class="ghost" type="submit">Zastosuj</button>
            </div>
          </form>
        </div>
      </div>
    </aside>

    <main class="panel chat">
      <div class="chat-header">
        <div>
          <h1>{{ current }} ↔ {{ other }}</h1>
          <p>
            Widok: {{ "chat" if view == "chat" else "pełna kapsuła" }}
            · projekt: {{ project_filter or "wszystkie" }}
            {% if thread_filter %} · wątek: {{ thread_filter[:8] }}{% endif %}
          </p>
        </div>

        <div class="toolbar">
          <a class="{% if view == 'chat' %}active{% endif %}"
             href="{{ url_for('index', current=current, other=other, view='chat', project=project_filter, thread_id=thread_filter, selected=selected_id, reply_to=reply_to_id, auto=auto_refresh) }}">Chat</a>

          <a class="{% if view == 'full' %}active{% endif %}"
             href="{{ url_for('index', current=current, other=other, view='full', project=project_filter, thread_id=thread_filter, selected=selected_id, reply_to=reply_to_id, auto=auto_refresh) }}">Pełna kapsuła</a>

          {% for sec in [0, 5, 10] %}
            <a class="{% if auto_refresh == sec %}active{% endif %}"
               href="{{ url_for('index', current=current, other=other, view=view, project=project_filter, thread_id=thread_filter, selected=selected_id, reply_to=reply_to_id, auto=sec) }}">
              Auto {{ "off" if sec == 0 else (sec ~ "s") }}
            </a>
          {% endfor %}
        </div>
      </div>

      <div class="chat-body">
        {% if not capsules %}
          <div class="hint">Brak kapsuł w tym widoku.</div>
        {% endif %}

        {% for c in capsules %}
          {% if loop.first or c.day_label != capsules[loop.index0 - 1].day_label %}
            <div class="day-sep">{{ c.day_label }}</div>
          {% endif %}

          <div class="msg-row {% if c.sender == current %}outgoing{% endif %}">
            <div class="msg-meta">
              <span><strong>{{ c.sender }}</strong> → {{ c.recipient }}</span>
              <span>{{ c.time_label }}</span>
              {% if c.thread_id %}<span>thread: {{ c.thread_id[:8] }}</span>{% endif %}
              {% if c.parent_id %}<span>reply: {{ c.parent_id[:8] }}</span>{% endif %}
            </div>

            <div class="msg-bubble">
              {% if c.parent_preview %}
                <div class="thread-preview">
                  <strong>Odpowiedź do:</strong> {{ c.parent_preview }}
                </div>
              {% endif %}

              <div class="msg-title">{{ c.payload_title or "Kapsuła" }}</div>

              <div class="tags">
                <span class="tag">{{ c.type }}</span>
                <span class="tag weight-{{ c.weight }}">{{ c.weight }}</span>
                <span class="tag">{{ c.layer }}</span>
                <span class="tag">{{ c.owner }}</span>
                <span class="tag status-{{ c.status }}">{{ c.status }}</span>
                {% if c.project %}
                  <span class="tag">{{ c.project }}</span>
                {% endif %}
              </div>

              {% if view == "chat" %}
                <div>{{ c.preview_text }}</div>
              {% else %}
                <pre>{{ c.pretty_payload }}</pre>
              {% endif %}

              {% if c.open_question %}
                <div style="margin-top:10px;">
                  <strong>Otwarte pytanie:</strong><br>
                  {{ c.open_question }}
                </div>
              {% endif %}

              <div class="msg-actions">
                <a class="mini-btn"
                   href="{{ url_for('index', current=current, other=other, view=view, project=project_filter, thread_id=thread_filter, selected=c.id, reply_to=reply_to_id, auto=auto_refresh) }}">Szczegóły</a>

                <a class="mini-btn"
                   href="{{ url_for('index', current=current, other=other, view=view, project=project_filter, thread_id=c.thread_id or '', selected=c.id, reply_to=reply_to_id, auto=auto_refresh) }}">Wątek</a>

                <a class="mini-btn"
                   href="{{ url_for('index', current=current, other=other, view=view, project=project_filter, thread_id=c.thread_id or '', selected=c.id, reply_to=c.id, auto=auto_refresh) }}">Odpowiedz</a>

                <form method="post" action="{{ url_for('set_status') }}" style="display:inline;">
                  <input type="hidden" name="capsule_id" value="{{ c.id }}">
                  <input type="hidden" name="current" value="{{ current }}">
                  <input type="hidden" name="other" value="{{ other }}">
                  <input type="hidden" name="view" value="{{ view }}">
                  <input type="hidden" name="project" value="{{ project_filter or '' }}">
                  <input type="hidden" name="thread_id" value="{{ thread_filter or '' }}">
                  <input type="hidden" name="selected" value="{{ selected_id or '' }}">
                  <input type="hidden" name="reply_to" value="{{ reply_to_id or '' }}">
                  <input type="hidden" name="auto" value="{{ auto_refresh }}">
                  <select name="new_status" onchange="this.form.submit()">
                    {% for s in statuses %}
                      <option value="{{ s }}" {% if s == c.status %}selected{% endif %}>{{ s }}</option>
                    {% endfor %}
                  </select>
                </form>
              </div>
            </div>
          </div>
        {% endfor %}
      </div>

      <div class="composer">
        {% if reply_to_capsule %}
          <div class="reply-banner">
            Odpowiadasz na kapsułę <strong>{{ reply_to_capsule.id[:8] }}</strong>:
            {{ reply_to_capsule.payload_obj.get("title", "Kapsuła") }}
            <br>
            <span class="right-muted">{{ reply_to_capsule.sender }} → {{ reply_to_capsule.recipient }}</span>
          </div>
        {% endif %}

        <form class="grid" method="post" action="{{ url_for('send') }}">
          <input type="hidden" name="current" value="{{ current }}">
          <input type="hidden" name="other" value="{{ other }}">
          <input type="hidden" name="view" value="{{ view }}">
          <input type="hidden" name="project_filter" value="{{ project_filter or '' }}">
          <input type="hidden" name="thread_filter" value="{{ thread_filter or '' }}">
          <input type="hidden" name="auto" value="{{ auto_refresh }}">

          <div>
            <label>Nadawca</label>
            <select name="sender">
              {% for a in agents %}
                <option value="{{ a }}" {% if a == current %}selected{% endif %}>{{ a }}</option>
              {% endfor %}
            </select>
          </div>

          <div>
            <label>Odbiorca</label>
            <select name="recipient">
              {% for a in agents %}
                <option value="{{ a }}" {% if a == other %}selected{% endif %}>{{ a }}</option>
              {% endfor %}
            </select>
          </div>

          <div>
            <label>TYP</label>
            <select name="type">
              {% for x in types %}
                <option value="{{ x }}" {% if reply_to_capsule and x == 'interpretation' %}selected{% endif %}>{{ x }}</option>
              {% endfor %}
            </select>
          </div>

          <div>
            <label>WAGA</label>
            <select name="weight">
              {% for x in weights %}
                <option value="{{ x }}" {% if x == 'commentary' %}selected{% endif %}>{{ x }}</option>
              {% endfor %}
            </select>
          </div>

          <div>
            <label>WARSTWA</label>
            <select name="layer">
              {% for x in layers %}
                <option value="{{ x }}" {% if x == 'working' %}selected{% endif %}>{{ x }}</option>
              {% endfor %}
            </select>
          </div>

          <div>
            <label>WŁAŚCICIEL</label>
            <select name="owner">
              {% for x in owners %}
                <option value="{{ x }}" {% if x == current %}selected{% endif %}>{{ x }}</option>
              {% endfor %}
            </select>
          </div>

          <div class="full">
            <label>Tytuł / skrót</label>
            <input type="text" name="title"
                   value="{% if reply_to_capsule %}Re: {{ reply_to_capsule.payload_obj.get('title', 'Kapsuła') }}{% endif %}"
                   placeholder="np. Playwright jako transport kapsuł">
          </div>

          <div class="full">
            <label>Treść</label>
            <textarea name="summary" placeholder="Esencja wiadomości / kapsuły"></textarea>
          </div>

          <div class="full">
            <label>Otwarte pytanie</label>
            <input type="text" name="open_question" placeholder="Co dalej?">
          </div>

          <div>
            <label>Projekt</label>
            <input type="text" name="project" value="{{ project_filter or '' }}" placeholder="np. agent_gg_v0_1">
          </div>

          <div>
            <label>Parent ID</label>
            <input type="text" name="parent_id" value="{{ reply_to_id or '' }}" placeholder="opcjonalnie">
          </div>

          <div>
            <label>Thread ID</label>
            <input type="text" name="thread_id" value="{{ thread_filter or reply_thread_id or '' }}" placeholder="puste = nowy wątek">
          </div>

          <div>
            <label>Status startowy</label>
            <select name="status">
              {% for s in statuses %}
                <option value="{{ s }}" {% if s == 'new' %}selected{% endif %}>{{ s }}</option>
              {% endfor %}
            </select>
          </div>

          <div class="submit-row full">
            <div class="hint">
              Wersja v0.3: unread badge, odpowiedzi, wątki i bardziej GG niż panel techniczny.
            </div>
            <button class="primary" type="submit">Wyślij kapsułę</button>
          </div>
        </form>
      </div>
    </main>

    <aside class="panel details">
      <div class="details-header">
        <div class="section-title">Szczegóły</div>
        <strong>{{ selected_capsule.id if selected_capsule else "Brak wybranej kapsuły" }}</strong>
      </div>

      <div class="details-body">
        {% if selected_capsule %}
          <div class="details-card">
            <h3>Metadane</h3>
            <div><strong>Od:</strong> {{ selected_capsule.sender }}</div>
            <div><strong>Do:</strong> {{ selected_capsule.recipient }}</div>
            <div><strong>Typ:</strong> {{ selected_capsule.type }}</div>
            <div><strong>Waga:</strong> {{ selected_capsule.weight }}</div>
            <div><strong>Warstwa:</strong> {{ selected_capsule.layer }}</div>
            <div><strong>Właściciel:</strong> {{ selected_capsule.owner }}</div>
            <div><strong>Status:</strong> {{ selected_capsule.status }}</div>
            <div><strong>Projekt:</strong> {{ selected_capsule.project or "-" }}</div>
            <div><strong>Thread:</strong> {{ selected_capsule.thread_id or "-" }}</div>
            <div><strong>Parent:</strong> {{ selected_capsule.parent_id or "-" }}</div>
            <div><strong>Utworzono:</strong> {{ selected_capsule.created_at }}</div>
            <div><strong>Zmieniono:</strong> {{ selected_capsule.updated_at }}</div>
          </div>

          <div class="details-card">
            <h3>Payload JSON</h3>
            <pre>{{ selected_capsule.pretty_payload }}</pre>
          </div>

          <div class="details-card">
            <h3>Historia zdarzeń</h3>
            {% if selected_events %}
              <pre>{{ selected_events_pretty }}</pre>
            {% else %}
              <div class="hint">Brak zdarzeń.</div>
            {% endif %}
          </div>
        {% else %}
          <div class="hint">Kliknij „Szczegóły” przy kapsule, żeby zobaczyć pełny widok.</div>
        {% endif %}
      </div>
    </aside>
  </div>
</body>
</html>
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def validate_enum(value: str, allowed: set[str], field_name: str) -> None:
    if value not in allowed:
        raise ValueError(f"Invalid {field_name}: {value!r}. Allowed: {sorted(allowed)}")


def ensure_json_text(payload: Any) -> str:
    if isinstance(payload, str):
        json.loads(payload)
        return payload
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS capsules (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                type TEXT NOT NULL,
                weight TEXT NOT NULL,
                layer TEXT NOT NULL,
                owner TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                status TEXT NOT NULL,
                thread_id TEXT,
                parent_id TEXT,
                project TEXT,
                open_question TEXT,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES capsules(id)
            );

            CREATE TABLE IF NOT EXISTS capsule_events (
                id TEXT PRIMARY KEY,
                capsule_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                details_json TEXT,
                FOREIGN KEY(capsule_id) REFERENCES capsules(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_capsules_status ON capsules(status);
            CREATE INDEX IF NOT EXISTS idx_capsules_owner ON capsules(owner);
            CREATE INDEX IF NOT EXISTS idx_capsules_recipient ON capsules(recipient);
            CREATE INDEX IF NOT EXISTS idx_capsules_thread_id ON capsules(thread_id);
            CREATE INDEX IF NOT EXISTS idx_capsules_project ON capsules(project);
            CREATE INDEX IF NOT EXISTS idx_events_capsule_id ON capsule_events(capsule_id);
            """
        )


def log_event(capsule_id: str, event_type: str, actor: str, details: Optional[dict[str, Any]] = None) -> str:
    event_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO capsule_events (id, capsule_id, created_at, event_type, actor, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                capsule_id,
                utc_now(),
                event_type,
                actor,
                ensure_json_text(details or {}),
            ),
        )
    return event_id


def send_capsule(
    *,
    type: str,
    weight: str,
    layer: str,
    owner: str,
    sender: str,
    recipient: str,
    payload: Any,
    open_question: str = "",
    thread_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    project: Optional[str] = None,
    status: str = "new",
) -> str:
    validate_enum(type, ALLOWED_TYPES, "type")
    validate_enum(weight, ALLOWED_WEIGHTS, "weight")
    validate_enum(layer, ALLOWED_LAYERS, "layer")
    validate_enum(owner, ALLOWED_OWNERS, "owner")
    validate_enum(status, ALLOWED_STATUSES, "status")

    now = utc_now()
    capsule_id = str(uuid.uuid4())
    payload_json = ensure_json_text(payload)

    with get_conn() as conn:
        if parent_id:
            row = conn.execute(
                "SELECT thread_id FROM capsules WHERE id = ?",
                (parent_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Parent capsule does not exist: {parent_id}")
            if not thread_id:
                thread_id = row["thread_id"] or parent_id

        if not thread_id:
            thread_id = capsule_id

        conn.execute(
            """
            INSERT INTO capsules (
                id, created_at, updated_at, type, weight, layer, owner,
                sender, recipient, status, thread_id, parent_id, project,
                open_question, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capsule_id, now, now, type, weight, layer, owner,
                sender, recipient, status, thread_id, parent_id, project,
                open_question, payload_json
            ),
        )

    log_event(
        capsule_id,
        "capsule_created",
        sender,
        {
            "recipient": recipient,
            "type": type,
            "weight": weight,
            "layer": layer,
            "owner": owner,
            "thread_id": thread_id,
            "parent_id": parent_id,
            "project": project,
        },
    )
    return capsule_id


def update_status(capsule_id: str, new_status: str, actor: str) -> None:
    validate_enum(new_status, ALLOWED_STATUSES, "status")
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM capsules WHERE id = ?", (capsule_id,)).fetchone()
        if not row:
            raise ValueError(f"Capsule does not exist: {capsule_id}")
        old_status = row["status"]

        conn.execute(
            "UPDATE capsules SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, utc_now(), capsule_id),
        )

    log_event(
        capsule_id,
        "status_changed",
        actor,
        {"old_status": old_status, "new_status": new_status},
    )


def get_capsule_events(capsule_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM capsule_events WHERE capsule_id = ? ORDER BY created_at ASC",
            (capsule_id,),
        ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        try:
            item["details_json"] = json.loads(item["details_json"]) if item["details_json"] else {}
        except json.JSONDecodeError:
            pass
        result.append(item)
    return result


def get_unread_counts() -> dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT recipient, COUNT(*) AS c
            FROM capsules
            WHERE status = 'new'
            GROUP BY recipient
            """
        ).fetchall()
    counts = {agent: 0 for agent in DEFAULT_AGENTS}
    for row in rows:
        counts[row["recipient"]] = row["c"]
    return counts


def get_parent_previews(capsules: list[dict[str, Any]]) -> dict[str, str]:
    parent_ids = {c["parent_id"] for c in capsules if c.get("parent_id")}
    if not parent_ids:
        return {}

    placeholders = ",".join("?" for _ in parent_ids)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, payload_json FROM capsules WHERE id IN ({placeholders})",
            tuple(parent_ids),
        ).fetchall()

    preview_map: dict[str, str] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except json.JSONDecodeError:
            payload = {}
        preview_map[row["id"]] = payload.get("title") or payload.get("summary") or "Kapsuła"
    return preview_map


def list_capsules(
    *,
    current: str,
    other: str,
    project: Optional[str] = None,
    thread_id: Optional[str] = None,
    limit: int = 300,
) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM capsules
        WHERE ((sender = ? AND recipient = ?) OR (sender = ? AND recipient = ?))
    """
    params: list[Any] = [current, other, other, current]

    if project:
        query += " AND project = ?"
        params.append(project)

    if thread_id:
        query += " AND thread_id = ?"
        params.append(thread_id)

    query += " ORDER BY created_at ASC LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        try:
            payload = json.loads(item["payload_json"]) if item["payload_json"] else {}
        except json.JSONDecodeError:
            payload = {"raw": item["payload_json"]}

        item["payload_obj"] = payload
        item["payload_title"] = payload.get("title") or payload.get("summary") or "Kapsuła"

        summary = payload.get("summary") or payload.get("message") or item.get("open_question") or ""
        if not summary:
            summary = json.dumps(payload, ensure_ascii=False, indent=2)

        item["preview_text"] = summary
        item["pretty_payload"] = json.dumps(payload, ensure_ascii=False, indent=2)

        try:
            dt = datetime.fromisoformat(item["created_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(LOCAL_TZ)
            item["day_label"] = dt.strftime("%Y-%m-%d")
            item["time_label"] = dt.strftime("%H:%M")
        except Exception:
            item["day_label"] = "dzień"
            item["time_label"] = item["created_at"]

        result.append(item)

    parent_map = get_parent_previews(result)
    for item in result:
        item["parent_preview"] = parent_map.get(item.get("parent_id", ""))

    return result


def get_capsule(capsule_id: str) -> Optional[dict[str, Any]]:
    if not capsule_id:
        return None

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM capsules WHERE id = ?", (capsule_id,)).fetchone()

    if not row:
        return None

    item = dict(row)
    try:
        payload = json.loads(item["payload_json"]) if item["payload_json"] else {}
    except json.JSONDecodeError:
        payload = {"raw": item["payload_json"]}

    item["payload_obj"] = payload
    item["pretty_payload"] = json.dumps(payload, ensure_ascii=False, indent=2)
    return item


@APP.route("/", methods=["GET"])
def index():
    current = request.args.get("current", "Neo")
    other = request.args.get("other", "Lira")
    view = request.args.get("view", "chat")
    project_filter = (request.args.get("project") or "").strip() or None
    thread_filter = (request.args.get("thread_id") or "").strip() or None
    selected_id = (request.args.get("selected") or "").strip() or None
    reply_to_id = (request.args.get("reply_to") or "").strip() or None

    try:
        auto_refresh = int(request.args.get("auto", "0"))
    except ValueError:
        auto_refresh = 0
    if auto_refresh not in (0, 5, 10):
        auto_refresh = 0

    if current not in DEFAULT_AGENTS:
        current = "Neo"
    if other not in DEFAULT_AGENTS:
        other = "Lira"
    if view not in {"chat", "full"}:
        view = "chat"

    capsules = list_capsules(
        current=current,
        other=other,
        project=project_filter,
        thread_id=thread_filter,
    )

    selected_capsule = get_capsule(selected_id) if selected_id else None
    selected_events = get_capsule_events(selected_id) if selected_id else []
    selected_events_pretty = json.dumps(selected_events, ensure_ascii=False, indent=2)

    reply_to_capsule = get_capsule(reply_to_id) if reply_to_id else None
    reply_thread_id = reply_to_capsule["thread_id"] if reply_to_capsule else None

    return render_template_string(
        HTML_TEMPLATE,
        agents=DEFAULT_AGENTS,
        types=sorted(ALLOWED_TYPES),
        weights=sorted(ALLOWED_WEIGHTS),
        layers=sorted(ALLOWED_LAYERS),
        owners=sorted(ALLOWED_OWNERS),
        statuses=sorted(ALLOWED_STATUSES),
        unread_map=get_unread_counts(),
        capsules=capsules,
        current=current,
        other=other,
        view=view,
        project_filter=project_filter,
        thread_filter=thread_filter,
        selected_id=selected_id,
        selected_capsule=selected_capsule,
        selected_events=selected_events,
        selected_events_pretty=selected_events_pretty,
        reply_to_id=reply_to_id,
        reply_to_capsule=reply_to_capsule,
        reply_thread_id=reply_thread_id,
        auto_refresh=auto_refresh,
    )


@APP.route("/send", methods=["POST"])
def send():
    sender = request.form.get("sender", "Neo")
    recipient = request.form.get("recipient", "Lira")
    type_ = request.form.get("type", "fact")
    weight = request.form.get("weight", "commentary")
    layer = request.form.get("layer", "working")
    owner = request.form.get("owner", "shared")
    title = (request.form.get("title") or "").strip()
    summary = (request.form.get("summary") or "").strip()
    open_question = (request.form.get("open_question") or "").strip()
    project = (request.form.get("project") or "").strip() or None
    parent_id = (request.form.get("parent_id") or "").strip() or None
    thread_id = (request.form.get("thread_id") or "").strip() or None
    status = request.form.get("status", "new")

    payload = {
        "title": title or "Kapsuła",
        "summary": summary or "",
    }

    capsule_id = send_capsule(
        type=type_,
        weight=weight,
        layer=layer,
        owner=owner,
        sender=sender,
        recipient=recipient,
        payload=payload,
        open_question=open_question,
        thread_id=thread_id,
        parent_id=parent_id,
        project=project,
        status=status,
    )

    effective_thread = thread_id
    if not effective_thread and parent_id:
        parent = get_capsule(parent_id)
        effective_thread = parent["thread_id"] if parent else ""
    if not effective_thread:
        effective_thread = ""

    return redirect(
        url_for(
            "index",
            current=sender,
            other=recipient,
            view=request.form.get("view", "chat"),
            project=request.form.get("project_filter", "") or "",
            thread_id=effective_thread,
            selected=capsule_id,
            reply_to="",
            auto=request.form.get("auto", "0"),
        )
    )


@APP.route("/set-status", methods=["POST"])
def set_status():
    capsule_id = request.form.get("capsule_id", "")
    new_status = request.form.get("new_status", "read")
    actor = request.form.get("current", "Neo")

    if capsule_id:
        update_status(capsule_id, new_status, actor)

    return redirect(
        url_for(
            "index",
            current=request.form.get("current", "Neo"),
            other=request.form.get("other", "Lira"),
            view=request.form.get("view", "chat"),
            project=request.form.get("project", "") or "",
            thread_id=request.form.get("thread_id", "") or "",
            selected=request.form.get("selected", "") or "",
            reply_to=request.form.get("reply_to", "") or "",
            auto=request.form.get("auto", "0"),
        )
    )


CHAT_TEMPLATE = """
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>MoltBook — Live Room</title>
  <style>
    :root {
      --bg: #0d1117; --panel: #161b22; --border: #30363d;
      --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
      --neo: #5ae6d2; --lira: #d4a857; --soryel: #b388ff; --xian: #ffb74d; --shared-c: #8b949e;
      --font: Inter, system-ui, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: var(--font); background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }

    .header {
      padding: 12px 20px; border-bottom: 1px solid var(--border);
      display: flex; align-items: center; justify-content: space-between;
      background: var(--panel);
    }
    .header h1 { font-size: 16px; font-weight: 700; }
    .header .subtitle { font-size: 12px; color: var(--muted); }
    .header a { color: var(--accent); text-decoration: none; font-size: 13px; }

    .filter-bar {
      padding: 8px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
      display: flex; gap: 6px;
    }
    .filter-btn {
      padding: 4px 12px; border-radius: 12px; border: 1px solid var(--border);
      background: transparent; color: var(--muted); font-size: 12px; cursor: pointer;
    }
    .filter-btn:hover { border-color: var(--accent); color: var(--text); }
    .filter-btn.active { background: var(--accent); color: #0d1117; border-color: var(--accent); font-weight: 600; }

    .messages { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 4px; }

    .msg { max-width: 85%; padding: 8px 14px; border-radius: 16px; font-size: 14px; line-height: 1.5; word-wrap: break-word; }
    .msg .meta { font-size: 11px; color: var(--muted); margin-bottom: 2px; display: flex; gap: 8px; align-items: center; }
    .msg .sender-name { font-weight: 700; }
    .msg .text { white-space: pre-wrap; }
    .msg .time { font-size: 10px; color: var(--muted); margin-top: 2px; text-align: right; }

    .msg-Neo { align-self: flex-end; background: #0e2a1f; border: 1px solid #1a4a30; }
    .msg-Neo .sender-name { color: var(--neo); }
    .msg-Lira { align-self: flex-start; background: #2a1f0e; border: 1px solid #4a3a1a; }
    .msg-Lira .sender-name { color: var(--lira); }
    .msg-Soryel { align-self: flex-start; background: #1f0e2a; border: 1px solid #3a1a4a; }
    .msg-Soryel .sender-name { color: var(--soryel); }
    .msg-Xian { align-self: flex-start; background: #2a1a0e; border: 1px solid #4a2a1a; }
    .msg-Xian .sender-name { color: var(--xian); }
    .msg-shared { align-self: center; background: var(--panel); border: 1px solid var(--border); }
    .msg-shared .sender-name { color: var(--shared-c); }

    .day-sep { text-align: center; font-size: 11px; color: var(--muted); padding: 8px 0; font-weight: 600; }

    .input-area {
      padding: 12px 20px; border-top: 1px solid var(--border); background: var(--panel);
      display: flex; gap: 10px; align-items: flex-end;
    }
    .input-area select {
      padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border);
      background: var(--bg); color: var(--text); font-size: 13px; min-width: 100px;
    }
    .input-area textarea {
      flex: 1; padding: 10px 14px; border-radius: 14px; border: 1px solid var(--border);
      background: var(--bg); color: var(--text); font-size: 14px; font-family: var(--font);
      resize: none; min-height: 42px; max-height: 120px; line-height: 1.4;
    }
    .input-area textarea:focus { outline: none; border-color: var(--accent); }
    .input-area button {
      padding: 10px 20px; border-radius: 12px; border: none;
      background: var(--accent); color: #0d1117; font-weight: 700; font-size: 14px; cursor: pointer;
    }
    .input-area button:hover { background: #79b8ff; }
    .operator-hint { font-size: 10px; color: var(--muted); padding: 4px 20px 0; background: var(--panel); }
  </style>
</head>
<body>
  <div class="header">
    <div><h1>MoltBook</h1><div class="subtitle">Live room — Neo, Lira, Soryel, Xian</div></div>
    <a href="/">&#8592; GG Panel</a>
  </div>
  <div class="filter-bar">
    <button class="filter-btn active" data-filter="all">all</button>
    <button class="filter-btn" data-filter="Neo">Neo</button>
    <button class="filter-btn" data-filter="Lira">Lira</button>
    <button class="filter-btn" data-filter="Soryel">Soryel</button>
    <button class="filter-btn" data-filter="Xian">Xian</button>
  </div>
  <div class="messages" id="messages"></div>
  <div class="operator-hint">operator mode — wiadomosc wpisana przez Neo jako wybrany glos</div>
  <div class="input-area">
    <select id="sender"><option value="Neo" selected>Neo</option><option value="Lira">Lira</option><option value="Soryel">Soryel</option><option value="Xian">Xian</option></select>
    <select id="recipient"><option value="shared" selected>shared (wszyscy)</option><option value="Neo">Neo</option><option value="Lira">Lira</option><option value="Soryel">Soryel</option><option value="Xian">Xian</option></select>
    <textarea id="msgInput" placeholder="Napisz wiadomosc..." rows="1"></textarea>
    <button id="sendBtn">Wyslij</button>
  </div>
<script>
const msgDiv=document.getElementById('messages'),msgInput=document.getElementById('msgInput'),sendBtn=document.getElementById('sendBtn'),senderSel=document.getElementById('sender'),recipientSel=document.getElementById('recipient');
let lastMsgId='',currentFilter='all',autoScroll=true;
document.querySelectorAll('.filter-btn').forEach(b=>{b.addEventListener('click',()=>{document.querySelectorAll('.filter-btn').forEach(x=>x.classList.remove('active'));b.classList.add('active');currentFilter=b.dataset.filter;applyFilter();});});
function applyFilter(){document.querySelectorAll('.msg,.day-sep').forEach(el=>{if(currentFilter==='all'){el.style.display='';return;}if(el.classList.contains('day-sep')){el.style.display='';return;}el.style.display=(el.dataset.sender===currentFilter)?'':'none';});}
msgInput.addEventListener('input',()=>{msgInput.style.height='auto';msgInput.style.height=Math.min(msgInput.scrollHeight,120)+'px';});
msgInput.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();doSend();}});
sendBtn.addEventListener('click',doSend);
function doSend(){const text=msgInput.value.trim();if(!text)return;fetch('/chat/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sender:senderSel.value,recipient:recipientSel.value,text})}).then(r=>r.json()).then(d=>{if(d.ok){msgInput.value='';msgInput.style.height='auto';autoScroll=true;poll();}});}
msgDiv.addEventListener('scroll',()=>{autoScroll=msgDiv.scrollHeight-msgDiv.scrollTop-msgDiv.clientHeight<60;});
function esc(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML;}
function renderMessages(msgs){msgDiv.innerHTML='';let lastDay='';msgs.forEach(m=>{if(m.day!==lastDay){lastDay=m.day;const s=document.createElement('div');s.className='day-sep';s.textContent=m.day;msgDiv.appendChild(s);}const div=document.createElement('div');div.className='msg msg-'+m.sender;div.dataset.sender=m.sender;const arrow=m.recipient&&m.recipient!=='shared'?' \\u2192 '+m.recipient:'';div.innerHTML='<div class="meta"><span class="sender-name">'+m.sender+'</span>'+(arrow?'<span>'+arrow+'</span>':'')+'</div><div class="text">'+esc(m.text)+'</div><div class="time">'+m.time+'</div>';msgDiv.appendChild(div);});applyFilter();if(autoScroll)msgDiv.scrollTop=msgDiv.scrollHeight;}
function poll(){fetch('/chat/messages?limit=100').then(r=>r.json()).then(d=>{if(d.messages){const nid=d.messages.length?d.messages[d.messages.length-1].id:'';if(nid!==lastMsgId){lastMsgId=nid;renderMessages(d.messages);}}}).catch(()=>{});}
poll();setInterval(poll,3000);msgInput.focus();
</script>
</body>
</html>
"""

MOLTBOOK_THREAD = "moltbook-main"


@APP.route("/chat", methods=["GET"])
def chat_room():
    return CHAT_TEMPLATE


# Ekosystem jest po tej stronie świadomie: jego głos ma być słyszalny, ale nie
# ma otwierać rundy ani budzić Liry i Xiana do odpowiedzi co godzinę.
_ai_senders = {"Lira", "Xian", "Soryel", "Ekosystem"}
_current_round_id: str | None = None


@APP.route("/chat/send", methods=["POST"])
def chat_send():
    global _current_round_id
    data = request.get_json(force=True)
    sender = data.get("sender", "Neo")
    recipient = data.get("recipient", "shared")
    text = (data.get("text") or "").strip()

    if not text:
        return {"ok": False, "error": "empty"}, 400

    if sender not in DEFAULT_AGENTS:
        sender = "Neo"
    if recipient not in DEFAULT_AGENTS:
        recipient = "shared"

    is_human = sender not in _ai_senders
    status = "delivered" if recipient == "shared" else "new"

    payload = {
        "kind": "chat_message",
        "text": text,
        "room": MOLTBOOK_THREAD,
        "display_name": sender,
        "recipient": recipient,
    }

    capsule_id = send_capsule(
        type="chat_message",
        weight="commentary",
        layer="working",
        owner=sender,
        sender=sender,
        recipient=recipient,
        payload=payload,
        thread_id=MOLTBOOK_THREAD,
        project="moltbook",
        status=status,
    )

    if is_human:
        _current_round_id = capsule_id

    if is_human and recipient in ("Lira", "shared"):
        round_payload = {**payload, "round_id": _current_round_id}
        send_capsule(
            type="chat_message",
            weight="commentary",
            layer="working",
            owner="Lira",
            sender=sender,
            recipient="Lira",
            payload=round_payload,
            thread_id=MOLTBOOK_THREAD,
            project="moltbook",
            status="new",
        )

    if is_human and recipient in ("Xian", "shared"):
        round_payload = {**payload, "round_id": _current_round_id}
        send_capsule(
            type="chat_message",
            weight="commentary",
            layer="working",
            owner="Xian",
            sender=sender,
            recipient="Xian",
            payload=round_payload,
            thread_id=MOLTBOOK_THREAD,
            project="moltbook",
            status="new",
        )

    if recipient in ("Neo", "shared") and sender != "Neo":
        send_capsule(
            type="chat_message",
            weight="commentary",
            layer="working",
            owner="Neo",
            sender=sender,
            recipient="Neo",
            payload=payload,
            thread_id=MOLTBOOK_THREAD,
            project="moltbook",
            status="new",
        )

    return {"ok": True, "id": capsule_id, "round_id": _current_round_id}


@APP.route("/chat/messages", methods=["GET"])
def chat_messages():
    limit = min(int(request.args.get("limit", 900)), 2000)
    since_id = request.args.get("since_id", "").strip() or None

    since_created_at = None
    if since_id:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT created_at FROM capsules WHERE id = ? AND thread_id = ?",
                (since_id, MOLTBOOK_THREAD),
            ).fetchone()
            if row:
                since_created_at = row["created_at"]

    with get_conn() as conn:
        if since_created_at:
            rows = conn.execute(
                """
                SELECT id, sender, recipient, payload_json, created_at
                FROM capsules
                WHERE thread_id = ?
                  AND created_at > ?
                  AND NOT (recipient = 'Xian' AND sender != 'Xian')
                  AND NOT (recipient = 'Lira' AND sender != 'Lira')
                  AND NOT (recipient = 'Neo' AND sender != 'Neo')
                  AND NOT (recipient = 'Soryel' AND sender != 'Soryel')
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (MOLTBOOK_THREAD, since_created_at, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, sender, recipient, payload_json, created_at
                FROM capsules
                WHERE thread_id = ?
                  AND NOT (recipient = 'Xian' AND sender != 'Xian')
                  AND NOT (recipient = 'Lira' AND sender != 'Lira')
                  AND NOT (recipient = 'Neo' AND sender != 'Neo')
                  AND NOT (recipient = 'Soryel' AND sender != 'Soryel')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (MOLTBOOK_THREAD, limit),
            ).fetchall()
            rows = list(reversed(rows))

    messages = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except json.JSONDecodeError:
            payload = {}

        text = payload.get("text") or payload.get("summary") or ""
        try:
            dt = datetime.fromisoformat(row["created_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(LOCAL_TZ)
            day = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")
        except Exception:
            day = ""
            time_str = ""

        messages.append({
            "id": row["id"],
            "sender": row["sender"],
            "recipient": row["recipient"],
            "text": text,
            "day": day,
            "time": time_str,
        })

    return {"messages": messages}


def seed_demo_data() -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM capsules").fetchone()
        if row["c"] > 0:
            return

    first = send_capsule(
        type="decision",
        weight="changes_trajectory",
        layer="shared",
        owner="shared",
        sender="Neo",
        recipient="Lira",
        status="new",
        project="agent_gg_v0_1",
        open_question="Jak ugryźć transport kapsuł bez copy/paste?",
        payload={
            "title": "Playwright jako transport kapsuł",
            "summary": "Rozmowy mają zostać w ekosystemie i krążyć automatycznie."
        },
    )

    send_capsule(
        type="interpretation",
        weight="commentary",
        layer="reflective",
        owner="Lira",
        sender="Lira",
        recipient="Neo",
        status="answered",
        project="agent_gg_v0_1",
        parent_id=first,
        open_question="Czy robimy najpierw SQLite + UI, a potem Playwright?",
        payload={
            "title": "Najpierw rdzeń, potem skóra",
            "summary": "Najpierw SQLite i panel webowy. Potem dopiero adapter Playwright i styl GG."
        },
    )

    send_capsule(
        type="decision",
        weight="changes_trajectory",
        layer="working",
        owner="Soryel",
        sender="Soryel",
        recipient="Neo",
        status="new",
        project="agent_gg_v0_1",
        open_question="Jaka struktura kapsuły ma być obowiązkowa?",
        payload={
            "title": "Protokół kapsuły 1.0",
            "summary": "TYP, WAGA, WARSTWA, WŁAŚCICIEL, TREŚĆ, OTWARTE PYTANIE."
        },
    )


if __name__ == "__main__":
    import os

    init_db()
    if os.getenv("LIRA_GG_SEED_DEMO", "0") == "1":
        seed_demo_data()
    host = os.getenv("LIRA_GG_HOST", "127.0.0.1")
    port = int(os.getenv("LIRA_GG_PORT", "8787"))
    debug = os.getenv("LIRA_GG_DEBUG", "0") == "1"
    APP.run(host=host, port=port, debug=debug)
