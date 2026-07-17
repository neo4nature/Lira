# Security Policy

## Reporting a vulnerability

If you find a security issue in Lira, please report it privately:

- Email: **dawpollneo@gmail.com** (subject: `[Lira Security]`)
- Do not open a public issue for security vulnerabilities.

We aim to respond within 7 days.

## Scope

This repository contains the ecosystem code only. It deliberately excludes:

- runtime databases (`capsule_bus.db`, memory stores)
- environment files (`.env`) and credentials
- personal diaries, identity files, and session logs

If you find any personal data or secret accidentally committed, report it immediately via the email above — it will be treated as a critical issue.

## Design notes

- Every irreversible action in the system requires human confirmation (see README — "Human as last voice").
- The execution bridge supports `dry_run` mode; use it when testing governance actions.
- Local models (Ollama) run offline; no data leaves the machine unless explicitly configured.
