"""Small availability interface; no ecosystem bus or phase files."""
from xian.chat_busy import is_chat_busy

def allows_background(require_veto_none=False):
    return (False,'backend_busy') if is_chat_busy() else (True,'ok')
