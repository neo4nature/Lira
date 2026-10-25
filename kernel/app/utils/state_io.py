import json
import os
import time
from typing import Any, Dict, Optional

def now_ts() -> float:
    return time.time()

def read_json(path: str, default: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    if default is None:
        default = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return dict(default)
    except Exception:
        return dict(default)

def write_json_atomic(path: str, data: Dict[str, Any]) -> None:
    import tempfile
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp', prefix=os.path.basename(path) + '_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def is_fresh(doc: Dict[str, Any], max_age_s: float, ts_key: str='ts_epoch') -> bool:
    ts = doc.get(ts_key)
    if ts is None:
        return False
    try:
        return now_ts() - float(ts) <= float(max_age_s)
    except Exception:
        return False

def get_env_bool(name: str, default: bool=False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}

def get_env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return int(v.strip())
    except Exception:
        return default

def get_env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return float(v.strip())
    except Exception:
        return default
