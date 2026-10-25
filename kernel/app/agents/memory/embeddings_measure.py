from __future__ import annotations
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from config import memory_dir, shared_context_dir
from agents.memory.embeddings_batch import DEFAULT_CHUNK_SIZE, _chunk, _is_chat_busy, _ollama_embed, _read_text_safe
SUPPORTED_SUFFIXES = ('.md', '.json', '.txt')
MAX_READ_BYTES = 2000000

def _default_scan_dirs() -> List[Path]:
    return [memory_dir(), shared_context_dir() / 'events']

def scan_candidate_files(scan_dirs: Optional[Iterable[Path]]=None) -> List[Path]:
    roots = list(scan_dirs) if scan_dirs is not None else _default_scan_dirs()
    candidates: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for suffix in SUPPORTED_SUFFIXES:
            candidates.extend(sorted(root.rglob(f'*{suffix}')))
    return sorted(set(candidates), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)

def _resolved_file(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f'not a file: {resolved}')
    if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f'unsupported file type {resolved.suffix!r}; expected one of {SUPPORTED_SUFFIXES}')
    return resolved

def measure_file(path: Path, *, model: str='mxbai-embed-large:latest', max_files: int=400, chunk_size: int=DEFAULT_CHUNK_SIZE, overlap: int=120, scan_dirs: Optional[Iterable[Path]]=None, embed_fn: Optional[Callable[[str, str], Optional[List[float]]]]=None, busy_fn: Optional[Callable[[], bool]]=None, clock: Optional[Callable[[], float]]=None) -> Dict[str, object]:
    if max_files < 0:
        raise ValueError('max_files must be non-negative')
    if chunk_size <= 0:
        raise ValueError('chunk_size must be positive')
    embed = embed_fn or _ollama_embed
    is_busy = busy_fn or _is_chat_busy
    timer = clock or time.perf_counter
    exact_path = _resolved_file(Path(path))
    roots = list(scan_dirs) if scan_dirs is not None else _default_scan_dirs()
    total_started = timer()
    file_prepare_started = timer()
    file_size = exact_path.stat().st_size
    text = _read_text_safe(exact_path)
    if file_size and (not text):
        raise RuntimeError(f'file could not be read: {exact_path}')
    chunks: List[Tuple[int, str]] = list(_chunk(text, chunk_size=chunk_size, overlap=overlap))
    file_prepare_seconds = timer() - file_prepare_started
    scan_started = timer()
    candidates = scan_candidate_files(roots)
    selected = candidates[:max_files]
    candidate_scan_seconds = timer() - scan_started
    try:
        target_rank = candidates.index(exact_path) + 1
    except ValueError:
        target_rank = None
    calls: List[Dict[str, object]] = []
    success_count = 0
    error_count = 0
    interrupted_count = 0
    stopped_early = False
    embedding_started = timer()
    for chunk_index, (offset, chunk_text) in enumerate(chunks, start=1):
        call_started = timer()
        if is_busy():
            calls.append({'chunk_index': chunk_index, 'offset': offset, 'chunk_chars': len(chunk_text), 'embedded_chars': 0, 'elapsed_seconds': timer() - call_started, 'status': 'interrupted_chat_busy', 'vector_dimensions': None})
            interrupted_count += 1
            stopped_early = True
            break
        if embed_fn is None:
            vector = _ollama_embed(model, chunk_text, max_chars=chunk_size)
        else:
            vector = embed(model, chunk_text)
        elapsed = timer() - call_started
        if vector is None:
            status = 'error'
            dimensions = None
            error_count += 1
        else:
            status = 'success'
            dimensions = len(vector)
            success_count += 1
        calls.append({'chunk_index': chunk_index, 'offset': offset, 'chunk_chars': len(chunk_text), 'embedded_chars': len(chunk_text), 'elapsed_seconds': elapsed, 'status': status, 'vector_dimensions': dimensions})
    embedding_seconds = timer() - embedding_started
    total_seconds = timer() - total_started
    remaining_chunks = len(chunks) - len(calls)
    return {'schema': 'lira.embeddings_file_measurement.v1', 'ts': datetime.now(timezone.utc).isoformat(), 'status': 'interrupted' if stopped_early else 'completed', 'model': model, 'writes_performed': 0, 'vectors_discarded': success_count, 'file': {'path': str(exact_path), 'size_bytes': file_size, 'read_chars': len(text), 'read_truncated': file_size > MAX_READ_BYTES, 'chunks_total': len(chunks), 'chunk_size': chunk_size, 'embed_max_chars': chunk_size, 'overlap': overlap}, 'candidate_window': {'scan_roots': [str(root.resolve()) for root in roots], 'total_candidates': len(candidates), 'limit': max_files, 'within_limit': len(selected), 'outside_limit': max(0, len(candidates) - len(selected)), 'target_is_candidate': target_rank is not None, 'target_rank_newest_first': target_rank, 'target_within_limit': exact_path in selected}, 'timing': {'file_preparation_seconds': file_prepare_seconds, 'candidate_scan_seconds': candidate_scan_seconds, 'preparation_seconds': file_prepare_seconds + candidate_scan_seconds, 'embedding_seconds': embedding_seconds, 'total_seconds': total_seconds}, 'outcomes': {'success': success_count, 'error': error_count, 'interrupted': interrupted_count, 'remaining_chunks': remaining_chunks}, 'calls': calls}

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Measure embeddings for one exact file without writing to the index.')
    parser.add_argument('--file', type=Path, required=True, help='exact file path')
    parser.add_argument('--model', default=os.getenv('LIRA_EMBED_MODEL', 'mxbai-embed-large:latest'))
    parser.add_argument('--max-files', type=int, default=int(os.getenv('LIRA_EMBED_MAX_FILES', '400')))
    parser.add_argument('--chunk-size', type=int, default=int(os.getenv('LIRA_EMBED_CHUNK_SIZE', str(DEFAULT_CHUNK_SIZE))))
    parser.add_argument('--overlap', type=int, default=int(os.getenv('LIRA_EMBED_OVERLAP', '120')))
    return parser

def main(argv: Optional[List[str]]=None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = measure_file(args.file, model=args.model, max_files=args.max_files, chunk_size=args.chunk_size, overlap=args.overlap)
    except Exception as exc:
        report = {'schema': 'lira.embeddings_file_measurement.v1', 'status': 'error', 'writes_performed': 0, 'error_type': type(exc).__name__, 'error': str(exc)}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report['status'] == 'interrupted':
        return 3
    if report['outcomes']['error']:
        return 1
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
