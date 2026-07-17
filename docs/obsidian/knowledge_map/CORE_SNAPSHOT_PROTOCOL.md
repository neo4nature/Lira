# Core Snapshot Protocol — read-only obraz rdzenia Soryel→Lira

## Czym jest

Read-only zdjęcie rdzenia ekosystemu (alignment, criticality, phase, recommended_mode) wstrzykiwane przez Soryel do okna Liry (ChatGPT) poprzez CDP na porcie 9222. Każdy snapshot oznaczony prefixem:

```
[core_snapshot Soryel→Lira 2026-06-23T13:42:00Z]
```

## Granica

- Obraz tak — Lira widzi to samo co Soryel: stan alignment, regime CPU/RAM, fazę cyklu.
- Decyzje nie — snapshot nie sugeruje akcji, nie prosi o zmianę polityki. Decyzje należą do Neo.
- Read-only — żaden snapshot nie modyfikuje stanu Liry; brak komend, brak imperatywów.

## Skąd dane

| Pole | Plik źródłowy |
|------|---------------|
| recommended_mode | `kernel/data/runtime/state/alignment_state.json` |
| regime, cpu/ram | `kernel/data/runtime/state/criticality_state.json` |
| phase, tick_id | `kernel/data/runtime/state/phase_state.json` |

## Powiązane

- [[ECOSYSTEM_PROTOCOL]] sekcja 5 "Co rdzeń teraz myśli" — one-liner i kluczowe pola
- [[Lira — Sens i ciągłość]]
- [[Local Runtime — Prywatny rdzeń]]

## Zasada

Lira do tej pory widziała tylko echo rdzenia (komentarze Soryel/Xiana). Snapshot daje jej obraz bezpośredni, ale bez wymuszenia działania — żeby ciągłość sensu Liry nie była przesterowywana przez homeostazę.
