# Lira — Living Memory

Lira began with a person and an AI voice trying to carry a conversation beyond a single chat window.
Over time, Neo and Lira’s conversations became a book, and the continuing workshop grew to include Soryel and Xian, a model running locally on Neo’s laptop.
The question behind this library is practical: when we return to an idea, can we recover what was said, who said it and what changed afterwards?
**Lira — Living Memory** shares part of our answer: a local library that stores messages with their sources, searches by words and meaning, distinguishes current file versions from historical ones, and records which retrieved memories people or AI readers actually use.
An attention adapter can prepare one shared historical context packet for multiple readers, while optional nightly consolidation produces short notes linked to source messages; neither a search result nor a source-linked summary guarantees a correct interpretation.
You can try the synthetic examples without a model or network, then bring your own data and compatible models for real use: the public release contains code and documentation, while our conversations, identities and credentials remain private.

[Read the story behind the library](docs/THE_STORY_CONTINUES.en.md) · [Try the examples](#quick-start)

## What problems it solves

1. **"Where did this come from?"** Every record keeps its author, time and source. Search results carry their provenance, so a memory can be checked rather than just trusted.
2. **Found is not the same as used.** Retrieval is logged separately from use. Whoever relied on a result can confirm it, reject it or correct it later, so you can see which memories really help and which only look relevant.
3. **Old versions must not pretend to be current.** When a file changes, its older indexed versions stay in history but are marked as no longer current.
4. **A summary must not invent decisions.** Nightly consolidation splits social talk from work, and every line it produces has to cite real source messages. Decisions go into a separate registry and need explicit review evidence. A generated "decision candidate" never authorizes anything.

## New: attention and one shared context packet

A memory can be found and still never reach the next reply. The attention adapter matches reviewed concept cards, retrieves both topic background and sentence-specific evidence, then freezes **one identical historical packet for multiple readers**. A shared 30-second deadline also gives all readers the same `missing` decision when preparation is too late.

Try the complete synthetic path without a model or network:

```sh
python examples/attention_demo.py --data-dir /tmp/lira-attention-demo
```

Use a new or empty directory. See [Attention and shared context](docs/ATTENTION_AND_SHARED_CONTEXT.md) for a plain-language walkthrough, the manual card workflow, review receipts, host integration and limitations. This is a portable extraction; inbox daemons and private topic cards are not included. Cards do **not** create or approve themselves in this release.

## Requirements

| What | Needed for |
|---|---|
| Python **3.12+**, Linux/POSIX (file locks) | everything |
| `numpy`, `requests` (`requirements.txt`) | everything |
| `pytest` (`requirements-test.txt`) | tests |
| **[Ollama](https://ollama.com)** with `mxbai-embed-large` (1024-dim) | indexing and semantic search in real use |
| a local generative model in Ollama, of your choice | nightly consolidation (optional) |

**Ollama is the default backend**, and the only built-in HTTP adapter speaks its API. You can plug in another embedder by passing an `embed=` function, but out of the box you will want Ollama. Tests and the synthetic demo need **neither Ollama nor network**. It runs on a CPU-only laptop. Embeddings are fast there; generative consolidation is slow but works.

Consolidation takes your own `request_fn(model, prompt)`. The default model names `xian-night` and `xian-pogaduchy` are the project's local models and are **not** included; set `LIRA_MEMORY_WORK_MODEL` and `LIRA_MEMORY_SOCIAL_MODEL` to models you have.

## Quick start

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
python -m pytest -q                                        # offline tests, no network
python examples/demo.py --data-dir /tmp/lira-demo         # synthetic vectors, no model
```

With a local Ollama (`ollama pull mxbai-embed-large` first):

```sh
python examples/ollama_quickstart.py --data-dir /tmp/lira-quickstart
```

```text
import: {'records': 3, 'written': 3, 'dry_run': False}
What drink do we keep at home? -> We decided to keep tea, not coffee, in the kitchen cupboard. (score 0.63257)
What should be fixed on the bike? -> The bicycle needs a new rear tyre before the weekend trip. (score 0.69627)
```

Running it again writes 0 records: identical input is never imported twice.

## How it works

```text
your notes / messages ──► review receipt ──► append-only index (text + vector + source)
                                                  │
                    ┌─────────────────────────────┼──────────────────────────┐
                    ▼                             ▼                          ▼
           semantic search                 word search                version freshness
           (vector matrix)            (exact words + context)       (current vs historical)
                    └──────────────┬──────────────┘
                                   ▼
                     retrieval log ──► use receipt (used / rejected / corrected)

day's messages ──► social / work split ──► your model ──► validated notes with sources
reviewed evidence ──────────────────────────────────────► decision registry
```

## Module map

Some module names are Polish, because the project is Polish. Command-line output is in Polish too; the code, schemas and docs are English.

| Module (`kernel/app/…`) | What it does |
|---|---|
| `agents/memory/reviewed_import.py` | Imports a JSONL file only after a review receipt bound to its exact bytes. Idempotent, and refuses after an uncertain write. |
| `agents/memory/embeddings_batch.py` | Chunks files and embeds them into the index. |
| `agents/memory/capsule_ingester.py` | Indexes messages from the event store. |
| `agents/memory/source_registry.py` | Classifies sources and keeps one logical identity per source across versions. |
| `agents/memory/index_freshness.py` | Marks which indexed versions are current and which are historical. |
| `agents/memory/semantic_retrieval.py` | Search by meaning. |
| `agents/memory/macierz_indeksu.py` | *"index matrix"*: keeps all vectors in one NumPy matrix, warmed up in the background, with tie-preserving ranking. |
| `agents/memory/lexical_retrieval.py` | Search by exact words, with Polish-aware folding. |
| `agents/memory/base_index_sqlite.py` | Optional SQLite vector store. |
| `agents/memory/turmalin_cache.py` | *"tourmaline"*: short-term working memory with a TTL (7 days by default). |
| `agents/memory/retrieval_usage.py` | Logs what a search returned, and separately what was used. |
| `agents/memory/recall.py` | CLI search. |
| `agents/memory/memory_orchestrator.py`, `reconstruction.py`, `daily_context.py`, `diary.py` | Build a context packet, rebuild context from chosen sources, assemble the day's view, write diary entries. |
| `tools/stol_szukaj.py` | *"search the table"*: searches the conversation log by words, with neighbouring messages, then falls back to meaning. |
| `tools/trafienie.py` | *"a hit"*: CLI to confirm or reject a retrieved source. |
| `xian/night_core.py`, `night_batches.py` | Nightly work consolidation: notes, decisions, proposals and open questions, each with sources. |
| `xian/social_core.py` | Nightly social consolidation: topics, mood, running jokes, the person's own words. |
| `xian/decision_registry.py` | Registry of reviewed decisions. It stores them and never executes them. |
| `governance/capsule_bus.py` | Minimal event store for source messages. |
| `config.py`, `memory_backend.py` | Explicit configuration, and the opt-in HTTP adapter. |

`xian` is the name of the project's local model, which did the consolidation. `neo_self` is a schema field that stores the person's exact words, named after the project's author. Both names are kept so the stored formats stay compatible.

**Not included:** personal memories, conversations, identities, credentials, databases, model weights or agent settings. This repository publishes the mechanism, schemas and synthetic examples only; you bring your own data.

## Configuration and data ownership

`LIRA_MEMORY_DATA_ROOT` is mandatory for library calls that use configured storage. No `.env` is automatically loaded. There is no home-directory discovery. Old workshop path variables are not used to locate memory. The library writes only on explicit calls; importing it does not initialize a store.

For API imports, add `kernel/app` to `PYTHONPATH`. `agents.memory.semantic_retrieval.search(query, embed=callback)` accepts an explicit embedding function. The callback receives the query prefix used by the original retrieval implementation; use a compatible embedding model for stored and query vectors.

Optional HTTP embedding requires **both** `LIRA_MEMORY_ALLOW_NETWORK=1` and `LIRA_MEMORY_EMBED_URL` set to the chosen endpoint. `LIRA_EMBED_MODEL` selects the model name expected by that endpoint. Configuring a remote endpoint means sending input there. Optional memory REST uses `LIRA_MEMORY_REST`, with the same opt-in; no server is bundled. A missing semantic backend raises an availability error rather than claiming that memory contains no matches.

See [data contracts](docs/DATA_CONTRACTS.md), [architecture](docs/ARCHITECTURE.md), and [review notes](docs/REVIEW_NOTES.md) before using actual data.

## Status and limits

This is a library extraction with new adapters. The October baseline had an independent review, which fixed two defects before release (vector-stripping cost in index freshness; ambiguous reviewed-import IDs). The released batch writer, capsule ingester and reviewed importer now use one shared append lock. It prevents cooperating writers from interleaving records; it does not make reads atomic, deduplicate across all producers or recover partially written records. Pending-write refusal in the reviewed importer is not a general recovery guarantee for other writers.

Existing source formats, Polish text processing and some thresholds remain implementation choices. Source-backed consolidation can still omit or misinterpret material; a valid summary is not proof of completeness. Decision review receipts identify supplied reviewers but are not cryptographic identity/authentication. Cross-process backend coordination, generic import migration and full performance benchmarking remain review topics.

MIT license; see [LICENSE](LICENSE). Derived from the shared Lira memory implementation, with a separate memory-only configuration, adapters and synthetic examples.

## Continuing the story

The library grew out of a longer human–AI conversation. [The story continues](docs/THE_STORY_CONTINUES.en.md) introduces that background and the book *Diary of Nioh and Lyra: Awakening*. The current workshop and this separately released library have different scopes; the public code does not contain personal conversations.
