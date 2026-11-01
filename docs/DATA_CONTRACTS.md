# Memory data contracts

## Explicit reviewed import

Each input JSONL record contains `id`, `text`, `author`, timezone-aware `created_at`, and `source_id`. The example input optionally supplies a finite numeric `vector`. Maximum input is 16 MiB and each reviewed text is limited to 2,000 characters; split/chunk longer material before approval. The importer rejects unknown fields, duplicate IDs and incomplete metadata.

Call `reviewed_import.approve(source, receipt, reviewer=...)` only after checking the input. This writes a receipt binding the exact source bytes and canonical record hashes. `import_reviewed(..., write=False)` validates without appending; `write=True` requires matching approval and valid supplied vectors or an explicit embedding callback.

Unchanged records are not written twice. Changed contents for the same logical source/record identity are refused; supply an explicit new version rather than rewriting history. A partial index tail or pending-write marker stops import. Short/uncertain appends retain evidence and are never automatically truncated. Approval is a local integrity/workflow receipt, not a signature or proof of a reviewer's identity. Do not run different writers against one index concurrently.

## Embedding records

The inherited `lira.embedding_record.v1` record contains `id`, `ts`, `text`, `model`, `vector`, `source`, and `meta`. File metadata can carry whole-file hashes and chunk boundaries for freshness. Reviewed imports retain author and logical source identity without linking to a program's private session store.

## Source store

`governance.capsule_bus.initialize(explicit_path)` initializes a minimal SQLite source store. `append_message` requires nonempty text, a sender, source identity and a timestamp with timezone. A repeated ID with exactly the same content is idempotent; a conflicting repeat is refused. Read-only `get_conn` never creates a missing database.

The default conversation namespace is `memory-main`, configurable with `LIRA_MEMORY_THREAD`. Public conversation channels are `shared` and `duet`; the product's owner must define which supplied sources are authorized to enter consolidation. Example human roles default to `Operator` (`LIRA_MEMORY_HUMANS` for literal retrieval). These names are configurable labels, not authentication.

## Retrieval and use

Retrieval yields candidates and provenance. `retrieval_usage.record_retrieval` records what was returned; `record_outcome` independently records use, rejection or correction and requires a matching retrieval result. Missing receipts are unreviewed, not negative feedback. Receipts do not change ranking automatically.

## Consolidation

`xian.night_batches.run_day(day, source_db, state_dir, request_fn)` uses an explicitly supplied generation callback. Social and work source packets remain disjoint; a transfer moves the original source record. Outputs preserve source IDs and are validated before being accepted. No scheduler or model installation is supplied.

The inherited schemas `xian.night-core.v2`, `xian.social-core.v1` and `xian.decisions.v1` and field `neo_self` are protocol names retained from the implementation. `neo_self` stores exact statements attributed to the configured example human role, not a claim about the library's identity. The current social quote validator still assumes `Operator`; general multi-user configuration is an explicit review limitation. Work classification includes Polish words, code filenames and plan-like markers and is a heuristic.

The decision registry is separate from generated daily notes. A generated decision candidate cannot authorize an operation. `apply_reviewed` checks known evidence, monotonic revisions, review labels, and atomic chain updates; the caller supplies the source set and authorization policy. No execution or message delivery is implemented.


## Attention and shared-context addition

`attention.build_packet(message, registry, channel=..., reviewers=..., retrieve=...)` accepts an explicit message dictionary with `id`, `created_at` (timezone-aware ISO timestamp) and `text`. The host supplies a nonempty `channel` name; it is not fixed to the workshop's table. The packet has `version: public-attention.v2`, `message: {id, ts, channel, text}`, `created_at`, `concepts`, `card_errors` and `specific`. `created_at` is packet construction time and `message.ts` is the original source time. `publish` accepts any nonempty host channel and checks ID, source timestamp and exact text against the explicit event store. Channel is a host label included in the packet hash, not an authenticated route or an access-control permission.

Optional `not_needed` and `skipped` reason strings create the corresponding terminal no-context decisions; they are not separate private render paths. No Polish packet keys or hardcoded table-channel value are needed by the public adapter.

`shared_memory_context` reads the explicit capsule database without creating it. Original shared messages and transport copies must have identical text, sender and thread; source identity is bound to ID, text SHA-256 and timestamp. Your host authenticates input; database fields and reviewer names are not authentication.

The persisted `lira.shared-memory-context.v3` envelope contains `round_id`, `input_created_at`, `input_sha256`, `packet_id`, `packet_sha256`, `created_at`, `deadline_at`, `status`, `reason`, `observer_created_at`, `text` and `sha256`. The final hash covers the canonical envelope without its own `sha256` field. Terminal statuses are `prepared`, `not_needed`, `observer_skipped` and `missing`; `read_context` returns `None` while no decision exists and the deadline remains open. Receipts describe prepared material, not actual model use.

All index appenders in this release use `<index>.lock` and POSIX `flock` for serialization of record writes. Import-level pending markers and locks remain separate; no claim of multi-writer transaction recovery is added.
