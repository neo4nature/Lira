"""Real embeddings through a local Ollama. Requires an explicit private output folder.

Before running:  ollama pull mxbai-embed-large
This script opts in to the network itself, but only for the local endpoint below.
"""
import argparse
import json
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data-dir',required=True,type=Path)
parser.add_argument('--endpoint',default='http://127.0.0.1:11434/api/embed')
args=parser.parse_args()
os.environ['LIRA_MEMORY_DATA_ROOT']=str(args.data_dir.resolve())
os.environ['LIRA_MEMORY_ALLOW_NETWORK']='1'
os.environ['LIRA_MEMORY_EMBED_URL']=args.endpoint
os.environ.setdefault('LIRA_EMBED_MODEL','mxbai-embed-large:latest')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'kernel/app'))
from config import paths
from memory_backend import post
from agents.memory import reviewed_import as importer
from agents.memory.semantic_retrieval import search

def embed(text):
    r=post(args.endpoint,json={'model':os.environ['LIRA_EMBED_MODEL'],'input':text},timeout=120)
    r.raise_for_status()
    return r.json()['embeddings'][0]

args.data_dir.mkdir(parents=True,exist_ok=True)
source=args.data_dir/'notes.jsonl'
notes=[('tea','We decided to keep tea, not coffee, in the kitchen cupboard.'),
       ('bike','The bicycle needs a new rear tyre before the weekend trip.'),
       ('plants','Water the tomato plants every second evening in summer.')]
source.write_text(''.join(json.dumps({'id':k,'text':t,'author':'Operator','created_at':'2025-01-01T10:00:00+00:00','source_id':'quickstart'})+'\n' for k,t in notes))
receipt=args.data_dir/'notes.review.json'
importer.approve(source,receipt,reviewer='Operator')
print('import:',importer.import_reviewed(source,receipt,paths()['EMBEDDINGS_INDEX'],embed=embed,write=True))
for question in ('What drink do we keep at home?','What should be fixed on the bike?'):
    hit=search(question,1)[0]
    print(f'{question} -> {hit["text"]} (score {hit["score"]})')
