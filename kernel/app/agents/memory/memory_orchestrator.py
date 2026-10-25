"""Build a context packet explicitly; no phase/policy/execution integration."""
from datetime import datetime,timezone

def build_context_packet(query,*,retrieve,top_k=5):
    if not callable(retrieve):raise TypeError('retrieve callback required')
    results=retrieve(query,top_k=top_k)
    return {'schema':'lira.memory_context.v1','created_at':datetime.now(timezone.utc).isoformat(),'query':query,'retrieval':results,'note':'Retrieved results are candidates, not proof of use or truth.'}
