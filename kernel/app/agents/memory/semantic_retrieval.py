from __future__ import annotations
from memory_backend import post as backend_post
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import logging
import requests
from config import memory_dir, shared_context_dir
log = logging.getLogger('memory.semantic_retrieval')
try:
    from agents.memory.base_index_sqlite import search_by_vector as _base_search_by_vec
except Exception:
    _base_search_by_vec = None
try:
    from agents.memory.turmalin_cache import iter_recent as _turmalin_iter_recent
except Exception:
    _turmalin_iter_recent = None
from xian.chat_busy import is_chat_busy as _is_chat_busy
QUERY_PREFIX = 'Represent this sentence for searching relevant passages: '

def _ollama_embed(model: str, text: str, timeout_s: int=int(os.getenv('LIRA_OLLAMA_EMBED_TIMEOUT', '1800')), defer_on_chat: bool=True) -> Optional[List[float]]:
    if defer_on_chat and _is_chat_busy():
        log.info('[semantic_retrieval] defer embed: Model chat busy')
        return None
    text = text[:600]
    if os.getenv('LIRA_MEMORY_ALLOW_NETWORK') != '1' or not os.getenv('LIRA_MEMORY_EMBED_URL'):
        raise RuntimeError('Provide an embedding callback or explicitly configure the memory embedding backend')
    url = os.getenv('LIRA_MEMORY_EMBED_URL', 'http://127.0.0.1:11434/api/embed')
    payload = {'model': model, 'input': text}
    try:
        r = backend_post(url, json=payload, timeout=timeout_s)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and isinstance(data.get('embeddings'), list) and data['embeddings']:
            vec = data['embeddings'][0]
            if vec:
                return vec
        if isinstance(data, dict) and 'embedding' in data and data['embedding']:
            return data['embedding']
        if isinstance(data, dict) and 'data' in data and data['data']:
            item = data['data'][0]
            if isinstance(item, dict) and 'embedding' in item and item['embedding']:
                return item['embedding']
    except Exception:
        return None
    return None

def _cosine(a: List[float], b: List[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)

def _iter_records(index_path: Path) -> Iterable[Dict[str, Any]]:
    if not index_path.exists():
        return
    nieswieze, logiczne_zrodla = _przystawka_swiezosci()
    try:
        with index_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get('schema') == 'lira.embedding_record.v1':
                        if nieswieze and obj.get('id') in nieswieze:
                            continue
                        if not _poza_pracownia(obj):
                            continue
                        yield _z_trwala_tozsamoscia(obj, logiczne_zrodla)
                except Exception:
                    continue
    except Exception:
        return

def _przystawka_swiezosci() -> Tuple[set, Dict[str, str]]:
    try:
        from agents.memory.index_freshness import logical_source_ids as _logical_source_ids, stale_ids as _stale_ids
        return (_stale_ids(), _logical_source_ids())
    except Exception:
        return (set(), {})

def _poza_pracownia(obj: Dict[str, Any]) -> bool:
    src = obj.get('source') or {}
    sciezka = src.get('path')
    if src.get('kind') == 'file' and sciezka:
        from agents.memory.source_registry import derived_artifact_reason
        if derived_artifact_reason(Path(str(sciezka))):
            return False
    return True

def _z_trwala_tozsamoscia(obj: Dict[str, Any], logiczne_zrodla: Dict[str, str]) -> Dict[str, Any]:
    src = obj.get('source') or {}
    sciezka = src.get('path')
    logical_id = src.get('logical_source_id') or logiczne_zrodla.get(sciezka)
    if logical_id and src.get('logical_source_id') != logical_id:
        obj = {**obj, 'source': {**src, 'logical_source_id': logical_id}}
    return obj

def search_live(query: str, top_k: int=4) -> List[Dict[str, Any]]:
    q = (query or '').strip()
    if not q:
        return []
    model = os.getenv('LIRA_EMBED_MODEL', 'mxbai-embed-large:latest')
    timeout_s = int(os.getenv('LIRA_LIVE_EMBED_TIMEOUT', '25'))
    min_score = float(os.getenv('LIRA_LIVE_MIN_SCORE', '0.75'))
    qvec = _ollama_embed(model, QUERY_PREFIX + q, timeout_s=timeout_s, defer_on_chat=False)
    if qvec is None:
        return []
    merged: List[Dict[str, Any]] = []
    try:
        if _base_search_by_vec is not None:
            merged.extend(_base_search_by_vec(qvec, top_k=top_k))
    except Exception as exc:
        log.warning('[semantic_live] base_index search failed: %s', exc)
    try:
        if _turmalin_iter_recent is not None:
            scored: List[Tuple[float, Dict[str, Any]]] = []
            cap = int(os.getenv('LIRA_LIVE_TURMALIN_MAX', '2000'))
            for rec in _turmalin_iter_recent(max_records=cap):
                vec = rec.get('vector')
                if not isinstance(vec, list) or len(vec) != len(qvec):
                    continue
                score = _cosine(qvec, vec)
                scored.append((score, {'id': rec.get('id'), 'score': round(float(score), 5), 'source': 'turmalin_cache', 'text': rec.get('text', ''), 'kind': rec.get('kind', 'temp'), 'meta': rec.get('meta') if isinstance(rec.get('meta'), dict) else {}}))
            scored.sort(key=lambda t: t[0], reverse=True)
            merged.extend([d for _, d in scored[:top_k]])
    except Exception as exc:
        log.warning('[semantic_live] turmalin_cache search failed: %s', exc)
    best: Dict[str, Dict[str, Any]] = {}
    for item in merged:
        rid = str(item.get('id') or '')
        if not rid:
            continue
        if rid not in best or float(item.get('score', 0)) > float(best[rid].get('score', 0)):
            best[rid] = item
    min_age_s = float(os.getenv('LIRA_LIVE_MIN_AGE_S', '1800'))
    now = time.time()
    hits = sorted((d for d in best.values() if float(d.get('score', 0)) >= min_score and (not d.get('created_ts') or now - float(d['created_ts']) >= min_age_s)), key=lambda d: float(d.get('score', 0)), reverse=True)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in hits:
        key = ' '.join((item.get('text') or '').split())[:120].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= top_k:
            break
    return out
_KARTY_ZAPAS = 6
_KARTY_MAX_FRAGMENTOW = 8

def _karty_wlaczone() -> bool:
    return os.getenv('LIRA_SOURCE_CARDS', '0').strip().lower() in ('1', 'true', 'yes', 'on')

def _klucz_zrodla(rid: str, logical_source_id: Optional[str]=None) -> str:
    if logical_source_id:
        return logical_source_id
    if rid.startswith('file:'):
        return rid.rsplit(':', 2)[0]
    czesci = rid.split(':')
    return ':'.join(czesci[:2]) if len(czesci) >= 2 else rid

def _karty_zrodel(wyniki: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    karty: Dict[str, Dict[str, Any]] = {}
    for item in wyniki:
        klucz = _klucz_zrodla(str(item.get('id') or ''), item.get('logical_source_id'))
        karta = karty.get(klucz)
        if karta is None:
            karty[klucz] = {**item, 'source_key': klucz, 'fragment_count': 1, 'fragments': []}
            continue
        karta['fragment_count'] += 1
        if len(karta['fragments']) < _KARTY_MAX_FRAGMENTOW - 1:
            karta['fragments'].append({'id': item.get('id'), 'score': item.get('score'), 'offset': item.get('offset', 0), 'text': item.get('text', '')})
    return sorted(karty.values(), key=lambda d: float(d.get('score', 0)), reverse=True)[:top_k]

def macierz_gotowa(rozgrzewaj: bool=True) -> bool:
    try:
        import numpy
        from agents.memory.macierz_indeksu import macierz as _macierz, wlaczona as _macierz_wlaczona
    except Exception:
        return True
    index_path = memory_dir() / 'embeddings' / 'embeddings_index.jsonl'
    if not _macierz_wlaczona() or not index_path.exists():
        return True
    m = _macierz()
    if m.gotowa(index_path):
        return True
    if rozgrzewaj:
        m.rozgrzej_w_tle(index_path, _poza_pracownia)
    return False

def search(query: str, top_k: int=6, *, embed=None) -> List[Dict[str, Any]]:
    q = (query or '').strip()
    if not q:
        return []
    model = os.getenv('LIRA_EMBED_MODEL', 'mxbai-embed-large:latest')
    index_path = memory_dir() / 'embeddings' / 'embeddings_index.jsonl'
    qvec = embed(QUERY_PREFIX + q) if embed is not None else _ollama_embed(model, QUERY_PREFIX + q)
    if qvec is None:
        return []
    merged: List[Dict[str, Any]] = []
    try:
        if _base_search_by_vec is not None and os.getenv('LIRA_ENABLE_BASE_INDEX', '1') == '1':
            merged.extend(_base_search_by_vec(qvec, top_k=top_k))
    except Exception as exc:
        log.warning('[semantic] base_index search failed: %s', exc)
    try:
        if _turmalin_iter_recent is not None and os.getenv('LIRA_ENABLE_TURMALIN', '1') == '1':
            cache_recs = list(_turmalin_iter_recent(max_records=int(os.getenv('LIRA_TURMALIN_SEARCH_MAX', '5000'))))
            tmp_scored: List[Tuple[float, Dict[str, Any]]] = []
            for rec in cache_recs:
                vec = rec.get('vector')
                if not isinstance(vec, list):
                    continue
                score = _cosine(qvec, vec)
                tmp_scored.append((score, {'id': rec.get('id'), 'score': round(float(score), 5), 'source': 'turmalin_cache', 'path': str(memory_dir() / 'embeddings' / 'turmalin_cache.jsonl'), 'offset': 0, 'text': rec.get('text', ''), 'model': model, 'kind': rec.get('kind', 'temp')}))
            tmp_scored.sort(key=lambda t: t[0], reverse=True)
            merged.extend([d for _, d in tmp_scored[:top_k]])
    except Exception as exc:
        log.warning('[semantic] turmalin_cache search failed: %s', exc)
    use_numpy = False
    np = None
    try:
        import numpy as _np
        np = _np
        use_numpy = True
    except Exception:
        use_numpy = False
    scored: List[Tuple[float, Dict[str, Any]]] = []
    qdim = len(qvec)
    ile_brac = top_k * _KARTY_ZAPAS if _karty_wlaczone() else top_k
    z_macierzy = None
    if use_numpy:
        try:
            from agents.memory.macierz_indeksu import macierz as _macierz, wlaczona as _macierz_wlaczona
            if _macierz_wlaczona() and index_path.exists():
                nieswieze, logiczne_zrodla = _przystawka_swiezosci()
                z_macierzy = _macierz().najlepsze(index_path, qvec, ile_brac, _poza_pracownia, nieswieze, lambda obj: _z_trwala_tozsamoscia(obj, logiczne_zrodla))
        except Exception as exc:
            log.warning('[semantic] macierz failed, pełny przegląd pliku: %s', exc)
            z_macierzy = None
    if z_macierzy is not None:
        scored = z_macierzy
    elif use_numpy and np is not None:
        qv = np.array(qvec, dtype=float)
        qn = float(np.linalg.norm(qv)) + 1e-12
        for rec in _iter_records(index_path):
            vec = rec.get('vector')
            if not isinstance(vec, list) or len(vec) != qdim:
                continue
            v = np.array(vec, dtype=float)
            dn = float(np.linalg.norm(v)) + 1e-12
            score = float(np.dot(qv, v) / (qn * dn))
            scored.append((score, rec))
    else:
        for rec in _iter_records(index_path):
            vec = rec.get('vector')
            if not isinstance(vec, list) or len(vec) != qdim:
                continue
            score = _cosine(qvec, vec)
            scored.append((score, rec))
    scored.sort(key=lambda t: t[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, rec in scored[:ile_brac]:
        src = rec.get('source', {}) or {}
        out.append({'id': rec.get('id'), 'score': round(float(score), 5), 'source': src.get('kind', 'unknown'), 'capsule_id': src.get('capsule_id'), 'path': src.get('path'), 'logical_source_id': src.get('logical_source_id'), 'offset': src.get('offset', 0), 'text': rec.get('text', ''), 'model': rec.get('model')})
    merged.extend(out)
    best: Dict[str, Dict[str, Any]] = {}
    for item in merged:
        rid = str(item.get('id') or '')
        if not rid:
            continue
        if rid not in best or float(item.get('score', 0)) > float(best[rid].get('score', 0)):
            best[rid] = item
    final = sorted(best.values(), key=lambda d: float(d.get('score', 0)), reverse=True)
    if _karty_wlaczone():
        return _karty_zrodel(final, top_k)
    return final[:top_k]
