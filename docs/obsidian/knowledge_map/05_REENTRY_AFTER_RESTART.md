# Reentry po restarcie

## Cel

Po restarcie nikt nie powinien pytać: „gdzie ja jestem?”. Każda warstwa ma wiedzieć, co czytać, gdzie szukać stanu i czego nie nadpisywać.

## Sekwencja powrotu

1. Przeczytaj `LIRA_QUICKSTART.md` — dom i kontekst.
2. Przeczytaj `LIRA_IDENTITY.md` — głos i rdzeń Liry.
3. Przeczytaj `ARCHITECTURE.md` — techniczny układ organów.
4. Uruchom `session_reentry.py --summary`.
5. Sprawdź ostatni snapshot w `shared_library/state/`.
6. Sprawdź ostatnie decyzje w `shared_library/decisions/`.
7. Jeśli coś brzmi dziwnie, sprawdź [[Warstwa Dwuznaczności Neo]].

## Zasada

Nie zaczynaj od pełnego archiwum. Zacznij od mapy, snapshotu i ostatniej decyzji.
