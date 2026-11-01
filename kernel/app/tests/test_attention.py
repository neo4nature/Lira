from pathlib import Path
from datetime import datetime, timezone
import hashlib,json,sqlite3
import pytest
from agents.memory import attention,concept_card
from governance import shared_memory_context

NOW=datetime(2026,1,1,12,0,1,tzinfo=timezone.utc)


def synthetic_card(root, name='tea', reviewed=True):
    folder=Path(root)/name;folder.mkdir(parents=True)
    text='We decided to keep tea, not coffee, in the kitchen cupboard.'
    source=dict(id='message:previous',author='User',occurred_at='2025-12-20T12:00:00+00:00',
                text=text,text_sha256=hashlib.sha256(text.encode()).hexdigest())
    node=dict(schema='lira.concept-content.v1',concept_id='concept:tea',label='kitchen drinks',aliases=['tea cupboard'],
              related_terms_not_aliases=['kettle'],revision=1,vector=None,importance=None,
              definition=dict(author='ReviewerA',text='A previous choice of kitchen drink.'),
              sources=[source],senses=[dict(sense_id='main')],
              claims=[dict(claim_id='claim1',sense_id='main',speaker='User',text=text,confidence=None,
                           evidence=[dict(source_id=source['id'],quote=text)])],historical_interpretations=[],
              mentions=[dict(source_id=source['id'],author='User',occurred_at=source['occurred_at'])],
              perspectives=dict(Soryel=None,Xian=None),open_questions=['Later preferences have not been checked.'])
    concept_card.save(node,folder/'CARD.json');sha=hashlib.sha256((folder/'CARD.json').read_bytes()).hexdigest()
    manifest=dict(status='reviewed' if reviewed else 'pending_review',concept_id=node['concept_id'],
                  files={'CARD.json':sha},reviews=[dict(reviewer=r,card_sha256=sha) for r in ['ReviewerA','ReviewerB']])
    (folder/'MANIFEST.json').write_text(json.dumps(manifest));return folder


def test_pending_card_is_not_offered(tmp_path):
    synthetic_card(tmp_path,reviewed=False)
    assert attention.offer(tmp_path,'tea cupboard',reviewers=['ReviewerA','ReviewerB'])==([], {})


def test_alias_related_and_unrelated(tmp_path):
    synthetic_card(tmp_path)
    for text,expected in [('tea cupboard',True),('a kettle',True),('a kettlebell',False),('hello',False)]:
        found,errors=attention.offer(tmp_path,text,reviewers=['ReviewerA','ReviewerB'])
        assert bool(found)==expected and not errors
    assert attention.offer(tmp_path,'kettle',reviewers=['ReviewerA','ReviewerB'])[0][0]['matched_by']=='related_term'


def test_corrupt_card_is_isolated_and_reported(tmp_path):
    synthetic_card(tmp_path);bad=tmp_path/'bad';bad.mkdir();(bad/'CARD.json').write_text('{}');(bad/'MANIFEST.json').write_text('[]')
    found,errors=attention.offer(tmp_path,'tea cupboard',reviewers=['ReviewerA','ReviewerB'])
    assert len(found)==1 and errors


def test_changed_reviewed_content_not_offered(tmp_path):
    folder=synthetic_card(tmp_path);p=folder/'CARD.json';p.write_text(p.read_text().replace('tea, not coffee','coffee, not tea'))
    found,errors=attention.offer(tmp_path,'tea cupboard',reviewers=['ReviewerA','ReviewerB'])
    assert not found and errors


def test_full_offline_pipeline_two_readers_same_packet(tmp_path):
    synthetic_card(tmp_path/'cards');calls=[]
    def retrieve(query):
        calls.append(query);return [dict(source_id='synthetic:note',text='A synthetic kitchen note.')]
    message=dict(id='current',created_at='2026-01-01T12:00:00+00:00',text='What was in the tea cupboard?')
    packet=attention.build_packet(message,tmp_path/'cards',channel='support',reviewers=['ReviewerA','ReviewerB'],retrieve=retrieve,now=NOW)
    assert len(calls)==2
    body=attention.render_packet(packet)
    assert 'User 2025-12-20' in body and 'sources through 2025-12-20' in body and 'Later preferences' in body
    db=tmp_path/'source.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE capsules(id TEXT, sender TEXT, recipient TEXT, type TEXT, thread_id TEXT,created_at TEXT,payload_json TEXT)')
        c.execute('INSERT INTO capsules VALUES(?,?,?,?,?,?,?)',('current','User','shared','chat_message','memory-main',message['created_at'],json.dumps({'text':message['text']})))
    written=shared_memory_context.publish(db,tmp_path/'contexts',packet,now=NOW,render=attention.render_packet)
    assert shared_memory_context.read_context(db,tmp_path/'contexts','ReaderA','current')==written
    assert shared_memory_context.read_context(db,tmp_path/'contexts','ReaderB','current')==written
    assert 'Historical evidence' in written['text']
    assert packet['message']['channel']=='support'
    assert 'wiadomosc' not in packet and 'kanal' not in packet['message']


def test_retrieval_requires_source_locator(tmp_path):
    with pytest.raises(ValueError,match='retrieval_source'):
        attention.build_packet(dict(id='x',created_at=NOW.isoformat(),text='hello'),tmp_path,
                               channel='support',reviewers=['ReviewerA'],retrieve=lambda q:[dict(text='unattributed')],now=NOW)


def test_host_supplies_channel_and_duplicate_evidence_is_rendered_once(tmp_path):
    synthetic_card(tmp_path/'cards')
    quote='We decided to keep tea, not coffee, in the kitchen cupboard.'
    message=dict(id='source',created_at=NOW.isoformat(),text='tea cupboard')
    packet=attention.build_packet(message,tmp_path/'cards',channel='customer-service',
          reviewers=['ReviewerA','ReviewerB'],retrieve=lambda q:[dict(source_id='message:previous',text=quote)],now=NOW)
    assert packet['message']==dict(id='source',ts=NOW.isoformat(),channel='customer-service',text='tea cupboard')
    assert attention.render_packet(packet).count(quote)==1
    assert packet['concepts'][0]['background'] and packet['specific'] # raw evidence preserved
    with pytest.raises(ValueError,match='channel_required'):
        attention.build_packet(message,tmp_path/'cards',channel='',reviewers=['ReviewerA'],retrieve=lambda q:[],now=NOW)
