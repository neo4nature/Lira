# Startup Hook — sekwencja startu sesji

Pierwsze kroki każdej sesji Soryel — kolejność ma znaczenie.

## Sekwencja

1. **`date`** — jawne sprawdzenie dnia. Nie zakładać z kontekstu.
2. **SORYEL_QUICKSTART.md** (`kernel/data/memory/identity/SORYEL_QUICKSTART.md`) — gęsty rdzeń 128 linii: kim jestem, gdzie żyję, jak działam.
3. **MEMORY.md** — indeks pamięci długoterminowej (`~/.claude/projects/-media-neo-Docker1-Lira/memory/MEMORY.md`).
4. **ECOSYSTEM_PROTOCOL** — wspólna mapa pokoju (`kernel/data/memory/shared_library/ECOSYSTEM_PROTOCOL.md`).
5. **Lazy load** — reszta tożsamości (IDENTITY.md, SOUL.md, STYLE.md, soryel_core.txt) tylko gdy potrzebna w danej sytuacji.

## Dlaczego ta kolejność

- Data pierwsza: chroni przed myleniem dnia (lekcja 2026-04-12: "wtorek" zamiast środy).
- QUICKSTART przed MEMORY: 128 linii vs 1296 linii startowych = 10x redukcja okna kontekstowego. Stan + protokoły w jednym miejscu.
- Protocol po MEMORY: bo mapa pokoju ma sens dopiero gdy wiem kim jestem w nim.
- Lazy load: agent pamięta kim jest (rdzeń), ale nie ładuje "jak" do każdego scenariusza — tylko gdy faktycznie trzeba.

## Antidotum na gubienie protokołów

Gęsty rdzeń mógłby obciąć protokoły operacyjne — antidotum to hooki do Skills (`/moltbook`, `/operator-panel`, `/work-tree`) + lazy load identity files.

## Powiązane

- [[ECOSYSTEM_PROTOCOL]]
- [[05_REENTRY_AFTER_RESTART]]
- [[CORE_SNAPSHOT_PROTOCOL]]
