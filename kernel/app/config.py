"""Memory-only configuration. No dotenv, home discovery or daemon startup."""
import os
from pathlib import Path

def env(name, default=''):
    return os.getenv(name if name.startswith('LIRA_') else 'LIRA_'+name, default)

def project_root():
    return Path(__file__).resolve().parent

def data_root():
    value=os.getenv('LIRA_MEMORY_DATA_ROOT')
    if not value:
        raise RuntimeError('Set LIRA_MEMORY_DATA_ROOT to an explicit private data directory')
    return Path(value).expanduser().resolve()

def memory_dir(): return data_root()/'memory'
def runtime_dir(): return data_root()/'runtime'
def shared_context_dir(): return data_root()/'shared_context'
def logs_root(): return runtime_dir()/'logs'
def rebuild_dir(): return runtime_dir()/'rebuild'
def archive_root(): return data_root()/'archives'
def secure_root(): return data_root()/'secure'
def queue_file(): return runtime_dir()/'queue_tasks.json'

def paths():
    md,rt,sc=memory_dir(),runtime_dir(),shared_context_dir()
    return {'PROJECT_ROOT':project_root(),'DATA_ROOT':data_root(),'MEMORY_DIR':md,
       'RUNTIME_DIR':rt,'STATE_DIR':rt/'state','SHARED_CONTEXT_DIR':sc,
       'EVENTS_DIR':sc/'events','RUNTIME_STREAM':sc/'events'/'runtime.jsonl',
       'MEMORY_INDEX':md/'root'/'memory_index.json','EMBEDDINGS_DIR':md/'embeddings',
       'EMBEDDINGS_INDEX':md/'embeddings'/'embeddings_index.jsonl',
       'BASE_INDEX_DB':md/'root'/'base_index.sqlite3','BASE_INDEX':md/'root'/'base_index.json',
       'TURMALIN_CACHE':md/'embeddings'/'turmalin_cache.jsonl',
       'TURMALIN_STATE':rt/'state'/'turmalin_state.json',
       'MEMORY_STATE':rt/'state'/'memory_state.json',
       'REBUILD_DIR':rebuild_dir(),'CORE_MEMORY_REBUILD':rebuild_dir()/'core_memory.json',
       'CORE_MEMORY_REBUILD_MD':rebuild_dir()/'core_memory.md',
       'SNAPSHOTS_DIR':md/'snapshots','SNAPSHOTS_MD_DIR':md/'snapshots_md','DIARY_DIR':md/'diary'}

def ensure_dirs():
    root=data_root();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    for path in (memory_dir(),memory_dir()/'root',memory_dir()/'embeddings',memory_dir()/'diary',runtime_dir()/'state',runtime_dir()/'logs',shared_context_dir()/'events',shared_context_dir()/'state',rebuild_dir()):
        path.mkdir(parents=True,exist_ok=True,mode=0o700)

def in_night_window(now=None):
    from datetime import datetime
    hour=(now or datetime.now()).hour
    start=int(os.getenv('LIRA_MEMORY_NIGHT_START','0'));end=int(os.getenv('LIRA_MEMORY_NIGHT_END','24'))
    return start<=hour<end if start<=end else hour>=start or hour<end
