"""All tests use temporary private storage; accidental networking fails."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('LIRA_MEMORY_DATA_ROOT',str(Path(os.environ['HOME'])/'isolated-memory-tests'))
import pytest

@pytest.fixture(autouse=True)
def isolated_storage(tmp_path,monkeypatch):
    monkeypatch.setenv('LIRA_MEMORY_DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.delenv('LIRA_MEMORY_ALLOW_NETWORK',raising=False)
    def blocked(*args,**kwargs):raise AssertionError('Unexpected network access in offline tests')
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',blocked)
