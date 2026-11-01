import importlib.util,json,sqlite3
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pytest
from governance import shared_memory_context as m
ROOT='11111111-2222-4333-8444-555555555555'
COPY='22222222-2222-4333-8444-555555555555'
NOW=datetime(2026,10,31,20,0,2,tzinfo=timezone.utc)

@pytest.fixture
def fixture(tmp_path):
 db=tmp_path/'bus.db';c=sqlite3.connect(db)
 c.execute('CREATE TABLE capsules(id TEXT,sender TEXT,recipient TEXT,type TEXT,thread_id TEXT,created_at TEXT,payload_json TEXT)')
 for cid,recipient,extra in [(ROOT,'shared',{}),(COPY,'ReaderA',{'round_id':ROOT})]:
  c.execute('INSERT INTO capsules VALUES (?,?,?,?,?,?,?)',(cid,'User',recipient,'chat_message','memory-main','2026-10-31T20:00:00+00:00',json.dumps({'text':'Do you remember tea?',**extra})))
 c.commit();c.close()
 packet={'version':'0.4','message':{'id':ROOT,'ts':'2026-10-31T20:00:00+00:00','channel':'discussion','text':'Do you remember tea?'},'created_at':'2026-10-31T20:00:01+00:00','retrieval_id':'ret_fixture','results':[]}
 p=tmp_path/'paczki.jsonl';p.write_text(json.dumps(packet)+'\n')
 return db,p,packet


def publish(f, **kw):
 return m.publish(f[0], f[1].parent/'store', f[2], now=NOW, render=kw.get('render', lambda p:'User: tea, not coffee'))

def test_both_recipients_receive_exact_same_frozen_record(fixture):
 r=publish(fixture)
 assert m.read_context(fixture[0],fixture[1].parent/'store','ReaderA',COPY)==r
 assert m.read_context(fixture[0],fixture[1].parent/'store','ReaderB',ROOT)==r
 assert r['status']=='prepared'

def test_retry_never_rerenders_changed_cards(fixture):
 r=publish(fixture)
 assert publish(fixture,render=lambda p:(_ for _ in ()).throw(AssertionError()))==r

def test_changed_observer_packet_is_conflict(fixture):
 publish(fixture);fixture[2]['retrieval_id']='other'
 with pytest.raises(ValueError,match='conflict'):publish(fixture)

def test_changed_stored_bytes_fail_closed(fixture):
 publish(fixture);p=m._path(fixture[1].parent/'store',ROOT)
 r=json.loads(p.read_text());r['text']='forged';p.write_text(json.dumps(r))
 with pytest.raises(ValueError,match='hash'):m.read_context(fixture[0],p.parent,'ReaderA',COPY)

def test_delayed_delivery_not_expired(fixture):
 publish(fixture)
 assert m.read_context(fixture[0],fixture[1].parent/'store','ReaderA',COPY)['status']=='prepared'

@pytest.mark.parametrize('field,value',[('text','other'),('ts','2026-10-31T19:00:00+00:00'),('channel','')])
def test_wrong_source_never_published(fixture,field,value):
 fixture[2]['message'][field]=value
 with pytest.raises(ValueError,match='source'):publish(fixture)

@pytest.mark.parametrize('key,status',[('not_needed','not_needed'),('skipped','observer_skipped')])
def test_empty_decisions_frozen_with_reason(fixture,key,status):
 fixture[2][key]='reason';r=publish(fixture)
 assert r['status']==status and r['text']=='' and r['reason']=='reason'

def test_failed_atomic_publish_leaves_no_final(fixture,monkeypatch):
 monkeypatch.setattr(m.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('disk')))
 with pytest.raises(OSError):publish(fixture)
 assert not m._path(fixture[1].parent/'store',ROOT).exists()
 assert not list((fixture[1].parent/'store').glob('.context-*'))

def test_missing_is_final_late_producer_cannot_reverse(fixture):
 folder=fixture[1].parent/'store'
 missing=m.read_context(fixture[0],folder,'ReaderA',COPY,now_fn=lambda:NOW+timedelta(seconds=28))
 assert missing['status']=='missing'
 assert m.publish(fixture[0],folder,fixture[2],now=NOW+timedelta(seconds=29))==missing
 assert m.read_context(fixture[0],folder,'ReaderB',ROOT)==missing


def test_renderer_crosses_deadline_both_get_missing(fixture):
 t=[NOW]
 def render(p):t[0]=NOW+timedelta(seconds=29);return 'late body'
 r=m.publish(fixture[0],fixture[1].parent/'store',fixture[2],now_fn=lambda:t[0],render=render)
 assert r['status']=='missing' and r['text']==''
 assert m.read_context(fixture[0],fixture[1].parent/'store','ReaderA',COPY)==r


def test_before_deadline_pending_then_publication_and_delayed_reader(fixture):
 folder=fixture[1].parent/'store'
 assert m.read_context(fixture[0],folder,'ReaderB',ROOT,now_fn=lambda:NOW) is None
 r=publish(fixture)
 assert m.read_context(fixture[0],folder,'ReaderB',ROOT,now_fn=lambda:NOW+timedelta(days=1))==r


def test_late_start_does_not_render_historical_packets(fixture):
 r=m.publish(fixture[0],fixture[1].parent/'store',fixture[2],now=NOW+timedelta(hours=1),
 render=lambda p:(_ for _ in ()).throw(AssertionError('late render')))
 assert r['status']=='missing'

def test_waiters_share_terminal_missing(fixture):
 folder=fixture[1].parent/'store';t=[NOW]
 def sleep(seconds):t[0]+=timedelta(seconds=seconds)
 a=m.wait_context(fixture[0],folder,'ReaderA',COPY,now_fn=lambda:t[0],sleep=sleep)
 b=m.wait_context(fixture[0],folder,'ReaderB',ROOT,now_fn=lambda:t[0])
 assert a==b and a['status']=='missing'
