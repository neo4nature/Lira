import importlib.util
import sys
import types
from pathlib import Path
import pytest
MODULE_PATH = Path(__file__).parents[1] / 'agents' / 'memory' / 'embeddings_measure.py'

@pytest.fixture
def measure_module(monkeypatch, tmp_path):
    config = types.ModuleType('config')
    config.memory_dir = lambda: tmp_path / 'memory'
    config.shared_context_dir = lambda: tmp_path / 'shared_context'
    batch = types.ModuleType('agents.memory.embeddings_batch')
    batch.DEFAULT_CHUNK_SIZE = 900
    batch._is_chat_busy = lambda: False
    batch._ollama_embed = lambda model, text: [0.1, 0.2]
    batch._read_text_safe = lambda path: path.read_text(encoding='utf-8')

    def chunks(text, chunk_size=900, overlap=120):
        for offset in range(0, len(text), chunk_size):
            yield (offset, text[offset:offset + chunk_size])
    batch._chunk = chunks
    agents = types.ModuleType('agents')
    agents.__path__ = []
    memory = types.ModuleType('agents.memory')
    memory.__path__ = []
    monkeypatch.setitem(sys.modules, 'config', config)
    monkeypatch.setitem(sys.modules, 'agents', agents)
    monkeypatch.setitem(sys.modules, 'agents.memory', memory)
    monkeypatch.setitem(sys.modules, 'agents.memory.embeddings_batch', batch)
    module_name = 'agents.memory.embeddings_measure_test'
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

def _write(path: Path, text: str, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    path.touch()
    import os
    os.utime(path, (mtime, mtime))

def test_exact_file_measurement_discards_vectors_and_reports_window(measure_module, tmp_path):
    root = tmp_path / 'memory'
    target = root / 'old_audit.md'
    _write(target, 'a' * 10, 100)
    _write(root / 'middle.txt', 'middle', 200)
    _write(root / 'new.json', '{}', 300)
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    report = measure_module.measure_file(target, max_files=2, chunk_size=6, scan_dirs=[root], embed_fn=lambda model, text: [1.0, 2.0, 3.0], busy_fn=lambda: False)
    after = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert after == before
    assert report['writes_performed'] == 0
    assert report['vectors_discarded'] == 2
    assert report['file']['chunks_total'] == 2
    assert report['file']['embed_max_chars'] == 6
    assert report['candidate_window'] == {'scan_roots': [str(root.resolve())], 'total_candidates': 3, 'limit': 2, 'within_limit': 2, 'outside_limit': 1, 'target_is_candidate': True, 'target_rank_newest_first': 3, 'target_within_limit': False}
    assert [call['status'] for call in report['calls']] == ['success', 'success']
    assert all(('vector' not in call for call in report['calls']))

def test_chat_busy_interrupts_before_embedding(measure_module, tmp_path):
    target = tmp_path / 'memory' / 'audit.md'
    _write(target, 'one chunk', 100)
    embed_calls = []
    report = measure_module.measure_file(target, scan_dirs=[target.parent], embed_fn=lambda model, text: embed_calls.append(text), busy_fn=lambda: True)
    assert embed_calls == []
    assert report['status'] == 'interrupted'
    assert report['outcomes']['interrupted'] == 1
    assert report['calls'][0]['status'] == 'interrupted_chat_busy'

def test_failed_embedding_is_reported_without_vector(measure_module, tmp_path):
    target = tmp_path / 'memory' / 'audit.md'
    _write(target, 'one chunk', 100)
    report = measure_module.measure_file(target, scan_dirs=[target.parent], embed_fn=lambda model, text: None, busy_fn=lambda: False)
    assert report['status'] == 'completed'
    assert report['outcomes']['error'] == 1
    assert report['vectors_discarded'] == 0
    assert report['calls'][0]['status'] == 'error'
    assert report['calls'][0]['vector_dimensions'] is None

def test_cli_exit_code_exposes_measurement_failure(measure_module, tmp_path, monkeypatch, capsys):
    target = tmp_path / 'memory' / 'audit.md'
    _write(target, 'one chunk', 100)
    monkeypatch.setattr(measure_module, 'measure_file', lambda *args, **kwargs: {'status': 'completed', 'outcomes': {'error': 1}})
    assert measure_module.main(['--file', str(target)]) == 1
    assert '"error": 1' in capsys.readouterr().out
