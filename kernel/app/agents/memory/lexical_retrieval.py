from __future__ import annotations
import argparse
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
EMBEDDING_SCHEMA = 'lira.embedding_record.v1'
LEXICAL_SCHEMA = 'lira.lexical_index.v1'
TOKEN_RE = re.compile('[^\\W_]+', re.UNICODE)
STOPWORDS = {'a', 'aby', 'albo', 'ale', 'bo', 'by', 'byc', 'byl', 'byla', 'bylo', 'co', 'czy', 'dla', 'do', 'i', 'ich', 'jak', 'jest', 'ktora', 'ktore', 'ktory', 'lub', 'ma', 'na', 'nie', 'o', 'od', 'oraz', 'po', 'pod', 'przez', 'sie', 'to', 'w', 'we', 'z', 'za', 'ze', 'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'in', 'is', 'it', 'of', 'on', 'or', 'that', 'the', 'this', 'to', 'was', 'with'}
POLISH_SUFFIXES = tuple(sorted({'owego', 'owej', 'owych', 'owymi', 'ami', 'ach', 'owie', 'eniu', 'aniu', 'ego', 'emu', 'owa', 'owe', 'owi', 'owy', 'cie', 'nia', 'nie', 'ciu', 'ia', 'ie', 'om', 'ow', 'em', 'ym', 'im', 'a', 'e', 'i', 'y', 'u'}, key=len, reverse=True))

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _kernel_dir() -> Path:
    return Path(__file__).resolve().parents[3]

def _default_jsonl_path():
    from config import paths
    return paths()['EMBEDDINGS_INDEX']

def _default_db_path():
    from config import memory_dir
    return memory_dir() / 'embeddings' / 'lexical_index.sqlite3'

def normalize_token(token: str) -> str:
    folded = token.casefold().translate(str.maketrans({'ł': 'l', 'đ': 'd'}))
    return ''.join((char for char in unicodedata.normalize('NFKD', folded) if not unicodedata.combining(char)))

def light_polish_stems(token: str) -> tuple[str, ...]:
    if len(token) < 5:
        return (token,)
    for suffix in POLISH_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            stems = [token[:-len(suffix)]]
            if suffix == 'nie' and len(token) - len('ie') >= 4:
                stems.append(token[:-len('ie')])
            return tuple(dict.fromkeys(stems))
    return (token,)

def query_concepts(text: str) -> List[tuple[str, tuple[str, ...]]]:
    concepts: List[tuple[str, tuple[str, ...]]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for raw in TOKEN_RE.findall(text):
        token = normalize_token(raw)
        if len(token) < 2 or token in STOPWORDS:
            continue
        concept = (token, light_polish_stems(token))
        if concept not in seen:
            seen.add(concept)
            concepts.append(concept)
    return concepts

def lexical_terms(text: str) -> List[str]:
    terms: List[str] = []
    seen: set[str] = set()
    for token, stems in query_concepts(text):
        for term in (token, *(f'plstemx{stem}' for stem in stems)):
            if term not in seen:
                seen.add(term)
                terms.append(term)
    return terms

def _source_priority(source_kind: str) -> int:
    return 1 if source_kind == 'file' else 0

def _snapshot_records(index_path: Path) -> Iterable[Dict[str, Any]]:
    snapshot_size = index_path.stat().st_size
    with index_path.open('rb') as handle:
        while handle.tell() < snapshot_size:
            raw = handle.readline()
            if not raw or handle.tell() > snapshot_size:
                break
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped.decode('utf-8', errors='replace'))
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get('schema') == EMBEDDING_SCHEMA:
                yield record

def _open_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("\n        PRAGMA journal_mode=DELETE;\n        PRAGMA synchronous=NORMAL;\n        CREATE TABLE documents (\n            docid INTEGER PRIMARY KEY,\n            record_id TEXT NOT NULL,\n            fingerprint TEXT NOT NULL UNIQUE,\n            ts TEXT NOT NULL,\n            source_kind TEXT NOT NULL,\n            source_path TEXT NOT NULL,\n            text TEXT NOT NULL,\n            search_text TEXT NOT NULL\n        );\n        CREATE VIRTUAL TABLE documents_fts USING fts5(\n            search_text,\n            tokenize='unicode61 remove_diacritics 2'\n        );\n        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n    ")

def build_index(index_path: Path, db_path: Path) -> Dict[str, Any]:
    if not index_path.is_file():
        raise FileNotFoundError(index_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = db_path.with_name(f'.{db_path.name}.tmp-{os.getpid()}')
    if temp_path.exists():
        temp_path.unlink()
    started_at = _now_iso()
    snapshot_size = index_path.stat().st_size
    canonical: Dict[str, tuple[int, str, int]] = {}
    source_counts: Counter[str] = Counter()
    stats: Dict[str, Any] = {'records_seen': 0, 'documents_indexed': 0, 'duplicate_records_seen': 0, 'duplicate_records_skipped': 0, 'canonical_source_replacements': 0, 'records_without_fingerprint': 0, 'records_without_search_terms': 0}
    connection = sqlite3.connect(temp_path)
    try:
        _open_schema(connection)
        for record in _snapshot_records(index_path):
            stats['records_seen'] += 1
            record_id = str(record.get('id') or '')
            meta = record.get('meta')
            fingerprint = str(meta.get('sha256') or '') if isinstance(meta, dict) else ''
            if not fingerprint:
                stats['records_without_fingerprint'] += 1
                fingerprint = f'record-id:{record_id}'
            text = str(record.get('text') or '')
            terms = lexical_terms(text)
            if not terms:
                stats['records_without_search_terms'] += 1
                continue
            source = record.get('source')
            source_kind = str(source.get('kind') or 'unknown') if isinstance(source, dict) else 'unknown'
            source_path = str(source.get('path') or '') if isinstance(source, dict) else ''
            priority = _source_priority(source_kind)
            previous = canonical.get(fingerprint)
            if previous is not None:
                stats['duplicate_records_seen'] += 1
                previous_docid, previous_source_kind, previous_priority = previous
                if priority <= previous_priority:
                    stats['duplicate_records_skipped'] += 1
                    continue
                connection.execute('UPDATE documents\n                       SET record_id=?, ts=?, source_kind=?, source_path=?, text=?, search_text=?\n                       WHERE docid=?', (record_id, str(record.get('ts') or ''), source_kind, source_path, text, ' '.join(terms), previous_docid))
                canonical[fingerprint] = (previous_docid, source_kind, priority)
                source_counts[previous_source_kind] -= 1
                if source_counts[previous_source_kind] <= 0:
                    del source_counts[previous_source_kind]
                source_counts[source_kind] += 1
                stats['canonical_source_replacements'] += 1
                continue
            cursor = connection.execute('INSERT INTO documents\n                   (record_id, fingerprint, ts, source_kind, source_path, text, search_text)\n                   VALUES (?, ?, ?, ?, ?, ?, ?)', (record_id, fingerprint, str(record.get('ts') or ''), source_kind, source_path, text, ' '.join(terms)))
            connection.execute('INSERT INTO documents_fts(rowid, search_text) VALUES (?, ?)', (cursor.lastrowid, ' '.join(terms)))
            canonical[fingerprint] = (int(cursor.lastrowid), source_kind, priority)
            stats['documents_indexed'] += 1
            source_counts[source_kind] += 1
            if stats['documents_indexed'] % 1000 == 0:
                connection.commit()
        finished_at = _now_iso()
        metadata = {'schema': LEXICAL_SCHEMA, 'started_at': started_at, 'finished_at': finished_at, 'source_index_path': str(index_path.resolve()), 'source_snapshot_size_bytes': snapshot_size, **stats, 'source_counts': dict(sorted(source_counts.items()))}
        connection.executemany('INSERT INTO metadata(key, value) VALUES (?, ?)', ((key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()))
        connection.commit()
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except Exception:
        connection.close()
        if temp_path.exists():
            temp_path.unlink()
        raise
    else:
        connection.close()
    os.replace(temp_path, db_path)
    return {'schema': LEXICAL_SCHEMA, 'started_at': started_at, 'finished_at': _now_iso(), 'source_snapshot_size_bytes': snapshot_size, 'db_path': str(db_path.resolve()), **stats, 'source_counts': dict(sorted(source_counts.items()))}

def _fts_expression(concepts: Sequence[tuple[str, tuple[str, ...]]]) -> str:
    terms: List[str] = []
    for token, stems in concepts:
        terms.append(f'"{token}"')
        terms.extend((f'"plstemx{stem}"' for stem in stems))
    return ' OR '.join(dict.fromkeys(terms))

def _concept_document_frequency(connection: sqlite3.Connection, concept: tuple[str, tuple[str, ...]]) -> int:
    row = connection.execute('SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?', (_fts_expression((concept,)),)).fetchone()
    return int(row[0]) if row is not None else 0

def _inverse_document_frequency(total_documents: int, frequency: int) -> float:
    return math.log((total_documents + 1) / (frequency + 1)) + 1.0

def search(db_path: Path, query: str, top_k: int=10) -> List[Dict[str, Any]]:
    if top_k <= 0:
        return []
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    concepts = query_concepts(query)
    if not concepts:
        return []
    expression = _fts_expression(concepts)
    connection = sqlite3.connect(f'file:{db_path.resolve()}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        total_documents = int(connection.execute('SELECT COUNT(*) FROM documents').fetchone()[0])
        concept_frequencies = [_concept_document_frequency(connection, concept) for concept in concepts]
        rows = connection.execute('SELECT d.record_id, d.fingerprint, d.ts, d.source_kind,\n                      d.source_path, d.text, d.search_text,\n                      bm25(documents_fts) AS raw_bm25\n               FROM documents_fts\n               JOIN documents AS d ON d.docid = documents_fts.rowid\n               WHERE documents_fts MATCH ?\n               ORDER BY raw_bm25\n               LIMIT ?', (expression, max(top_k * 8, top_k))).fetchall()
    finally:
        connection.close()
    concept_weights = [_inverse_document_frequency(total_documents, frequency) for frequency in concept_frequencies]
    total_query_weight = sum(concept_weights)
    results: List[Dict[str, Any]] = []
    for row in rows:
        document_terms = set(str(row['search_text']).split())
        matched: List[str] = []
        rarity_evidence: List[Dict[str, Any]] = []
        exact_matches = 0
        stem_matches = 0
        matched_query_weight = 0.0
        for (token, stems), frequency, weight in zip(concepts, concept_frequencies, concept_weights):
            matched_by: str | None = None
            if token in document_terms:
                matched.append(token)
                exact_matches += 1
                matched_by = token
            else:
                matching_stem = next((stem for stem in stems if f'plstemx{stem}' in document_terms), None)
                if matching_stem is not None:
                    matched.append(f'{token}->{matching_stem}')
                    stem_matches += 1
                    matched_by = f'plstemx{matching_stem}'
            if matched_by is not None:
                matched_query_weight += weight
            rarity_evidence.append({'token': token, 'document_frequency': frequency, 'weight': round(weight, 6), 'matched_by': matched_by})
        coverage = (exact_matches + stem_matches) / len(concepts)
        weighted_coverage = matched_query_weight / total_query_weight if total_query_weight > 0.0 else 0.0
        if coverage <= 0.0:
            continue
        results.append({'record_id': row['record_id'], 'fingerprint': row['fingerprint'], 'ts': row['ts'], 'source_kind': row['source_kind'], 'source_path': row['source_path'], 'text': row['text'], 'lexical_score': round(-float(row['raw_bm25']), 6), 'query_coverage': round(coverage, 6), 'weighted_query_coverage': round(weighted_coverage, 6), 'matched_query_weight': round(matched_query_weight, 6), 'total_query_weight': round(total_query_weight, 6), 'exact_matches': exact_matches, 'stem_matches': stem_matches, 'matched_terms': matched, 'rarity_evidence': rarity_evidence})
    results.sort(key=lambda item: (item['weighted_query_coverage'], item['query_coverage'], item['exact_matches'], item['lexical_score']), reverse=True)
    return results[:top_k]

def _metadata(db_path: Path) -> Dict[str, Any]:
    connection = sqlite3.connect(f'file:{db_path.resolve()}?mode=ro', uri=True)
    try:
        rows = connection.execute('SELECT key, value FROM metadata').fetchall()
    finally:
        connection.close()
    return {key: json.loads(value) for key, value in rows}

def main(argv: Sequence[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description='Cieniowy tor leksykalny pamięci.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    build_parser = subparsers.add_parser('build', help='zbuduj indeks pochodny')
    build_parser.add_argument('--index', type=Path, default=_default_jsonl_path())
    build_parser.add_argument('--db', type=Path, default=_default_db_path())
    search_parser = subparsers.add_parser('search', help='zwróć kandydatów')
    search_parser.add_argument('query')
    search_parser.add_argument('--db', type=Path, default=_default_db_path())
    search_parser.add_argument('--top-k', type=int, default=10)
    info_parser = subparsers.add_parser('info', help='pokaż metadane indeksu')
    info_parser.add_argument('--db', type=Path, default=_default_db_path())
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            result: Any = build_index(args.index, args.db)
        elif args.command == 'search':
            result = search(args.db, args.query, args.top_k)
        else:
            result = _metadata(args.db)
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as exc:
        print(f'lexical_retrieval: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
