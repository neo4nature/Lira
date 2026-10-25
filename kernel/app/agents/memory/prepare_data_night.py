"""Explicit consolidation input preparation, no machine telemetry or scheduler."""
from xian.night_core import load_day_messages

def prepare(db_path,start,end):
    return {'window_start':start.isoformat(),'window_end':end.isoformat(),'messages':load_day_messages(db_path,start,end)}
