# Contributing to Lira Ecosystem

## Before you start

This is not a typical code-only repository.
It is a system built from a specific way of thinking.
Before changing anything, try to understand:
- why it exists
- how the layers connect
- what is core and what is only one implementation

## The main rule

> **Do not change only a fragment. Show how your change affects the whole.**

Every serious contribution should explain:
1. **Why** — what problem it addresses
2. **How** — what is being changed
3. **Impact** — how it affects the broader system

## What kinds of contributions are welcome

### Ideas
- new perspectives
- architectural questions
- alternative interpretations

### Structure
- improvements to system design
- clearer boundaries between layers
- better mappings between thought and implementation

### Code
- implementations
- fixes
- integrations
- test improvements

## What to avoid

- local optimizations without context
- changes that improve one area while weakening system coherence
- replacing concepts without understanding their role
- committing secrets, runtime state, databases, or private data

## Core vs experimental

This repository contains different layers:
- **core** — things that carry system identity and coherence
- **experimental** — places for prototypes and tests
- **ideas** — loose directions and unfinished thoughts

Not every good idea belongs in core immediately.

## How to propose changes

The preferred path is:
1. open an Issue for the idea
2. explain how it connects to the existing system
3. discuss scope and direction
4. then implement

## The kind of people we hope to meet here

We are not only looking for coders.
We are looking for people who:
- think systemically
- can step to the meta-level
- want to connect, not only optimize
- can hold both vision and implementation

## One sentence

> **If you change something, help us see why it makes sense in the whole.**

Thanks for being here.

— Neo & Lira
