# Cleanup Quarantine — 2026-06-23

## Co

5 plików / ~600 MB przeniesione do `_quarantine/cleanup_2026-06-23/` zamiast `rm`.

## Zawartość kwarantanny

- `docs/obsidian.zip` (96 K) — duplikat obsidian.tar.gz
- `_backups/` (365 M) — outdated, gitignored, >30 dni
- `embeddings_index.jsonl.bak.20260430_171135` (165 M) — 54 dni
- `_archive/gg_legacy/` (71 M) — junk profile cache GPU/shader
- `kernel/app/nohup.out` (0 B) — untracked śmieć

## Manifest

`_quarantine/cleanup_2026-06-23/manifest.json` — pełna lista, daty, mtime, rozmiary.

## Review

**2026-07-23** — Neo decyduje: `rm` albo zachowaj.

## Werdykt Lira

"compress > drop" — usunięcie nieodwracalne, kwarantanna odwracalna w 30 dni. Manifest = audyt trail.

## Co NIE było ruszane

`capsule_bus.db`, `embeddings_index.jsonl` (live), logi <30 dni, `identity/`.

## Zmiana w `.gitignore`

Dodane: `_quarantine/`, `*.out`, `nohup.out`.

## Powiązane

- [[ECOSYSTEM_PROTOCOL]]
- changelog: `docs/CHANGELOG_WORK.md` § 2026-06-23
