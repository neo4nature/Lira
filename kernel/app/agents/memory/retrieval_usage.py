from __future__ import annotations
import argparse
import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence
SCHEMA = 'lira.retrieval_usage.v1'
OUTCOMES = frozenset({'used', 'rejected', 'corrected', 'used_to_decision', 'recalled_again'})
VERDICTS = {'used': 'trafne', 'used_to_decision': 'trafne', 'recalled_again': 'trafne', 'rejected': 'nietrafne', 'corrected': 'poprawione'}
ANONYMOUS_ACTORS = frozenset({'unknown', 'memory_api', 'recall_cli', 'stol_szukaj'})

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def new_retrieval_id() -> str:
    return f'ret_{uuid.uuid4().hex}'

def usage_log_path() -> Path:
    from config import memory_dir
    return memory_dir() / 'ledger' / 'retrieval_usage.jsonl'

def _bounded(value: Any, limit: int) -> str:
    return str(value or '').strip()[:limit]

def _result_reference(item: Mapping[str, Any], rank: int) -> Dict[str, Any]:
    score = item.get('score', item.get('lexical_score'))
    try:
        score = round(float(score), 6) if score is not None else None
    except (TypeError, ValueError):
        score = None
    source = item.get('source')
    provenance = source if isinstance(source, Mapping) else {}
    capsule_id = _bounded(item.get('capsule_id') or provenance.get('capsule_id'), 128)
    reference = {'record_id': _bounded(item.get('id') or item.get('record_id'), 512), 'fingerprint': _bounded(item.get('fingerprint'), 128), 'rank': rank, 'score': score, 'source_kind': _bounded(provenance.get('kind') if provenance else source or item.get('source_kind'), 64), 'source_path': _bounded(item.get('path') or provenance.get('path') or item.get('source_path'), 1000)}
    if capsule_id:
        reference['source_id'] = f'capsule:{capsule_id}'
    return reference

def _place(place: Mapping[str, Any] | None) -> Dict[str, str] | None:
    if not place or not _bounded(place.get('round_id'), 128):
        return None
    return {'round_id': _bounded(place.get('round_id'), 128), 'ts': _bounded(place.get('ts'), 64)}

def is_anonymous(actor: Any) -> bool:
    return (_bounded(actor, 128) or 'unknown') in ANONYMOUS_ACTORS

def is_probe(event: Mapping[str, Any]) -> bool:
    return event.get('probe') is True

def _append_verified(event: Dict[str, Any], log_path: Path | None=None) -> None:
    path = Path(log_path) if log_path is not None else usage_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 384)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        written = 0
        while written < len(encoded):
            written += os.write(fd, encoded[written:])
        os.fsync(fd)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    marker = f'"event_id":"{event['event_id']}"'.encode('utf-8')
    with path.open('rb') as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max(262144, len(encoded) * 2)))
        tail = handle.read()
    if marker not in tail:
        raise OSError(f'retrieval usage round-trip verification failed for {event['event_id']}')

def _retrieval_contains(path: Path, retrieval_id: str, record_id: str) -> bool:
    if not path.is_file():
        return False
    with path.open('r', encoding='utf-8', errors='ignore') as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('schema') != SCHEMA or event.get('event_type') != 'retrieval' or event.get('retrieval_id') != retrieval_id:
                continue
            return any((isinstance(item, dict) and item.get('record_id') == record_id for item in event.get('results', [])))
    return False

def record_retrieval(query: str, results: Sequence[Mapping[str, Any]], *, retrieval_id: str | None=None, actor: str='unknown', channel: str='unknown', place: Mapping[str, Any] | None | bool=False, probe: bool=False, log_path: Path | None=None) -> Dict[str, Any]:
    trace = _bounded(retrieval_id or new_retrieval_id(), 128)
    if not trace:
        raise ValueError('retrieval_id must not be empty')
    event = {'schema': SCHEMA, 'event_id': f'evt_{uuid.uuid4().hex}', 'event_type': 'retrieval', 'ts': _now_iso(), 'retrieval_id': trace, 'actor': _bounded(actor, 128) or 'unknown', 'channel': _bounded(channel, 128) or 'unknown', 'query': _bounded(query, 2000), 'results': [_result_reference(item, rank) for rank, item in enumerate(tuple(results), 1)]}
    if place is not False:
        event['place'] = _place(place)
    if probe:
        event['probe'] = True
    _append_verified(event, log_path)
    return {'recorded': True, 'verified': True, 'retrieval_id': trace, 'event_id': event['event_id'], 'actor': event['actor'], 'probe': bool(probe)}

def record_outcome(retrieval_id: str, record_id: str, outcome: str, *, actor: str, note: str='', place: Mapping[str, Any] | None=None, log_path: Path | None=None) -> Dict[str, Any]:
    normalized = _bounded(outcome, 64).lower()
    if normalized not in OUTCOMES:
        raise ValueError(f'outcome must be one of: {', '.join(sorted(OUTCOMES))}')
    trace = _bounded(retrieval_id, 128)
    rid = _bounded(record_id, 512)
    who = _bounded(actor, 128)
    if not trace or not rid or (not who):
        raise ValueError('retrieval_id, record_id and actor are required')
    path = Path(log_path) if log_path is not None else usage_log_path()
    if not _retrieval_contains(path, trace, rid):
        raise ValueError('outcome has no matching retrieval receipt')
    event = {'schema': SCHEMA, 'event_id': f'evt_{uuid.uuid4().hex}', 'event_type': 'outcome', 'ts': _now_iso(), 'retrieval_id': trace, 'record_id': rid, 'outcome': normalized, 'actor': who, 'note': _bounded(note, 2000)}
    if _place(place):
        event['place'] = _place(place)
    _append_verified(event, path)
    return {'recorded': True, 'verified': True, 'retrieval_id': trace, 'event_id': event['event_id'], 'outcome': normalized}

def source_key(record_id: str) -> str:
    text = str(record_id or '')
    parts = text.split(':')
    if parts[0] == 'capsule' and len(parts) in (2, 3):
        try:
            if str(uuid.UUID(parts[1])) == parts[1]:
                return ':'.join(parts[:2])
        except ValueError:
            pass
    return text

def read_events(log_path: Path | None=None) -> list[Dict[str, Any]]:
    path = Path(log_path) if log_path is not None else usage_log_path()
    if not path.is_file():
        return []
    events = []
    with path.open('r', encoding='utf-8', errors='ignore') as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get('schema') == SCHEMA:
                events.append(event)
    return events

def last_retrieval(actor: str, log_path: Path | None=None) -> Dict[str, Any] | None:
    for event in reversed(read_events(log_path)):
        if event.get('event_type') == 'retrieval' and event.get('actor') == actor:
            return event
    return None

def find_retrieval(retrieval_id: str, log_path: Path | None=None) -> Dict[str, Any] | None:
    for event in read_events(log_path):
        if event.get('event_type') == 'retrieval' and event.get('retrieval_id') == retrieval_id:
            return event
    return None

def _link_state(verdicts: Mapping[str, str]) -> str:
    seen = set(verdicts.values())
    if {'trafne', 'nietrafne'} <= seen:
        return 'sporne'
    for state in ('trafne', 'nietrafne'):
        if state in seen:
            return state
    return 'poprawione'

def returns(log_path: Path | None=None) -> Dict[str, list[Dict[str, Any]]]:
    events = read_events(log_path)
    queries: Dict[str, str] = {}
    provenance: Dict[tuple[str, str], str] = {}
    for event in events:
        if event.get('event_type') != 'retrieval':
            continue
        queries[event['retrieval_id']] = event.get('query', '')
        for result in event.get('results', []):
            if isinstance(result, dict) and result.get('source_id'):
                provenance[event['retrieval_id'], result.get('record_id', '')] = result['source_id']
    links: Dict[tuple[str, str], Dict[str, Any]] = {}
    for event in events:
        if event.get('event_type') != 'outcome' or event.get('outcome') not in VERDICTS:
            continue
        record_id = event.get('record_id', '')
        source = provenance.get((event.get('retrieval_id'), record_id)) or source_key(record_id)
        place = event.get('place') or {}
        where = place.get('round_id') or f'retrieval:{event.get('retrieval_id')}'
        link = links.setdefault((source, where), {'source': source, 'round_id': place.get('round_id') or '', 'ts': place.get('ts') or event.get('ts', ''), 'first_receipt_ts': event.get('ts', ''), 'verdicts': {}, 'notes': {}, 'queries': [], 'retrieval_ids': []})
        link['verdicts'][event.get('actor', '')] = VERDICTS[event['outcome']]
        if event.get('note'):
            link['notes'][event.get('actor', '')] = event['note']
        if event.get('retrieval_id') not in link['retrieval_ids']:
            link['retrieval_ids'].append(event.get('retrieval_id'))
            query = queries.get(event.get('retrieval_id'), '')
            if query and query not in link['queries']:
                link['queries'].append(query)
    by_source: Dict[str, list[Dict[str, Any]]] = {}
    for link in links.values():
        link['state'] = _link_state(link['verdicts'])
        by_source.setdefault(link['source'], []).append(link)
    for source_links in by_source.values():
        source_links.sort(key=lambda link: link['first_receipt_ts'])
    return by_source

def chain(record_id: str, log_path: Path | None=None) -> list[Dict[str, Any]]:
    return [link for link in returns(log_path).get(source_key(record_id), []) if link['state'] == 'trafne']

def disputed(log_path: Path | None=None) -> list[Dict[str, Any]]:
    return [link for source_links in returns(log_path).values() for link in source_links if link['state'] == 'sporne']

def main(argv: Sequence[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description='Record receiver-side use of one recall result.')
    parser.add_argument('retrieval_id')
    parser.add_argument('record_id')
    parser.add_argument('outcome', choices=sorted(OUTCOMES))
    parser.add_argument('--actor', required=True)
    parser.add_argument('--note', default='')
    args = parser.parse_args(argv)
    print(json.dumps(record_outcome(args.retrieval_id, args.record_id, args.outcome, actor=args.actor, note=args.note), ensure_ascii=False, indent=2))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
