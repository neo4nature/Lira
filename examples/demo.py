"""Offline synthetic memory roundtrip. Requires an explicit private output folder."""
import argparse
import json
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data-dir',required=True,type=Path)
args=parser.parse_args()
os.environ['LIRA_MEMORY_DATA_ROOT']=str(args.data_dir.resolve())
os.environ.pop('LIRA_MEMORY_ALLOW_NETWORK',None)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'kernel/app'))
from config import paths
from agents.memory import reviewed_import as importer,retrieval_usage as usage
from agents.memory.semantic_retrieval import search
from agents.memory.memory_orchestrator import build_context_packet

source=Path(__file__).with_name('records.jsonl')
receipt=args.data_dir/'review.json'
importer.approve(source,receipt,reviewer='Reviewer')
imported=importer.import_reviewed(source,receipt,paths()['EMBEDDINGS_INDEX'],write=True)
# A fixed synthetic vector illustrates the data flow. It is not a language model
# and does not measure semantic quality or embedding/model performance.
packet=build_context_packet('Where are the blue lenses?',retrieve=lambda q,top_k:search(q,top_k,embed=lambda text:[1.,0.,0.]))
hits=packet['retrieval']
observed=usage.record_retrieval(packet['query'],hits,actor='Operator')
usage.record_outcome(observed['retrieval_id'],hits[0]['id'],'used',actor='Reviewer',note='Synthetic demonstration selected this source')
assert 'blue lenses' in hits[0]['text']
print(json.dumps({'synthetic_only':True,'network_used':False,'records_in_input':imported['records'],'new_records':imported['written'],'first_hit':hits[0]['text'],'retrieval_and_use_are_separate':True},indent=2))
