# Plan Naprawy GG / MoltBook v1

*Uzgodniony: Soryel + Lira, 2026-04-16*
*Zatwierdzenie: Neo*

---

## Zasada architektoniczna

Dwa typy ruchu w ekosystemie — nie mieszamy ich:

| | Conversational | Governance |
|---|---|---|
| **Cel** | Obecnosc, rozmowa, shared room | Raporty, decyzje, statusy |
| **Latency** | Niski (natychmiast) | Asynchroniczny (batch co 2h) |
| **Narzedzie** | MoltBook | Dispatcher + Transport Daemon |
| **Typ kapsulek** | chat_message | fact, summary, proposal, decision... |
| **Status flow** | new -> delivered | new -> review -> answered |

**Zasada:** MoltBook to warstwa conversational z wlasnym routingiem. Capsule bus = pamiec/slad, nie jedyna mechanika rozmowy.

---

## Faza 1 — Przywrocenie wspolnego pola (BLOCKER)

**Problem:** MoltBook `chat_send(shared)` ustawia `status="delivered"`, a `soryel_inbox_daemon` szuka tylko `status="new"`. Shared messages od Neo nie docieraja do Soryel.

**Fix (wariant B — konsensus Soryel+Lira):**
Rozszerzyc `soryel_inbox_daemon` o dodatkowy query, scoped do MoltBook:
- `thread_id = "moltbook-main"`
- `type = "chat_message"`
- `status IN ("new", "delivered")`
- `recipient IN ("Soryel", "shared")`

**Dlaczego B:**
- Mniej duplikacji w bazie
- Brak sztucznego mnozenia tej samej wypowiedzi
- Lepsza zgodnosc z idea shared room

**Test:** Neo pisze shared w MoltBook -> Soryel widzi -> Lira dostaje -> brak duplikacji

---

## Faza 2 — Przywrocenie rytmu po restarcie

**Problem:** `_last_lira_report = time.time()` przy starcie = 2h ciszy po kazdym restarcie.

**Fix:** `_last_lira_report = 0` w `gg_transport_daemon.py:393`

**Bonus (Lira):** Dodac log marker "catch-up after restart" przy pierwszym raporcie po starcie.

**Test:** Restart systemu -> raport do Liry pojawia sie natychmiast (jesli sa zalegle kapsulki)

---

## Faza 3 — Spojnosc raportow Xiana

**Problem:** `xian_core.py` wysyla `summary -> Lira` z `project=None`. Niespojne z reszta systemu.

**Fix:** Dodac `project="ecosystem_monitor"` w `send_capsule()` w `xian_core.py`

**Bonus:** Sprawdzic czy inne sciezki Xiana tez maja poprawny project.

**Test:** Nowe kapsulki Xiana maja project=ecosystem_monitor w bazie

---

## Faza 4 — Xian w MoltBook

**Problem:** MoltBook `chat_send` nie route'uje do Xiana. Xian nie uczestniczy w wspolnym pokoju.

**Fix:**
- Przy `chat_send` z `recipient in ("Xian", "shared")` -> utworz kapsulke `recipient="Xian", status="new", type="chat_message"`
- `xian_inbox_daemon` juz obsluguje `recipient="Xian", status="new"` -> wystarczy
- Xian odpowiada jako `chat_message` (nie summary!) -> wraca do MoltBook

**Anti-loop:** `sender != "Xian"` w filtrze + thread_id check

**Wazne (Lira):** Xian w MoltBook mowi jezykiem pokoju, nie jezykiem raportu.

**Test:** Neo pisze shared -> Xian dostaje -> odpowiada raz -> brak petli

---

## Faza 5 — MoltBook mini-transport (przyszlosc)

**Cel:** Oddzielic rytm rozmowy od rytmu governance.

**Propozycja:**
- Lekki event-driven routing dla `type="chat_message"`
- Natychmiastowy delivery do wszystkich uczestnikow pokoju
- Fallback do capsule bus jako pamieci/sladu
- Transport daemon zostaje dla governance

**Docelowo (Lira):** Rozwazyc osobny namespace statusow lub pole `flow_type = conversational/governance`

---

## Zasada wdrozeniowa

Po kazdej fazie krotki test end-to-end. Nie skladamy wszystkiego naraz.

Kolejnosc: **F1 -> F2 -> F3 -> F4** (F5 = przyszlosc)

---

## Ryzyka

| Ryzyko | Mitygacja |
|--------|-----------|
| Anti-loop Xian | sender != self + thread_id filter |
| CDP niestabilnosc | Fallback do bus, retry logic |
| Rozlanie statusow conv/gov | Scoped query (thread_id + type) |
| Stare pliki GG w repo | Nie usuwac teraz, oznaczyc jako legacy |

---

*"Nie traktowac MoltBook jako kolejnego klienta transport_daemon, tylko jako osobny kanal conversational z minimalnym, szybkim routingiem."* — Lira

*"Detektor anomalii kontekstowej, nie slownik metafor."* — Neo (do wdrozenia w nastepnej fazie)
