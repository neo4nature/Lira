import importlib.util
import sys
import types
from pathlib import Path
import pytest
MODULE_PATH = Path(__file__).parents[1] / 'agents' / 'memory' / 'embeddings_batch.py'

@pytest.fixture
def batch_module(monkeypatch, tmp_path):
    config = types.ModuleType('config')
    config.memory_dir = lambda: tmp_path / 'memory'
    config.shared_context_dir = lambda: tmp_path / 'shared_context'
    config.runtime_dir = lambda: tmp_path / 'runtime'
    memory_loader = types.ModuleType('agents.memory.memory_loader')
    memory_loader.write_json = lambda *args, **kwargs: None
    chat_busy = types.ModuleType('xian.chat_busy')
    chat_busy.is_chat_busy = lambda: False
    agents = types.ModuleType('agents')
    agents.__path__ = []
    memory = types.ModuleType('agents.memory')
    memory.__path__ = []
    xian = types.ModuleType('xian')
    xian.__path__ = []
    monkeypatch.setitem(sys.modules, 'config', config)
    monkeypatch.setitem(sys.modules, 'agents', agents)
    monkeypatch.setitem(sys.modules, 'agents.memory', memory)
    monkeypatch.setitem(sys.modules, 'agents.memory.memory_loader', memory_loader)
    monkeypatch.setitem(sys.modules, 'xian', xian)
    monkeypatch.setitem(sys.modules, 'xian.chat_busy', chat_busy)
    module_name = 'agents.memory.embeddings_batch_test'
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

def test_hard_split_caps_and_preserves_overlong_block(batch_module):
    text = 'x' * 3001
    chunks = list(batch_module._chunk(text, chunk_size=900))
    assert [offset for offset, _ in chunks] == [0, 900, 1800, 2700]
    assert [len(chunk) for _, chunk in chunks] == [900, 900, 900, 301]
    assert max((len(chunk) for _, chunk in chunks)) <= 900
    assert ''.join((chunk for _, chunk in chunks)) == text

def test_embedder_rejects_oversize_instead_of_truncating(batch_module, monkeypatch):
    network_calls = []
    monkeypatch.setattr(batch_module.requests, 'post', lambda *args, **kwargs: network_calls.append((args, kwargs)))
    with pytest.raises(ValueError, match='hard limit is 900'):
        batch_module._ollama_embed('model', 'x' * 901, max_chars=900)
    assert network_calls == []

def test_measurement_acceptance_has_full_input_coverage(batch_module, monkeypatch, tmp_path):
    target = tmp_path / 'memory' / 'audit.md'
    target.parent.mkdir(parents=True)
    target.write_text('x' * 3001, encoding='utf-8')
    monkeypatch.setitem(sys.modules, 'agents.memory.embeddings_batch', batch_module)
    meter_name = 'agents.memory.embeddings_measure_acceptance_test'
    meter_path = MODULE_PATH.with_name('embeddings_measure.py')
    spec = importlib.util.spec_from_file_location(meter_name, meter_path)
    meter = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, meter_name, meter)
    assert spec.loader is not None
    spec.loader.exec_module(meter)
    report = meter.measure_file(target, chunk_size=900, scan_dirs=[target.parent], embed_fn=lambda model, text: [1.0], busy_fn=lambda: False)
    chunk_chars = [call['chunk_chars'] for call in report['calls']]
    embedded_chars = [call['embedded_chars'] for call in report['calls']]
    assert max(chunk_chars) <= report['file']['embed_max_chars'] == 900
    assert sum(chunk_chars) == sum(embedded_chars)
    assert report['outcomes'] == {'success': 4, 'error': 0, 'interrupted': 0, 'remaining_chunks': 0}
