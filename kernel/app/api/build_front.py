"""MA Build Front — panel monitoringu budowy sklepu MA + artBHP na porcie 9225.

Prosty Flask, jeden endpoint /, HTML dashboard z live-status:
- Rawpol cache (SQLite: produkty, ostatni import)
- artBHP feed status (waiting_for_credentials / active / error)
- Rundy pracy (co robimy, co czeka)
- Last activity z capsule_bus + MoltBook

Uruchamianie:
    python3 -m api.build_front
    (z kernel/app/)

Port: 9225 (localhost only).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = APP_ROOT.parent / "data"
RAWPOL_DB = DATA_ROOT / "runtime" / "state" / "rawpol_catalog.db"
RAWPOL_RAW_DIR = DATA_ROOT / "runtime" / "state" / "rawpol_raw"
CAPSULE_DB = DATA_ROOT / "shared_context" / "state" / "capsule_bus.db"
MOLTBOOK_API = "http://127.0.0.1:8787/chat/messages?limit=5"

app = Flask(__name__)


def _rawpol_status() -> dict:
    if not RAWPOL_DB.exists():
        return {"state": "not_imported", "products": 0, "last_import": None, "message": "brak cache DB — importer nigdy nie uruchomiony"}
    try:
        con = sqlite3.connect(str(RAWPOL_DB))
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        count = 0
        for t in ("products", "products_cache", "rawpol_products", "items"):
            if t in tables:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                count = cur.fetchone()[0]
                break
        con.close()
        raw_files = list(RAWPOL_RAW_DIR.glob("*.shtml")) if RAWPOL_RAW_DIR.exists() else []
        last_raw = max((f.stat().st_mtime for f in raw_files), default=None)
        last_import = datetime.fromtimestamp(last_raw, tz=timezone.utc).isoformat() if last_raw else None
        return {"state": "active" if count else "empty_db", "products": count, "last_import": last_import, "raw_files": len(raw_files)}
    except Exception as e:
        return {"state": "error", "products": 0, "last_import": None, "message": str(e)}


def _artbhp_status() -> dict:
    feed_url = os.environ.get("RAWPOL_FEED_URL", "")
    if not feed_url:
        return {"state": "waiting_for_credentials", "message": "czekam na login/hasło do panel.artbhp.pl → URL XML feedu w env RAWPOL_FEED_URL"}
    return {"state": "configured", "feed_url": feed_url[:60] + "...", "parser": "STUB — parse_xml() NotImplementedError, czeka na próbkę XML"}


def _capsule_last_activity() -> dict:
    if not CAPSULE_DB.exists():
        return {"last_ts": None, "recent_projects": []}
    try:
        con = sqlite3.connect(str(CAPSULE_DB))
        cur = con.cursor()
        cur.execute("SELECT MAX(created_at) FROM capsules")
        last_ts = cur.fetchone()[0]
        cur.execute("""SELECT project, COUNT(*) FROM capsules
                       WHERE created_at > datetime('now','-1 hour')
                       GROUP BY project ORDER BY 2 DESC LIMIT 5""")
        recent = [{"project": r[0], "count": r[1]} for r in cur.fetchall()]
        con.close()
        return {"last_ts": last_ts, "recent_projects": recent}
    except Exception as e:
        return {"last_ts": None, "recent_projects": [], "error": str(e)}


def _moltbook_tail() -> list:
    try:
        import urllib.request, json
        with urllib.request.urlopen(MOLTBOOK_API, timeout=2) as r:
            data = json.loads(r.read())
        return [{"time": m["time"], "sender": m["sender"], "text": (m["text"][:180] + "…") if len(m["text"]) > 180 else m["text"]}
                for m in data.get("messages", [])]
    except Exception:
        return []


HTML = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>MA Build Front — port 9225</title>
<meta http-equiv="refresh" content="30">
<style>
body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2em auto; padding: 0 1em; background: #0e1116; color: #d1d5db; }
h1 { color: #7dd3fc; margin-bottom: 0.2em; }
.hint { color: #6b7280; font-size: 0.85em; margin-bottom: 1.5em; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1em 1.2em; margin-bottom: 1em; }
.state-ok { color: #4ade80; }
.state-wait { color: #fbbf24; }
.state-err { color: #f87171; }
.state-none { color: #6b7280; }
.k { color: #93c5fd; font-weight: bold; }
.msg { border-left: 2px solid #30363d; padding-left: 0.8em; margin: 0.5em 0; font-size: 0.9em; }
.msg .who { color: #a78bfa; font-weight: bold; }
.msg .t { color: #6b7280; }
</style>
</head>
<body>
<h1>MA Build Front <span style="color:#6b7280;font-size:0.6em;">port 9225</span></h1>
<div class="hint">Odświeżanie co 30s. Sklep MA + artBHP silnik. Front budowy, nie sklepu.</div>

<div class="card">
<h2>Rawpol cache</h2>
<div>Stan: <span class="k state-{{ 'ok' if rawpol.state=='active' else 'wait' if rawpol.state=='empty_db' else 'none' }}">{{ rawpol.state }}</span></div>
<div>Produktów w cache: <span class="k">{{ rawpol.products }}</span></div>
<div>Ostatni import: <span class="k">{{ rawpol.last_import or '—' }}</span></div>
{% if rawpol.message %}<div class="state-wait">{{ rawpol.message }}</div>{% endif %}
</div>

<div class="card">
<h2>artBHP XML feed (panel.artbhp.pl)</h2>
<div>Stan: <span class="k state-{{ 'wait' if artbhp.state=='waiting_for_credentials' else 'ok' }}">{{ artbhp.state }}</span></div>
{% if artbhp.message %}<div class="state-wait">{{ artbhp.message }}</div>{% endif %}
{% if artbhp.parser %}<div>Parser: <span class="k state-wait">{{ artbhp.parser }}</span></div>{% endif %}
</div>

<div class="card">
<h2>Runda pracy (2026-07-01)</h2>
<ul>
<li>✅ R1 recon — Rawpol i artBHP to jedna rodzina, panel.artbhp.pl = B2B feed</li>
<li>⏳ R1.5 Neo — logowanie na panel.artbhp.pl, poszukiwanie URL XML feed</li>
<li>⏳ R2 Lira — taxonomy BHP (7 meta-kategorii proponowane)</li>
<li>✅ R3 Soryel — port 9225 front robót (ten panel)</li>
<li>⏸️ R4 mapping kategorii → ShowroomProduct — czeka na dane</li>
<li>⏸️ R5 parse_xml() implementacja — czeka na próbkę XML</li>
<li>⏸️ R6 MA showroom front-end — najostatniej</li>
</ul>
</div>

<div class="card">
<h2>Capsule bus (ostatnia godzina)</h2>
<div>Ostatnia aktywność: <span class="k">{{ capsule.last_ts or '—' }}</span></div>
{% if capsule.recent_projects %}
<div style="margin-top:0.5em;">Projekty:</div>
<ul>{% for p in capsule.recent_projects %}<li><span class="k">{{ p.project }}</span> — {{ p.count }} kapsuł</li>{% endfor %}</ul>
{% else %}
<div class="state-none">cisza</div>
{% endif %}
</div>

<div class="card">
<h2>MoltBook — ostatnie 5</h2>
{% for m in moltbook %}
<div class="msg"><span class="t">{{ m.time }}</span> <span class="who">{{ m.sender }}</span><br>{{ m.text }}</div>
{% else %}
<div class="state-none">brak wiadomości</div>
{% endfor %}
</div>

<div class="hint" style="margin-top:2em;">MA Build Front v0.1 · {{ now }}</div>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(
        HTML,
        rawpol=_rawpol_status(),
        artbhp=_artbhp_status(),
        capsule=_capsule_last_activity(),
        moltbook=_moltbook_tail(),
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


@app.route("/status.json")
def status_json():
    return jsonify(
        rawpol=_rawpol_status(),
        artbhp=_artbhp_status(),
        capsule=_capsule_last_activity(),
        ts=datetime.now(timezone.utc).isoformat(),
    )


# ---------------- MA Showroom prototyp ----------------

from flask import request


def _showroom_query(category: str = "", search: str = "", brand: str = "", protection: str = "", limit: int = 60, offset: int = 0):
    if not RAWPOL_DB.exists():
        return {"products": [], "total": 0, "categories": [], "brands": [], "protections": []}
    con = sqlite3.connect(str(RAWPOL_DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    where = ["price_net > 0"]
    params: list = []
    if category:
        where.append("category = ?")
        params.append(category)
    if brand:
        where.append("brand = ?")
        params.append(brand)
    if protection:
        where.append("protection_tag = ?")
        params.append(protection)
    if search:
        where.append("(sku LIKE ? OR symbol LIKE ? OR variant_base LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    where_sql = " AND ".join(where)

    cur.execute(f"SELECT COUNT(*) FROM products WHERE {where_sql}", params)
    total = cur.fetchone()[0]

    cur.execute(
        f"""SELECT sku, ean, price_net, price_gross, vat_rate, unit,
                   brand, category, symbol, version, deep_link,
                   variant_base, variant_size, protection_tag
            FROM products WHERE {where_sql}
            ORDER BY category, brand, sku LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    products = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT category, COUNT(*) FROM products WHERE price_net > 0 GROUP BY category ORDER BY 2 DESC")
    categories = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]

    cur.execute("SELECT brand, COUNT(*) FROM products WHERE price_net > 0 GROUP BY brand ORDER BY 2 DESC LIMIT 20")
    brands = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]

    cur.execute("SELECT protection_tag, COUNT(*) FROM products WHERE price_net > 0 AND protection_tag != '' GROUP BY protection_tag ORDER BY 2 DESC")
    protections = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]

    con.close()
    return {"products": products, "total": total, "categories": categories, "brands": brands, "protections": protections}


SHOWROOM_HTML = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>MA Showroom (proto) — BHP</title>
<style>
* { box-sizing: border-box; }
body { font-family: system-ui, sans-serif; margin: 0; background: #f9fafb; color: #111827; }
header { background: #1e40af; color: #fff; padding: 1em 1.5em; display: flex; justify-content: space-between; align-items: center; }
header h1 { margin: 0; font-size: 1.2em; }
header .badge { background: #fbbf24; color: #78350f; padding: 0.2em 0.6em; border-radius: 4px; font-size: 0.75em; font-weight: bold; }
.wrap { display: grid; grid-template-columns: 240px 1fr; gap: 1.5em; padding: 1.5em; max-width: 1400px; margin: 0 auto; }
aside { background: #fff; padding: 1em; border-radius: 8px; border: 1px solid #e5e7eb; height: fit-content; position: sticky; top: 1em; }
aside h3 { margin: 0 0 0.5em; font-size: 0.85em; text-transform: uppercase; color: #6b7280; letter-spacing: 0.05em; }
aside a { display: block; padding: 0.3em 0.5em; color: #374151; text-decoration: none; border-radius: 4px; font-size: 0.9em; }
aside a:hover, aside a.active { background: #dbeafe; color: #1e40af; font-weight: bold; }
aside a .c { color: #9ca3af; font-size: 0.8em; float: right; }
main .searchbar { background: #fff; padding: 0.8em 1em; border-radius: 8px; border: 1px solid #e5e7eb; margin-bottom: 1em; display: flex; gap: 0.5em; }
main .searchbar input { flex: 1; padding: 0.5em; border: 1px solid #d1d5db; border-radius: 4px; font-size: 0.9em; }
main .searchbar button { padding: 0.5em 1.2em; background: #1e40af; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.stats { color: #6b7280; font-size: 0.85em; margin-bottom: 1em; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 1em; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 1em; display: flex; flex-direction: column; }
.card .sku { font-family: ui-monospace, monospace; color: #6b7280; font-size: 0.75em; margin-bottom: 0.3em; }
.card .name { font-weight: 600; color: #111827; margin-bottom: 0.3em; font-size: 0.95em; }
.card .meta { color: #6b7280; font-size: 0.8em; margin-bottom: 0.6em; }
.card .price { color: #059669; font-size: 1.2em; font-weight: bold; margin-bottom: 0.6em; }
.card .price .base { color: #9ca3af; font-size: 0.7em; font-weight: normal; display: block; margin-top: 0.2em; }
.card .btn { display: block; text-align: center; padding: 0.5em; background: #fbbf24; color: #78350f; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-top: auto; }
.card .btn:hover { background: #f59e0b; }
.pager { text-align: center; margin: 2em 0; }
.pager a, .pager span { display: inline-block; margin: 0 0.3em; padding: 0.4em 0.8em; background: #fff; border: 1px solid #d1d5db; border-radius: 4px; color: #374151; text-decoration: none; }
.pager .now { background: #1e40af; color: #fff; border-color: #1e40af; }
.footer { text-align: center; color: #9ca3af; font-size: 0.8em; padding: 2em; }
</style>
</head>
<body>
<header>
  <h1>MA Showroom <span class="badge">PROTOTYP</span></h1>
  <div style="font-size: 0.85em; opacity: 0.9;">źródło: Rawpol PLN-A · {{ snapshot }} · {{ total_all }} produktów</div>
</header>

<div class="wrap">
<aside>
  <h3>Co chronisz (oś A · Lira)</h3>
  <a href="/showroom" class="{{ 'active' if not protection else '' }}">Wszystko <span class="c">{{ total_all }}</span></a>
  {% for p in protections %}
  <a href="/showroom?protection={{ p.name|urlencode }}" class="{{ 'active' if protection == p.name else '' }}">Ochrona {{ p.name|replace('_', ' ') }} <span class="c">{{ p.count }}</span></a>
  {% endfor %}
  <h3 style="margin-top: 1em;">Kategoria PKWiU</h3>
  {% for c in categories %}
  <a href="/showroom?category={{ c.name|urlencode }}" class="{{ 'active' if category == c.name else '' }}">{{ c.name }} <span class="c">{{ c.count }}</span></a>
  {% endfor %}
  <h3 style="margin-top: 1em;">Marki (top)</h3>
  {% for b in brands[:10] %}
  <a href="/showroom?brand={{ b.name|urlencode }}" class="{{ 'active' if brand == b.name else '' }}">{{ b.name }} <span class="c">{{ b.count }}</span></a>
  {% endfor %}
</aside>

<main>
  <form class="searchbar" method="get" action="/showroom">
    <input name="search" placeholder="Szukaj po SKU lub symbolu (np. ALASKA, 3M)…" value="{{ search }}">
    {% if category %}<input type="hidden" name="category" value="{{ category }}">{% endif %}
    {% if brand %}<input type="hidden" name="brand" value="{{ brand }}">{% endif %}
    <button type="submit">Szukaj</button>
  </form>

  <div class="stats">
    {% if category %}Kategoria: <b>{{ category }}</b> · {% endif %}
    {% if brand %}Marka: <b>{{ brand }}</b> · {% endif %}
    {% if search %}Fraza: <b>{{ search }}</b> · {% endif %}
    Znaleziono <b>{{ total }}</b> produktów. Pokazuję {{ offset+1 }}–{{ offset + products|length }}.
  </div>

  <div class="grid">
    {% for p in products %}
    <div class="card">
      <div class="sku">{{ p.sku }}{% if p.ean %} · EAN {{ p.ean }}{% endif %}</div>
      <div class="name">{{ p.variant_base or p.symbol or p.sku }}{% if p.variant_size %} · rozmiar <b>{{ p.variant_size }}</b>{% endif %}</div>
      <div class="meta">
        {{ p.brand }} · {{ p.category }}{% if p.version %} · {{ p.version }}{% endif %}
        {% if p.protection_tag %}<br><span style="color:#059669;">✓ ochrona {{ p.protection_tag|replace('_',' ') }}</span>{% endif %}
      </div>
      <div class="price">
        {{ "%.2f"|format(p.price_net * markup) }} PLN <span style="font-size:0.7em; color: #9ca3af;">netto z marżą</span>
        <span class="base">
          brutto MA: {{ "%.2f"|format(p.price_net * markup * (1.0 + p.vat_rate)) }} PLN ({{ (p.vat_rate * 100)|round(0)|int }}% VAT)<br>
          baza Rawpol: {{ "%.2f"|format(p.price_net) }} netto · {{ "%.2f"|format(p.price_gross) }} brutto · marża {{ (markup * 100 - 100)|round(0)|int }}%
        </span>
      </div>
      <a class="btn" href="{{ p.deep_link }}" target="_blank" rel="noopener">Karta artBHP ↗</a>
    </div>
    {% else %}
    <div style="grid-column: 1/-1; text-align: center; padding: 3em; color: #9ca3af;">Brak produktów w tym filtrze.</div>
    {% endfor %}
  </div>

  <div class="pager">
    {% if offset > 0 %}<a href="?{{ qs_prev }}">← poprzednia</a>{% endif %}
    <span class="now">strona {{ page }}/{{ pages }}</span>
    {% if offset + products|length < total %}<a href="?{{ qs_next }}">następna →</a>{% endif %}
  </div>
</main>
</div>

<div class="footer">
  MA Showroom prototyp v0.1 · <a href="/">← panel budowy</a> · deep_link = podgląd karty artBHP (kanał pomocniczy); zamówienia pójdą przez zapytanie ofertowe MA (OFFLINE_INVOICE)
</div>
</body>
</html>"""


@app.route("/showroom")
def showroom():
    from urllib.parse import urlencode
    category = request.args.get("category", "").strip()
    brand = request.args.get("brand", "").strip()
    protection = request.args.get("protection", "").strip()
    search = request.args.get("search", "").strip()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 60
    offset = (page - 1) * per_page
    try:
        markup = float(request.args.get("markup", "1.35"))
    except ValueError:
        markup = 1.35

    data = _showroom_query(category=category, search=search, brand=brand, protection=protection, limit=per_page, offset=offset)
    pages = max(1, (data["total"] + per_page - 1) // per_page)

    base_qs = {}
    if category: base_qs["category"] = category
    if brand: base_qs["brand"] = brand
    if protection: base_qs["protection"] = protection
    if search: base_qs["search"] = search
    if markup != 1.35: base_qs["markup"] = markup

    qs_prev = urlencode({**base_qs, "page": page - 1}) if page > 1 else ""
    qs_next = urlencode({**base_qs, "page": page + 1}) if page < pages else ""

    total_all = sum(c["count"] for c in data["categories"])
    snapshot = ""
    if RAWPOL_DB.exists():
        try:
            con = sqlite3.connect(str(RAWPOL_DB))
            row = con.execute("SELECT snapshot_date, group_name FROM imports ORDER BY id DESC LIMIT 1").fetchone()
            if row: snapshot = f"{row[1]} · {row[0]}"
            con.close()
        except Exception: pass

    return render_template_string(
        SHOWROOM_HTML,
        products=data["products"], total=data["total"], total_all=total_all,
        categories=data["categories"], brands=data["brands"], protections=data["protections"],
        category=category, brand=brand, protection=protection, search=search,
        offset=offset, page=page, pages=pages,
        qs_prev=qs_prev, qs_next=qs_next,
        markup=markup, snapshot=snapshot,
    )


# ---------------- Bundle presets ----------------

from api.bundle_presets import (
    ROLE_BUNDLES, list_bundles, get_bundle, list_topics,
    list_lira_bundles, get_lira_bundle,
)


def _bundle_slot_candidates(protection: str, search: str, limit: int = 6) -> list[dict]:
    if not RAWPOL_DB.exists():
        return []
    con = sqlite3.connect(str(RAWPOL_DB))
    con.row_factory = sqlite3.Row
    where = ["price_net > 0"]
    params: list = []
    if protection:
        where.append("protection_tag = ?")
        params.append(protection)
    if search:
        where.append("(sku LIKE ? OR symbol LIKE ? OR variant_base LIKE ?)")
        s = f"%{search}%"
        params.extend([s, s, s])
    q = f"""SELECT sku, symbol, brand, price_net, price_gross, vat_rate,
                   variant_base, variant_size, protection_tag, deep_link
            FROM products WHERE {" AND ".join(where)}
            ORDER BY brand, sku LIMIT ?"""
    rows = [dict(r) for r in con.execute(q, params + [limit]).fetchall()]
    con.close()
    return rows


BUNDLES_INDEX_HTML = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>MA Bundles — kompletne zestawy BHP</title>
<style>
body { font-family: system-ui, sans-serif; margin: 0; background: #f9fafb; color: #111827; }
header { background: #1e40af; color: #fff; padding: 1em 1.5em; }
header h1 { margin: 0; font-size: 1.3em; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 2em 1.5em; }
.intro { color: #4b5563; margin-bottom: 2em; font-size: 0.95em; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1.5em; }
.bundle { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 1.5em; text-decoration: none; color: inherit; transition: all 0.15s; }
.bundle:hover { border-color: #1e40af; transform: translateY(-2px); box-shadow: 0 8px 24px rgba(30,64,175,0.08); }
.bundle .icon { font-size: 2.5em; margin-bottom: 0.3em; }
.bundle h3 { margin: 0 0 0.3em; color: #1e40af; }
.bundle .sub { color: #6b7280; font-size: 0.9em; margin-bottom: 0.8em; }
.bundle .slots { color: #4b5563; font-size: 0.85em; }
.footer { text-align: center; color: #9ca3af; font-size: 0.85em; padding: 2em; }
</style>
</head>
<body>
<header><h1>MA Bundles · kompletne zestawy BHP dla ról</h1></header>
<div class="wrap">
<div class="intro">Kierownik budowy wysyła ekipie link do konkretnego zestawu — każdy pracownik wybiera swój rozmiar i dokłada do zapytania ofertowego (artBHP tylko jako podgląd karty). Killer B2B: jedna paczka zamiast listy SKU.</div>
<div class="grid">
{% for b in bundles %}
<a class="bundle" href="/bundle/{{ b.role }}">
  <div class="icon">{{ b.icon }}</div>
  <h3>{{ b.title }}</h3>
  <div class="sub">{{ b.subtitle }}</div>
  <div class="slots">{{ b.slots|length }} elementów · sytuacja: <b>{{ b.situation_tag }}</b></div>
</a>
{% endfor %}
</div>
</div>
<div class="footer"><a href="/showroom">← Showroom</a> · <a href="/">← Panel budowy</a></div>
</body>
</html>"""


BUNDLE_DETAIL_HTML = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>{{ bundle.title }} — MA Bundle</title>
<style>
body { font-family: system-ui, sans-serif; margin: 0; background: #f9fafb; color: #111827; }
header { background: #1e40af; color: #fff; padding: 1em 1.5em; display: flex; justify-content: space-between; align-items: center; }
header h1 { margin: 0; font-size: 1.2em; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 2em 1.5em; }
.intro { color: #4b5563; margin-bottom: 2em; display: flex; gap: 1em; align-items: center; }
.intro .icon { font-size: 3em; }
.slot { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 1.2em; margin-bottom: 1em; }
.slot h3 { margin: 0 0 0.8em; color: #1e40af; display: flex; justify-content: space-between; align-items: baseline; }
.slot h3 .filter { font-size: 0.7em; color: #6b7280; font-weight: normal; }
.candidates { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.8em; }
.cand { background: #f3f4f6; border-radius: 6px; padding: 0.8em; font-size: 0.85em; }
.cand .sku { color: #6b7280; font-size: 0.75em; font-family: monospace; }
.cand .name { font-weight: bold; color: #111827; margin: 0.2em 0; }
.cand .brand { color: #6b7280; font-size: 0.8em; }
.cand .price { color: #059669; font-weight: bold; margin: 0.3em 0; }
.cand a { display: block; text-align: center; padding: 0.4em; background: #fbbf24; color: #78350f; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 0.8em; margin-top: 0.4em; }
.empty { color: #9ca3af; font-style: italic; }
.footer { text-align: center; color: #9ca3af; font-size: 0.85em; padding: 2em; }
</style>
</head>
<body>
<header><h1>{{ bundle.title }}</h1><div><a href="/bundles" style="color:#fbbf24;text-decoration:none;">← wszystkie zestawy</a></div></header>
<div class="wrap">
<div class="intro">
  <div class="icon">{{ bundle.icon }}</div>
  <div>
    <div style="font-size: 1.1em; color: #111827;">{{ bundle.subtitle }}</div>
    <div style="color: #6b7280; margin-top: 0.3em;">Zestaw {{ bundle.slots|length }} elementów. Poniżej propozycje z bazy {{ total_products }} produktów Rawpol.</div>
  </div>
</div>

{% for slot in slots %}
<div class="slot">
  <h3>
    {{ slot.label }}
    <span class="filter">filtr: ochrona {{ slot.protection|replace('_',' ') }}{% if slot.search %} + fraza „{{ slot.search }}"{% endif %}</span>
  </h3>
  {% if slot.candidates %}
  <div class="candidates">
    {% for c in slot.candidates %}
    <div class="cand">
      <div class="sku">{{ c.sku }}{% if c.variant_size %} · {{ c.variant_size }}{% endif %}</div>
      <div class="name">{{ c.variant_base or c.symbol or c.sku }}</div>
      <div class="brand">{{ c.brand }}</div>
      <div class="price">{{ "%.2f"|format(c.price_net * markup) }} PLN netto</div>
      <a href="{{ c.deep_link }}" target="_blank" rel="noopener">Karta artBHP ↗</a>
    </div>
    {% endfor %}
  </div>
  <div style="margin-top: 0.6em; font-size: 0.85em;">
    <a href="/showroom?protection={{ slot.protection }}{% if slot.search %}&search={{ slot.search }}{% endif %}" style="color: #1e40af;">Zobacz wszystkie kandydatów w showroom →</a>
  </div>
  {% else %}
  <div class="empty">Brak kandydatów spełniających filtry — może trzeba dopracować mapping w bazie.</div>
  {% endif %}
</div>
{% endfor %}

</div>
<div class="footer"><a href="/bundles">← wszystkie zestawy</a> · <a href="/showroom">Showroom</a> · <a href="/">Panel budowy</a></div>
</body>
</html>"""


@app.route("/bundles")
def bundles_index():
    """Nowa struktura Lira v0.1 — required/recommended osobno."""
    bundles = list_lira_bundles()
    for b in bundles:
        b["slots"] = b["required"] + b["recommended"]  # legacy field dla index HTML
        b["situation_tag"] = ",".join(b["situation_tags"][:2])
    return render_template_string(BUNDLES_INDEX_HTML, bundles=bundles)


DISCOVER_HTML = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>MA Odkryj — discovery boards BHP</title>
<style>
body { font-family: system-ui, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
header { background: #020617; padding: 1.5em; text-align: center; border-bottom: 1px solid #1e293b; }
header h1 { margin: 0; font-size: 1.5em; color: #f1f5f9; }
header .sub { color: #94a3b8; font-size: 0.9em; margin-top: 0.3em; }
.wrap { max-width: 1400px; margin: 0 auto; padding: 2em 1.5em; }
.intro { color: #94a3b8; margin-bottom: 2em; font-size: 0.95em; text-align: center; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1.2em; }
.topic { border-radius: 16px; padding: 2em 1.5em; text-decoration: none; color: #fff; transition: all 0.2s; position: relative; overflow: hidden; min-height: 180px; display: flex; flex-direction: column; justify-content: space-between; }
.topic:hover { transform: translateY(-4px) scale(1.02); box-shadow: 0 12px 32px rgba(0,0,0,0.4); }
.topic .icon { font-size: 3em; }
.topic h3 { margin: 0.5em 0 0.2em; font-size: 1.2em; }
.topic .sub { font-size: 0.85em; opacity: 0.9; }
.topic .cta { margin-top: 1em; font-size: 0.8em; opacity: 0.8; font-weight: bold; text-transform: uppercase; letter-spacing: 0.05em; }
.nav { text-align: center; padding: 2em; color: #64748b; font-size: 0.85em; }
.nav a { color: #94a3b8; margin: 0 0.5em; text-decoration: none; }
.nav a:hover { color: #f1f5f9; }
</style>
</head>
<body>
<header>
  <h1>MA · Odkryj</h1>
  <div class="sub">Tematy zamiast drzewa kategorii · feed nigdy się nie kończy</div>
</header>
<div class="wrap">
<div class="intro">Wybierz temat który Cię interesuje — otworzy się showroom z gotowymi filtrami. Tematy rotują sezonowo.</div>
<div class="grid">
{% for t in topics %}
<a class="topic" href="/showroom?{{ t.qs }}" style="background: linear-gradient(135deg, {{ t.color }}, {{ t.color }}dd);">
  <div>
    <div class="icon">{{ t.icon }}</div>
    <h3>{{ t.title }}</h3>
    <div class="sub">{{ t.subtitle }}</div>
  </div>
  <div class="cta">Zobacz produkty →</div>
</a>
{% endfor %}
</div>
</div>
<div class="nav">
<a href="/showroom">Showroom</a> · <a href="/bundles">Zestawy dla ról</a> · <a href="/">Panel budowy</a>
</div>
</body>
</html>"""


@app.route("/discover")
def discover_index():
    from urllib.parse import urlencode
    topics = []
    for t in list_topics():
        qs = urlencode(t.get("url_params", {}))
        topics.append({**t, "qs": qs})
    return render_template_string(DISCOVER_HTML, topics=topics)


@app.route("/bundle/<role>")
def bundle_detail(role: str):
    """Bundle detail wg Lira v0.1: required + recommended osobno, situation_tags filter."""
    from flask import abort
    bundle = get_lira_bundle(role)
    if not bundle:
        abort(404)
    try:
        markup = float(request.args.get("markup", "1.35"))
    except ValueError:
        markup = 1.35

    # Dla każdego slotu — pobierz kandydatów; jeśli mamy situation_tags, dodatkowo filtruj po nich
    situation_filter = bundle.get("situation_tags", [])

    def slot_with_candidates(slot: dict) -> dict:
        cands = _bundle_slot_candidates_lira(slot["protection"], situation_filter, limit=6)
        return {**slot, "candidates": cands, "search": ""}

    required = [slot_with_candidates(s) for s in bundle["required_slots"]]
    recommended = [slot_with_candidates(s) for s in bundle["recommended_slots"]]

    total_products = 0
    if RAWPOL_DB.exists():
        con = sqlite3.connect(str(RAWPOL_DB))
        total_products = con.execute("SELECT COUNT(*) FROM products WHERE price_net > 0").fetchone()[0]
        con.close()

    return render_template_string(
        BUNDLE_DETAIL_LIRA_HTML,
        bundle=bundle, required=required, recommended=recommended,
        markup=markup, total_products=total_products,
    )


def _bundle_slot_candidates_lira(protection: str, situation_tags: list, limit: int = 6) -> list[dict]:
    """Kandydaci: filtr po protection + preferuje pasujące do wybranych situation_tags."""
    if not RAWPOL_DB.exists():
        return []
    con = sqlite3.connect(str(RAWPOL_DB))
    con.row_factory = sqlite3.Row

    # Kolejność parametrów musi zgadzać się z kolejnością ? w SQL:
    # najpierw ? w CASE WHEN (SELECT), potem ? w WHERE, na końcu LIMIT ?
    situation_boost = ""
    situation_params: list = []
    order = "brand, sku"
    if situation_tags:
        situation_conds = " OR ".join(["situation_tags LIKE ?" for _ in situation_tags])
        situation_boost = f", CASE WHEN ({situation_conds}) THEN 1 ELSE 0 END AS sit_match"
        situation_params = [f"%{s}%" for s in situation_tags]
        order = "sit_match DESC, brand, sku"

    q = f"""SELECT sku, symbol, brand, price_net, price_gross, vat_rate,
                   variant_base, variant_size, protection_tag, situation_tags, deep_link{situation_boost}
            FROM products WHERE price_net > 0 AND protection_tag = ?
            ORDER BY {order} LIMIT ?"""
    params = situation_params + [protection, limit]
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    con.close()
    return rows


BUNDLE_DETAIL_LIRA_HTML = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>{{ bundle.title }} — MA Bundle (Lira v0.1)</title>
<style>
body { font-family: system-ui, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
header { background: #020617; color: #f1f5f9; padding: 1.2em 1.5em; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e293b; }
header h1 { margin: 0; font-size: 1.3em; }
header a { color: #fbbf24; text-decoration: none; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 2em 1.5em; }
.intro { display: flex; gap: 1em; align-items: center; margin-bottom: 2em; padding: 1.5em; background: #1e293b; border-radius: 12px; }
.intro .icon { font-size: 3em; }
.situation-tags { margin-top: 0.5em; }
.tag { display: inline-block; background: #334155; color: #cbd5e1; padding: 0.2em 0.6em; border-radius: 12px; font-size: 0.75em; margin-right: 0.4em; }
.section-title { font-size: 1.1em; color: #f1f5f9; margin: 2em 0 1em; padding-bottom: 0.5em; border-bottom: 1px solid #334155; }
.section-title .badge { background: #dc2626; color: #fff; padding: 0.1em 0.6em; border-radius: 4px; font-size: 0.7em; margin-left: 0.5em; vertical-align: middle; text-transform: uppercase; }
.section-title .badge.rec { background: #059669; }
.slot { background: #1e293b; border-radius: 8px; padding: 1.2em; margin-bottom: 1em; border-left: 3px solid #dc2626; }
.slot.recommended { border-left-color: #059669; }
.slot h3 { margin: 0 0 0.8em; color: #f1f5f9; }
.candidates { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.8em; }
.cand { background: #0f172a; border-radius: 6px; padding: 0.8em; font-size: 0.85em; border: 1px solid #334155; }
.cand .sku { color: #64748b; font-size: 0.75em; font-family: monospace; }
.cand .name { font-weight: bold; color: #e2e8f0; margin: 0.2em 0; }
.cand .brand { color: #94a3b8; font-size: 0.8em; }
.cand .sit-match { color: #10b981; font-size: 0.7em; }
.cand .price { color: #fbbf24; font-weight: bold; margin: 0.3em 0; }
.cand a { display: block; text-align: center; padding: 0.4em; background: #fbbf24; color: #78350f; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 0.8em; margin-top: 0.4em; }
.empty { color: #64748b; font-style: italic; }
.footer { text-align: center; color: #64748b; font-size: 0.85em; padding: 2em; }
.footer a { color: #94a3b8; margin: 0 0.5em; text-decoration: none; }
</style>
</head>
<body>
<header><h1>{{ bundle.title }}</h1><div><a href="/bundles">← wszystkie zestawy</a></div></header>
<div class="wrap">
<div class="intro">
  <div class="icon">{{ bundle.icon }}</div>
  <div>
    <div style="font-size: 1.15em; color: #f1f5f9;">Zestaw {{ required|length }} wymaganych + {{ recommended|length }} zalecanych elementów</div>
    <div class="situation-tags">
      {% for st in bundle.situation_tags %}<span class="tag">🏷️ {{ st|replace('_',' ') }}</span>{% endfor %}
    </div>
  </div>
</div>

<div class="section-title">Wymagane <span class="badge">REQUIRED</span></div>
{% for slot in required %}
<div class="slot">
  <h3>{{ slot.label }}</h3>
  {% if slot.candidates %}
  <div class="candidates">
    {% for c in slot.candidates %}
    <div class="cand">
      <div class="sku">{{ c.sku }}{% if c.variant_size %} · {{ c.variant_size }}{% endif %}</div>
      <div class="name">{{ c.variant_base or c.symbol or c.sku }}</div>
      <div class="brand">{{ c.brand }}</div>
      {% if c.sit_match %}<div class="sit-match">✓ pasuje do sytuacji</div>{% endif %}
      <div class="price">{{ "%.2f"|format(c.price_net * markup) }} PLN netto</div>
      <a href="{{ c.deep_link }}" target="_blank" rel="noopener">Karta artBHP ↗</a>
    </div>
    {% endfor %}
  </div>
  <div style="margin-top: 0.6em; font-size: 0.85em;">
    <a href="/showroom?protection={{ slot.protection }}" style="color: #fbbf24;">Zobacz wszystkie ({{ slot.label|lower }}) →</a>
  </div>
  {% else %}
  <div class="empty">Brak kandydatów — mapping wymaga korekty.</div>
  {% endif %}
</div>
{% endfor %}

{% if recommended %}
<div class="section-title">Zalecane <span class="badge rec">RECOMMENDED</span></div>
{% for slot in recommended %}
<div class="slot recommended">
  <h3>{{ slot.label }}</h3>
  {% if slot.candidates %}
  <div class="candidates">
    {% for c in slot.candidates %}
    <div class="cand">
      <div class="sku">{{ c.sku }}{% if c.variant_size %} · {{ c.variant_size }}{% endif %}</div>
      <div class="name">{{ c.variant_base or c.symbol or c.sku }}</div>
      <div class="brand">{{ c.brand }}</div>
      {% if c.sit_match %}<div class="sit-match">✓ pasuje do sytuacji</div>{% endif %}
      <div class="price">{{ "%.2f"|format(c.price_net * markup) }} PLN netto</div>
      <a href="{{ c.deep_link }}" target="_blank" rel="noopener">Karta artBHP ↗</a>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">Brak kandydatów — mapping wymaga korekty.</div>
  {% endif %}
</div>
{% endfor %}
{% endif %}

</div>
<div class="footer">
<a href="/bundles">← zestawy</a> · <a href="/discover">discover</a> · <a href="/showroom">showroom</a> · <a href="/">panel</a>
<div style="margin-top:0.5em;font-size:0.75em;">bundle wg <b>Lira v0.1</b> · {{ total_products }} produktów w bazie</div>
</div>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9225, debug=False)
