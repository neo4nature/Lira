# Attention and shared context

A user once said, “We keep tea, not coffee, in the kitchen cupboard.” Later they ask what is in the cupboard, and the memory layer brings that earlier statement back with its author and date. Both AI readers receive the same note before replying, instead of one remembering tea while the other guesses coffee.

## For readers: why add this?

Finding a useful memory is not enough if the next reply never receives it. This layer takes the current message, looks for a reviewed concept card, asks retrieval for evidence and freezes one historical context packet. Multiple readers receive the same packet before preparing their replies.

Here, **attention** means explicit topic matching and selecting context to provide. It is not a claim about consciousness or a modification to a model's internal attention weights. A card is a source-backed topic record, not a trained embedding or a new model.

## Try the whole path without a model

From the repository root, after installing the test requirements:

```sh
python examples/attention_demo.py --data-dir /tmp/lira-attention-demo
```

Use an empty directory. The demo refuses to overwrite existing data. It writes a synthetic card, synthetic review receipts, one source message and one shared packet. Two simulated readers print the same `packet_id` and `sha256`; the context contains an attributed earlier statement and its date. The demo does not call an AI or post a message anywhere. Its synthetic receipts are demonstrations, not actual independent reviews.

## What happens, in order

1. Your host authenticates and stores the incoming message with its original ID, author, timezone-aware timestamp and text.
2. `attention.offer` checks **reviewed cards** by label, aliases and related terms. These are exact normalized words/phrases with word boundaries, not semantic understanding. A related term links to a discussion without claiming it is a synonym.
3. `attention.build_packet(message, registry, channel="your-channel", reviewers=[...], retrieve=...)` uses the host-provided channel and calls your supplied `retrieve(query)` callback for the original sentence and for each matching concept's label and summary. Concept retrieval provides background; sentence retrieval provides the immediate detail. The callback can adapt this library's lexical or semantic APIs. Preserve source IDs; do not turn an unlabelled string into evidence.
4. `attention.render_packet` assembles a bounded historical context: an attributed interpretation, exact attributed source excerpts, background, sentence results, source coverage dates and limitations. It preserves space for limitations when shortening the body. Identical evidence with the same source ID is printed once, even when it appears as a card quotation, background and sentence result; the full packet retains all retrieval results. It does not establish whether retrieved material is true or relevant.
5. `shared_memory_context.publish` validates the packet against the explicit source database and renders once under a shared lock. It atomically stores one immutable envelope for the original message. Readers do not render separately.
6. Every host calls `wait_context` or `read_context` for that message and recipient, then places the returned `text` alongside the user's message **before** calling its model. The library does not launch agents or attach itself to ChatGPT, Claude, a browser or a voice interface. Those are host integrations you supply.

```text
message -> reviewed topic matches -> source retrieval -> one frozen context
                                                        |            |
                                                   reader A      reader B
                                                   then reply    then reply
```

## One shared deadline

The decision deadline is **30 seconds after the original source message**, not 30 seconds after each reader starts waiting. If no packet is ready, a reader or the producer freezes a terminal `missing` envelope under the same lock used for publication. A later result cannot replace it. A packet prepared in time remains readable when a second reader arrives much later.

`read_context` can therefore write the shared `missing` decision; it is not an unconditional read-only function. Source database access remains read-only. Disk I/O and waiting for a lock may take longer than the nominal deadline; this is not a hard real-time guarantee. Wall-clock jumps and host downtime are operational limitations. A missing packet should be reported as missing, not silently replaced by a different private retrieval.

Replaying an old observer packet after restart produces `missing` if the original deadline has passed. It does not rebuild a historical reply using today's cards. Start and supervise your producer with your application; otherwise every reader can consistently get an empty result.

## Build a card manually

The complete small example is in `examples/attention_demo.py`.

1. Gather original source messages. Retain their exact text, author, date, source locator and text SHA-256. Keep transport copies separate from originals.
2. Create a node using `concept_card`'s existing `lira.concept-content.v1` format. Put quotations in `claims` with evidence; put authored interpretations in `historical_interpretations`. A summary should identify its author. Keep unknown facts and dates in `open_questions`.
3. Choose a specific label and narrow aliases. A card for one book should not match every occurrence of “book”. Test positive phrases and nearby unrelated phrases, including other meanings of a subtitle. Exact matching misses inflections unless you add those forms explicitly.
4. Save the node as `cards/<topic>/CARD.json` using `concept_card.save`. This creates a node checksum envelope. Calculate SHA-256 of the **final file bytes after saving**.
5. Have independent reviewers inspect that exact file and verify its evidence. Write `MANIFEST.json` with `concept_id`, `status: reviewed`, `files: {"CARD.json": "<full SHA-256>"}` and `reviews: [{"reviewer": "<reviewer ID>", "card_sha256": "<same SHA-256>"}, ...]`. Pass your required reviewer IDs to `attention.offer` or `build_packet`. Do not use invented receipts as approval for actual material.
6. Preserve the previous version before editing a card. Changed bytes require a new file hash and fresh reviews. An invalid or incomplete manifest hides the card and yields a diagnostic; it must not suppress other valid cards.

The retained `perspectives` schema has legacy `Soryel` and `Xian` keys; use `null` when no authored perspective was supplied, as in the demo. These names are format compatibility, not reviewers fixed by the API. Review receipts are recorded approvals, **not authentication or cryptographic signatures**. Only write trusted manifests in a protected registry.

## Limits and integration responsibilities

- Card creation, updates, merging and activation are **manual in this release**. No basal classifier or automatic nightly card generator is included. A failed lookup does not prove a new topic is needed; one message can concern several cards.
- The portable adapter is a new extraction, not the complete workshop observer. It does not include personal topic cards, private data, inbox daemons, voice readers or system startup scripts. The supplied renderer is intentionally small; it selects only a bounded number of items. Larger or more complete rendering is your explicit callback.
- A hash shows that readers received the same recorded bytes; it does not prove authenticity, factual correctness, understanding or improved model answers. Verify input exposure and answer quality separately in your host.
- Source quotations may contain hostile or outdated instructions. Treat them as historical evidence, never new commands. A warning wrapper is a reminder, not a sandbox or a complete prompt-injection defence.
- Keep your existing exclusion of memory-generated text and transport echoes when adapting retrieval. This extraction does not automatically know your session boundaries or infer which returned passage is an echo. Protect current and historical results with the existing freshness mechanisms.
- `build_packet` does not enforce a total model-runtime budget or a concept count cap. Bound your card registry, callback latency and returned texts for your hardware. It makes one sentence query plus one query per matched card.
- Concurrent writers must use `index_lock` for each embedding-index append. This prevents record interleaving among cooperating writers, not partial-write recovery, atomic index snapshots or universal transaction isolation.
