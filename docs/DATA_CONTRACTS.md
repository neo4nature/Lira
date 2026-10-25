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
