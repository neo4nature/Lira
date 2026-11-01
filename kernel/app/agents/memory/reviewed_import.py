"""Explicit reviewed JSONL import; no discovery of programs, homes or sessions.

Review receipts bind source bytes. They attest a caller's approval, not identity
or a cryptographic signature. All writers sharing an index must be serialized.
An interrupted append leaves a pending marker and refuses further writes rather
than truncating uncertain evidence. No automatic repair or pruning is provided.
"""
from agents.memory.index_lock import wylacznie
from pathlib import Path
import fcntl
import hashlib
import json
import math
import os
from agents.memory.memory_loader import _atomic_write_text

def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def _digest(data):return hashlib.sha256(data).hexdigest()

def read_records(path):
    with Path(path).open('rb') as source:
        raw=source.read(16*1024*1024+1)
    if len(raw)>16*1024*1024:raise ValueError('input quota')
    rows=[];seen=set()
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():continue
        row=json.loads(line)
        if not isinstance(row,dict) or set(row)-{'id','text','author','created_at','source_id','vector'}:raise ValueError('unsupported record fields')
        if not all(isinstance(row.get(k),str) and row[k].strip() for k in ('id','text','author','created_at','source_id')):raise ValueError('missing source metadata')
        from datetime import datetime
        if datetime.fromisoformat(row['created_at']).tzinfo is None:raise ValueError('timezone required')
        if row['id'] in seen:raise ValueError('duplicate record ID')
        if len(row['text'])>2000:raise ValueError('reviewed record exceeds 2000 characters; chunk before review')
        if 'vector' in row:
            v=row['vector']
            if not isinstance(v,list) or not v or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in v):raise ValueError('invalid embedding')
        _canonical(row);seen.add(row['id']);rows.append(row)
    if not rows:raise ValueError('empty input')
    return raw,rows

def approve(source,receipt,*,reviewer):
    if not reviewer.strip():raise ValueError('reviewer required')
    raw,rows=read_records(source)
    value={'schema':'lira.reviewed-input.v1','input_sha256':_digest(raw),'reviewer':reviewer,'record_hashes':{r['id']:_digest(_canonical(r).encode()) for r in rows}}
    _atomic_write_text(Path(receipt),json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    return value

def import_reviewed(source,receipt,index,*,embed=None,model='provided-vector',write=False):
    raw,rows=read_records(source);review=json.loads(Path(receipt).read_text())
    hashes={r['id']:_digest(_canonical(r).encode()) for r in rows}
    if review.get('schema')!='lira.reviewed-input.v1' or review.get('input_sha256')!=_digest(raw) or review.get('record_hashes')!=hashes or not review.get('reviewer'):raise ValueError('review does not match exact source')
    target=Path(index);marker=target.with_suffix(target.suffix+'.pending')
    if not write:return {'records':len(rows),'written':0,'dry_run':True}
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with target.with_suffix(target.suffix+'.write.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if marker.exists():raise ValueError('uncertain prior write; inspect pending marker manually')
        old={}
        if target.exists():
            prior=target.read_bytes()
            if prior and not prior.endswith(b'\n'):raise ValueError('incomplete index tail')
            for line in prior.splitlines():
                r=json.loads(line);src=r.get('source',{})
                if src.get('import_id'):
                    if src['import_id'] in old and old[src['import_id']]!=r['meta']['reviewed_sha256']:raise ValueError('conflicting existing import IDs')
                    old[src['import_id']]=r['meta']['reviewed_sha256']
        pending=[]
        for r in rows:
            ident=r['source_id'].replace('%','%25').replace(':','%3A')+':'+r['id'];digest=hashes[r['id']]
            if ident in old:
                if old[ident]!=digest:raise ValueError('same source record changed; explicit version required')
                continue
            vec=r.get('vector')
            if vec is None:
                if embed is None:raise ValueError('explicit embedding callback required')
                vec=embed(r['text'])
            if not isinstance(vec,list) or not vec or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vec):raise ValueError('invalid embedding result')
            pending.append({'schema':'lira.embedding_record.v1','id':'reviewed:'+ident,'ts':r['created_at'],'model':model,'text':r['text'],'vector':vec,'source':{'kind':'reviewed','logical_source_id':ident,'import_id':ident,'author':r['author'],'source_id':r['source_id']},'meta':{'reviewed_sha256':digest,'input_sha256':review['input_sha256']}})
        if not pending:return {'records':len(rows),'written':0,'dry_run':False}
        _atomic_write_text(marker,json.dumps({'input_sha256':review['input_sha256'],'records':len(pending)})+'\n')
        dirfd=os.open(target.parent,os.O_RDONLY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
        with wylacznie(target):
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:
                for rec in pending:
                    data=(_canonical(rec)+'\n').encode()
                    if os.write(fd,data)!=len(data):raise OSError('short write; pending marker retained')
                os.fsync(fd)
            finally:os.close(fd)
        marker.unlink()
        dirfd=os.open(target.parent,os.O_RDONLY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
        return {'records':len(rows),'written':len(pending),'dry_run':False}
