# Independent review checklist

Candidate only. No service was deployed and no remote repository was updated.

Review the entire candidate and hashes, not only changed filenames. This package contains no private test corpus. A synthetic end-to-end test is not evidence of performance or completeness on a user's data.

Material differences from the workshop implementation:

- No dotenv or default home/workshop paths. Explicit memory data root, empty package initializers, minimal event storage and process-local availability callback.
- Product delivery, model HTTP generation, session-context handoff and daemon entrypoints are removed from consolidation. Generation is injected by the caller.
- Explicit generic reviewed JSONL import replaces product session/origin discovery. Identity is supplied, not authenticated. A write marker retains uncertainty after partial writes.
- Semantic search accepts `embed=callback`; HTTP is opt-in and unconfigured semantic retrieval raises an availability error.
- Legacy context/reconstruction/telemetry preparation wrappers are replaced by small explicit memory interfaces.
- A correctness fix in `_bez_wektora` preserves neighboring fields regardless of JSON key order. The old code could discard source metadata between vector and meta. The candidate tests three key orders; this change was not applied to the running workshop.
- Workbench comments/docstrings are removed in derived sources, generic participant strings replace personal roles, and UTC is the example default. Protocol namespace/field names are retained for compatibility.

Priority review points:

1. Source and export hashes; ensure no private data, actual transcript content, secret, session path or personal fixture entered the package.
2. Exact import review, byte binding, replay/idempotency, conflict and uncertain-write refusal. A universal lock across all writers is not implemented: serialize writers.
3. Freshness and source-boundary filtering, vector dimension errors, ties, appended/truncated/replaced indexes and background warmup. No performance claims beyond the test environment.
4. Social/work splitting, missing/unknown evidence, partial pairs/retries and independent registry revision checks. The fixed Operator social quote role needs generalization for multi-user deployments.
5. No filesystem mutation or network calls on import. Test from an unpacked archive with a clean environment, not from inside the private repository.
6. Configuration and source-format compatibility. This candidate intentionally does not ship a complete API server, automatic service startup, installer or migration of existing personal memories.

Do not activate it on an existing memory store as part of this review. Use synthetic sources and temporary directories. First independent review: 2026-10-24. It replaced the character-by-character vector scanner with a regex tokenizer (same results on 20,000 randomized key orders, ~26x faster) and made reviewed-import IDs unambiguous by escaping `%` and `:` in `source_id`.
