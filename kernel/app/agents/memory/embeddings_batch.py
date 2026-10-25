from __future__ import annotations
from memory_backend import post as backend_post
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
import requests
from config import memory_dir, shared_context_dir, runtime_dir
from agents.memory.memory_loader import write_json
DEFAULT_CHUNK_SIZE = 900
DEFAULT_MIN_RECHUNK_CHARS = 64
DEFAULT_MAX_RECHUNK_DEPTH = 4

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()

@dataclass(frozen=True)
class SourceReadResult:
    status: str
    text: str = ''
    total_bytes: int = 0
    reason: str = ''

@dataclass(frozen=True)
class EmbedResult:
    status: str
    vector: Optional[List[float]] = None
    reason: str = ''
    http_status: Optional[int] = None

def _read_text_result(path: Path, max_bytes: int=2000000) -> SourceReadResult:
    try:
        declared_size = path.stat().st_size
        if declared_size > max_bytes:
            return SourceReadResult('source_truncated', total_bytes=declared_size, reason=f'source has {declared_size} bytes; limit is {max_bytes}')
        data = path.read_bytes()
        if len(data) > max_bytes:
            return SourceReadResult('source_truncated', total_bytes=len(data), reason=f'source has {len(data)} bytes; limit is {max_bytes}')
        return SourceReadResult('success', data.decode('utf-8', errors='ignore'), len(data))
    except Exception as exc:
        return SourceReadResult('read_error', reason=f'{type(exc).__name__}: {exc}')

def _read_text_safe(path: Path, max_bytes: int=2000000) -> str:
    result = _read_text_result(path, max_bytes=max_bytes)
    return result.text if result.status == 'success' else ''

def _split_sentences(text: str) -> List[str]:
    import re
    parts = re.split('(?<=[.!?…])\\s+', text)
    return [p for p in parts if p.strip()]

def _hard_split(offset: int, text: str, chunk_size: int) -> Iterable[Tuple[int, str]]:
    if chunk_size <= 0:
        raise ValueError('chunk_size must be positive')
    for relative_offset in range(0, len(text), chunk_size):
        piece = text[relative_offset:relative_offset + chunk_size]
        if piece:
            yield (offset + relative_offset, piece)

def _chunk(text: str, chunk_size: int=DEFAULT_CHUNK_SIZE, overlap: int=120) -> Iterable[Tuple[int, str]]:
    if not text:
        return
    for offset, chunk_text in _chunk_pass1(text, chunk_size):
        yield from _hard_split(offset, chunk_text, chunk_size)

def _chunk_pass1(text: str, chunk_size: int) -> Iterable[Tuple[int, str]]:
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        return
    current = ''
    current_offset = 0
    pos = 0
    for para in paragraphs:
        idx = text.find(para, pos)
        if idx == -1:
            idx = pos
        if len(para) > chunk_size:
            if current.strip():
                yield (current_offset, current.strip())
                current = ''
            sentences = _split_sentences(para)
            sent_buf = ''
            sent_offset = idx
            for sent in sentences:
                if not sent_buf:
                    sent_buf = sent
                    sent_offset = idx + para.find(sent)
                elif len(sent_buf) + 1 + len(sent) <= chunk_size:
                    sent_buf = sent_buf + ' ' + sent
                else:
                    if sent_buf.strip():
                        yield (sent_offset, sent_buf.strip())
                    sent_buf = sent
                    sent_offset = idx + para.find(sent)
            if sent_buf.strip():
                yield (sent_offset, sent_buf.strip())
        elif not current:
            current = para
            current_offset = idx
        elif len(current) + 2 + len(para) <= chunk_size:
            current = current + '\n\n' + para
        else:
            if current.strip():
                yield (current_offset, current.strip())
            current = para
            current_offset = idx
        pos = idx + len(para)
    if current.strip():
        yield (current_offset, current.strip())
_EMBED_MAX_CHARS = DEFAULT_CHUNK_SIZE
from xian.chat_busy import is_chat_busy as _is_chat_busy

def _ollama_embed_result(model: str, text: str, timeout_s: int=int(os.getenv('LIRA_OLLAMA_EMBED_TIMEOUT', '1800')), max_chars: int=_EMBED_MAX_CHARS) -> EmbedResult:
    if len(text) > max_chars:
        raise ValueError(f'embedding input has {len(text)} chars; hard limit is {max_chars}')
    if _is_chat_busy():
        print('[embeddings_batch] defer embed: Model chat busy', flush=True)
        return EmbedResult('deferred_busy', reason='Model chat busy')
    url = os.getenv('LIRA_MEMORY_EMBED_URL', 'http://127.0.0.1:11434/api/embed')
    payload = {'model': model, 'input': text}
    try:
        r = backend_post(url, json=payload, timeout=timeout_s)
        if r.status_code == 400 and 'context' in (r.text or '').lower():
            return EmbedResult('context_too_long', reason=(r.text or 'input exceeds model context')[:500], http_status=400)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and isinstance(data.get('embeddings'), list) and data['embeddings']:
            vec = data['embeddings'][0]
            if vec:
                return EmbedResult('success', vector=vec)
        if isinstance(data, dict) and 'embedding' in data and data['embedding']:
            return EmbedResult('success', vector=data['embedding'])
        if isinstance(data, dict) and 'data' in data and data['data']:
            item = data['data'][0]
            if isinstance(item, dict) and 'embedding' in item and item['embedding']:
                return EmbedResult('success', vector=item['embedding'])
        return EmbedResult('invalid_response', reason='response contains no embedding')
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        return EmbedResult('http_error', reason=f'{type(exc).__name__}: {exc}', http_status=status)
    except requests.RequestException as exc:
        return EmbedResult('transport_error', reason=f'{type(exc).__name__}: {exc}')
    except (ValueError, TypeError, KeyError) as exc:
        return EmbedResult('invalid_response', reason=f'{type(exc).__name__}: {exc}')

def _ollama_embed(model: str, text: str, timeout_s: int=int(os.getenv('LIRA_OLLAMA_EMBED_TIMEOUT', '1800')), max_chars: int=_EMBED_MAX_CHARS) -> Optional[List[float]]:
    result = _ollama_embed_result(model, text, timeout_s=timeout_s, max_chars=max_chars)
    return result.vector if result.status == 'success' else None

def _adaptive_embed(model: str, text: str, *, max_chars: int, min_chars: int=DEFAULT_MIN_RECHUNK_CHARS, max_depth: int=DEFAULT_MAX_RECHUNK_DEPTH, embed_fn: Optional[Callable[..., EmbedResult]]=None) -> Dict[str, Any]:
    call = embed_fn or _ollama_embed_result
    pending: List[Tuple[int, int, int]] = [(0, len(text), 0)]
    embedded: List[Dict[str, Any]] = []
    attempts = 0
    rechunked = False
    while pending:
        start, end, depth = pending.pop(0)
        fragment = text[start:end]
        result = call(model, fragment, max_chars=max_chars)
        attempts += 1
        if result.status == 'success' and result.vector:
            embedded.append({'start': start, 'end': end, 'depth': depth, 'text': fragment, 'vector': result.vector})
            continue
        if result.status == 'context_too_long':
            if depth >= max_depth or len(fragment) < 2 * min_chars:
                return {'status': 'failed_embedding', 'reason': 'context_too_long_after_rechunk', 'detail': result.reason, 'attempts': attempts, 'rechunked': rechunked, 'fragments': []}
            midpoint = start + len(fragment) // 2
            rechunked = True
            pending[0:0] = [(start, midpoint, depth + 1), (midpoint, end, depth + 1)]
            continue
        return {'status': result.status, 'reason': result.reason, 'http_status': result.http_status, 'attempts': attempts, 'rechunked': rechunked, 'fragments': []}
    embedded.sort(key=lambda item: item['start'])
    return {'status': 'success', 'attempts': attempts, 'rechunked': rechunked, 'fragments': embedded}
_ID_RE = re.compile('"id"\\s*:\\s*"([^"]+)"')

def _id_pliku(path: Path, offset: int, chunk: str) -> str:
    return f'file:{path}:{offset}:{_sha256(chunk)[:10]}'

def _id_podfragmentu(parent_id: str, start: int, end: int, text: str) -> str:
    return f'{parent_id}:child:{start}:{end}:{_sha256(text)[:10]}'

def _rekord_pliku(path: Path, offset: int, chunk: str, tekst_pliku: str, vector, model: str, chunk_size: int, overlap: int, logical_source_id: Optional[str]=None, record_id: Optional[str]=None, parent_id: Optional[str]=None, parent_range: Optional[Tuple[int, int]]=None) -> dict:
    source = {'kind': 'file', 'path': str(path), 'offset': offset, 'len': len(chunk)}
    if logical_source_id:
        source['logical_source_id'] = logical_source_id
    if parent_id is not None and parent_range is not None:
        source['parent_id'] = parent_id
        source['parent_range'] = [parent_range[0], parent_range[1]]
    return {'schema': 'lira.embedding_record.v1', 'id': record_id or _id_pliku(path, offset, chunk), 'ts': now_iso(), 'model': model, 'source': source, 'text': chunk[:2000], 'vector': vector, 'meta': {'sha256': _sha256(chunk), 'file_sha256': _sha256(tekst_pliku), 'chunk': f'{chunk_size}/0'}}

def _existing_ids(index_path: Path, limit: int=200000) -> set:
    ids: set = set()
    if not index_path.exists():
        return ids
    try:
        with index_path.open('rb') as f:
            for raw in f:
                m = _ID_RE.search(raw[:512].decode('utf-8', errors='ignore'))
                if m:
                    ids.add(m.group(1))
                    if len(ids) >= limit:
                        raise RuntimeError(f'dedup id limit reached ({limit}); refusing an incomplete view')
    except Exception as exc:
        raise RuntimeError(f'cannot read complete embedding ids: {exc}') from exc
    return ids

def _load_batch_cursor(state_path: Path) -> int:
    try:
        data = json.loads(state_path.read_text(encoding='utf-8'))
        return max(0, int(data.get('known_source_cursor', 0)))
    except Exception:
        return 0

def _fair_candidate_window(never_indexed: List[Path], already_indexed: List[Path], max_files: int, known_cursor: int) -> Tuple[List[Path], int]:
    if max_files <= 0:
        return ([], known_cursor)
    if not already_indexed:
        return (never_indexed[:max_files], 0)
    known_quota = min(len(already_indexed), max(1, max_files // 4))
    new_quota = max_files - known_quota
    selected_new = never_indexed[:new_quota]
    known_quota += max(0, new_quota - len(selected_new))
    known_quota = min(known_quota, len(already_indexed), max_files - len(selected_new))
    cursor = known_cursor % len(already_indexed)
    rotated = already_indexed[cursor:] + already_indexed[:cursor]
    selected_known = rotated[:known_quota]
    next_cursor = (cursor + len(selected_known)) % len(already_indexed)
    return (selected_new + selected_known, next_cursor)

def _append_retry(state_dir: Path, entry: Dict[str, Any]) -> None:
    path = state_dir / 'embeddings_retry.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps({'ts': now_iso(), **entry}, ensure_ascii=False) + '\n')

def build_embeddings(model: str='mxbai-embed-large:latest', max_files: int=400, chunk_size: int=DEFAULT_CHUNK_SIZE, overlap: int=120, top_dirs: Optional[List[Path]]=None) -> Dict[str, Any]:
    mem = memory_dir()
    emb_dir = mem / 'embeddings'
    emb_dir.mkdir(parents=True, exist_ok=True)
    index_path = emb_dir / 'embeddings_index.jsonl'
    existing = _existing_ids(index_path)
    from agents.memory.source_registry import classify_batch_source, quarantine_entry, registry_manifest
    events_root = shared_context_dir() / 'events'
    registry = registry_manifest(mem, shared_events_root=events_root)
    quarantine: List[Dict[str, Any]] = []
    try:
        from agents.memory.index_freshness import logical_source_ids as _logical_source_ids
        logical_by_path = _logical_source_ids()
    except Exception:
        logical_by_path = {}
    files_indexed = 0
    chunks_embedded = 0
    skipped = 0
    files_empty = 0
    primary_inputs = 0
    primary_already_present = 0
    primary_embedded = 0
    primary_embedded_after_rechunk = 0
    primary_failed = 0
    primary_deferred = 0
    rechunked = 0
    subfragments_created = 0
    read_errors = 0
    source_truncated = 0
    filtered_source = 0
    run_degraded = False
    run_deferred = False
    if top_dirs is None:
        top_dirs = [mem, events_root]
    cand: List[Path] = []
    for d in top_dirs:
        if not d.exists():
            continue
        cand.extend(sorted(d.rglob('*.md')))
        cand.extend(sorted(d.rglob('*.json')))
        cand.extend(sorted(d.rglob('*.txt')))
        cand.extend(sorted(d.rglob('*.jsonl')))
    discovered = sorted(set(cand))
    cand_all: List[Path] = []
    for path in discovered:
        decision = classify_batch_source(path, mem, shared_events_root=events_root)
        if decision.allowed:
            cand_all.append(path)
        else:
            filtered_source += 1
            quarantine.append(quarantine_entry(path, decision))
    write_json(runtime_dir() / 'state' / 'embedding_source_quarantine.json', {'schema': 'lira.embedding_source_quarantine.v1', 'ts': now_iso(), 'registry_version': registry['version'], 'registry_hash': registry['registry_hash'], 'count': len(quarantine), 'entries': quarantine})
    indexed_paths = {i.split(':', 2)[1] for i in existing if i.startswith('file:') and i.count(':') >= 2}
    never_indexed = [p for p in cand_all if str(p) not in indexed_paths]
    already_indexed = [p for p in cand_all if str(p) in indexed_paths]
    never_indexed.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    already_indexed.sort(key=lambda p: str(p))
    cand_total = len(cand_all)
    backlog_total = len(never_indexed)
    last_run_path = runtime_dir() / 'state' / 'embeddings_batch_last_run.json'
    known_cursor = _load_batch_cursor(last_run_path)
    cand, next_known_cursor = _fair_candidate_window(never_indexed, already_indexed, max_files, known_cursor)
    never_set = set(never_indexed)
    never_done = 0
    index_path.parent.mkdir(parents=True, exist_ok=True)
    f = index_path.open('a', encoding='utf-8')
    try:
        for path in cand:
            read_result = _read_text_result(path)
            if read_result.status != 'success':
                run_degraded = True
                if read_result.status == 'read_error':
                    read_errors += 1
                else:
                    source_truncated += 1
                _append_retry(runtime_dir() / 'state', {'producer': 'embeddings_batch', 'source': str(path), 'source_hash': None, 'reason': read_result.status, 'detail': read_result.reason, 'attempt': 1})
                continue
            text = read_result.text
            if not text.strip():
                skipped += 1
                files_empty += 1
                continue
            files_indexed += 1
            file_complete = True
            for offset, ch in _chunk(text, chunk_size=chunk_size, overlap=overlap):
                rid = _id_pliku(path, offset, ch)
                primary_inputs += 1
                if rid in existing:
                    skipped += 1
                    primary_already_present += 1
                    continue
                outcome = _adaptive_embed(model, ch, max_chars=chunk_size)
                if outcome['status'] == 'deferred_busy':
                    primary_deferred += 1
                    run_deferred = True
                    file_complete = False
                    break
                if outcome['status'] != 'success':
                    primary_failed += 1
                    run_degraded = True
                    file_complete = False
                    _append_retry(runtime_dir() / 'state', {'producer': 'embeddings_batch', 'source': str(path), 'source_hash': _sha256(text), 'fragment_id': rid, 'fragment_hash': _sha256(ch), 'offset': offset, 'reason': outcome['status'], 'detail': outcome.get('reason', ''), 'attempt': outcome.get('attempts', 1)})
                    continue
                fragments = outcome['fragments']
                if outcome['rechunked']:
                    rechunked += 1
                    subfragments_created += len(fragments)
                    primary_embedded_after_rechunk += 1
                else:
                    primary_embedded += 1
                for nr, fragment in enumerate(fragments):
                    start = int(fragment['start'])
                    end = int(fragment['end'])
                    fragment_text = str(fragment['text'])
                    record_id = rid if nr == len(fragments) - 1 else _id_podfragmentu(rid, start, end, fragment_text)
                    if record_id in existing:
                        continue
                    rec = _rekord_pliku(path=path, offset=offset + start, chunk=fragment_text, tekst_pliku=text, vector=fragment['vector'], model=model, chunk_size=chunk_size, overlap=overlap, logical_source_id=logical_by_path.get(str(path)), record_id=record_id, parent_id=rid if outcome['rechunked'] else None, parent_range=(start, end) if outcome['rechunked'] else None)
                    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                    existing.add(record_id)
                    chunks_embedded += 1
                if chunks_embedded % 50 == 0:
                    time.sleep(0.05)
            if file_complete and path in never_set:
                never_done += 1
            if run_deferred:
                break
    finally:
        f.close()
    primary_terminal = primary_already_present + primary_embedded + primary_embedded_after_rechunk + primary_failed + primary_deferred
    if primary_terminal != primary_inputs:
        run_degraded = True
    report = {'schema': 'lira.embeddings_batch_report.v1', 'ts': now_iso(), 'model': model, 'status': 'deferred' if run_deferred else 'degraded' if run_degraded else 'ok', 'registry_version': registry['version'], 'registry_hash': registry['registry_hash'], 'files_discovered': len(discovered), 'files_candidates': cand_total, 'filtered_source': filtered_source, 'quarantine_path': str(runtime_dir() / 'state' / 'embedding_source_quarantine.json'), 'files_taken_this_run': len(cand), 'files_left_out_this_run': max(0, cand_total - len(cand)), 'backlog_never_indexed': backlog_total, 'backlog_cleared_this_run': never_done, 'backlog_remaining_after_run': max(0, backlog_total - never_done), 'files_empty': files_empty, 'read_error': read_errors, 'source_truncated': source_truncated, 'max_files_limit': max_files, 'files_scanned': len(cand), 'files_indexed': files_indexed, 'chunks_embedded': chunks_embedded, 'skipped': skipped, 'primary_inputs': primary_inputs, 'primary_already_present': primary_already_present, 'primary_embedded': primary_embedded, 'primary_embedded_after_rechunk': primary_embedded_after_rechunk, 'primary_failed': primary_failed, 'primary_deferred': primary_deferred, 'primary_terminal_total': primary_terminal, 'primary_accounting_ok': primary_terminal == primary_inputs, 'rechunked': rechunked, 'subfragments_created': subfragments_created, 'index_path': str(index_path), 'dedup_ids_loaded': len(existing), 'dedup_ids_limit': 200000}
    write_json(runtime_dir() / 'state' / 'embeddings_batch_report.json', report)
    if not run_deferred:
        write_json(last_run_path, {'ts': now_iso(), 'status': report['status'], 'files_indexed': files_indexed, 'backlog_remaining': max(0, backlog_total - never_done), 'known_source_cursor': next_known_cursor, 'registry_version': registry['version'], 'registry_hash': registry['registry_hash']})
    return {'status': report['status'], 'files_indexed': files_indexed, 'chunks_embedded': chunks_embedded, 'skipped': skipped, 'failed_embedding': primary_failed, 'deferred_busy': primary_deferred, 'filtered_source': filtered_source}

def main():
    lock_path = runtime_dir() / 'state' / 'embeddings_batch.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open('w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({'skipped': 'already_running', 'lock': str(lock_path)}, ensure_ascii=False))
        return
    model = os.getenv('LIRA_EMBED_MODEL', 'mxbai-embed-large:latest')
    max_files = int(os.getenv('LIRA_EMBED_MAX_FILES', '400'))
    chunk_size = int(os.getenv('LIRA_EMBED_CHUNK_SIZE', str(DEFAULT_CHUNK_SIZE)))
    overlap = int(os.getenv('LIRA_EMBED_OVERLAP', '120'))
    res = build_embeddings(model=model, max_files=max_files, chunk_size=chunk_size, overlap=overlap)
    if res.get('status') != 'deferred' and (res.get('chunks_embedded') or res.get('filtered_source')):
        try:
            from agents.memory.index_freshness import przebuduj as _przebuduj_odsiew
            res['freshness'] = {k: v for k, v in _przebuduj_odsiew().items() if k != 'stale_ids'}
        except Exception as exc:
            res['freshness_error'] = f'{type(exc).__name__}: {exc}'
    print(json.dumps(res, ensure_ascii=False))
if __name__ == '__main__':
    main()
