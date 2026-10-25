import copy
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.memory import retrieval_usage as usage

def _events(path: Path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

def test_retrieval_and_receiver_outcome_are_separate_verified_events(tmp_path):
    log = tmp_path / 'retrieval_usage.jsonl'
    results = [{'id': 'mem-1', 'score': 0.77, 'source': 'file', 'path': '/memory/a.md'}]
    observed = usage.record_retrieval('what do we know', results, retrieval_id='ret-test', actor='memory_api', channel='semantic_rest', log_path=log)
    receipt = usage.record_outcome('ret-test', 'mem-1', 'used', actor='Reviewer', note='quoted in answer', log_path=log)
    assert observed['verified'] is True
    assert receipt['verified'] is True
    events = _events(log)
    assert [event['event_type'] for event in events] == ['retrieval', 'outcome']
    assert events[0]['results'][0]['rank'] == 1
    assert events[1]['outcome'] == 'used'

def test_observer_does_not_mutate_results_or_ranking(tmp_path):
    log = tmp_path / 'retrieval_usage.jsonl'
    results = [{'id': 'first', 'score': 0.2, 'source': 'file'}, {'id': 'second', 'score': 0.9, 'source': 'capsule'}]
    before = copy.deepcopy(results)
    usage.record_retrieval('query', results, log_path=log)
    assert results == before
    assert [item['record_id'] for item in _events(log)[0]['results']] == ['first', 'second']

@pytest.mark.parametrize('outcome', sorted(usage.OUTCOMES))
def test_every_agreed_outcome_is_recordable(tmp_path, outcome):
    log = tmp_path / 'usage.jsonl'
    usage.record_retrieval('query', [{'id': 'mem-1'}], retrieval_id='ret-test', log_path=log)
    result = usage.record_outcome('ret-test', 'mem-1', outcome, actor='Operator', log_path=log)
    assert result['outcome'] == outcome

def test_unknown_outcome_is_rejected_without_claiming_success(tmp_path):
    log = tmp_path / 'retrieval_usage.jsonl'
    with pytest.raises(ValueError):
        usage.record_outcome('ret-test', 'mem-1', 'probably_used', actor='Model', log_path=log)
    assert not log.exists()

def test_outcome_without_matching_retrieval_is_rejected(tmp_path):
    log = tmp_path / 'retrieval_usage.jsonl'
    usage.record_retrieval('query', [{'id': 'mem-real'}], retrieval_id='ret-real', log_path=log)
    before = log.read_text(encoding='utf-8')
    with pytest.raises(ValueError, match='no matching retrieval'):
        usage.record_outcome('ret-invented', 'mem-real', 'used', actor='Reviewer', log_path=log)
    assert log.read_text(encoding='utf-8') == before
