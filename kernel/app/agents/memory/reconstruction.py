"""Recover context from an explicit source selection, without personal identity."""
def rebuild_context(records,source_ids):
    wanted=list(dict.fromkeys(source_ids));available={r['id']:r for r in records}
    missing=[ident for ident in wanted if ident not in available]
    if missing:raise ValueError('Missing referenced source IDs: '+','.join(missing))
    return {'schema':'lira.memory_reconstruction.v1','source_ids':wanted,'records':[dict(available[i]) for i in wanted]}
