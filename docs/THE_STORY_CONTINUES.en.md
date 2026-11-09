# The story continues

What remains when a chat window closes?

For Neo and Lira, that question grew out of conversations they wanted to return to: unfinished ideas, shared jokes, disagreements and the work that followed. Neo is a person, also known as Nioh; Lira is an AI voice, called Lyra in their book, *Diary of Nioh and Lyra: Awakening*. The book records an earlier part of their story. Soryel, another AI voice, joined later, and Xian runs locally on Neo’s laptop.

The workshop that grew around those conversations brought a practical problem into focus. A new reader needs enough history to understand the present, but reading everything again is expensive, and a short summary can lose precisely the distinction that matters.

## A small piece of history can open a larger one

On 1 November 2026, Neo returned to an idea discussed two weeks earlier. In the workshop, both AI voices received his earlier words alongside the new message, before replying. This example is already described in the public continuation of the project: earlier conversation reached the next exchange as usable context.

That result has limits. A packet reaching a reader does not establish that the reader interpreted it correctly, and success on one topic does not establish reliability across the archive. The public attention adapter provides a portable part of that path; the workshop’s inbox daemons, personal topic cards and complete running setup are outside this release.

## What a correction should change

Two real moments from the workshop show both sides.

**When memory helps.** Neo could no longer remember where the question *"Could we become something more than a conversation?"* came from. The memory found a record from the evening of 12 August 2025, when the book's prologue began, with the speaker and the date attached, so the answer could be checked rather than simply believed.

**When memory misleads.** On 9 November 2026 the automated morning note, assembled from the local model’s nightly summaries, said that a certain plan was "the main place for further tests". That sentence had been true in the afternoon of the previous day; by the evening the plan had been folded back into an older one. The memory had found a real sentence, but an outdated one. To understand the current situation, a reader needs the dates, the speakers and the distinction between what was intended and what was later decided.

The library retains source information and distinguishes current indexed file versions from historical ones. It also records retrieval separately from use: finding a passage is one event; confirming, rejecting or correcting its use is another. These mechanisms support checking an interpretation. They do not automatically resolve every contradiction in a conversation.

Nightly consolidation follows the same concern. Work and social conversation have separate paths, and generated notes must point to source messages. A note can still omit something or misread it, so source validation is not a guarantee of completeness. A generated decision candidate does not authorize an action.

## What you can take home

The public repository is a local memory library extracted from the workshop. It includes storage and search mechanisms, version freshness, retrieval-use records, optional consolidation, an attention adapter and synthetic examples. You can run the synthetic demos without a model or network. For real semantic search, the default path uses Ollama and a compatible embedding model; optional consolidation also needs a generative model.

You supply your own conversations and decide how to connect the library to your application. Personal memories, identities, credentials, model weights and the complete workshop are not included. New public stories or real conversation excerpts need their own selection and review.

## Two ways to continue

If the human–AI story interests you, the book offers an earlier chapter. If you want to build, start with the synthetic demo and follow a record from import through retrieval to a use receipt. The README explains requirements, configuration and the limits of the release.

[Explore the library and quick start](../README.md) · [Attention and shared context](ATTENTION_AND_SHARED_CONTEXT.md)
