"""Memory-local append-only observation stream; not a daemon."""
from datetime import datetime,timezone
import json
from config import paths

def append_event(kind,source,payload=None,level='info'):
    path=paths()['RUNTIME_STREAM'];path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    row={'ts':datetime.now(timezone.utc).isoformat(),'kind':kind,'source':source,'level':level,'payload':payload or {}}
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
