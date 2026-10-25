from __future__ import annotations
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple
REGISTRY_VERSION = 'pa082.v2'

@dataclass(frozen=True)
class SourceDecision:
    allowed: bool
    reason: str
    source_kind: str
    producer: str
    parser: str
    canonical_root: str
_FILE_RULES: Tuple[Dict[str, object], ...] = ({'root': '.', 'extensions': ('.md', '.txt'), 'parser': 'root_text'}, {'root': 'diary', 'extensions': ('.md', '.txt', '.json', '.jsonl'), 'parser': 'diary_text'}, {'root': 'Embedy', 'extensions': ('.md', '.txt', '.jsonl'), 'parser': 'legacy_memory_text'}, {'root': 'docs', 'extensions': ('.md', '.txt'), 'parser': 'document_text'}, {'root': 'identity', 'extensions': ('.md', '.txt'), 'parser': 'identity_text'}, {'root': 'participant', 'extensions': ('.md', '.txt'), 'parser': 'document_text'}, {'root': 'plans', 'extensions': ('.md', '.txt'), 'parser': 'document_text'}, {'root': 'shared_library', 'extensions': ('.md', '.txt'), 'parser': 'shared_document_text'}, {'root': 'snapshots_md', 'extensions': ('.md', '.txt'), 'parser': 'snapshot_text'})
_CONVERSATION_EVENT_RULE: Dict[str, str] = {'root': 'shared_context/events', 'pattern': 'conv_*.json', 'parser': 'conversation_event_json'}
_STREAM_RULES = {('generic_session', 'reviewed_import', 'generic_jsonl'), ('capsule', 'capsule_ingester', 'capsule_bus'), ('import_staging', 'reviewed_import', 'origin_staging_jsonl')}
_PRACOWNIE_IMPORTERA = frozenset({'import_staging', 'review_staging'})
_DERIVED_NAMES = {'session_occurrences.json', 'reviewed_import_state.json', 'capsule_ingester_state.json', 'embeddings_batch_report.json', 'embeddings_batch_last_run.json', 'index_freshness.json', 'base_index.json', 'memory_index.json'}

def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)

def _suffix(path: Path) -> str:
    if path.name.endswith('.vec.json'):
        return '.vec.json'
    return path.suffix.lower()

def derived_artifact_reason(path: Path) -> Optional[str]:
    p = _canonical(path)
    czesci = p.parts
    for i in range(1, len(czesci) - 1):
        if czesci[i - 1] == 'shared_library' and czesci[i] in _PRACOWNIE_IMPORTERA:
            return 'pracownia_genezy'
    if 'embeddings' in p.parts:
        return 'derived_memory_artifact'
    if p.name in _DERIVED_NAMES or p.name.endswith('.vec.json'):
        return 'derived_memory_artifact'
    return None

def classify_batch_source(path: Path, memory_root: Path, shared_events_root: Optional[Path]=None) -> SourceDecision:
    path = _canonical(path)
    root = _canonical(memory_root)
    derived = derived_artifact_reason(path)
    if derived:
        return SourceDecision(False, derived, 'file', 'embeddings_batch', 'none', str(root))
    if shared_events_root is not None:
        events_root = _canonical(shared_events_root)
        try:
            event_relative = path.relative_to(events_root)
        except ValueError:
            event_relative = None
        if event_relative is not None and event_relative.parent == Path('.') and event_relative.match(_CONVERSATION_EVENT_RULE['pattern']):
            return SourceDecision(True, 'allowed_source', 'conversation_file', 'embeddings_batch', _CONVERSATION_EVENT_RULE['parser'], str(events_root))
    try:
        relative = path.relative_to(root)
    except ValueError:
        return SourceDecision(False, 'unclassified_source', 'file', 'embeddings_batch', 'unknown', str(root))
    extension = _suffix(path)
    for rule in _FILE_RULES:
        rule_root = Path(str(rule['root']))
        inside = relative.parent == Path('.') if rule_root == Path('.') else relative != rule_root and rule_root in relative.parents
        if inside and extension in rule['extensions']:
            canonical_root = root if rule_root == Path('.') else root / rule_root
            return SourceDecision(True, 'allowed_source', 'file', 'embeddings_batch', str(rule['parser']), str(_canonical(canonical_root)))
    return SourceDecision(False, 'unclassified_source', 'file', 'embeddings_batch', 'unknown', str(root))

def classify_stream_source(source_kind: str, producer: str, parser: str, canonical_root: Path) -> SourceDecision:
    allowed = (source_kind, producer, parser) in _STREAM_RULES
    return SourceDecision(allowed, 'allowed_source' if allowed else 'unclassified_source', source_kind, producer, parser, str(_canonical(canonical_root)))

def registry_manifest(memory_root: Path, shared_events_root: Optional[Path]=None) -> Dict[str, object]:
    serializable = {'version': REGISTRY_VERSION, 'file_rules': [{'root': str(r['root']), 'extensions': list(r['extensions']), 'parser': str(r['parser'])} for r in _FILE_RULES], 'external_file_rules': [dict(_CONVERSATION_EVENT_RULE)], 'stream_rules': sorted([list(r) for r in _STREAM_RULES]), 'derived_names': sorted(_DERIVED_NAMES)}
    encoded = json.dumps(serializable, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {**serializable, 'registry_hash': hashlib.sha256(encoded.encode('utf-8')).hexdigest(), 'canonical_memory_root': str(_canonical(memory_root)), 'canonical_shared_events_root': str(_canonical(shared_events_root)) if shared_events_root is not None else None}

def quarantine_entry(path: Path, decision: SourceDecision) -> Dict[str, object]:
    return {'path': str(_canonical(path)), **asdict(decision)}
