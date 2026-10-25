"""Synthetic end-to-end contracts for the extracted package, no personal data."""
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from datetime import date,datetime,timezone
import pytest
from agents.memory import reviewed_import as ingest

STAMP='2025-01-01T10:00:00+00:00'

def records(tmp_path):
    source=tmp_path/'records.jsonl'
    rows=[{'id':'a','text':'Synthetic observatory blue lens cabinet','author':'Operator','created_at':STAMP,'source_id':'demo','vector':[1.,0.]},
          {'id':'b','text':'Synthetic observatory red lens cabinet','author':'Reviewer','created_at':STAMP,'source_id':'demo','vector':[0.,1.]}]
    source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return source

def test_review_import_search_receipt_roundtrip(tmp_path,monkeypatch):
    from config import paths
    from agents.memory import semantic_retrieval as retrieval,retrieval_usage as usage
    source=records(tmp_path);receipt=tmp_path/'review.json';index=paths()['EMBEDDINGS_INDEX']
    ingest.approve(source,receipt,reviewer='Reviewer')
    assert ingest.import_reviewed(source,receipt,index)['written']==0 and not index.exists()
    assert ingest.import_reviewed(source,receipt,index,write=True)['written']==2
    before=index.read_bytes()
    assert ingest.import_reviewed(source,receipt,index,write=True)['written']==0
    assert index.read_bytes()==before
    monkeypatch.setattr(retrieval,'_ollama_embed',lambda *a,**kw:[1.,0.])
    monkeypatch.setattr(retrieval,'_base_search_by_vec',None)
    monkeypatch.setattr(retrieval,'_turmalin_iter_recent',None)
    hits=retrieval.search('blue lens',top_k=2)
    assert hits[0]['id']=='reviewed:demo:a'
    log=tmp_path/'usage.jsonl'
    observed=usage.record_retrieval('blue lens',hits,actor='Operator',log_path=log)
    usage.record_outcome(observed['retrieval_id'],hits[0]['id'],'used',actor='Reviewer',log_path=log)
    assert [r['event_type'] for r in usage.read_events(log)]==['retrieval','outcome']

def test_tamper_requires_fresh_review_and_does_not_write(tmp_path):
    source=records(tmp_path);receipt=tmp_path/'review';index=tmp_path/'index'
    ingest.approve(source,receipt,reviewer='Reviewer')
    source.write_text(source.read_text().replace('blue','green'))
    with pytest.raises(ValueError,match='review'):ingest.import_reviewed(source,receipt,index,write=True)
    assert not index.exists()

def test_changed_same_id_is_not_silently_added(tmp_path):
    source=records(tmp_path);receipt=tmp_path/'review';index=tmp_path/'index'
    ingest.approve(source,receipt,reviewer='Reviewer');ingest.import_reviewed(source,receipt,index,write=True)
    before=index.read_bytes();source.write_text(source.read_text().replace('blue','green'));ingest.approve(source,receipt,reviewer='Reviewer')
    with pytest.raises(ValueError,match='explicit version'):ingest.import_reviewed(source,receipt,index,write=True)
    assert index.read_bytes()==before

@pytest.mark.parametrize('condition',['partial_tail','pending'])
def test_uncertain_state_refuses_without_truncation(tmp_path,condition):
    source=records(tmp_path);receipt=tmp_path/'review';index=tmp_path/'index'
    ingest.approve(source,receipt,reviewer='Reviewer')
    if condition=='partial_tail':index.write_bytes(b'{"partial":')
    else:index.with_suffix('.pending').write_text('uncertain')
    before=index.read_bytes() if index.exists() else None
    with pytest.raises(ValueError):ingest.import_reviewed(source,receipt,index,write=True)
    assert (index.read_bytes() if index.exists() else None)==before

def test_short_write_leaves_evidence_and_blocks_retry(tmp_path,monkeypatch):
    source=records(tmp_path);receipt=tmp_path/'review';index=tmp_path/'index'
    ingest.approve(source,receipt,reviewer='Reviewer')
    original=os.write
    def short(fd,data):return original(fd,data[:10])
    monkeypatch.setattr(ingest.os,'write',short)
    with pytest.raises(OSError,match='short write'):ingest.import_reviewed(source,receipt,index,write=True)
    assert index.with_suffix('.pending').exists()
    before=index.read_bytes()
    with pytest.raises(ValueError,match='uncertain prior'):ingest.import_reviewed(source,receipt,index,write=True)
    assert index.read_bytes()==before

def test_explicit_backend_is_disabled_before_any_request(monkeypatch):
    import memory_backend,requests
    with pytest.raises(requests.RequestException,match='disabled'):memory_backend.post('http://127.0.0.1:11434/api/embed',json={})
    monkeypatch.setenv('LIRA_MEMORY_ALLOW_NETWORK','1');monkeypatch.setenv('LIRA_MEMORY_EMBED_URL','http://localhost:1234/embed')
    with pytest.raises(requests.RequestException,match='disabled'):memory_backend.post('http://wrong.example/embed',json={})

def test_old_workshop_path_variables_are_ignored(tmp_path,monkeypatch):
    import config
    monkeypatch.setenv('LIRA_DATA_ROOT','/private/workshop')
    monkeypatch.setenv('MEMORY_DIR','/private/workshop/memory')
    assert config.memory_dir()==tmp_path/'data'/'memory'
    monkeypatch.delenv('LIRA_MEMORY_DATA_ROOT')
    with pytest.raises(RuntimeError,match='explicit'):config.data_root()

def test_reading_event_store_does_not_create_database(tmp_path):
    from governance.capsule_bus import get_conn
    import sqlite3
    target=tmp_path/'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        with get_conn(target):pass
    assert not target.exists()

def test_events_lexical_search_and_idempotent_source(tmp_path):
    from governance.capsule_bus import initialize,append_message
    from tools.stol_szukaj import szukaj
    db=tmp_path/'sources.db';initialize(db)
    kw=dict(sender='Operator',source_id='synthetic-source',created_at=STAMP,db_path=db)
    append_message('Synthetic observatory has blue lenses',**kw)
    append_message('Synthetic observatory has blue lenses',**kw)
    with pytest.raises(ValueError,match='reused'):append_message('Different source text',**kw)
    found=szukaj('blue lenses',db=db,znaczenie='nie',kontekst=0)
    assert found['slowa'] and found['slowa'][0]['id']=='synthetic-source'

def test_night_pair_uses_injected_backend_and_no_delivery(tmp_path):
    from governance.capsule_bus import initialize,append_message
    from xian import night_batches as nb,night_core as nc,social_core as sc
    db=tmp_path/'sources.db';initialize(db)
    append_message('A synthetic garden has silver flowers',sender='Operator',source_id='social-source',created_at=STAMP,db_path=db)
    append_message('Implement API tests for the synthetic observatory',sender='Reviewer',source_id='work-source',created_at=STAMP,db_path=db)
    state=tmp_path/'night';state.mkdir()
    calls=[]
    def model(name,prompt,**kwargs):
        data=json.loads(prompt);alias=data['messages'][0]['source_id'];calls.append(name)
        if name==nb.SOCIAL_MODEL:return 'summary | | A synthetic garden\npersonal_open_threads | '+alias+' | Discuss synthetic flowers','stop',10
        return 'summary | | A synthetic API plan\ndecisions | '+alias+' | 0.7 | Implement observatory API tests','stop',10
    result=nb.run_day(date(2025,1,1),db,state,model)
    assert result['messages']==2 and result['capsules']==0 and len(calls)==2
    work=nc.read_core(state/'xian_night_core.json');social=sc.read_core(state/'xian_social_core.json')
    assert set(work['day_message_ids'])=={'work-source'}
    assert set(social['day_message_ids'])=={'social-source'}
    nb.run_day(date(2025,1,1),db,state,model)
    assert len(calls)==2
    with pytest.raises(ValueError,match='Delivery'):nb.run_day(date(2025,1,1),db,state,model,send_fn=lambda **kw:None)

@pytest.mark.parametrize('line',['decisions | S99 | 0.7 | Unbacked claim','decisions | S1 | 0.7 | '+'word '*40])
def test_consolidation_rejects_unbacked_or_oversized_items(line):
    from xian import night_core as nc
    if 'S99' in line:
        with pytest.raises(ValueError,match='rejected'):
            nc.parse_lines('summary | | Synthetic summary\n'+line,{'S1':'source-1'},done_reason='stop')
    else:
        result=nc.parse_lines('summary | | Synthetic summary\n'+line,{'S1':'source-1'},done_reason='stop')
        assert result['decisions']==[]

def test_decision_registry_requires_known_evidence_and_revision(tmp_path):
    from xian import decision_registry as dr
    path=tmp_path/'registry.json';sources={'s1':{'source_id':'s1','sender':'Operator','text':'Choose blue lenses','created_at':STAMP}}
    op={'op':'add','id':'choice-1','source_ids':['s1'],'text':'Use blue lenses'}
    doc=dr.apply_reviewed(path,[op],sources,reviewer='Reviewer',expected_revision=dr.EMPTY_REVISION)
    assert dr.active_decisions(doc)[0]['id']=='choice-1'
    before=path.read_bytes()
    with pytest.raises(ValueError,match='stale'):dr.apply_reviewed(path,[],sources,reviewer='Reviewer',expected_revision=dr.EMPTY_REVISION)
    with pytest.raises(ValueError,match='unknown'):dr.apply_reviewed(path,[dict(op,id='other',source_ids=['unknown'])],sources,reviewer='Reviewer',expected_revision=doc['revision'])
    assert path.read_bytes()==before

def test_current_source_version_is_distinguished_from_stale(tmp_path,monkeypatch):
    from agents.memory import index_freshness as fresh
    root=tmp_path/'memory';root.mkdir();(root/'embeddings').mkdir()
    source=root/'note.md';source.write_text('Current synthetic lens inventory')
    records=[]
    for ident,text in [('old','Old synthetic inventory'),('new',source.read_text())]:
        records.append({'schema':'lira.embedding_record.v1','id':ident,'text':text,'vector':[1.,0.], 'source':{'kind':'file','path':str(source)},'meta':{}})
    (root/'embeddings'/'embeddings_index.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    monkeypatch.setattr(fresh,'memory_dir',lambda:root)
    report=fresh.przebuduj()
    assert 'old' in report['stale_ids'] and 'new' not in report['stale_ids']

@pytest.mark.parametrize('order',[['vector','source','meta','text'],['text','meta','source','vector'],['source','text','vector','meta']])
def test_freshness_vector_skip_preserves_all_other_fields(order):
    from agents.memory.index_freshness import _bez_wektora
    fields={'vector':[1.,2.], 'source':{'path':'synthetic.md'},'meta':{'version':'one'},'text':'A quoted "vector" label with [brackets]'}
    value={k:fields[k] for k in order}
    compact=json.loads(_bez_wektora(json.dumps(value)))
    assert compact['vector']==[]
    assert {k:v for k,v in compact.items() if k!='vector'}=={k:v for k,v in value.items() if k!='vector'}

def test_embedding_callback_does_not_need_network(tmp_path):
    from agents.memory import semantic_retrieval as retrieval
    hits=retrieval.search('synthetic',embed=lambda text:[1.,0.])
    assert hits==[]

def test_disabled_embedding_backend_is_not_reported_as_no_matches():
    from agents.memory import semantic_retrieval as retrieval
    with pytest.raises(RuntimeError,match='embedding callback'):retrieval.search('synthetic')

def test_review_import_ids_cannot_collide_across_colons(tmp_path):
    source=tmp_path/'colon.jsonl';receipt=tmp_path/'colon.receipt.json';index=tmp_path/'index.jsonl'
    rows=[{'id':'c','text':'first','author':'Operator','created_at':STAMP,'source_id':'a:b','vector':[1.,0.]},
          {'id':'b:c','text':'second','author':'Operator','created_at':STAMP,'source_id':'a','vector':[0.,1.]}]
    source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    ingest.approve(source,receipt,reviewer='Reviewer')
    assert ingest.import_reviewed(source,receipt,index,write=True)['written']==2
    ids=[json.loads(l)['source']['import_id'] for l in index.read_text().splitlines()]
    assert len(set(ids))==2
    later=tmp_path/'later.jsonl';later_receipt=tmp_path/'later.receipt.json'
    later.write_text(json.dumps({'id':'z','text':'later','author':'Operator','created_at':STAMP,'source_id':'other','vector':[1.,1.]})+'\n')
    ingest.approve(later,later_receipt,reviewer='Reviewer')
    assert ingest.import_reviewed(later,later_receipt,index,write=True)['written']==1
