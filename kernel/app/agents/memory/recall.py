from __future__ import annotations
from memory_backend import get as backend_get
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
from xian.chat_busy import is_chat_busy as _is_chat_busy
from agents.memory.retrieval_usage import new_retrieval_id, record_retrieval
from agents.memory.retrieval_usage import OUTCOMES as _USAGE_OUTCOMES
_REST_URL = os.getenv('LIRA_MEMORY_REST', 'http://127.0.0.1:8000/memory/search')
_REST_TIMEOUT = float(os.getenv('LIRA_MEMORY_REST_TIMEOUT', '30'))

def _via_rest(q: str, top_k: int, kto: str='', proba: bool=False) -> Optional[Dict[str, Any]]:
    params: Dict[str, Any] = {'q': q, 'top_k': top_k}
    if kto:
        params['actor'] = kto
    if proba:
        params['probe'] = '1'
    try:
        r = backend_get(_REST_URL, params=params, timeout=_REST_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        res = data.get('results')
        if not isinstance(res, list):
            return None
        return {'results': res, 'retrieval_id': str(data.get('retrieval_id') or ''), 'usage_observer': data.get('usage_observer') if isinstance(data.get('usage_observer'), dict) else {}}
    except Exception:
        return None

def _via_import(q: str, top_k: int) -> Optional[List[Dict[str, Any]]]:
    try:
        from agents.memory.semantic_retrieval import search as _search
    except Exception as exc:
        print(f'[recall] brak importu search(): {exc}', file=sys.stderr)
        return None
    try:
        return _search(q, top_k=top_k)
    except Exception as exc:
        print(f'[recall] search() error: {exc}', file=sys.stderr)
        return None
_TOR_BACKEND = {'REST': 'REST /memory/search -> search() (api/routes/memory.py)', 'import': 'bezposredni import search() (semantic_retrieval.py)'}

def _tor_line(source_used: str) -> str:
    backend = _TOR_BACKEND.get(source_used, f'{source_used} -> tor NIEPOTWIERDZONY')
    return f'# tor: {backend} — z QUERY_PREFIX (od 2026-08-20), BEZ progu, MIESZA zrodla. To NIE jest tor Modela (search_live + base_index.sqlite3 + LIRA_LIVE_MIN_SCORE) — liczb stad nie przenos na PA-039 ani na prog zywej pamieci.'

def _mix_line(results: List[Dict[str, Any]]) -> str:
    mix = Counter((it.get('source') or it.get('kind') or 'unknown' for it in results))
    return '# zrodla wyniku: ' + ', '.join((f'{k} x{n}' for k, n in mix.most_common()))

def _rdzenie(text: str) -> set:
    return {w[:5] for w in re.findall('\\w+', text.lower()) if len(w) >= 4}

def word_coverage(query: str, text: str) -> tuple[int, int]:
    q = _rdzenie(query)
    return (len(q & _rdzenie(text)), len(q))

def is_echo(text: str) -> bool:
    return 'memory.recall' in text

def _fmt_line(item: Dict[str, Any], query: str='') -> str:
    score = float(item.get('score', 0.0))
    kind = item.get('source') or item.get('kind') or 'unknown'
    path = item.get('path') or ''
    rid = item.get('id') or ''
    text = (item.get('text') or '').replace('\n', ' ').strip()
    if len(text) > 200:
        text = text[:197] + '...'
    date = ''
    if isinstance(rid, str) and len(rid) >= 10 and (rid[4] == '-') and (rid[7] == '-'):
        date = rid[:10]
    full = item.get('text') or ''
    hit, total = word_coverage(query, full) if query else (0, 0)
    tag = '[ECHO]' if is_echo(full) else f'[{hit}/{total}]'.ljust(6)
    return f'{tag} {score:.3f}  {kind:<14} {date:<10}  {path}\n         {text}\n         id={rid}'

def _usage_line(retrieval_id: str, kto: str='') -> str:
    trace = retrieval_id or 'TRACE_ID'
    name = kto or 'NAME'
    return f'# pokwituj: python3 -m tools.trafienie tak <początek id> --kto {name} --slad {trace}   |   python3 -m tools.trafienie nie --kto {name} --slad {trace}\n# pełna forma: python3 -m agents.memory.retrieval_usage {trace} RECORD_ID OUTCOME --actor NAME; OUTCOME={','.join(sorted(_USAGE_OUTCOMES))}'

def _observe_import(q: str, results: List[Dict[str, Any]], kto: str='', proba: bool=False) -> tuple[str, Dict[str, Any]]:
    retrieval_id = new_retrieval_id()
    try:
        observer = record_retrieval(q, results, retrieval_id=retrieval_id, actor=kto or 'recall_cli', channel='semantic_import', probe=proba)
    except Exception:
        observer = {'recorded': False, 'verified': False, 'retrieval_id': retrieval_id}
    return (retrieval_id, observer)

def _kto_lines(kto: str, proba: bool, observer: Dict[str, Any], source_used: str) -> List[str]:
    lines: List[str] = []
    if not kto:
        lines.append('# ślad anonimowy — podaj --kto IMIĘ (albo LIRA_KTO), żeby wyszukanie miało autora')
    elif observer.get('actor') != kto:
        if source_used == 'REST' and observer.get('recorded'):
            lines.append(f'# serwer pamięci nie zapisał imienia „{kto}” (proces sprzed tej zmiany) — ślad jest anonimowy, kwituj z --slad')
        else:
            lines.append(f'# ślad wyszukania nie został zapisany — imię „{kto}” nigdzie nie trafiło')
    if proba and observer.get('probe') is not True:
        lines.append('# znak próby NIE został zapisany — to wyszukanie wygląda w dzienniku jak zwykłe pytanie')
    return lines

def main() -> int:
    ap = argparse.ArgumentParser(prog='recall', description='Reviewer recall — semantic memory search')
    ap.add_argument('query', nargs='+', help='Zapytanie w cudzysłowach lub wiele słów')
    ap.add_argument('--top-k', type=int, default=5, help='ile wyników (default 5)')
    ap.add_argument('--json', action='store_true', help='wypisz surowy JSON')
    ap.add_argument('--no-rest', action='store_true', help='pomiń REST, prosto z modułu')
    ap.add_argument('--kto', default=os.environ.get('LIRA_KTO', ''), help='kto pyta (Reviewer, Lira…) — imię idzie do śladu wyszukania')
    ap.add_argument('--proba', action='store_true', default=os.environ.get('LIRA_PROBA') == '1', help='pytanie próbne (kontrola, pomiar) — ślad dostaje znak próby')
    args = ap.parse_args()
    kto = (args.kto or '').strip()
    q = ' '.join(args.query).strip()
    if not q:
        print('Puste zapytanie.', file=sys.stderr)
        return 2
    t0 = time.monotonic()
    results: Optional[List[Dict[str, Any]]] = None
    source_used = ''
    retrieval_id = ''
    observer: Dict[str, Any] = {}
    if not args.no_rest:
        rest = _via_rest(q, args.top_k, kto, args.proba)
        if rest is not None:
            results = rest['results']
            retrieval_id = rest['retrieval_id']
            observer = rest['usage_observer']
            source_used = 'REST'
    if results is None:
        results = _via_import(q, args.top_k)
        if results is not None:
            source_used = 'import'
            retrieval_id, observer = _observe_import(q, results, kto, args.proba)
    dt = time.monotonic() - t0
    if results is None:
        print(f'[recall] Blad: ani REST ani import nie zwrocily wynikow ({dt:.1f}s).', file=sys.stderr)
        return 1
    observer_state = 'verified' if observer.get('verified') else 'missing'
    if not results:
        if _is_chat_busy():
            print(f'Model mowi — sprobuj za chwile (chat_busy flag aktywna, {dt:.1f}s przez {source_used}).')
        else:
            print(f'Brak trafien dla: {q!r} ({dt:.1f}s przez {source_used}).')
        print(f'# retrieval_id={retrieval_id or 'missing'} | observer={observer_state}')
        for line in _kto_lines(kto, args.proba, observer, source_used):
            print(line)
        return 0
    if args.json:
        print(json.dumps({'query': q, 'top_k': args.top_k, 'source': source_used, 'elapsed_s': round(dt, 2), 'retrieval_fn': 'search', 'backend': _TOR_BACKEND.get(source_used, 'NIEPOTWIERDZONY'), 'query_prefix_used': True, 'score_threshold': None, 'index_mix': dict(Counter((it.get('source') or it.get('kind') or 'unknown' for it in results))), 'retrieval_id': retrieval_id or None, 'usage_observer': observer, 'asked_by': kto or None, 'actor_recorded': bool(kto) and observer.get('actor') == kto, 'probe_recorded': observer.get('probe') is True, 'usage_outcomes': sorted(_USAGE_OUTCOMES), 'usage_command': f'python3 -m agents.memory.retrieval_usage {retrieval_id or 'TRACE_ID'} RECORD_ID OUTCOME --actor NAME', 'results': [dict(it, word_coverage=list(word_coverage(q, it.get('text') or '')), echo=is_echo(it.get('text') or '')) for it in results]}, ensure_ascii=False, indent=2))
        return 0
    print(f'# recall: {q!r}  |  top_k={args.top_k}  |  {source_used}  |  {dt:.1f}s  |  n={len(results)}')
    print(f'# retrieval_id={retrieval_id or 'missing'} | observer={observer_state}')
    print(_usage_line(retrieval_id, kto))
    for line in _kto_lines(kto, args.proba, observer, source_used):
        print(line)
    print(_mix_line(results))
    print(_tor_line(source_used))
    for i, item in enumerate(results, 1):
        print(f'\n[{i}] ' + _fmt_line(item, q))
    return 0
if __name__ == '__main__':
    sys.exit(main())
