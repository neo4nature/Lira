"""Backend availability callback, scoped to the memory library process."""
from contextlib import contextmanager
import threading
_lock=threading.Lock()
_active=0
_probe=None

def set_busy_probe(callback=None):
    global _probe
    if callback is not None and not callable(callback):raise TypeError('callback must be callable')
    _probe=callback

def is_chat_busy():
    with _lock: busy=_active>0
    return busy or bool(_probe and _probe())

@contextmanager
def chat_busy(who='memory'):
    global _active
    with _lock:_active+=1
    try:yield
    finally:
        with _lock:_active-=1
