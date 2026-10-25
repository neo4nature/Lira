from __future__ import annotations
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any
from config import ensure_dirs, paths

@dataclass
class MemoryFile:
    path: str
    bytes: int
    mtime: str

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec='seconds')

def build_index(memory_dir: Path, limit: int=2000) -> Dict[str, Any]:
    files: List[MemoryFile] = []
    for fp in memory_dir.rglob('*'):
        if not fp.is_file():
            continue
        if fp.suffix.lower() not in {'.json', '.md', '.txt'}:
            continue
        rel = fp.relative_to(memory_dir)
        st = fp.stat()
        files.append(MemoryFile(path=str(rel), bytes=int(st.st_size), mtime=_iso(st.st_mtime)))
        if len(files) >= limit:
            break
    files.sort(key=lambda x: x.mtime, reverse=True)
    return {'updated_at': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds') + 'Z', 'count': len(files), 'files': [asdict(f) for f in files]}

def main():
    ensure_dirs()
    p = paths()
    memory_dir = Path(p['MEMORY_DIR'])
    out = Path(p['MEMORY_INDEX'])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_index(memory_dir), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[OK] memory index written to {out}')
if __name__ == '__main__':
    main()
