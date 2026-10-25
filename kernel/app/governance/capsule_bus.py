"""Minimal memory event store. No delivery, agents, leases or task execution."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import uuid
from config import shared_context_dir
DB_PATH=shared_context_dir()/'state'/'capsule_bus.db'

@contextmanager
def get_conn(db_path=DB_PATH):
    """Read existing sources; reading never creates or initializes a database."""
    path=Path(db_path).resolve()
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    try:yield con
    finally:con.close()

def initialize(db_path=DB_PATH):
    path=Path(db_path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS capsules(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,sender TEXT NOT NULL,recipient TEXT NOT NULL,type TEXT NOT NULL,thread_id TEXT NOT NULL,project TEXT,status TEXT,payload_json TEXT NOT NULL)')

def append_message(text,*,sender,source_id=None,created_at=None,thread_id='memory-main',recipient='shared',kind='chat_message',db_path=DB_PATH):
    if not isinstance(text,str) or not text.strip() or not sender:raise ValueError('text and sender required')
    stamp=created_at or datetime.now(timezone.utc).isoformat()
    dt=datetime.fromisoformat(stamp)
    if dt.tzinfo is None:raise ValueError('timestamp must include timezone')
    ident=source_id or str(uuid.uuid4());payload=json.dumps({'text':text},ensure_ascii=False)
    path=Path(db_path)
    if not path.is_file():raise FileNotFoundError('Initialize an explicit source store before appending')
    with sqlite3.connect(path) as db:
        old=db.execute('SELECT created_at,sender,recipient,type,thread_id,project,status,payload_json FROM capsules WHERE id=?',(ident,)).fetchone()
        row=(stamp,sender,recipient,kind,thread_id,'memory','recorded',payload)
        if old:
            if old!=row:raise ValueError('source ID reused with different content')
            return ident
        db.execute('INSERT INTO capsules VALUES(?,?,?,?,?,?,?,?,?)',(ident,*row))
    return ident
