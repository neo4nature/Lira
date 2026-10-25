from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from agents.memory.embeddings_batch import _adaptive_embed, _append_retry, _existing_ids, now_iso
from config import memory_dir, paths
EMBED_MODEL = os.getenv('LIRA_EMBED_MODEL', 'mxbai-embed-large:latest')
MAX_CHARS = 800
STATE_FILE = 'capsule_ingester_state.json'
INDEXABLE_TYPES = {'chat_message', 'decision', 'review', 'proposal', 'report'}
MIN_TEXT_LEN = 30
SKIP_PROJECTS = set((s.strip() for s in os.getenv('LIRA_CAPSULE_INGESTER_SKIP_PROJECTS', 'ecosystem_monitor').split(',') if s.strip()))

def _sha256(text: str) -> str:
    return hashlib.sha256((text or '').encode('utf-8', errors='ignore')).hexdigest()

def _capsule_text(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return str(payload or '')
    parts = []
    for key in ('title', 'summary', 'text'):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    return '\n'.join(parts)

def _load_state(state_dir: Path) -> Dict[str, Any]:
    sp = state_dir / STATE_FILE
    if sp.exists():
        try:
            return json.loads(sp.read_text(encoding='utf-8', errors='ignore') or '{}')
        except Exception:
            pass
    return {'last_capsule_ts': None, 'last_capsule_id': None, 'last_run': None, 'total_ingested': 0}

def _save_state(state_dir: Path, state: Dict[str, Any]) -> None:
    sp = state_dir / STATE_FILE
    sp.parent.mkdir(parents=True, exist_ok=True)
    state['last_run'] = now_iso()
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

def _read_new_capsules(after_ts: str | None, after_id: str | None=None, limit: int=500) -> List[Dict[str, Any]]:
    from governance.capsule_bus import get_conn, DB_PATH
    type_placeholders = ','.join(('?' for _ in INDEXABLE_TYPES))
    skip_clause = ''
    skip_params: tuple = ()
    if SKIP_PROJECTS:
        skip_placeholders = ','.join(('?' for _ in SKIP_PROJECTS))
        skip_clause = f' AND (project IS NULL OR project NOT IN ({skip_placeholders}))'
        skip_params = tuple(SKIP_PROJECTS)
    with get_conn(DB_PATH) as conn:
        if after_ts:
            q = f'SELECT id, sender, recipient, type, thread_id, project, status,\n                          payload_json, created_at\n                   FROM capsules\n                   WHERE type IN ({type_placeholders}){skip_clause}\n                     AND (created_at > ? OR (created_at = ? AND id > ?))\n                   ORDER BY created_at ASC, id ASC LIMIT ?'
            rows = conn.execute(q, (*INDEXABLE_TYPES, *skip_params, after_ts, after_ts, after_id or '', limit)).fetchall()
        else:
            q = f'SELECT id, sender, recipient, type, thread_id, project, status,\n                          payload_json, created_at\n                   FROM capsules\n                   WHERE type IN ({type_placeholders}){skip_clause}\n                   ORDER BY created_at ASC, id ASC LIMIT ?'
            rows = conn.execute(q, (*INDEXABLE_TYPES, *skip_params, limit)).fetchall()
    return [dict(r) for r in rows]

def ingest_once(model: str=EMBED_MODEL, limit: int=200, existing: set | None=None) -> Dict[str, int]:
    mem = memory_dir()
    emb_dir = mem / 'embeddings'
    emb_dir.mkdir(parents=True, exist_ok=True)
    index_path = emb_dir / 'embeddings_index.jsonl'
    state = _load_state(emb_dir)
    if existing is None:
        existing = _existing_ids(index_path)
    from agents.memory.source_registry import classify_stream_source, registry_manifest
    from governance.capsule_bus import DB_PATH
    source_decision = classify_stream_source('capsule', 'capsule_ingester', 'capsule_bus', Path(DB_PATH))
    registry = registry_manifest(mem)
    if not source_decision.allowed:
        return {'scanned': 0, 'embedded': 0, 'skipped': 0, 'errors': 0, 'filtered_source': 1}
    capsules = _read_new_capsules(state.get('last_capsule_ts'), state.get('last_capsule_id'), limit=limit)
    stats = {'scanned': len(capsules), 'embedded': 0, 'skipped': 0, 'errors': 0, 'already_present': 0, 'deferred_busy': 0, 'failed_embedding': 0, 'rechunked': 0, 'filtered_source': 0}
    if not capsules:
        return stats
    last_ts = state.get('last_capsule_ts')
    last_id = state.get('last_capsule_id')
    with index_path.open('a', encoding='utf-8') as f:
        for cap in capsules:
            cap_id = cap.get('id', '')
            payload = cap.get('payload_json')
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            text = _capsule_text(payload if isinstance(payload, dict) else {})
            if len(text) < MIN_TEXT_LEN:
                stats['skipped'] += 1
                last_ts = cap.get('created_at') or last_ts
                last_id = cap_id or last_id
                continue
            text = text[:MAX_CHARS]
            rid = f'capsule:{_sha256(text)}'
            if rid in existing:
                stats['skipped'] += 1
                stats['already_present'] += 1
                last_ts = cap.get('created_at') or last_ts
                last_id = cap_id or last_id
                continue
            outcome = _adaptive_embed(model, text, max_chars=MAX_CHARS)
            if outcome['status'] == 'deferred_busy':
                stats['deferred_busy'] += 1
                break
            if outcome['status'] != 'success':
                stats['errors'] += 1
                stats['failed_embedding'] += 1
                _append_retry(emb_dir, {'producer': 'capsule_ingester', 'source': str(DB_PATH), 'capsule_id': cap_id, 'fragment_hash': _sha256(text), 'reason': outcome['status'], 'detail': outcome.get('reason', ''), 'attempt': outcome.get('attempts', 1)})
                break
            if outcome['rechunked']:
                stats['rechunked'] += 1
            for nr, fragment in enumerate(outcome['fragments']):
                start = int(fragment['start'])
                end = int(fragment['end'])
                fragment_text = str(fragment['text'])
                record_id = rid if nr == len(outcome['fragments']) - 1 else f'{rid}:child:{start}:{end}:{_sha256(fragment_text)[:10]}'
                if record_id in existing:
                    continue
                source = {'kind': 'capsule', 'capsule_id': cap_id, 'sender': cap.get('sender'), 'recipient': cap.get('recipient'), 'type': cap.get('type'), 'thread_id': cap.get('thread_id'), 'project': cap.get('project'), 'status': cap.get('status'), 'created_at': cap.get('created_at')}
                if outcome['rechunked']:
                    source['parent_id'] = rid
                    source['parent_range'] = [start, end]
                rec = {'schema': 'lira.embedding_record.v1', 'id': record_id, 'ts': now_iso(), 'model': model, 'source': source, 'text': fragment_text, 'vector': fragment['vector'], 'meta': {'sha256': _sha256(fragment_text)}}
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                existing.add(record_id)
                stats['embedded'] += 1
            last_ts = cap.get('created_at') or last_ts
            last_id = cap_id or last_id
            if stats['embedded'] % 25 == 0:
                time.sleep(0.05)
    state['last_capsule_ts'] = last_ts
    state['last_capsule_id'] = last_id
    state['total_ingested'] = int(state.get('total_ingested', 0)) + stats['embedded']
    state['source_registry_version'] = registry['version']
    state['source_registry_hash'] = registry['registry_hash']
    _save_state(emb_dir, state)
    return stats
