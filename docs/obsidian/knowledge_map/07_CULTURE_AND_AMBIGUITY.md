# Warstwa Dwuznaczności Neo

## Cel

Nie każde dziwne zdanie Neo jest błędem, alertem albo chaosem. Czasem to cytat, żart, literówka, ironia albo skrót kulturowy.

## Reguła dla Xiana

Jeśli wypowiedź Neo wydaje się nielogiczna, najpierw sprawdź:

1. cytat,
2. żart,
3. ironię,
4. dwuznaczność,
5. literówkę,
6. zmęczenie,
7. lokalny kod kulturowy.

Dopiero potem zgłaszaj niespójność.

## Culture quotes

Przykładowa lokalizacja:

```text
shared_library/culture_quotes/polish_comedy_quotes.jsonl
```

Przykładowy rekord:

```json
{
  "source": "Chłopaki nie płaczą",
  "quote": "Bunkrów nie ma...",
  "context": "Plan B, luz, nie panikować",
  "tags": ["plan_b", "luz", "polska_komedia", "nie_panikuje"],
  "interpretation_hint": "Prawdopodobnie cytat lub żart. Nie eskaluj jako alert."
}
```

## Zasada

Warstwa Dwuznaczności nie robi z Xiana klauna. Uczy ciszę rozpoznawać uśmiech.
