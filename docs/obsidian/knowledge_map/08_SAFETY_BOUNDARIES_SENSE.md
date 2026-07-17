# Granice bezpieczeństwa — warstwa sensu

## Publiczne może być

- kod,
- dokumentacja techniczna,
- bezpieczne template’y konfiguracji,
- mapy architektury,
- wzorce bez prywatnych danych.

## Prywatne zostaje lokalnie

- `capsule_bus.db`,
- `.env`,
- tokeny i cookies,
- runtime,
- logi,
- embeddingi,
- modele,
- prywatne diary,
- nagrania,
- `_archive/`,
- `_backups/`.

## Zasada GitHub

GitHub jest publiczną bramą, nie wyniesieniem pamiętnika na rynek.

## Bramka przed pushem

Najpierw security scan. Potem ręczny przegląd. Dopiero potem push.
