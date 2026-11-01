"""PA-057 real content layer: stable topic address, attributed sources; no vectors or services."""
import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import unicodedata


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def address(text):
    text=unicodedata.normalize('NFKD',text.casefold()).replace('ł','l')
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).split())


def validate(node):
    if node.get('schema')!='lira.concept-content.v1' or not node.get('concept_id','').startswith('concept:'):
        raise ValueError('schema')
    if node.get('vector') is not None or node.get('importance') is not None:
        raise ValueError('unmeasured_representation')
    sources=node['sources']
    if len({s['id'] for s in sources})!=len(sources):raise ValueError('duplicate_source')
    by={s['id']:s for s in sources}
    for s in sources:
        if s['text_sha256']!=hashlib.sha256(s['text'].encode()).hexdigest():raise ValueError('source_hash')
        if not s.get('author') or datetime.fromisoformat(s['occurred_at']).tzinfo is None:raise ValueError('source_metadata')
    claims=node['claims']
    if len({c['claim_id'] for c in claims})!=len(claims):raise ValueError('duplicate_claim')
    senses={s['sense_id'] for s in node['senses']}
    def evidence(items):
        if not items:raise ValueError('missing_evidence')
        ids=[e['source_id'] for e in items]
        if len(set(ids))!=len(ids):raise ValueError('duplicate_evidence')
        for e in items:
            if e['source_id'] not in by:raise ValueError('unknown_source')
            if not e['quote'] or e['quote'] not in by[e['source_id']]['text']:raise ValueError('quote_changed')
    for c in claims:
        if c['sense_id'] not in senses or c['confidence'] is not None:raise ValueError('claim_sense_or_confidence')
        evidence(c['evidence'])
        if any(by[e['source_id']]['author']!=c['speaker'] for e in c['evidence']):raise ValueError('claim_author')
    for i in node['historical_interpretations']:
        evidence(i['evidence'])
        if any(by[e['source_id']]['author']!=i['author'] for e in i['evidence']):raise ValueError('interpretation_author')
    history=node['mentions']
    if len({m['source_id'] for m in history})!=len(history) or {m['source_id'] for m in history}!=set(by):raise ValueError('mention_coverage')
    if [m['occurred_at'] for m in history]!=sorted(m['occurred_at'] for m in history):raise ValueError('mention_order')
    for m in history:
        s=by[m['source_id']]
        if m['author']!=s['author'] or m['occurred_at']!=s['occurred_at']:raise ValueError('mention_metadata')
    if node['perspectives']['Soryel'] is not None or node['perspectives']['Xian'] is not None:raise ValueError('unprovided_personal_perspective')
    return node


def save(node,path):
    validate(node)
    target=Path(path);tmp=target.with_suffix('.tmp')
    envelope={'schema':'lira.concept-content.snapshot.v1','sha256':checksum(node),'node':node}
    tmp.write_text(json.dumps(envelope,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(target)


def load(path):
    data=json.loads(Path(path).read_text())
    if data.get('schema')!='lira.concept-content.snapshot.v1' or checksum(data['node'])!=data.get('sha256'):raise ValueError('snapshot_hash')
    return validate(data['node'])


def lookup(node,topic,*,sense=None):
    validate(node)
    aliases={address(x) for x in [node['concept_id'],node['label'],*node['aliases']]}
    if address(topic) not in aliases:return None
    if sense is not None and sense not in {s['sense_id'] for s in node['senses']}:raise ValueError('unknown_sense')
    result=copy.deepcopy(node)
    if sense:
        result['claims']=[c for c in result['claims'] if c['sense_id']==sense]
        result['senses']=[s for s in result['senses'] if s['sense_id']==sense]
        # Full attributed history remains available; selecting a sense does not erase context.
        result['selection']={'sense_id':sense,'history_scope':'all_topic_mentions'}
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('topic');p.add_argument('--sense');p.add_argument('--json',action='store_true');p.add_argument('--snapshot',required=True,help='Explicit reviewed card snapshot path')
    a=p.parse_args();node=lookup(load(a.snapshot),a.topic,sense=a.sense)
    if node is None:raise SystemExit('Nieznany adres pojęcia')
    if a.json:print(json.dumps(node,ensure_ascii=False,indent=2))
    else:
        print(node['concept_id']+' — '+node['label'])
        print(node['definition']['text'])
        print('Status: '+node['status'])
        for c in node['claims']:print(c['sense_id']+': '+c['text'])
        print('Powroty tematu (oryginały, nie licznik prawdziwości):')
        for m in node['mentions']:print(m['occurred_at']+' '+m['author']+' '+m['source_id'])
