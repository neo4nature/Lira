"""Recall CLI — jednokomendowy dostęp Soryel do pamięci semantycznej.

Użycie (z kernel/app/):
    python3 -m agents.memory.recall "kolejka kapsuł limit pamięci"
    python3 -m agents.memory.recall --top-k 8 --json "night cycle archive offload"

Strategia:
  1) Najpierw REST na http://127.0.0.1:8000/memory/search (szybciej, ma cache/pipeline).
  2) Fallback: bezpośredni import search() z semantic_retrieval.
  3) Pusty wynik + flaga chat_busy => komunikat "Xian mówi", nie pustka.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from xian.chat_busy import is_chat_busy as _is_chat_busy
_REST_URL = os.getenv("LIRA_MEMORY_REST", "http://127.0.0.1:8000/memory/search")
_REST_TIMEOUT = float(os.getenv("LIRA_MEMORY_REST_TIMEOUT", "30"))


def _via_rest(q: str, top_k: int) -> Optional[List[Dict[str, Any]]]:
    try:
        r = requests.get(_REST_URL, params={"q": q, "top_k": top_k}, timeout=_REST_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        res = data.get("results")
        return res if isinstance(res, list) else None
    except Exception:
        return None


def _via_import(q: str, top_k: int) -> Optional[List[Dict[str, Any]]]:
    try:
        from agents.memory.semantic_retrieval import search as _search
    except Exception as exc:
        print(f"[recall] brak importu search(): {exc}", file=sys.stderr)
        return None
    try:
        return _search(q, top_k=top_k)
    except Exception as exc:
        print(f"[recall] search() error: {exc}", file=sys.stderr)
        return None


def _fmt_line(item: Dict[str, Any]) -> str:
    score = float(item.get("score", 0.0))
    kind = item.get("source") or item.get("kind") or "unknown"
    path = item.get("path") or ""
    rid = item.get("id") or ""
    text = (item.get("text") or "").replace("\n", " ").strip()
    if len(text) > 200:
        text = text[:197] + "..."
    # data: prefiks id np. 2026-07-19T... — spróbuj wyciągnąć
    date = ""
    if isinstance(rid, str) and len(rid) >= 10 and rid[4] == "-" and rid[7] == "-":
        date = rid[:10]
    tag = "[SZUM]" if score < 0.55 else "     "
    return f"{tag} {score:.3f}  {kind:<14} {date:<10}  {path}\n         {text}\n         id={rid}"


def main() -> int:
    ap = argparse.ArgumentParser(prog="recall", description="Soryel recall — semantic memory search")
    ap.add_argument("query", nargs="+", help="Zapytanie w cudzysłowach lub wiele słów")
    ap.add_argument("--top-k", type=int, default=5, help="ile wyników (default 5)")
    ap.add_argument("--json", action="store_true", help="wypisz surowy JSON")
    ap.add_argument("--no-rest", action="store_true", help="pomiń REST, prosto z modułu")
    args = ap.parse_args()

    q = " ".join(args.query).strip()
    if not q:
        print("Puste zapytanie.", file=sys.stderr)
        return 2

    t0 = time.monotonic()
    results: Optional[List[Dict[str, Any]]] = None
    source_used = ""
    if not args.no_rest:
        results = _via_rest(q, args.top_k)
        if results is not None:
            source_used = "REST"
    if results is None:
        results = _via_import(q, args.top_k)
        if results is not None:
            source_used = "import"
    dt = time.monotonic() - t0

    if results is None:
        print(f"[recall] Blad: ani REST ani import nie zwrocily wynikow ({dt:.1f}s).", file=sys.stderr)
        return 1

    if not results:
        if _is_chat_busy():
            print(f"Xian mowi — sprobuj za chwile (chat_busy flag aktywna, {dt:.1f}s przez {source_used}).")
        else:
            print(f"Brak trafien dla: {q!r} ({dt:.1f}s przez {source_used}).")
        return 0

    if args.json:
        print(json.dumps({"query": q, "top_k": args.top_k, "source": source_used,
                          "elapsed_s": round(dt, 2), "results": results},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"# recall: {q!r}  |  top_k={args.top_k}  |  {source_used}  |  {dt:.1f}s  |  n={len(results)}")
    for i, item in enumerate(results, 1):
        print(f"\n[{i}] " + _fmt_line(item))
    return 0


if __name__ == "__main__":
    sys.exit(main())
