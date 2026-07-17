# Shared Library — Żywa Pamięć

## Cel

Shared Library ma przechowywać skondensowane ślady, które pomagają systemowi wrócić do pracy bez ładowania całej rzeki danych.

## Proponowana struktura

```text
shared_library/
  architecture/     # opis organów i połączeń
  patterns/         # powtarzalne wzorce pracy
  decisions/        # decyzje architektoniczne i dlaczego zapadły
  state/            # reentry points i snapshoty sesji
  culture_quotes/   # Warstwa Dwuznaczności Neo
```

## Co trafia do decyzji

- zamknięcie faz,
- zmiana ownera pliku,
- nowe kontrakty przepływu,
- bezpieczeństwo publikacji,
- decyzje o prywatnym/publicznym zakresie.

## Co trafia do wzorców

- TTS lock,
- MoltBook window 500,
- TEST_ID delivery debug,
- Warstwa Dwuznaczności Neo,
- Snapshot before shutdown.

## Czego nie wrzucać

- pełnych logów,
- baz danych,
- prywatnych dzienników,
- tokenów,
- cookies,
- modeli,
- ciężkich embeddingów.
