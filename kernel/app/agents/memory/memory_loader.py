import json
from pathlib import Path

def _atomic_write_text(path: Path, text: str) -> None:
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix='.tmp', prefix=path.stem + '_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

def read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        try:
            p = Path(path)
            bak = p.with_suffix(p.suffix + '.bak')
            with open(bak, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists():
            bak = path.with_suffix(path.suffix + '.bak')
            bak.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
    except Exception:
        pass
    payload = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
    _atomic_write_text(path, payload)
