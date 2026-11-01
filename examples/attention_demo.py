"""A synthetic shared-context round. No model, network, private data or agents."""
import argparse
from pathlib import Path
import sys
import os
import json
import hashlib
from datetime import datetime,timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'kernel/app'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',required=True,help='New or empty explicit directory for this demo')
    root=Path(parser.parse_args().data_dir).expanduser().resolve()
    root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()):
        raise SystemExit('Use an empty directory; existing data will not be overwritten.')
    os.environ.setdefault('LIRA_MEMORY_DATA_ROOT',str(root/'library-data'))
    from agents.memory import concept_card,attention
    from governance import capsule_bus,shared_memory_context
    source=dict(id='synthetic:previous',author='User',occurred_at='2026-01-01T12:00:00+00:00',
                text='We keep tea, not coffee, in the kitchen cupboard.')
    source['text_sha256']=hashlib.sha256(source['text'].encode()).hexdigest()
    node=dict(schema='lira.concept-content.v1',revision=1,concept_id='concept:kitchen-drinks',
              label='kitchen drinks',aliases=['tea cupboard'],related_terms_not_aliases=['kettle'],
              vector=None,importance=None,definition=dict(author='ReviewerA',text='A previous kitchen choice.'),
              sources=[source],senses=[dict(sense_id='main')],
              claims=[dict(claim_id='claim1',sense_id='main',speaker='User',text=source['text'],confidence=None,
                           evidence=[dict(source_id=source['id'],quote=source['text'])])],
              historical_interpretations=[],mentions=[dict(source_id=source['id'],author='User',occurred_at=source['occurred_at'])],
              perspectives=dict(Soryel=None,Xian=None),open_questions=['New preferences may not be recorded here.'])
    folder=root/'cards/kitchen-drinks';folder.mkdir(parents=True)
    concept_card.save(node,folder/'CARD.json')
    sha=hashlib.sha256((folder/'CARD.json').read_bytes()).hexdigest()
    # Synthetic demo receipts only, not signatures or real independent review.
    (folder/'MANIFEST.json').write_text(json.dumps(dict(status='reviewed',concept_id=node['concept_id'],
        files={'CARD.json':sha},reviews=[dict(reviewer=r,card_sha256=sha) for r in ['ReviewerA','ReviewerB']]),indent=2)+'\n')
    db=root/'sources.db';capsule_bus.initialize(db)
    message=dict(id='synthetic:current',created_at=datetime.now(timezone.utc).isoformat(),text='What do we keep in the tea cupboard?')
    capsule_bus.append_message(message['text'],sender='User',source_id=message['id'],created_at=message['created_at'],db_path=db)
    # Replace this callback with the library's lexical/semantic search adapter.
    # Keep source_id when adapting real results. No backend is enabled by this demo.
    def retrieve(query):
        return [dict(source_id=source['id'],text=source['text'])]
    packet=attention.build_packet(message,root/'cards',channel='demo',reviewers=['ReviewerA','ReviewerB'],retrieve=retrieve)
    shared_memory_context.publish(db,root/'contexts',packet,render=attention.render_packet)
    first=shared_memory_context.wait_context(db,root/'contexts','ReaderA',message['id'])
    second=shared_memory_context.wait_context(db,root/'contexts','ReaderB',message['id'])
    assert first==second
    print('ReaderA:',first['status'],first['packet_id'],first['sha256'])
    print('ReaderB:',second['status'],second['packet_id'],second['sha256'])
    print(first['text'])


if __name__=='__main__':main()
