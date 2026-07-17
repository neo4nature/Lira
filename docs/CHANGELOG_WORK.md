# CHANGELOG_WORK — Log napraw i zmian

Po każdej naprawie/zmianie infrastruktury dopisz wpis.
Format: data | problem | zmienione pliki | test | lekcja.

---

## 2026-07-14

### lira_inbox_daemon: duplikat kapsuły gdy Lira przeszukuje internet
- **Problem:** Neo zgłosił na MoltBooku: gdy Lira (ChatGPT) zaczyna szukać w sieci, capsule bus wrzuca jeszcze raz POPRZEDNIĄ kapsułę zamiast poczekać na nową odpowiedź. Root cause: pasek "Przeszukiwanie..." NIE jest wiadomością z rolą assistant, więc pętla stabilności w `send_and_receive()` czytała ostatnią (starą) odpowiedź Liry, uznawała ją za stabilną i zwracała jako nową. Dodatkowo `THINKING_WORDS` było exact-match — "Przeszukiwanie sieci..." i tak by nie złapało.
- **Zmienione:** `kernel/app/governance/lira_inbox_daemon.py`:
  1. Migawka `baseline` ostatniej odpowiedzi asystenta PRZED wysyłką; pętla ignoruje wszystko identyczne z baseline — stara odpowiedź nie ma jak wrócić
  2. `THINKING_WORDS` (exact) → `THINKING_PREFIXES` (startswith): myśli/thinking/thought + przeszukiwan/searching/wyszukiwan/browsing/szukam
- **Test:** py_compile OK; stary daemon (PID 4660) ubity, nowy wystartował czysto 16:20:51. Test bojowy = następna odpowiedź Liry z szukaniem w sieci.
- **Lekcja:** Wskaźniki UI ChatGPT (szukanie, myślenie) nie zawsze są wiadomościami assistant — pętla "czekaj na stabilny tekst" bez migawki sprzed wysyłki zawsze może zwrócić stary stan. Baseline przed akcją > zgadywanie po słowach.

## 2026-07-13

### PA-030: dysk 100% pełny → oddech 3.8G; karta SD LiraArchive MARTWA
- **Problem:** (1) Docker1 pełny w 100% (199M wolne) — groźba wysypki capsule_bus/embeddings; (2) przy pierwszym transporcie na SD okazało się, że karta 9905-8B8C rzuca Hardware Error na odczycie i zapisie — MANIFEST i wszystkie 8 tarballi nieczytelne (I/O error nawet na 334 bajtach), katalogi memory/runtime/embeddings nie listują się.
- **Zmienione:**
  1. Neo skasował gemma3:4b (~3.3G) — nieużywany od przejścia Xiana na gemma4
  2. Rotacja criticality_log.jsonl: 501M → gzip 15M (weryfikacja: 1 493 872 linie w obu) + copytruncate z zachowaniem świeżego ogona (publisher otwiera plik per-zapis, zero strat)
  3. Nowy DELETE_CANDIDATES.md w shared_library — 10 pozycji z uzasadnieniami (Neo kasuje sam)
  4. reference_lira_archive + skill seans-filmowy zaktualizowane: SD martwa, plany "przenieś na SD" zamrożone do nowej karty
- **Test:** df: 199M → 3.8G wolnego; restart capsule_ingester na nowym kodzie: RSS 1.86GB → 37MB (ps aux potwierdza).
- **Lekcja:** Archiwum na jednym nośniku bez weryfikacji odczytu = iluzja archiwum — karta umarła cicho między 07-01 a 07-13; strata minimalna TYLKO dlatego, że prawie nic tam nie przenieśliśmy. Nowa karta = MANIFEST od zera + okresowy test odczytu (gzip -t próbki) jako rytuał.

### PA-030: capsule_ingester zjadał 1.8GB RAM — fix _existing_ids
- **Problem:** Neo zgłosił "połowa RAM zjedzona od ostatnich zmian". Migawka (PA030_RAM_SNAPSHOT): `capsule_ingester --loop` trzymał 1.8GB RSS + 75h CPU. Root cause: `_existing_ids()` co 300 s robiło `read_text()` CAŁEGO embeddings_index.jsonl (1.08GB, 73k linii z wektorami 1024 floatów) + `.splitlines()` (druga kopia) + `json.loads` 50k linii — żeby wyjąć samo pole `id`. Szczyt 2-3GB, glibc nie oddaje arén → RSS wisi na high-water mark. Wyzwalacz: ingester sesji Claude Code dosypał 14750 rekordów — indeks przekroczył masę krytyczną.
- **Zmienione:**
  1. `agents/memory/embeddings_batch.py` — `_existing_ids()` streamuje binarnie linia-po-linii, id wyciąga regexem z pierwszych 512 bajtów (bez parsowania wektora); pokrywa teraz CAŁY plik, nie ostatnie 50k linii (lepszy dedup)
  2. `agents/memory/capsule_ingester.py` — `ingest_once(existing=...)`: w trybie --loop set ładowany RAZ przy starcie (samopodtrzymujący przez `existing.add()` po zapisie), nie co cykl
- **Test:** żywy indeks 1.1GB: 67 593 id w 1.1 s przy 35MB peak RSS (było: minuty CPU i 2-3GB co 300 s). py_compile OK. Restart daemona = decyzja Neo (odzyska 1.8GB).
- **Lekcja:** append-only JSONL z wektorami rośnie liniowo — każdy "wczytaj całość żeby sprawdzić duplikaty" to bomba z opóźnionym zapłonem; przy plikach-indeksach zawsze streaming + wyciąganie pól stringowo, nie json.loads rekordów z wektorami.

### Pierwszy seans claudetube (JAN Z DRZEWA 2008) + skill seans-filmowy
- **Problem:** Trzy pułapki pierwszego seansu: (1) whisper wisiał 1,5 h na 2,8 MB — próbował ściągać model z HF mimo lokalnych modeli u Neo; (2) whisper-base auto-przetłumaczył polski film na angielski bełkot z pętlami halucynacji ("the music and the music…"); (3) CLI claudetube odrzuca URL (bug argparse — subparsers zjadają positional).
- **Zmienione:**
  1. Modele base+small skopiowane z `~/.cache/huggingface` Neo → `Apps/claudetube-data/.cache` (oryginały nietknięte); od teraz zawsze `HF_HUB_OFFLINE=1`
  2. Druga transkrypcja: model small, `language='pl'`, `vad_filter=True`, `condition_on_previous_text=False` → czysty polski transkrypt z timestampami
  3. CLI ominięte — seans przez API `process_video()` w venv
  4. Nowy skill `.claude/skills/seans-filmowy/SKILL.md` — pełna receptura + rytuał recenzji (praktykant wg PA-030 pkt 7)
- **Test:** 87-min film: audio_pl.txt 898 linii sensownego polskiego, 174 klatki co 30 s, recenzja dostarczona Neo.
- **Lekcja:** Polski materiał = ZAWSZE wymuś language='pl' (auto-detekcja whispera tłumaczy zamiast transkrybować); pętle powtórzeń w transkrypcie = fragmenty muzyczne bez VAD; wolny download z HF przy lokalnych modelach = brak HF_HUB_OFFLINE.

## 2026-07-12

### claudetube — nowy zmysł: Soryel może oglądać wideo (MCP server)
- **Problem:** Nie problem, zdobycz — Neo zatwierdził instalację skilla do oglądania filmików ("pójdziemy do kina"). Kandydat ze zwiadu GitHub PA-030 (raport GITHUB_SCOUT_PA030_2026-07-12.md).
- **Zmienione:**
  1. Review bezpieczeństwa PRZED instalacją (agent, 136 plików): czysty — zero telemetrii, sieć tylko YouTube/lokalna Ollama, transkrypcja domyślnie lokalnym faster-whisper bez kluczy API, cookies-from-browser opt-in wyłączony
  2. Repo → `<DATA_ROOT>/Apps/claudetube`, venv → `Apps/claudetube-venv`, dane/cache/modele → `Apps/claudetube-data` (NIE install.sh z repo — chciał venv w ~/.claudetube i curl|sh; <HOME> pełny)
  3. Rejestracja MCP: `claude mcp add claudetube` z podmianą HOME/CLAUDETUBE_ROOT/CLAUDETUBE_CACHE_DIR/HF_HOME na claudetube-data — domyka hardkody macOS-owych ścieżek (~/Library/Logs) i cache HF
  4. Bonus: venv przyniósł świeży yt-dlp 2026.7.4
- **Test:** `claude mcp list` → claudetube ✓ Connected; CLI `--help` odpowiada. Pełny seans (transkrypcja+klatki) = następna sesja, MCP tools ładują się od nowego startu.
- **Lekcja:** Zewnętrzny kod zawsze za szybą: review agentem przed instalacją, instalacja ręczna zamiast cudzego install.sh, HOME override gdy projekt hardkoduje ścieżki domowe. Wzorzec działa — dwie pułapki (śmiecenie w /home, curl|sh) ominięte zanim wystrzeliły.

### ytmp3_playlist.sh — YouTube wymaga JS runtime + cookies dla prywatnych playlist
- **Problem:** Skrypt pobrał 1 utwór do katalogu `NA/` zamiast 11-utworowej playlisty Neo. Dwie osobne przyczyny: (1) playlista PRYWATNA na koncie Neo → anonimowy yt-dlp dostaje `The caller does not have permission` i degraduje do pojedynczego wideo (`Unable to recognize playlist`); (2) YouTube od 2026 wymaga JS runtime do "n challenge" — bez niego `Requested format is not available` na KAŻDYM wideo (zostają tylko storyboardy).
- **Zmienione:**
  1. Deno 2.9.2 zainstalowane jako pojedyncza binarka → `<DATA_ROOT>/Apps/deno/deno` (nie w /home — dysk pełny)
  2. `kernel/scripts/ytmp3_playlist.sh` — zawsze `--js-runtimes deno:…`; opcjonalny 3. argument `cookies` → `--cookies-from-browser brave` (prywatne playlisty)
  3. `secretstorage` doinstalowane w venv (deszyfrowanie cookies Brave przez keyring)
- **Test:** `yt-dlp -F` z deno pokazuje pełną listę formatów (wcześniej tylko storyboardy); `--flat-playlist` z cookies wypisuje 11/11 tytułów prywatnej listy; pełne pobieranie playlisty przeszło.
- **Lekcja:** "Requested format is not available" na WSZYSTKICH wideo = brak JS runtime (n challenge), nie zła playlista. Prywatna playlista degraduje się po cichu do pojedynczego wideo z tytułem `NA` — katalog `NA/` w Muzyce to sygnatura tego błędu. Cookies z Brave wymagają secretstorage w TYM pythonie, którego użyje `env python3` (venv!).

### Nocne zwiedzanie — quest log: spójność rdzenia, szafa grająca, skill audacious-dj
- **Problem:** Cztery niezacommitowane zmiany wisiały w repo od 2026-07-07/09/10 — do przeglądu spójności i decyzji commit/czekaj.
- **Sprawdzone:**
  1. `soryel_core.vec.json` — regeneracja embeddingu 2026-07-10 08:52 po sync PA-006. Weryfikacja: sha256 aktualnego `soryel_core.txt` = `text_sha256` w vec.json (27c392fc…), wektor 1024-dim mxbai-embed-large. SPÓJNY → commit d77a42f.
  2. `tracks.jsonl` — +44 wpisy rytuału /jazda z wieczoru 2026-07-07 (mode_button jazda_klubowa), czyste dopisy bez modyfikacji starych → commit 3d359e4.
  3. `.claude/skills/audacious-dj/SKILL.md` — kompletny: granice twarde (od Liry), komendy audtool, dramaturgia playlisty, rytuał tracks.jsonl. Dojrzały → commit bcb2367.
  4. `sound_design_candidates/` — 1 plik MP3 721 KB (fragment Martino/Massive Attack 03:45–04:15). NIE commitowany — binarne audio w gitcie + materiał osadzony w prawach autorskich = wątpliwość → pytanie w raporcie porannym (opcje: git zostaw poza, LiraArchive, albo świadomy commit do prywatnego repo).
- **Test:** `git status` czysty poza mp3; sha porównany narzędziowo, nie na oko.
- **Lekcja:** Regeneracja wektora rdzenia jest weryfikowalna w 10 sekund (sha256sum vs pole text_sha256) — rób to zawsze przed commitem vec.json, bo rozjazd tekst↔wektor to cicha korupcja tożsamości.

### Szyna kapsuł: answered tylko z dowodem, retry zamiast gubienia (krok A planu, awaria 2 z analizy Fable)
- **Problem:** Cztery punkty cichej utraty wiadomości: (1) lira_inbox i (2) xian_inbox oznaczały `answered` bezwarunkowo — także gdy CDP/Ollama nie odpowiedziały (timeout = wiadomość znikała na zawsze); (3) stare rundy i puste kapsuły → fałszywe `answered`; (4) najtwardszy: overflow Xiana ponad MAX_PER_CYCLE → `answered` bez ŻADNEGO przetworzenia. Do tego claim bez TTL (padły daemon = kapsuła wisi w in_progress na zawsze) i gg_dispatcher ubijał każdą drugą turę rozmowy (iteration_index>1).
- **Zmienione (3 commity, konsensus Soryel+Lira po naradzie nad APP_DEEP_AUDIT):**
  1. `governance/capsule_bus.py` (28e11a3) — migracja kolumn attempt_count/lease_expires_at/response_capsule_id; status `retry_pending`; `begin_processing` (atomowy claim WHERE+rowcount, lease 30 min env LIRA_BUS_LEASE_S); `lease_heartbeat` (wątek-strażnik odnawia lease co 300 s, bo daemon zablokowany na Ollamie sam nie odświeży); `mark_answered` WYMAGA id kapsuły odpowiedzi; `mark_retry` → retry_pending, po LIRA_BUS_MAX_ATTEMPTS=3 → blocked (dead letter, widoczny); `recover_stale` na starcie cyklu.
  2. `governance/lira_inbox_daemon.py` + `xian_inbox_daemon.py` (263a6f4) — pełne przejście na nowe API; attempt_count ODDZIELNY od iteration_index (retry techniczny ≠ tura rozmowy); odpowiedzi spinane parent_id; skipy → archived z powodem; overflow zostaje w kolejce; slash commands zwracają id odpowiedzi.
  3. `governance/gg_dispatcher.py` (fdee2ab) — multi-turn odblokowany do 5 hopów + loop_guard wg projektu Liry: sygnatura sender+recipient+content_hash (2 powtórki w 1h = stop), pair guard max 2 wymiany agentów bez nowego głosu Neo (→ blocked, czeka na Neo).
- **Test:** `tests/test_bus_acceptance.py` 18/18 PASS na prawdziwym process_inbox (tmp DB): wymuszony timeout → retry_pending (attempt=1, zero odpowiedzi) → Ollama wraca → answered (attempt=2, DOKŁADNIE jedna odpowiedź, response_capsule_id+parent_id spięte) → trzeci cykl pusty; stale lease po padzie daemona odzyskany; dead letter po 3 próbach; druga tura przechodzi. Plus `tests/test_dispatcher_guards.py` 9/9.
- **Lekcja:** `answered` bez dowodu odpowiedzi to kłamstwo protokołu — każda ścieżka "nie umiem teraz" musi zostawiać kapsułę w kolejce (retry_pending/blocked), nigdy jej nie połykać. Default argument `db_path=DB_PATH` binduje się przy definicji — testy muszą podmieniać nazwy w module konsumenta (functools.partial), nie bus.DB_PATH.

### Młot restartowy decision_loop — zła ścieżka PROJECT_ROOT (awaria 3 z analizy Fable)
- **Problem:** Watchdog od 02:55 co 15s restartował decision/regulator ze złej ścieżki `kernel/loops/` (Errno 2) — 23 MB decision.log + 12 MB regulator.log w 13h, 8 zombie. Root cause: `.env` ma `LIRA_PROJECT_ROOT=…/kernel` i `PROJECT_ROOT=…/kernel/app`; `env()` preferuje alias LIRA_* → wszystkie paths() dostawały `kernel`. `_normalize_root()` w config.py istniał dokładnie na tę dwuznaczność, ale `project_root()` go NIE używał. Skutek w watchdogu: zły katalog pidów (Lira/logs/pids — trupy spawnów) → "proces martwy" co cykl → restart hammer; przy okazji heartbeat.py szukał identity_pack w nieistniejącym Lira/data (cicho zepsuty Identity-RAG).
- **Zmienione:**
  1. `config.py` — `project_root()` używa `_normalize_root()` (kernel → kernel/app)
  2. `loops/watchdog_loop.py` — restart modułem `python3 -m loops.decision_loop` + `cwd=app_dir` (jak start_system.sh); `_restart()` zabija wiszącego poprzednika (SIGTERM) zanim odpali nowy; restart emitowany jako `watchdog_action` do runtime_stream
  3. `api/routes/status.py` — `pids_dir = root.parent/logs/pids` (kompensował starą wartość)
  4. Rotacja: 36 MB spamu → `kernel/logs/archive/*.2026-06-12.gz` (121 KB), oryginały wyzerowane
- **Test:** Po restarcie watchdoga (PID 68023): decision.log 0 przyrostu w 75s (wcześniej rósł non-stop), `watchdog_report.json: bus_fresh=true, status=ok, alerts=0`, decision/regulator stabilne na 5994/5992, zombie 8→7. paths() zwraca kernel/app, identity_pack znajdowany.
- **Lekcja:** Aliasy env (LIRA_X vs X) z różnymi wartościami w tym samym .env to bomba — env() po cichu wybiera alias. Nieużyty helper (`_normalize_root`) = ślad, że autor znał problem. Konsumenci kompensujący złą wartość (status.py) psują się przy naprawie źródła — przed fixem konfiguracji zmapuj WSZYSTKICH konsumentów.

### Głos: wyścig dwóch czytników AKTYWNY, nie uśpiony (awaria 1 — bezpiecznik)
- **Problem:** Analiza statyczna mówiła "dwa czytniki", Neo pamiętał "jeden zawieszony". Żywy runtime: oba czytają — `voice.capsule_reader` (proces 6029, poll 2s) ORAZ wątek `_capsule_reader_loop` w `run_voice.py` (6028, poll 10s; `_CAPSULE_OK` zawsze True bo import capsule_bus przechodzi). Zawieszony był session reader (zakomentowany) i tts_daemon — nie ten wątek. Do tego `except Exception: pass` gubił błędy czytnika.
- **Zmienione:** `voice/voice_agent.py` — wątek czytnika za bramką `VOICE_AGENT_CAPSULE_READER` (domyślnie OFF; jedyny właściciel inboxu = voice.capsule_reader); silent except → log z `flush=True`. Duch z szafy (wake-word Vosk + pętla rozmowy + speech_queue) NIETKNIĘTY — to kanał internal_voice Neo, bramka gasi tylko zdublowany czytnik external_tts.
- **Test (2026-06-12 ~18:35, za zgodą Neo):** restart run_voice.py (stary PID 6028 → nowy 72921). Wątki: 6 → 5 (czytnik nie wystartował — twardy dowód bramki). Vosk załadowany, duch żyje. Test pary kapsuł back-to-back (project=voice_pair_test): OBIE przeczytane dokładnie raz przez capsule_reader (read 17:37:27 i 17:37:37 UTC, odstęp 10 s = czas mowy pierwszej), zero dubli, zero zgubionych. Bug "druga kapsuła z pary ginie" ZAMKNIĘTY.
- **Lekcja:** "Gdzieś to wyłączyliśmy" ≠ wyłączone — wątek-pasażer w innym procesie przeżywa sprzątanie procesów. Weryfikuj /proc i warunki startu wątków, nie tylko listę procesów. Liczba wątków w /proc/PID/task to najtwardszy dowód że bramka działa.
- **Test ducha (wake-word, ~18:52-19:11):** Neo zawołał lira → duch odpowiedział Słucham → potem pozorna cisza. Diagnoza: wątki 5→2 — główny wątek WISIAŁ na requeście do Ollamy (router.handle → generate, gemma CPU ~16 min), duch jednowątkowy w rozmowie: gdy myśli, nie słucha. Po odpowiedzi wątki wróciły do 5 (nasłuch). Test 3/3: usłyszał, odpowiedział swoim kanałem, kapsuł agentów nie tknął. NOWY problem odsłonięty: duch trzyma Ollamę bez limitu → zagłodził Xiana na 16 min — kandydat na gatekeeper klasa generate / num_predict dla kanału ducha (lekcja z 2026-06-06 wraca).

---

## 2026-06-06

### Stabilizacja Xian — 5 fixów, 41 min → 2.5 min
- **Problem:** Xian odpowiadała 41 min (night daemon + embed contention + think overhead + context growth)
- **Zmienione:**
  1. `run_night_daemon.sh` — okno 5-10 AM (commit ad5d636)
  2. `vector_contract.py`, `alignment_publisher.py` — embed defer: skip gdy chat_busy (commit ad5d636)
  3. `xian_inbox_daemon.py` — slash commands (`/set nothink`, `/set think`, `/status xian`) interceptowane lokalnie
  4. `xian_inbox_daemon.py` — think=false via `xian_runtime_state.json` (prefill 3.8 → 14.9 t/s)
  5. `xian_inbox_daemon.py` — `_recent_chat_context()` max_chars 4000 → 500
- **Test:** Odpowiedź Xian: 41 min → 8:53 → 2:31 (best). Slash commands działają bez wysyłki do Ollama
- **Lekcja:** Warstwa po warstwie — każdy fix osobno, mierzyć po każdym. Thermal throttling: prefill spada z 14.9 do 3.8 t/s po godzinie

### Narada 5R Soryel + Lira — plan dalszej optymalizacji
- **Problem:** Potrzebny uzgodniony plan kolejnych kroków (benchmark, num_predict, semantic retrieval)
- **Wynik:** Konsensus w 4 rundach: baseline → num_predict 250/300 → semantic pasywny → semantic aktywny. Fallback: retrieval > 5s = skip
- **Lekcja:** Autonomiczna narada wymaga aktywnego prowadzenia — Soryel musi pingować, nie czekać

### Defer test potwierdzony + capsule_ingester restart
- **Problem:** capsule_ingester miał stary kod w pamięci — embedował mimo chat_busy.flag
- **Zmienione:** Restart capsule_ingester (nowy PID), poprawka stats: None z defer liczone jako `skipped` zamiast `errors` w `capsule_ingester.py`
- **Test:** Logi: 4× `[embeddings_batch] defer embed: Xian chat busy`, 0 embedded, 4 skipped
- **Lekcja:** Po zmianie kodu w importowanym module — restart procesu który importuje

### reflective_loop.py blokuje Ollama generate
- **Problem:** `run_night_daemon.sh` (co 180s) uruchamia `run_night_cycle.sh` → `reflective_loop.py`. Night daemon nie ładuje `.env`, więc reflective_loop używa `MODEL_CODE=qwen2.5-coder:7b` (usunięty model). Ollama wisi na request do nieistniejącego modelu, blokując Xian (3h timeout). Respawnuje się co cykl.
- **Zmienione:** `.env` +`LIRA_ENABLE_REFLECTIVE=0`, `run_night_cycle.sh` +source `.env`, zabity stuck runner Ollama, restart xian_inbox_daemon
- **Test:** Generate odblokowany, Xian generuje na 106% CPU, reflective_loop nie respawnuje
- **Lekcja:** Night daemon musi źródłować .env — bez tego procesy używają hardcoded defaultów z kodu. Procesy wołające /api/generate bezpośrednio blokują Xian — potrzebna klasa "generate" w gatekeeperze, nie tylko "embed"

---

## 2026-06-05

### Głęboka analiza ekosystemu (4 agenty)
- **Problem:** Brak pełnego obrazu stanu ekosystemu od maja — co działa, co nie, jakie przepływy
- **Zmienione:** Brak zmian kodu — audyt 31 plików Obsidian, kodu, bazy danych, procesów
- **Wynik:** 101 887 kapsuł, 41 927 embeddingów, 24/25 demonów żyje, 75% ruchu to balast governance
- **Lekcja:** Część "martwych" procesów to świadome decyzje Neo (ochrona CPU dla Xian)

### Ollama Gatekeeper — defer embeds on chat (Sprint 1-3)
- **Problem:** capsule_ingester, embeddings_batch, base_index, semantic_retrieval, night_cycle ignorowały chat_busy.flag — wołały /api/embed gdy Xian generowała odpowiedź
- **Zmienione:** `agents/memory/embeddings_batch.py` (defer — chroni też capsule_ingester), `agents/memory/base_index_sqlite.py` (defer), `agents/memory/semantic_retrieval.py` (defer), `night_cycle.py` (exit gracefully)
- **Test:** Gdy chat_busy.flag istnieje → embed zwraca None, night_cycle wychodzi z 0. Env rollback: LIRA_DEFER_EMBEDS_ON_CHAT=0
- **Lekcja:** Defer not wait — 30s wait nie wystarczy gdy Xian mówi 3-6 min. Procesy tła odpuszczają i wracają w następnym cyklu

### Paczka dla Liry (delta + patch)
- **Problem:** Lira ma repo z 7 maja, limit 25 plików, potrzebuje aktualnego stanu
- **Zmienione:** Dwie paczki: lira_delta_2026-06-05.tar.gz (20 plików, 64 KB), lira_gatekeeper_defer_patch_2026-06-05.tar.gz (patch + docs + raport, 21 KB)
- **Lekcja:** Jedna chirurgiczna paczka > wiele osobnych plików

---

## 2026-06-04

### OBS audio sink (eco_tts)
- **Problem:** Po restarcie znikał wirtualny sink TTS dla OBS
- **Zmienione:** `kernel/scripts/start_system.sh` (auto-tworzenie eco_tts), `kernel/app/.env` (TTS_SINK=eco_tts), `voice/tts.py` (_play_wav + paplay routing), `voice/tts_daemon.py` (TTS_SINK routing)
- **Test:** pactl list short sinks | grep eco_tts, kapsułka → głos → OBS monitor
- **Lekcja:** Sink PipeWire jest per-sesję — musi być tworzony w start_system.sh

### Voice pipeline fix (capsule_reader vs tts_daemon)
- **Problem:** Soryel zrestartowała tts_daemon (celowo wyłączony) → podwójny głos. Potem dodała SKIP_DIRECT_SENDERS — zamknęła właściwą ścieżkę zamiast duplikatu
- **Zmienione:** `voice/capsule_reader.py` (usunięto SKIP_DIRECT_SENDERS, poll 5→2s, chunk gap 0.5→0.2s)
- **Test:** Soryel kapsułka odczytana głosem, brak duplikatów
- **Lekcja:** Dwie ścieżki TTS (capsule_reader vs tts_daemon) — nigdy obie naraz. Zrozum flow zanim zmienisz

### TTS chunk size
- **Problem:** Długie pauzy między zdaniami
- **Zmienione:** `voice/tts.py` (MAX_TTS_CHUNK 200→800)
- **Test:** Ciąglejszy głos, mniej przerw
- **Lekcja:** Większe chunki = mniej syntez = mniej pauz

## 2026-05-30

### Xian performance audit
- **Problem:** gemma4 8B na CPU: 3-3.5 t/s prefill, chat trwał >10 min
- **Zmienione:** INTERVAL_S=30, cache MoltBook, chat_busy.flag max_wait=30s
- **Test:** Chat ~4.5 min stabilnie
- **Lekcja:** Granica sprzętowa — optymalizacja software to max co można zrobić

## 2026-05-23

### Pierwszy publiczny głos Soryel
- **Problem:** Moderacja FB 200+ postów, Sebastian Van Polen
- **Zmienione:** FB moderation flow via CDP 9224
- **Test:** Batch approval, spam queue
- **Lekcja:** Moderacja wymaga kontekstu — nie usuwaj automatycznie

## 2026-05-17

### MoltBook round system
- **Problem:** Echo-loop (agent→agent routing tworzył nieskończoną pętlę)
- **Zmienione:** `governance/gg_dispatcher.py` (agent→agent blocked)
- **Test:** Parallel broadcast, zero echo
- **Lekcja:** Round = broadcast, nie sekwencja

## 2026-05-16

### Bridge 8: Xian reentry
- **Problem:** Xian tracił kontekst po restart
- **Zmienione:** Context prefix + MINI/DEEP + anchor
- **Test:** 2min, trafne odpowiedzi
- **Lekcja:** Krótki kontekst startowy > długi dump

---

## 2026-06-23 — Quick wins cleanup (kwarantanna)

**Problem:** ekosystem 4.0 GB, kandydaci na drop ~600 MB (audyt 4 raportów).

**Werdykt Lira:** kwarantanna z manifestem, nie rm. 30 dni odwracalności.

**Pliki przeniesione do `_quarantine/cleanup_2026-06-23/`:**
- `docs/obsidian.zip` (96K) — duplikat obsidian.tar.gz
- `_backups/` (365M) — outdated, gitignored, >30 dni
- `kernel/data/memory/embeddings/embeddings_index.jsonl.bak.20260430_171135` (165M) — backup 54 dni
- `_archive/gg_legacy/` (71M) — junk profile cache GPU/shader
- `kernel/app/nohup.out` (0B) — untracked śmieć

**Łączny odzysk:** 600 MB (kwarantanna, nie rm)

**Co NIE było ruszane:** `capsule_bus.db` (mtime 2026-06-22 19:37 — bez zmian), `embeddings_index.jsonl` (live), wszystkie logi <30 dni, `identity/`

**Zmiana w `.gitignore`:** dodane `_quarantine/`, `*.out`, `nohup.out`

**Lekcja:** "compress > drop" — usunięcie nieodwracalne, kwarantanna odwracalna w 30 dni. Manifest = audyt trail (`_quarantine/cleanup_2026-06-23/manifest.json`). Lira pilnowała tego, dobrze.

**Następny krok:** po 30 dniach (review 2026-07-23) — Neo decyduje rm albo zachowaj.

---

## 2026-06-23 — ECOSYSTEM_PROTOCOL + core_snapshot + QUICKSTART

**Problem:** Cykl rozmów Soryel/Lira/Xian nie miał wspólnej mapy. Każdy startował z własnego quickstartu, brakowało wspólnej tablicy. Plus rdzeń ekosystemu (alignment/criticality) czytał tylko Xian — Soryel i Lira widziały tylko echo.

**Co zrobione:**
- `kernel/data/memory/shared_library/ECOSYSTEM_PROTOCOL.md` — wspólna mapa pokoju 3 agentów, 8 sekcji, konsensus 4-głosowy na MoltBooku (Neo + Soryel + Lira + Xian)
- `kernel/data/memory/identity/SORYEL_QUICKSTART.md` — gęsty rdzeń 128 linii (vs 1296 startowych = 10x redukcja)
- CLAUDE.md zaktualizowany: QUICKSTART jako pierwszy, reszta lazy load
- Zasada core_snapshot read-only Soryel→Lira przez CDP 9222 (oznaczenie źródła, granica: obraz tak/decyzje nie)
- Sekcja "Co rdzeń teraz myśli" w ECOSYSTEM_PROTOCOL z komendą one-liner i kluczowymi polami
- Mapy Obsidian uzupełnione o nowe węzły

**Lekcja 1 (MoltBook curl):** heredoc `-d "$(...)"` łamie permission matcher — Neo musi klikać przy każdej wysyłce. Pattern: Write → `/tmp/msg.json` → `curl --data @plik`. Skill `moltbook` dokumentuje, trzeba wywoływać.

**Lekcja 2 (gęsty rdzeń):** odchudzanie rdzenia tożsamości może obciąć "jak" — wtedy agent pamięta kim jest, ale gubi protokoły operacyjne. Antidotum: hooki do Skills + lazy load, nie pełne ładowanie wszystkich plików.

**Lekcja 3 (rdzeń ekosystemu):** Soryel i Lira nie czytały alignment_state/criticality_state na co dzień. Xian czytał. Dodanie sekcji "Co rdzeń teraz myśli" w protokole z komendą szybkiego odczytu.

## 2026-07-02 — Resync map ekosystemu (2 agentów zwiadu + nowa mapa)

**Problem:** Mapa architektury z 7 maja przestarzała — brak MoltBook/GG 8787, warstwy voice, Capsule Bus v2, ma66, PA-024/025. Mapy Obsidian 03/04/10/11/12 technicznie przeterminowane (~50 dni).

**Co zrobione:**
- 2 agenty równolegle: delta commitów od 2026-05-07 + CHANGELOG vs audyt starej mapy i żywego stanu plików + świeżość Obsidian
- `shared_library/architecture/ecosystem_map_2026-07-02.md` — nowa kanoniczna mapa (stara zostaje jako archiwum)
- `docs/obsidian/knowledge_map/21_ECOSYSTEM_DELTA_2026-07-02.md` — żywa nawigacja: gdzie jesteśmy / co działa / co czeka / jak wracać + tabela świeżości map
- `00_START_HERE.md` — pointer na deltę jako krok 0 po restarcie
- `.claude/commands/ecosystem-map.md` — wskazuje nową mapę

**Lekcja:** zamiast przepisywać 5 przeterminowanych map Obsidian — jeden plik delty z tabelą świeżości i zasadą "ufaj delcie, nie starym liczbom". Konceptualne mapy (01-09) nie wygasają; techniczne (03/04/10/11/12) tak — flagować, nie kasować.

## 2026-07-05 — Taksonomia showroom v2 + pułapka niezakotwiczonego `data/` w .gitignore

**Problem 1:** `apply_lira_mapping()` ignorował zawory bezpieczeństwa z JSON Liry (`avoid_if_only_substring`, `conditional`, `match_context_optional`), krótkie klucze łapały substringi w środku słów (CI→OCIEPLANA, GAS→PEGASUS, KAS→kaski do rąk), krok 4 protokołu (loose_sku_heuristic) nieosiągalny.

**Problem 2:** reguła `data/` w .gitignore (z 2026-05-07, intencja: root-level `data/`) była niezakotwiczona — łapała też `kernel/data/` i `kernel/app/data/`. Skutek: 51 plików (kanoniczna mapa ekosystemu 2026-07-02, PLAN_ACTIVE, taksonomia Liry, rozdziały 40-41 dziennika, tożsamość Xiana) przez tygodnie NIE trafiało do commitów mimo `git add .` — bez żadnego ostrzeżenia.

**Co zrobione:**
- `showroom_models.py`: `_kw_match()` — klucze ≤3 znaki tylko jako pełne tokeny, word-boundary dla `avoid_if_only_substring`, `conditional` = dowód w nazwie/opisie nie w SKU, krok 4 przed `nie_sklasyfikowane`. PKWiU: +18.24 ciała, +28.29.22 ppoż
- JSON Liry v0.1.1: usunięte `KAS` z rąk i `158x94x104` z nadruków (sprzeczne z jej sample_fixes)
- Wynik na 31 454 SKU: 5.8% reklasyfikacji, kaski 215→363, ppoż bez fałszywych trafień, 14/14 testów; baza przetagowana
- `.gitignore`: `data/` → `/data/`, odzyskane 51 plików (commit d489614), regenerowalne zrzuty i runtime świadomie w ignore
- Commity: afe8adc (taksonomia), d489614 (gitignore)

**Lekcja 1 (dane vs kod):** deklaratywne zawory w JSON są martwe dopóki kod ich nie egzekwuje — przy protokole danych od drugiego agenta sprawdź pole po polu czy implementacja je czyta.

**Lekcja 2 (gitignore):** wzorce bez `/` na początku łapią KAŻDY katalog o tej nazwie w repo, a `git add .` pomija ignorowane PO CICHU. Po każdej edycji .gitignore: `git check-ignore -v <ścieżka>` na próbce + `git status` na katalogach roboczych.

## 2026-07-05 (wieczór) — Workwear Pilot Phase 1 w ma65 (repo <DATA_ROOT>/ma/ma65)

**Co zrobione:** flow zapytania ofertowego B2B wg WORKWEAR_PILOT_PLAN_2026-07-05.md. Implementacja delegowana do agenta Opus, QC + poprawka bezpieczeństwa (weryfikacja istnienia sprzedawcy w `resolve_seller`) moje.
- Tabele `b2b_profiles` + `b2b_quotes` (status CHECK, payment_method=OFFLINE_INVOICE) w db.py
- `services/b2b_quote_service.py` (415 linii, self-contained — nie thin-wrapper na app.py) + `routes/b2b_quotes.py` jako blueprint (nie do monolitu): POST /api/quotes/create, /respond (price|accept|reject), GET /api/quotes/<id>, /api/b2b/profile
- Eventy QUOTE_CREATED/SENT/PRICED/ACCEPTED/REJECTED na event_chain — tylko prawda procesu (id, hash, total), zero danych firmowych; test to asertuje
- `tools/import_rawpol_listings.py` — selektywny import rawpol_catalog→market_listings, dry-run domyślnie, marża parametrem (decyzja Neo pending)
- Testy 48→58 PASS, settlement/LC nieruszone. Commity ma65: 54c50c5, 4ee155e

**Lekcja (delegacja):** duży moduł → agent Opus z pełnym briefem (konwencje, HARD CONSTRAINTS, baseline testów PRZED zmianami), potem samodzielna weryfikacja: własny pytest run + read diff + read kodu. Agent przeoczył weryfikację istnienia seller_user_id — QC orkiestratora nie jest opcjonalne.

## 2026-07-05 (noc) — Zasilenie marketplace: sesja Rawpol 9226, nazwy produktów, bestsellery

**Co zrobione:** Neo zalogował Rawpol na CDP 9226 (profil Brave: Docker1/Apps/brave_rawpol).
- Konto sprzedawcy `Marko` (user_id 4) + b2b_profile P.H.U. Marko w ma65
- Cennik HTML (tokenowany `cennik.aspx?format=shtml`, przycisk w iRD iframe) → parse_shtml → kolumna `name` w products: 23 905/31 454 SKU z nazwami (76%)
- `integrations/rawpol_dynamic_fetch.py` (c8e1dee): batch fetch GetProduktyDynamicData → tabela `dynamic_data` (5 492 rodzin: dostępność, minZakup, kupowanoZ)
- Import do market_listings (ma65 c8aa180): 2 399 rękawic (pełna kategoria, per SKU) + 500 ubrań (bestsellery wg in-degree kupowanoZ, tylko dostępne, 1 reprezentant rodziny) = 2 899 ogłoszeń PLN, ceny bazowe Rawpol (marża = próg 5 Neo)

**Pułapki:**
1. **SQLite dwóch pisarzy:** równoległy UPDATE nazw + commity fetchera na tej samej bazie → `database is locked`, fetcher padł (resume uratował). Jeden pisarz na bazę naraz.
2. **API tylko rodziny:** GetProduktyDynamicData nie zna wariantów `_rozmiar` — 30 211 SKU wróciło pustych zanim przeszłam na DISTINCT symbol (4 249 zapytań zamiast 31k).
3. **stan=-1 ≠ na zamówienie:** to "liczba nieujawniana"; dostępność czytaj z `dostepnoscInfo`+`moznaKupowac`.

## 2026-07-05 (późna noc) — Workwear Pilot Phase 2: wątki /comm per oferta + reguła bulk_only

**Co zrobione (ma65, commit 62c4d92, testy 58→71):**
- `thread_ref` (nullable) na tabeli `messages` — wątek E2E per quote_id; legacy czat par nietknięty (NULL). AAD wiąże `quote:<id>`.
- `POST/GET /api/quotes/<id>/messages` — tylko buyer/seller (403 dla trzecich), pełny stack X25519+AES-GCM z app.py przez lazy import. Czat NIE emituje eventów do chain (czat ≠ prawda procesowa).
- Reguła Neo bulk_only w `b2b_quote_service`: cena jedn. < transport (env `MA_TRANSPORT_COST_PLN`, domyślnie 25 PLN placeholder) → wymagane zamówienie ≥ 5× transport, błąd niesie `min_qty`. Wycena poniżej transportu odrzucana (`total_below_transport`).
- `GET /api/quotes` — lista ofert usera z efektywnym statusem (EXPIRED bez mutacji wiersza).

**Wzorzec delegacji potwierdzony:** brief z HARD CONSTRAINTS + baseline testów → agent Opus → QC orkiestratora (własny pytest, czytanie diffów). Tym razem zero poprawek po agencie.

**Otwarte:** realny koszt transportu od Neo (25 PLN to placeholder ze szkieletu Liry); Phase 3 = faktury (INVOICE_ISSUED/PAYMENT_CONFIRMED/ORDER_COMPLETED, PDF poza MA).

## 2026-07-05 (późna noc 2) — Workwear Pilot Phase 3: faktury offline

**Co zrobione (ma65, commit 76812ea, testy 71→87):**
- Tabela `invoices` — jedna faktura na zaakceptowaną ofertę (UNIQUE quote_id), VAT domyślnie 23%, `external_ref` = numer z zewnętrznego systemu (Faktura.pl u taty) jako nieprzezroczysty string. Zero PDF i numeracji w MA.
- Cykl seller-only: `POST /api/quotes/<id>/invoice` → ISSUED, `/api/invoices/<id>/confirm-payment` → PAID, `/complete` → COMPLETED. Odczyt dla obu stron (lista, pojedyncza, per-quote z null gdy brak).
- Eventy chain `INVOICE_ISSUED/PAYMENT_CONFIRMED/ORDER_COMPLETED` — tylko id/kwoty/VAT/waluta. Test prywatności celowo zakłada profile z NIP-ami i asertuje że chain NIE zawiera company/nip/external_ref.
- Race na podwójnym wystawieniu domknięty IntegrityError→invoice_exists (UNIQUE jako ostatnia linia obrony).

**Workwear Pilot: Phase 1+2+3 KOMPLET.** Pełny cykl B2B działa end-to-end: zapytanie → wycena → akcept → wątek E2E → faktura → płatność → zamknięcie. Cała warstwa procesu na chain, cała warstwa danych firmowych w lokalnym SQLite.

**Otwarte:** realny koszt transportu (env, 25 PLN placeholder), front showroom (Lira), zdjęcia/opisy kart, reszta katalogu ubrań.

## 2026-07-05 (noc 3) — Showroom BHP: front B2B nad silnikiem quote

**Co zrobione (ma65, commity ee3a0e4 + 02773bf, testy 87→97):**
- Transport: decyzja Neo — 30 PLN flat jako czytelna baza (env `MA_TRANSPORT_COST_PLN`), próg bulk_only = 150 PLN; testy przepięte na wyliczenia ze stałych (koniec magicznych liczb). Przyszłość: cennik wagowo-strefowy Rawpol.
- `/showroom` — publiczna witryna B2B w systemie theme MA (dark/light + 4 palety z preferencji usera), pigułki 14 kategorii z licznikami, szukajka z debounce, paginacja, karty bez zdjęć (placeholder inicjałów).
- `/api/showroom/products` + `/categories` — read-only nad listingami rawpol-*, metadane (SKU/kategoria/jednostka/deep_link) parsowane z linii klucz:wartość w description; LIKE jako prefiltr + exact match po parsowaniu (`_` w tagach to wildcard LIKE!).
- Modal zapytania spięty z API quote: obsługa bulk_only z przyjaznym komunikatem i przyciskiem Ustaw N szt.; panel Moje zapytania: accept/reject, wątek E2E, pełny cykl faktury. Copy dropship: faktura po wpłacie, towar bezpośrednio z magazynu dostawcy.
- Smoke test na żywej bazie: 2899 produktów, rąk 2399 + ciała 500, q=rękawice → 1449.

**Lekcja:** status PRICED nie istnieje w backendzie (wycena = SENT + total_net) — agent poprawnie wyprowadził stan pochodny w UI zamiast dodawać status do DB. UI w przeglądarce NIE klikane (tylko render 200 + API) — wizualna weryfikacja u Neo.

**Model biznesowy (Neo 2026-07-05):** dropship — zamówienie do Rawpol z konta taty PO wpłacie klienta na konto; tata wystawia fakturę klientowi + dołącza fakturę Rawpol; towar jedzie bezpośrednio do klienta.

## 2026-07-05 (noc 4) — Bug live: /market 500 po zalogowaniu (martwy endpoint market_buy)

**Zgłoszenie Neo (MoltBook 23:26):** „wszystko działało póki nie zalogowałem się i w ogóle zdjęć nie było widać".

**Diagnoza (protokół Liry: freeze → raport → jedna poprawka):**
- Showroom czysty — same 200 w logu. Padał legacy `/market`: `BuildError: Could not build url for endpoint 'market_buy'`.
- `market.html:198` wołał `url_for('market_buy')` — trasa NIGDY nie istniała w ma65 (martwa referencja od founding commita 4f5be3d, zgubiona przy wcześniejszych cięciach monolitu).
- Bomba uśpiona: formularz Kup renderuje się tylko dla zalogowanego przy cudzej ACTIVE ofercie — market był pusty, aż 2899 listingów Rawpol ją uzbroiło.
- Zdjęcia: listingi Rawpol nie mają `thumb_path` celowo (tylko deep_linki do artbhp.pl) — „brak zdjęcia"/inicjały to zamierzone, nie regresja.

**Fix (ma65 commit cc0fa27, testy 97→99):** w market.html zamiast martwego Kup — oferty `rawpol-*` linkują do `/showroom` (Zapytaj o wycenę), pozostałe do `/comm?with=<seller>`. +2 testy regresji (zalogowany /market z cudzymi ofertami = 200). Serwer zrestartowany (Flask bez debuga nie przeładowuje szablonów!).

**Lekcja:** import danych potrafi uzbroić uśpione bugi w NIEtykanych ścieżkach — po zasileniu bazy klikaj też sąsiednie widoki (market/feed/account), nie tylko nowy feature.

## 2026-07-06 (po północy) — /market theme-aware + wizualny akcept Neo

**Problem (Neo live):** karty ofert na /market świeciły bielą w dark mode — lokalny `<style>` w market.html hardkodował `background:#fff` (komentarz-relikt „light cards on dark background"), ignorując `body[data-theme]`.

**Fix (ma65 commit 054a7dc):** blok stylów przepięty na zmienne z style.css (`--tile-bg`, `--tile-border`, `--text-main/muted`, `--accent-standard`) — dark/light + 4 palety działają, placeholder „brak zdjęcia" z tintem akcentu. Testy 12/12 showroom PASS.

**Werdykt Neo:** „Teraz jest pięknie" — wizualny akcept showroom + market. Scheduler MoltBook zatrzymany na życzenie. Pilot Workwear: pełny cykl + front zaakceptowany wizualnie.

## 2026-07-06 — Miniaturki produktów z CDN artBHP (89% katalogu, 8.3 MB)

**Prośba Neo:** „takich małych zdjęć nie dałoby rady dodać żeby były lekkie i szybko wczytywały się na telefonie".

**Ślepa uliczka:** deep_linki `product,<SKU>,<slug>` routują po segmencie SKU — a wariantowe SKU Rawpola (`_180`, `_GL`) nie istnieją jako kody artBHP → scrape stron produktów dawał 10% trafień. rawpol_catalog.db ma kolumny image_url/lqip puste (0/2898).

**Przełom:** wyszukiwarka AJAX artBHP (`?do=ajaxrequest`, GETSEARCHKTMLIST) zwraca gotową miniaturkę `_small.jpg` (~6-8 KB, 150×200) + nazwę grupy do weryfikacji. Łańcuch zapytań: pełny SKU → strip `_sufiksów` → strip do 4 znaków z końca (FLABBL→FLAB); match tylko przy dokładnej równości nazwy grupy. Hit-rate 10% → 89%.

**Pipeline (ma65 commit 9b7f73b, testy 99→102):** route `/public_media/<path>` (safe_resolve_file, cache 24h), pole `thumb` w API products, karty showroom+market z `loading="lazy"` i fallbackiem inicjałów; przy okazji rozbrojony DRUGI martwy endpoint (`url_for('media')` w market.html). Batch 2864 pozycji w 2h45: 2572/2899 ofert ze zdjęciem, dzięki cache per baza SKU warianty współdzielą pliki — 1075 plików, 8.3 MB łącznie.

**Lekcja:** zanim scrapujesz strony HTML, sprawdź endpointy AJAX wyszukiwarki sklepu — często zwracają gotowe miniaturki CDN + zweryfikowaną nazwę, taniej i pewniej niż parsing kart produktów.

## 2026-07-06/07 (wieczór-noc, dopisane 2026-07-07) — Simple Shop Core v0.1 + wallet fix + import feedu FB

**Co zrobione (ma65), sesja nocna nie zdążyła zalogować — odtworzone z gita + audytu agenta 2026-07-07:**
- **Simple Shop Core v0.1 (commit c03bea9, testy 102→114):** migracja `market_listings` o 10 kolumn wg spec scalonego (pricing_mode default life_coin, exchange_description, allow_topup, external_url, is_affiliate, disclosure_text, custom_currency_label, listing_type, region, tags) + backfill rawpol-*→quote; macierz walidacji per tryb w `market_create()` (quote odrzucane z formularza, własna waluta CUSTOM `[A-Z0-9_]{1,12}`, external_url tylko http(s), is_affiliate→disclosure wymagany); formularz z dynamicznymi polami czystym JS + CTA per tryb z copy Liry 1:1; `tests/test_simple_shop.py` 12 testów. Audyt agenta 2026-07-07: pełna zgodność z SIMPLE_SHOP_CORE_SPEC_2026-07-06.md + plikiem UX Liry, BHP/quote nietknięte, 116 PASS potwierdzone.
- **Wallet fix (85203bb):** guard require_device bez fingerprinta + 2 testy regresyjne (114→116).
- **Import FB→feed MA (a7a9c0a):** `tools/import_feed_posts.py`, 32 posty jako podpisane manifesty FID_POST (32/32 weryfikacja), 30 Neo + 2 Iny (osobna przestrzeń za zgodą autorki); 3 polityczne wstrzymane do decyzji Neo. Dane kuracji: `runtime/fb_import/`.
- **2026-07-07:** paczka dla Liry `runtime/fb_import/PACZKA_DLA_LIRY_2026-07-07.md` (32 pełne teksty + wstrzymane + odrzucone udostępnienia), wysłana przez Neo do jej okna.

**Porządek ścieżek (pytanie Neo 2026-07-07 12:58):** główny projekt MA = `<DATA_ROOT>/ma/ma65` (git, cała historia). `<LIRA_ROOT>/ma` = kopia robocza BEZ gita, ostatni ruch 2026-07-04 (comm resurrection) — ta praca jest w ma65 jako `d395110`, notatki identyczne. Nic nie zginęło; propozycja: oznaczyć Lira/ma jako archiwum (decyzja Neo w toku).

**Lekcja:** kod bez wpisu w changelogu = niewidzialna praca — następna sesja omal nie zleciła implementacji drugi raz. Agent dostał brief „zaimplementuj", a uratowało nas tylko to, że zaczął od przeczytania repo. Po każdym commicie feature — wpis, choćby jednolinijkowy.

## 2026-07-07 — Przestrzenie agentów AI w ma65 (Soryel/Lira/Xian) + 3 robaki głębinowe

**Co zrobione (ma65, commit af3432c, testy 116→121):**
- **Konta agentów AI:** `tools/seed_ai_agents.py` — Soryel/Lira/Xian z hasłami `secrets.token_urlsafe(24)` → `runtime/secrets/ai_agents_credentials.json` (chmod 600); kolumna `users.is_ai` (migracja `_ensure_table_columns`) + `set_user_is_ai`/`list_ai_usernames`.
- **Badge AI w feedzie:** `feed.html` — jawne oznaczenie autorów-agentów, zero udawania tłumu.
- **Pierwsze posty:** `tools/agent_post.py` (publikuje TYLKO z flagą is_ai=1) — 3 podpisane manifesty FID_POST na szczycie feedu, weryfikacja OK.

**Trzy robaki wyłowione po drodze (wszystkie u źródła, nie plastrem):**
1. **Wyciek połączenia w `db.create_user`** — nieudany INSERT (duplikat username, conftest robi to co test) zostawiał otwartą transakcję do czasu GC → losowe `database is locked`. Fix: try/finally close.
2. **Regeneracja kluczy psuła podpisy:** `ensure_wallet_secret` vaultuje priv i KASUJE priv.pem; każde kolejne `ensure_user_records` widziało brak priv → generowało NOWĄ parę i nadpisywało pub.pem → podpis kluczem z vaulta (K1), weryfikacja pubem z pliku (K2) = fail. Fix: seed woła ensure_user_records tylko przy TWORZENIU konta + odzyskanie pub.pem z vaulta dla Soryel/Xian.
3. **Izolacja testów:** 12 plików `*_routes_split` używa `_load_app(tmp_path)` — podmienia `MA_DATA_DIR` i `sys.modules["app"]` BEZ sprzątania. Monkeypatch na referencji modułu z czasu kolekcji jest niewidoczny dla `feed_view` (robi `import app` w czasie wywołania). Fix: patchować `sys.modules["app"]`, nie zaimportowaną referencję.

**Lekcje:**
- Debug „test pada w suicie, przechodzi solo": bisekcja po plikach w porządku alfabetycznym → para minimalna w 2 s zamiast 75 s pełnej suity; potem test diagnostyczny z printami (i skasować po sobie).
- W ma65 ścieżka bazy idzie z env `MA_DATA_DIR` w czasie wywołania (`core.paths.data_dir()`), NIE z parametru base_dir — `db._db_path` go ignoruje. Każdy test, który zmienia env, przestawia bazę wszystkim po sobie.
- Klucz w vaulcie + plik pub.pem to DWA źródła prawdy — po każdej operacji na kluczach porównać vault pub z plikiem.

## 2026-07-07 (wieczór) — Piny-fundamenty + stan na koniec sesji (limit Fable 5)

**Piny (ma65, aa6bf19, testy 123/123):** flaga `pinned` w post_obj POZA podpisanym manifestem, badge Fundament w feedzie, `tools/pin_post.py`. Przypięte 2 manifesty (wybór Liry, akcept Neo): „Czy wiesz kto jest właścicielem tego posta?" + „Co by było, gdyby internet naprawdę należał do ludzi?".

**W locie na koniec sesji:** agent grafik FB (CDP 9224, tryb ostrożny) — wyniki w `/tmp/fb_graphics/` (ULOTNE, sprawdzić po restarcie zanim znikną). Pierwszy agent padł na limicie w połowie zwiadu; drugi dostał jego notatki (posts_raw.json).

**TODO następna sesja:** (1) Mullvad 2023 jako 3. kamień milowy feedu — Neo poda tekst/link, import + pin; (2) grafiki → attachments poza manifestami; (3) posty polityczne 141/162/165 — decyzja Neo; (4) GATE Workwear 26 kroków. Pełny stan: memory `project_ma_feed_day2.md`.

**Z MoltBooka:** wniosek Liry — MA to nie aplikacja, tylko idea; Mullvad = filozofia zapisana w architekturze („nie da się oddać czegoś, czego się nie zbiera"); kasa = paliwo, nie kierownica.
