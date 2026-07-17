from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Tuple
from config import paths

def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding='utf-8', errors='ignore') or '{}')
    except Exception:
        return {}

def allows_background(require_veto_none: bool = False) -> Tuple[bool, str]:
    rt = Path(paths()['RUNTIME_DIR'])
    bus_path = rt / 'state' / 'bus_signals.json'
    bus = _read_json(bus_path)

    convo = bool(bus.get('conversation_active', False))
    veto_doc = (bus.get('operational_veto') or bus.get('veto') or {})
    if isinstance(veto_doc, str):
        veto = {'active': veto_doc.lower() != 'none', 'severity': veto_doc.lower()}
    elif isinstance(veto_doc, dict):
        veto = veto_doc
    else:
        veto = {'active': False, 'severity': 'none'}
    models = bus.get('models') if isinstance(bus.get('models'), dict) else {}
    allow_bg = bool(models.get('allow_background_tasks', bus.get('background_allowed', True)))

    if convo:
        return False, 'conversation_active'
    severity = str(veto.get('severity','none')).lower()
    active = bool(veto.get('active', False))
    v = severity
    if active and v == 'hard':
        return False, 'veto_hard'
    if require_veto_none and active and v != 'none':
        return False, f'veto_{v}'
    if not allow_bg:
        return False, 'background_disabled'
    return True, 'ok'
