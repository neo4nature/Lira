"""Bundle presets per rola — killer B2B feature MA Marketplace (Workwear Pilot).

Predefiniowane paczki `spawacz M/L/XL`, `elektryk`, `magazynier`,
`gastronomia` — jeden przycisk `zapytaj o cały komplet`.

Flow (decyzja Neo+Lira 2026-07-05, bez zależności od artBHP):
    bundle → zapytanie ofertowe B2B → wątek /comm (MA) → oferta
    → faktura offline (OFFLINE_INVOICE) → potwierdzenie → event chain.
MA zapisuje status procesu (quote_created ... payment_confirmed),
dane klienta i faktury zostają poza chainem.

Struktura: preset = lista slotów. Każdy slot ma etykietę + filtr zapytania
(protection_tag + fraza) → showroom pokazuje kandydatów, user wybiera
konkretny SKU/rozmiar, dodaje do bundle.

Data-driven — cała konfiguracja w tym module, żeby Lira mogła
edytować/rozszerzać bez ruszania endpointu.
"""
from __future__ import annotations
from typing import Optional


ROLE_BUNDLES: dict = {
    "spawacz": {
        "title": "Zestaw spawacza",
        "subtitle": "Ochrona przy pracach spawalniczych",
        "icon": "🔥",
        "situation_tag": "spawacz",
        "slots": [
            {"label": "Przyłbica spawalnicza", "protection": "oczu_i_twarzy", "search": "spaw"},
            {"label": "Kombinezon lub fartuch spawalniczy", "protection": "ciała", "search": "spaw"},
            {"label": "Rękawice spawalnicze skórzane", "protection": "rąk", "search": "spaw"},
            {"label": "Obuwie z podnoskiem S3", "protection": "nóg", "search": ""},
            {"label": "Ochronniki słuchu", "protection": "słuchu", "search": ""},
        ],
    },
    "elektryk": {
        "title": "Zestaw elektryka",
        "subtitle": "Ochrona przy pracach pod napięciem i instalacyjnych",
        "icon": "⚡",
        "situation_tag": "elektryk",
        "slots": [
            {"label": "Rękawice dielektryczne", "protection": "rąk", "search": "diel"},
            {"label": "Kask elektroizolacyjny", "protection": "głowy", "search": ""},
            {"label": "Okulary ochronne", "protection": "oczu_i_twarzy", "search": ""},
            {"label": "Obuwie dielektryczne S3", "protection": "nóg", "search": "diel"},
            {"label": "Ubranie robocze", "protection": "ciała", "search": ""},
        ],
    },
    "budowlaniec": {
        "title": "Zestaw pracownika budowy",
        "subtitle": "Uniwersalny komplet BHP na budowę",
        "icon": "🏗️",
        "situation_tag": "budowa",
        "slots": [
            {"label": "Kask ochronny", "protection": "głowy", "search": ""},
            {"label": "Rękawice robocze", "protection": "rąk", "search": ""},
            {"label": "Kamizelka ostrzegawcza", "protection": "ciała", "search": "ostrz"},
            {"label": "Obuwie robocze S3", "protection": "nóg", "search": ""},
            {"label": "Ochronniki słuchu", "protection": "słuchu", "search": ""},
            {"label": "Okulary ochronne", "protection": "oczu_i_twarzy", "search": ""},
        ],
    },
    "magazynier": {
        "title": "Zestaw magazyniera",
        "subtitle": "Komfort i ochrona w magazynie",
        "icon": "📦",
        "situation_tag": "magazyn",
        "slots": [
            {"label": "Obuwie robocze antypoślizgowe", "protection": "nóg", "search": ""},
            {"label": "Rękawice do prac magazynowych", "protection": "rąk", "search": ""},
            {"label": "Ubranie robocze", "protection": "ciała", "search": ""},
            {"label": "Kamizelka odblaskowa", "protection": "ciała", "search": "odbla"},
        ],
    },
    "gastronomia": {
        "title": "Zestaw dla gastronomii",
        "subtitle": "Higiena i BHP w kuchni/restauracji",
        "icon": "👨‍🍳",
        "situation_tag": "gastronomia",
        "slots": [
            {"label": "Ubranie kucharza", "protection": "ciała", "search": "kuchar"},
            {"label": "Rękawice jednorazowe", "protection": "rąk", "search": "jedno"},
            {"label": "Obuwie antypoślizgowe białe", "protection": "nóg", "search": ""},
            {"label": "Fartuch", "protection": "ciała", "search": "fartu"},
            {"label": "Środki higieny", "protection": "higiena", "search": ""},
        ],
    },
    "zima_na_budowie": {
        "title": "Zima 2026 na budowie",
        "subtitle": "Ocieplenie + ochrona przed niską temperaturą",
        "icon": "❄️",
        "situation_tag": "zima",
        "slots": [
            {"label": "Kurtka ocieplana", "protection": "ciała", "search": "ALASKA"},
            {"label": "Spodnie ocieplane", "protection": "ciała", "search": "zim"},
            {"label": "Rękawice ocieplane", "protection": "rąk", "search": "zim"},
            {"label": "Buty zimowe S3 CI", "protection": "nóg", "search": "zim"},
            {"label": "Czapka ocieplana", "protection": "głowy", "search": ""},
        ],
    },
    "chemik": {
        "title": "Zestaw laboratoryjny/chemik",
        "subtitle": "Ochrona przy pracach z substancjami chemicznymi",
        "icon": "🧪",
        "situation_tag": "chemik",
        "slots": [
            {"label": "Kombinezon chemoodporny", "protection": "ciała", "search": ""},
            {"label": "Rękawice nitrylowe", "protection": "rąk", "search": "nitr"},
            {"label": "Okulary chemoodporne", "protection": "oczu_i_twarzy", "search": ""},
            {"label": "Półmaska/maska z filtrem", "protection": "dróg_oddechowych", "search": ""},
            {"label": "Fartuch chemoodporny", "protection": "ciała", "search": "fart"},
        ],
    },
    "ochrona": {
        "title": "Zestaw ochroniarza",
        "subtitle": "Komplet umundurowania i akcesoriów",
        "icon": "🛡️",
        "situation_tag": "ochrona",
        "slots": [
            {"label": "Kurtka ochroniarza", "protection": "ciała", "search": ""},
            {"label": "Spodnie taktyczne", "protection": "ciała", "search": ""},
            {"label": "Obuwie taktyczne", "protection": "nóg", "search": ""},
            {"label": "Kamizelka odblaskowa", "protection": "ciała", "search": "odbla"},
            {"label": "Latarka + akcesoria", "protection": "wyposażenie_zakładów", "search": ""},
        ],
    },
}


def get_bundle(role: str) -> Optional[dict]:
    return ROLE_BUNDLES.get(role)


def list_bundles() -> list[dict]:
    return [{"role": r, **cfg} for r, cfg in ROLE_BUNDLES.items()]


# ---------------- Lira bundle presets v0.1 ----------------

import json as _json
from pathlib import Path as _Path

_LIRA_BUNDLES_CACHE: Optional[dict] = None

# Etykiety UI dla tagów ochrony (matchują showroom_models.PROTECTION_LABELS)
_TAG_LABELS = {
    "rąk": "Ochrona rąk",
    "nóg": "Ochrona nóg",
    "ciała": "Ochrona ciała",
    "głowy": "Ochrona głowy",
    "oczu_i_twarzy": "Ochrona oczu i twarzy",
    "słuchu": "Ochrona słuchu",
    "dróg_oddechowych": "Ochrona dróg oddechowych",
    "upadek_z_wysokości": "Praca na wysokości",
    "higiena": "Higiena i czystość",
    "ppoż": "PPOŻ / trudnopalne",
    "wyposażenie_zakładów": "Wyposażenie zakładów",
    "branżowe": "Produkty branżowe",
    "nadruki_opakowania": "Nadruki i opakowania",
    "nie_sklasyfikowane": "Do klasyfikacji",
}

_ROLE_ICONS = {
    "spawacz": "🔥", "elektryk": "⚡", "budowlaniec": "🏗️",
    "magazynier": "📦", "gastronomia": "👨‍🍳",
    "zima_na_budowie": "❄️", "chemik": "🧪", "ochrona": "🛡️",
}


def _load_lira_bundles_raw() -> dict:
    global _LIRA_BUNDLES_CACHE
    if _LIRA_BUNDLES_CACHE is not None:
        return _LIRA_BUNDLES_CACHE
    path = _Path(__file__).parent.parent / "integrations" / "mapping" / "bundle_presets_lira_v0_1.json"
    if not path.exists():
        _LIRA_BUNDLES_CACHE = {"roles": {}}
        return _LIRA_BUNDLES_CACHE
    with open(path, encoding="utf-8") as f:
        _LIRA_BUNDLES_CACHE = _json.load(f)
    return _LIRA_BUNDLES_CACHE


def list_lira_bundles() -> list[dict]:
    """Zwraca 8 ról Liry w strukturze użytkowej dla UI."""
    raw = _load_lira_bundles_raw()
    result = []
    for role, cfg in raw.get("roles", {}).items():
        result.append({
            "role": role,
            "title": cfg.get("label", role),
            "subtitle": f"Wymagane: {len(cfg.get('required', []))} · Zalecane: {len(cfg.get('recommended', []))}",
            "icon": _ROLE_ICONS.get(role, "🛒"),
            "required": cfg.get("required", []),
            "recommended": cfg.get("recommended", []),
            "situation_tags": cfg.get("situation_tags", []),
        })
    return result


def get_lira_bundle(role: str) -> Optional[dict]:
    raw = _load_lira_bundles_raw()
    cfg = raw.get("roles", {}).get(role)
    if not cfg:
        return None
    return {
        "role": role,
        "title": cfg.get("label", role),
        "icon": _ROLE_ICONS.get(role, "🛒"),
        "required_slots": [
            {"protection": t, "label": _TAG_LABELS.get(t, t.title()), "kind": "required"}
            for t in cfg.get("required", [])
        ],
        "recommended_slots": [
            {"protection": t, "label": _TAG_LABELS.get(t, t.title()), "kind": "recommended"}
            for t in cfg.get("recommended", [])
        ],
        "situation_tags": cfg.get("situation_tags", []),
    }


# ---------------- Discovery topics ----------------
# Pomysł 7 z Część 1: Pinterest-style tematyczne feedy zamiast drzewa kategorii.
# Homepage nie ma listy kategorii — ma karty tematów, każda z pre-set filtrami.
# Feed nigdy się nie kończy bo tematy się rotują sezonowo.

DISCOVERY_TOPICS: list = [
    {
        "slug": "zima-2026",
        "title": "Zima 2026 na budowie",
        "subtitle": "Ocieplenie i ochrona przed niską temperaturą",
        "icon": "❄️",
        "color": "#0284c7",
        "url_params": {"search": "ALASKA"},
        "season": "winter",
    },
    {
        "slug": "normy-en-iso-20345",
        "title": "Nowe normy EN ISO 20345:2022",
        "subtitle": "Obuwie robocze zgodne z aktualną normą",
        "icon": "📐",
        "color": "#7c3aed",
        "url_params": {"protection": "nóg"},
        "season": "always",
    },
    {
        "slug": "rekawice-nitrylowe",
        "title": "Rękawice nitrylowe",
        "subtitle": "Chemoodporne, elastyczne, wielobranżowe",
        "icon": "🧤",
        "color": "#2563eb",
        "url_params": {"protection": "rąk", "search": "nitr"},
        "season": "always",
    },
    {
        "slug": "ochrona-glowy",
        "title": "Kaski i ochrona głowy",
        "subtitle": "Od bumpcap po pełne kaski elektroizolacyjne",
        "icon": "⛑️",
        "color": "#dc2626",
        "url_params": {"protection": "głowy"},
        "season": "always",
    },
    {
        "slug": "bestsellery-reis",
        "title": "Bestsellery marki REIS",
        "subtitle": "Marka własna Rawpolu — najlepsza cena/jakość",
        "icon": "🏆",
        "color": "#ea580c",
        "url_params": {"brand": "ZZ"},  # najczęstszy prefix REIS
        "season": "always",
    },
    {
        "slug": "spawanie-lipiec",
        "title": "Zestaw dla spawaczy",
        "subtitle": "Przyłbice, rękawice skórzane, kombinezony",
        "icon": "🔥",
        "color": "#f59e0b",
        "url_params": {"search": "spaw"},
        "season": "always",
    },
    {
        "slug": "higiena-food",
        "title": "Higiena w gastronomii",
        "subtitle": "Rękawice jednorazowe, fartuchy, środki myjące",
        "icon": "🧼",
        "color": "#059669",
        "url_params": {"protection": "higiena"},
        "season": "always",
    },
    {
        "slug": "do-50-zl",
        "title": "Wejście w BHP · do 50 zł",
        "subtitle": "Podstawowe produkty w cenie startowej",
        "icon": "💰",
        "color": "#65a30d",
        "url_params": {"category": "Odzież robocza"},
        "season": "always",
    },
    {
        "slug": "3m-professional",
        "title": "3M · profesjonalna ochrona",
        "subtitle": "Filtry, adaptery, słuchawki, uszczelki",
        "icon": "🎯",
        "color": "#0891b2",
        "url_params": {"brand": "3M"},
        "season": "always",
    },
    {
        "slug": "ochrona-oczu",
        "title": "Ochrona oczu i twarzy",
        "subtitle": "Okulary, gogle, przyłbice",
        "icon": "👓",
        "color": "#9333ea",
        "url_params": {"protection": "oczu_i_twarzy"},
        "season": "always",
    },
    {
        "slug": "obuwie-s3",
        "title": "Obuwie robocze S3",
        "subtitle": "Podnosek + wkładka antyprzebiciowa + wodoodporność",
        "icon": "👢",
        "color": "#1e40af",
        "url_params": {"protection": "nóg"},
        "season": "always",
    },
    {
        "slug": "chemia-labor",
        "title": "Laboratorium i chemia",
        "subtitle": "Kombinezony, rękawice, maski z filtrem",
        "icon": "🧪",
        "color": "#0d9488",
        "url_params": {"protection": "dróg_oddechowych"},
        "season": "always",
    },
]


def list_topics() -> list[dict]:
    return DISCOVERY_TOPICS


def get_topic(slug: str) -> Optional[dict]:
    return next((t for t in DISCOVERY_TOPICS if t["slug"] == slug), None)
