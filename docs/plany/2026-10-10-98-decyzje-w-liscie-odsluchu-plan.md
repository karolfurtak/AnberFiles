# Plan 98a — decyzje i raporty automatów na liście „Do przesłuchania”

Zlecenie 98a (10.10.2026), etap planu. Kod i testy w tym biegu bez zmian. Kolejne etapy: 98b przegląd (Opus 5.5),
98c implementacja (Opus 5.5, bramka: werdykt ZATWIERDZONY).
Polecenie Karola (10.10, ok. 13:00): „Wciąż w http://100.67.174.47:8790/?przesluchania=1 nie widzę raportów zadań
dziennych np. skill-scout'a. Najlepsze byłoby zatwierdzanie każdej propozycji skilla oddzielnie.” oraz
„(wszystko co wymaga decyzji zwrotnej usera)”. Decyzja: „A — robimy teraz”.

## 1. Stan zastany

Każdy fakt poniżej sprawdzony własnym odczytem 10.10.2026 (klon `/tmp/jarwis-wykonawca/anberfiles`, vault odczytany
tylko do odczytu).

### 1.1 Repozytorium i testy

| Fakt | Zmierzone |
|---|---|
| HEAD `main` | `a567a59` — zgodny z promptem |
| CI ostatniego commita `a567a59` | zielone (`gh run list`: success); commit `d5e7a5c` dwa wcześniej był czerwony, naprawiony w `421a318` |
| `app/przesluchania.py` | 596 linii — zgodne |
| `tests/test_przesluchania.py` | 690 linii, 31 testów — zgodne |
| Testy lokalne na Pi 5 (Python 3.13.5, venv w `/tmp/jarwis-wykonawca/venv-anber`, zależności przypięte ze skrótami) | 421 zielonych, 1 pominięty, 1 czerwony |
| Czerwony lokalnie | `tests/test_lektor_piper.py::test_edge_tworzy_cues_z_przesunieciem_chunkow` — `assert 0 == 3` (atrapa Edge nie jest wywołana). Czerwień środowiskowa Pi 5 (ten sam kod jest zielony na CI); przyczyny nie badano — poza zakresem planu. 98c ma odjąć ten test od własnego wyniku „przed/po” |

### 1.2 Sprzeczność z promptem, którą trzeba znać: gdzie leży klon vaulta

Prompt: „`/srv/anberfiles/korzen/vault` widziane z konta `karol` jest puste”. Wyjaśnienie z `systemctl cat anberfiles`:

- Prawdziwy klon vaulta (źródło GitHub `karolfurtak/backup-asystent-ai`) leży w **`/srv/anberfiles/vault`**
  (z `.git`, katalogi `Zasoby/`, `.claude/`, plik `NOW-pending.md`).
- `/srv/anberfiles/korzen/vault` na hoście to pusty punkt montowania. Jednostka `anberfiles.service` ma
  `BindReadOnlyPaths=/srv/anberfiles/vault:/srv/anberfiles/korzen/vault` — dopiero w przestrzeni nazw usługi
  `korzen/vault` pokazuje klon, **tylko do odczytu**. Dlatego usługa widzi 80 notatek, a zwykłe konto — pusty katalog.
- `ReadWritePaths` usługi: tylko `/srv/anberfiles/dane` i `/srv/anberfiles/korzen/lektor`. Wszystko, co 98c zapisze,
  musi trafić do jednego z nich (decyzje → `dane`; tekst dla lektora → `korzen/lektor`).
- Odświeżanie klonu: `anberfiles-odswiez` (`git pull --ff-only`, limit 120 s) z `anberfiles-vault.timer`, co 15 minut.
  Skutek dla planu: decyzja naniesiona przez laptop widoczna jest w AnberFiles po 15–30 minutach (push laptopa +
  timer) — pozycja musi w tym oknie zachować się poprawnie (punkt 3.7).
- Proces testów (konto `claude`) nie widzi tego montowania i nie ma go widzieć: testy budują własne drzewa w `tmp_path`.

### 1.3 Kod, który istnieje (potwierdzony odczytem)

- `app/przesluchania.py`: `Magazyn` (jeden plik JSON `katalog_danych/przesluchania.json`, zapis atomowy), `WERSJA_STANU = 1`,
  `dodaj_decyzje` (zakres tylko `notatka`), `potwierdz`, `eksport_json`, `eksport_markdown`, `ZAKLADKI`
  (`decyzja`, `wdrozenia`, `informacyjne`, `zaakceptowane`, `rozstrzygniete`), `ZAKRESY = ('notatka',)`.
  W `_wczytaj` brakujące klucze stanu są uzupełniane (`setdefault`), więc nieznane klucze **przeżywają** zapis starego kodu.
  Uszkodzony plik jest odkładany obok (`.uszkodzony-<czas>`), nie nadpisywany.
- `app/server.py`: `przesluchania_get` (5188), `przesluchania_post` (5222, polecenia `ui` i `potwierdz`),
  `notatka_post` (decyzja przy notatce), `render_przesluchania_page` (5391), `_prz_notatki` (skan w
  `run_in_executor`, pamięć podręczna `PRZ_SKAN_TTL_S = 30`), `_prz_licznik`, `_cors_strony_startowej`, inicjalizacja
  `Magazyn` (6004). Lektor jest oparty o PLIKI: `lektor_item` przyjmuje `POST <ścieżka>.md?lektor=1` dla pliku pod
  `ROOT`, wynik trafia do `katalog_lektora/<katalog względny>/<nazwa>_lektor.mp3`.
- Ochrona zapisu: middleware `straz_zrodla` — każdy POST/PUT/PATCH/DELETE bez nagłówka `X-AnberFiles: 1` → 403 (istnieje,
  nic nie dodawać). Dostęp do GET: logowanie formularzem (`/__anberfiles/zaloguj`, pole `haslo`, ciasteczko) — bez sesji 401.
  Żądanie bez nagłówka `Origin` (skrypt, curl) przechodzi logowanie bez tokenu formularza (`origin_zgodny(None) = True`).
- CI: `lint` (ruff `E9,F` na `app/ tools/ tests/ narzedzia/anberfiles-reset-hasla` + `py_compile` jawnej listy plików)
  i `testy` (macierz `anbernic` Python 3.10 oraz `jarvis` Python 3.13). Nowe pliki trzeba dopisać do obu poleceń lint.
- Przeglądarka bez okna: na Jarvisie nie ma `chromium`, `google-chrome` ani `playwright` w PATH; zrzuty z 02.10
  (`Zasoby/Wykonawca-i-Puls/zrzuty-anberfiles-lektor/*.png`) istnieją, ale narzędzia, które je zrobiło, nie ustalono.
  98c ustala je na początku porcji C (propozycja: Chromium bez roota w `/tmp/jarwis-wykonawca/chromium`).

### 1.4 Faktyczny kształt czterech źródeł (poprawki względem promptu)

**Źródło 1: `Zasoby/raporty/skill-scout/_proposed.json`** (93 KB, zmieniony 10.10 12:39).
- Obiekt `{ "proposed": [ … ] }`, **87 elementów**: 73 `type: new`, 14 `type: update` (z `update_target`), 87 unikalnych `slug`.
- Pola każdego elementu (wszystkie obecne we wszystkich 87): `slug`, `title`, `type`, `update_target`, `freq`,
  `minutes_per_run`, `saved_per_week_min`, `priority`, `what`, `evidence` (lista tekstów), `first_proposed` (data).
  10 elementów ma dodatkowo `machine` (`jarvis`). Najdłuższe `what` ma 626 znaków, najdłuższy `title` 133.
- Korekta: **w elemencie nie ma żadnego pola statusu ani decyzji.** Plik nie wie, co zatwierdzono. Liczba 68 „kandydatów
  skill-scout” z `NOW.md` nie zgadza się z 87 w pliku (skąd 68 — nieustalone; do pytania kontrolnego).

> PRZEGLĄD: (ustalenie 1) Plik nie ma pola statusu, ale vault WIE, co już zrobiono: 18 elementów `type: new` ma
> `slug` równy istniejącemu skillowi `.claude/skills/<slug>/SKILL.md` w klonie (m.in. `poranny-briefing`,
> `audyt-pulpitu`, `odbior-pracy-agenta`, `kaskada-testow-regresyjnych`), a 2 elementy `type: update` mają slug
> istniejącego skilla. Plan pokazałby 18 gotowych skilli jako „do decyzji”. 87 − 18 = 69, co niemal wyjaśnia 68 z
> `NOW.md`. Poprawka w 3.1 (wykluczenie) i 6.2 (bez liczby 87). Skilli w klonie jest 41 (każdy z `SKILL.md`);
> skilli wtyczki (np. `daily`, `puls`) w klonie nie ma, więc dla `update` stanu nie da się ustalić — zostają pozycją.
- `data/<data>.json` (np. `2026-10-10.json`) to NIE ten sam kształt: `{date, window_days, stats{intents, sessions},
  candidates[]}` — wynik pojedynczego przebiegu (10.10: 2 kandydatów). Pliki mają przyrostki (`2026-08-12_jarvis.json`).

**Źródło 2: `Zasoby/Jarwis-vault-i-skille/_reflect-pending.md`** (28 KB).
- 27 sekcji `## ` (nagłówki unikalne, np. `## persona.md → § 7 (Rób ✓) — praca w oknie czasowym`), wszystkie mają
  dokładnie jedną linię `- [ ] ✅ Zatwierdź tę zmianę` — **ostatnia niepusta linia sekcji**, tuż po bloku ```diff.
- Przed pierwszą sekcją: długi wstęp (blockquote `>`), a na końcu pliku `### Odrzucone w tym przebiegu (świadomie)`
  (poziom H3, bez checkboxa) — to NIE jest pozycja do decyzji; kończy ostatnią sekcję `##`.
- Korekta: **nie ma sekcji „zastosowanych”.** Zastosowana propozycja jest po prostu usuwana z pliku (nagłówek pliku:
  „Gdy plik się opróżni — znika sam”). Numeracja „pozycje 1–27” z wstępu nie występuje w nagłówkach — identyfikatorem
  nie może być numer. Jedyny znacznik decyzji to `- [x]` (zatwierdzenie); odrzucenie nie ma znacznika.

> PRZEGLĄD: (ustalenie 18) Stan na 10.10 wieczorem (po commicie vaulta `916bdde` 19:46): 24 sekcje `## ` i 24 linie
> `- [ ] ✅ Zatwierdź tę zmianę` (nie 27; trzy usunięte), plik 25 808 bajtów, a po `### Odrzucone w tym przebiegu
> (świadomie)` doszła druga sekcja H3 `### Rozstrzygnięte przez CEO 10.10.2026 (usunięte z listy)`. Reguła „sekcja `##`
> kończy się na `##`/`###`/końcu pliku” obsługuje to bez zmian; liczb sekcji nie wolno wpisywać w kryteria odbioru (6.2).
> Linia nagłówka pliku brzmi `*Wygenerowane: 2026-09-28, uzupełnione 2026-10-10 | Zakres: …*` — patrz poprawka klucza raportu w 3.1.

**Źródło 3a: `.claude/rules/NOW.md`, sekcja `## Otwarte decyzje`** (linie 53–69, `- [ ]` w jednej linii każdy, 15 pozycji,
żadnego `- [x]`). Warianty: „A włączyć · B zostawić wyłączoną”, „A nie blokują · B blokują”, „A awatar-lustro · B podgląd
dla trenera”; pytania „USB-A czy USB-C”, „−6 czy AUTO”; kilka pozycji łączy dwie sprawy (`**…** · **…**`, `**…**; **…**`);
ostatnia („Kolejki propozycji: 20 pozycji `/reflect` … 68 kandydatów skill-scout”) jest metawpisem o źródłach 1 i 2 —
**duplikat**, do pominięcia. Ścieżka leży w katalogu ukrytym `.claude` (skan notatek go pomija; parser czyta ją wprost).

**Źródło 3b: `NOW-pending.md` (korzeń vaulta), `## Decyzje do briefingu`** (linie 130–556, 427 linii).
- Plik (116 KB, 1357 linii) zawiera w środku KOPIĘ starej treści `NOW.md` (blok `NOW:START`, linie 13–119) z drugą sekcją
  `## Otwarte decyzje` w linii 54 — parser MUSI ograniczyć się do sekcji `## Decyzje do briefingu` aż do następnego `## `
  (tu `## Stan pracy długotrwałej`).
- Sekcja ma 92 wpisy `### `; **87 to powiadomienia o biegach Wykonawcy** (`### Wykonawca: 86b: PRZEGLAD Opus - … (data, wykonawca, jarvis)`,
  treść: numer biegu, status, dziennik), 5 to inne (m.in. `### ALARM automaty (10.10.2026 12:30, laptop)`,
  dwa „Automat wypychania vaulta zatrzymany na konflikcie scalania”). Korekta: **wpisy nie są pytaniami o decyzję w formie A/B**;
  to powiadomienia do wiadomości. Wykonawca-powiadomienia (87 z 92) zaśmieciłyby listę.

> PRZEGLĄD: (ustalenie 2) W pliku są DWIE sekcje `## Decyzje do briefingu`: linia 130 (92 wpisy, 87 Wykonawcy, 5 innych)
> i linia 1304 (10 wpisów, żaden Wykonawcy: dziewięć „Automat wypychania vaulta zatrzymany…” i „Aktualizacja pamięci
> uruchamiana potrójnie…”), sekcja kończy się na `## ` albo końcu pliku. Wpis „(2026-09-04 17:20, …)” jest w obu
> sekcjach. Pozycji nie-Wykonawcy jest więc 15, nie 5. Poprawka w 3.1 i teście 10.

**Źródło 4: najnowszy raport automatów.** Korekta względem promptu — raporty osobne istnieją tylko dla jednego automatu:

| Automat | Co faktycznie istnieje | Wejście dla pozycji „do przeczytania” |
|---|---|---|
| `/skill-scout` | `Zasoby/raporty/skill-scout/Raporty/raport-aktualny.html` (132 KB, ta sama treść co `2026-10-10.html`, importuje czcionki z internetu — nie nadaje się do czytania ani lektora) oraz `data/<data>.json` | tekst zbudowany z najnowszego `data/*.json` (data, okno, liczby, tytuły kandydatów) + odnośnik do HTML |
| `/daily` i `/memory-update` | **brak osobnych raportów.** Wynik każdego to zapis nowej treści `NOW.md` (nagłówek `*Ostatni update: 2026-10-10 12:15 (daily; poprzedni: daily 09.10 20:40)*`, linia 3) i bloki `NOW:START` w `NOW-pending.md`; surowe archiwum `Zasoby/raporty/biegi/` zawiera jeden plik (2026-09-11) | linia „Ostatni update” + pierwszy blockquote `NOW.md` (linia 10); jedna pozycja „Pamięć (daily/weekly)”, nie dwie (tryb z nawiasu) |
| `/reflect` | brak raportu; jedyny ślad to wstęp `_reflect-pending.md` („Wygenerowane: … Zakres …”, akapity `>`) | pierwsze dwa blockquote wstępu; plik nieobecny = kolejka pusta ALBO plik przeniesiony — stan „nie da się ustalić” |

## 2. Rozbiór problemu

1. **Brak mostu do vaulta w stronę zapisu.** AnberFiles nie może zaznaczyć `- [x]` ani edytować `_proposed.json`
   (decyzja Karola 01.10, wiązanie tylko do odczytu). Decyzja musi być rekordem u AnberFiles, a nanoszenie robi agent
   laptopa. To już istnieje dla notatek (`potwierdz`, eksport) — rozszerzamy, nie dublujemy.
2. **Dwa różne rodzaje pozycji w jednym liczniku.** Notatki mają plik i lektora; nowe pozycje są fragmentami plików
   (element JSON, sekcja Markdown, linia). Reguła Karola „ten sam tekst do czytania i do odsłuchu” wymusza, żeby
   lektor czytał dokładnie ten krótki tekst, który widać — a lektor (`lektor_item`) czyta tylko PLIKI pod `ROOT`.
   Rozwiązanie: tekst pozycji zapisywany jako mały plik `.md` w `katalog_lektora/_pozycje/` (jedyny zapisywalny katalog
   z korzenia) — patrz 3.6.
3. **Tożsamość pozycji między odczytami.** Notatka ma ścieżkę; pozycja tylko treść. Identyfikator musi przeżyć odświeżenie
   klonu (15 min) i zmianę liczb pomocniczych (priorytet skill-scouta jest przeliczany).
4. **Brak zwrotnej informacji o naniesieniu.** Źródło 1 nie ma statusu — po decyzji pozycja będzie nadal w pliku, dopóki
   agent laptopa jej nie zmieni. Dlatego rekord decyzji u AnberFiles ma pierwszeństwo, dopóki odcisk treści się nie zmieni
   (ta sama zasada co dla notatek: `odcisk_notatki`).
5. **Cisza.** Zmiana układu vaulta (trwa „plan porządku vaulta”) cicho opróżniłaby listę. Każde źródło ma trzy rozróżnialne
   stany (3.5) i wpis w rejestrze zdarzeń przy zmianie stanu.

## 3. Zmiany

### 3.1 Moduł parserów: nowy `app/pozycje.py` (czyste funkcje, standardowa biblioteka, Python 3.10)

Parsery nie zapisują niczego i nie importują serwera. Wspólne: limit rozmiaru pliku (`LIMIT_ZRODLA_B = 2 MiB`, dla
`NOW-pending.md` czytane tylko do końca swojej sekcji), kodowanie `utf-8` z `errors='replace'`, wynik
`(pozycje, stan_zrodla)`.

Pozycja (słownik): `rodzaj`, `id` (stabilny), `tytul` (≤ 140 znaków), `tekst` (≤ 600 znaków, ucięty na granicy słowa z „…”),
`zrodlo_plik` (ścieżka względna), `kotwica` (co agent laptopa ma znaleźć w pliku), `warianty` (lista `{kod, napis}`),
`odcisk`, `kolejnosc`, `znaczniki` (np. `{"typ": "update", "priorytet": 72}`).

| Źródło | Wejście | Wyodrębnienie | `id` | `odcisk` liczony z | Warianty przycisków |
|---|---|---|---|---|---|
| skill-scout | `_proposed.json` | `proposed[]`; element nie-obiektem albo bez `slug` → pominięty i policzony w `stan.pominiete`; element z polem `status` lub `decyzja` ∈ {zatwierdzone, odrzucone} → już rozstrzygnięty, nie pozycja | `skill-scout:<slug>` | `title + type + update_target + what` (NIE `priority`, `freq`, `saved_per_week_min`, `evidence` — liczby są przeliczane przy każdym przebiegu) | Zatwierdź · Odrzuć |
| reflect | `_reflect-pending.md` | każda sekcja `## ` do następnego `## ` albo `### `/końca pliku; sekcja z `- [x]` pominięta; sekcja bez żadnej linii checkboxa → pozycja z ostrzeżeniem „brak checkboxa” (kotwica = nagłówek) | `reflect:` + pierwsze 12 znaków SHA-256 nagłówka | treść sekcji bez linii checkboxa | Zatwierdź · Odrzuć |
| NOW.md | sekcja `## Otwarte decyzje` | każda linia `- [ ]` osobno; `- [x]` pominięta; pozycja ze złączeniem `**…** · **…**` pozostaje jedną pozycją (nie zgadujemy podziału); meta-wpis „Kolejki propozycji” pomijany | `now:` + 12 znaków SHA-256 tytułu (tekst pogrubienia na początku linii, albo pierwsze 60 znaków) | cała linia | wzorzec `A … · B …` → przyciski A, B (kod = litera); wzorzec „X czy Y” → dwa przyciski; inaczej Zatwierdź · Odrzuć + pole uwagi |
| NOW-pending | `## Decyzje do briefingu` | `### ` osobno do następnego `### `/`## `; domyślnie pomijane wpisy `### Wykonawca: …` (87 z 92); powtórzony tytuł → ten sam wpis z licznikiem | `now-pending:` + 12 znaków SHA-256 pełnej linii nagłówka | nagłówek + treść | Przyjąłem do wiadomości (jedyny przycisk, wybór `przyjete`) |
| raporty | patrz 1.4 | jedna pozycja na automat: `raport:skill-scout:<data>`, `raport:pamiec:<data i godzina z nagłówka NOW.md>`, `raport:reflect:<data z „Wygenerowane”>` | z daty/godziny w kluczu | — (klucz zawiera datę) | brak decyzji; znacznik „przeczytane” przez istniejący `Magazyn.ustaw_odsluch` |

Zasady wspólne:
- **Tekst z vaulta to dane.** Parsery nie wykonują i nie interpretują niczego; render escapuje (`_esc`) — test XSS na tytule
  `<script>`.
- **Determinizm:** kolejność = kolejność w pliku; wyjątek skill-scout: `priority` malejąco, remis wg kolejności w pliku
  (stabilne sortowanie). Brak losowości, brak zależności od `os.listdir`.
- **Tolerancja:** brak pola → wartość pusta (np. brak `what` → tekst z `title`), nie wyjątek. Błąd składni całego pliku → stan
  „nieczytelne” z powodem; pojedynczy zły element → pominięty i policzony.
- Podział złączonych pozycji NOW.md oraz sposób nanoszenia decyzji w źródle 1 (3.8) — założenia do potwierdzenia.

> PRZEGLĄD: poprawki tabeli 3.1 (obowiązują 98c):
> - **skill-scout (ustalenie 1):** element `type: new`, którego `slug` ma w klonie plik `.claude/skills/<slug>/SKILL.md`,
>   nie jest pozycją; stan źródła dostaje licznik `juz_skillem` (pasek: „18 propozycji jest już skillami”). Odczyt
>   `.claude/skills` to `Path.is_file()` — bez zapisu, test 12 bez zmian. Nowy test 31.
> - **NOW-pending (ustalenie 2):** czytane są WSZYSTKIE sekcje `## Decyzje do briefingu` (każda do następnego `## ` albo
>   końca pliku); wpis o tym samym `id` z dwóch sekcji = jedna pozycja z licznikiem. Plik czytany w całości w limicie
>   2 MiB (zasada „tylko do końca swojej sekcji” odpada — sekcji jest więcej niż jedna). Test 10 z próbką o dwóch sekcjach
>   i powtórzonym wpisie.
> - **Wzorzec „X czy Y” (ustalenie 3):** reguła „cały tekst przed i po »czy«” sprawdzona na 15 prawdziwych liniach daje
>   4 dopasowania, z których 3 mają złe napisy przycisków („Kalibracje kamer: ekspozycja ręczna −6” / „AUTO** (plan 78)”,
>   „wyzwalacz po pushu + timer 15 min —” / „zostają oba”, „**Epic: Trader” / „Non-Trader** oraz VAT”). Obowiązuje reguła
>   tokenowa: wariant = jedno słowo bezpośrednio przed i jedno po ` czy ` (gwiazdki `*` zdjęte), lewe słowo musi zawierać
>   literę albo cyfrę; inaczej przyciski domyślne. Daje USB-A/USB-C, −6/AUTO, Trader/Non-Trader, a „— czy zostają oba” →
>   Zatwierdź/Odrzuć. Wzorzec `A … · B …` (koniec wariantu na ` · `, ` — ` albo końcu linii) zmierzony: 3 z 3 poprawne.
>   Test 9 zawiera wszystkie cztery przypadki „czy” (próbki przepisane w teście, nie czytane z vaulta).
> - **Klucze raportów (ustalenie 9):** reflect — klucz z OSTATNIEJ daty w linii `Wygenerowane: …` (dziś „2026-10-10”), bo
>   pierwsza data „2026-09-28” nie zmienia się przy uzupełnieniach i nowy przebieg nie wróciłby jako nieprzeczytany.
>   skill-scout — w katalogu są pary tej samej daty (`2026-09-03.json` i `2026-09-03_jarvis.json`); remis rozstrzyga plik
>   bez przyrostka (laptop, jedyny z generatorem raportu), klucz `raport:skill-scout:<nazwa pliku bez .json>`. Test 11 musi
>   mieć taki remis, inaczej mutant „leksykograficznie ostatni” przechodzi (bez remisu porządek leksykograficzny = porządek dat).

### 3.2 Stan i migracja (w `Magazyn`, `WERSJA_STANU` 1 → 2)

- Nowy klucz stanu `wybory` (lista). **Wspólny licznik `nastepne_id` i wspólny `potwierdzono_do`** z `decyzje`
  — jedno potwierdzenie przenosi oba rodzaje, eksport i `ostatnie_id` pozostają spójne.
- Rekord wyboru:

| Pole | Znaczenie | Przykład |
|---|---|---|
| `id` | numer ze wspólnego licznika | `207` |
| `czas` | ISO 8601 z przesunięciem, czas systemowy Jarvisa (Europe/Warsaw) | `2026-10-10T21:04:12+02:00` |
| `zrodlo` | `skill-scout` / `reflect` / `now` / `now-pending` | `skill-scout` |
| `id_pozycji` | identyfikator z 3.1 | `skill-scout:audyt-skilli` |
| `wybor` | kod: `zatwierdz` / `odrzuc` / `przyjete` / `A` / `B` / `C` | `zatwierdz` |
| `uwaga` | tekst, ≤ 4000 znaków (limit istniejący) | `` |
| `odcisk` | odcisk treści pozycji z chwili wyboru (liczony po stronie serwera) | `9f2c…` |
| `tytul`, `tekst` | migawka tytułu i krótkiego tekstu (do zakładki „Rozstrzygnięte”, gdy źródło już pozycji nie ma) | |
| `kotwica` | `{plik, naglowek lub slug lub linia}` — gdzie agent ma nanieść | `{"plik": "Zasoby/raporty/skill-scout/_proposed.json", "slug": "audyt-skilli"}` |
| `przeniesione` | pole wyliczane w odpowiedzi JSON: `id <= potwierdzono_do` (w pliku stanu nie przechowywane) | `false` |

- Migracja w górę: plik w wersji 1 ładuje się bez strat (klucze brakujące uzupełnia istniejące `setdefault`), `wersja`
  podnosi się do 2 przy pierwszym zapisie, `nastepne_id` i `potwierdzono_do` bez zmian. Plik z `wersja` > 2 traktowany jak
  nieczytelny (odłożony `.uszkodzony-…`, komunikat w rejestrze) — nie nadpisujemy nieznanego formatu po cichu.

> PRZEGLĄD: (ustalenie 10) Odłożenie pliku nowszej wersji oznacza, że po cofnięciu kodu z przyszłej wersji 3 do 2 lista
> startuje z pustym stanem i pierwszy zapis tworzy nowy plik — decyzje znikają z widoku. To łamie wsteczną zgodność.
> Obowiązuje: `wersja` > `WERSJA_STANU` → stan wczytany TYLKO DO ODCZYTU, każdy zapis kończy się `BladStanu` (500
> „stan z nowszej wersji N — zapis wstrzymany”) i wpisem w rejestrze; plik nietknięty. Test 13 sprawdza to zamiast odłożenia.
- Cofnięcie kodu do wersji 1: stary kod zachowuje nieznany klucz `wybory` przy zapisie (cały słownik `_stan` jest zapisywany),
  więc rollback nie kasuje wyborów (test na tym zachowaniu, 5.1 nr 14).

> PRZEGLĄD: (ustalenie 11) Po zmianie modułu „starej ścieżki zapisu” w kodzie już nie ma, a CI robi `actions/checkout@v4`
> z domyślną głębokością 1, więc `git show a567a59:app/przesluchania.py` w teście nie zadziała. Test 14 ładuje zamrożoną
> kopię modułu z `a567a59` zapisaną jako `tests/dane/przesluchania_v1.py` (importlib, osobna nazwa modułu), buduje w teście
> stan v2 z `wybory`, woła `ustaw_ui` starej klasy i sprawdza, że `wybory` są w pliku. Plik dochodzi do inwentarza (4).
- Starych rekordów `decyzje` (notatki) nie ruszamy; ich eksport i `eksport_markdown` bez zmian co do bajtu dla stanu bez
  wyborów (test regresji).

### 3.3 Skan, pamięć podręczna, wątek

`_prz_pozycje(odswiez)` w `app/server.py` obok `_prz_notatki`: czytanie czterech źródeł w `run_in_executor`
(nie blokuje pętli zdarzeń), pamięć podręczna 30 s (`PRZ_SKAN_TTL_S`) wspólna z przyciskiem 🔄 (`odswiez=1`).
Wynik: `(lista pozycji, słownik stanów źródeł)`.

### 3.4 Wiersze: pozycja + rekord → zakładka

Reguła (zastępuje „zniknięcie” z promptu, patrz 3.7):

| Pozycja w źródle | Rekord wyboru | Zakładka | Znacznik |
|---|---|---|---|
| tak | brak | Do decyzji | — |
| tak | jest, odcisk zgodny | Rozstrzygnięte | „czeka na przeniesienie” (id > granica) albo „przeniesione, czeka na odświeżenie vaulta” |
| tak | jest, odcisk INNY | Do decyzji | „treść zmieniona po decyzji z <data>” |
| nie | jest | Rozstrzygnięte (z migawki) | „usunięte ze źródła — naniesione” |
| nie | brak | nie pokazywana | — (pozycja po prostu zniknęła) |

Pozycje trafiają do istniejących zakładek: „Do decyzji” (sekcje: Raporty do przeczytania → grupy wg źródła → notatki)
i „Rozstrzygnięte”. Raport przeczytany (`odsluchane`) znika z sekcji raportów i nie liczy się do licznika.

> PRZEGLĄD: (ustalenie 17) Raporty NIGDY nie wchodzą do `do_decyzji` (także nieprzeczytane) — liczy je osobne pole
> `raporty_nieprzeczytane`. Wpis rejestru przy zapisie wyboru: id rekordu, źródło, `id_pozycji`, wybór — bez treści uwagi.

### 3.5 Trzy stany źródła, nigdy cisza

Stan źródła: `ok` (z liczbą pozycji, także 0 — „źródło poprawne, 0 pozycji do decyzji”), `brak` (plik nie istnieje:
„źródło <nazwa>: brak pliku <ścieżka>”), `nieczytelne` (zły JSON, błąd odczytu, przekroczony limit, brak sekcji:
„źródło <nazwa>: nieczytelne — <powód>”). Raport reflect bez pliku to stan `brak` z dopiskiem „kolejka pusta albo plik
przeniesiony — nie da się ustalić”. Czerwony pasek na górze listy dla `brak` i `nieczytelne`; wpis `_evlog('przesluchania', …,
level='warn')` **przy zmianie stanu** (nie przy każdym żądaniu), pamiętany w pamięci procesu. Licznik JSON zwraca
`zrodla_problemy` (liczba).

### 3.6 Interfejs i lektor

Makieta tekstowa (jedna pozycja; ten sam tekst idzie do lektora):

```
[skill-scout · nowy · priorytet 72]
Audyt i synchronizacja skilli między lokalizacjami
Powtarzalny proces: porównaj wersje skilli w SSH, na konsoli Anbernic i w bazie Jarvis, wykryj nowsze, zaproponuj przeniesienie…
(źródło: Zasoby/raporty/skill-scout/_proposed.json)         [🔊 generuj]
[ Zatwierdź ]  [ Odrzuć ]      uwaga: [_______________________]
```

- Przyciski min. 44 px wysokości (reguła CSS `.dec button{min-height:44px}`), pole uwagi pełnej szerokości; wariant
  przycisków A/B z napisami z tekstu pozycji.
- Zwijanie grup i filtr „pokaż źródło” w stanie serwera: `ui['zwiniete']`, `ui['zrodlo']` (rozszerzenie istniejącego
  `POST /?przesluchania=ui`, z walidacją listy dozwolonych wartości) — przeżywa F5.
- Lektor: dla pozycji serwer zapisuje `katalog_lektora/_pozycje/<rodzaj>/<id-skrótu>.md` z DOKŁADNIE wyświetlanym
  tytułem i tekstem (nazwa pliku z SHA, nigdy z tekstu pozycji — brak przejścia po katalogach) i przycisk „generuj”
  wywołuje istniejące `POST /<ścieżka>.md?lektor=1`. Zmiana odcisku nadpisuje plik i istniejący mechanizm odświeżania
  nagrań (metryka SHA) oznacza nagranie jako nieaktualne. Zapis tylko do `katalog_lektora`, nigdy do `przesluchania_zakres`
  (test strukturalny 5.1 nr 12). Nagrania znikają po `wiek_nagran_dni = 3` — generowanie na żądanie.

> PRZEGLĄD: (ustalenie 6) Sprawdzone w kodzie:
> - `nagrania.sprzataj` usuwa tylko pliki, dla których `jest_nagraniem_lektora` jest prawdą — pliki `.md` pozycji NIE są
>   sprzątane (wiersz ryzyk w 7 twierdził inaczej). 98c dodaje własne sprzątanie: przy skanie usuwane są pliki
>   `_pozycje/**/*.md`, których identyfikatora nie ma w bieżącym skanie i których czas modyfikacji jest starszy niż
>   `wiek_nagran_dni`; test.
> - Plik pozycji zapisywany TYLKO, gdy jego treść różni się od zapisanej (porównanie bajtów). Przepisywanie co skan
>   (30 s) zmienia czas modyfikacji, a `odswiezanie.przeglad` dla nagrania bez metryki uznaje dokument nowszy od nagrania
>   za nieaktualny; do tego zbędne zapisy na kartę SD. Test: dwa skany bez zmiany treści → czas modyfikacji bez zmian.
> - `_lektor_dir_for` liczy katalog wyniku względem `ROOT`, więc nagranie pliku `korzen/lektor/_pozycje/<r>/<h>.md` trafi
>   do `korzen/lektor/lektor/_pozycje/<r>/<h>_lektor.mp3`. Działa (zapis w katalogu lektora), ale test 28 sprawdza tę
>   faktyczną ścieżkę, a nie wyobrażoną.
>
> PRZEGLĄD: (ustalenie 7) Brak drogi do czytania z lektorem: widok `?sluchaj=1` przyjmuje tylko pliki pod
> `przesluchania_zakres` (`_prz_notatka`), a plik pozycji leży w katalogu lektora. Wiersz pozycji pokazuje więc sam
> odtwarzacz `<audio>` z nagraniem znalezionym przez istniejące `_lektor_audio_for` i stan nagrania jak przy notatkach
> („gotowe / w kolejce / brak — 🔊 generuj”); wiersz raportu ma dodatkowo odnośnik do pliku źródłowego w podglądzie
> plików. Test 28 obejmuje odtwarzacz po wygenerowaniu atrapą lektora (`ATRAPA_TTS` z `conftest.py`).

### 3.7 Znikanie i powroty (kryterium 6)

Pozycja z wyborem znika z „Do decyzji” w chwili zapisu (odcisk zgodny). Gdy laptop naniesie zmianę i vault się odświeży:
(a) element/sekcja/linia zniknęła → wiersz „nie/jest” z tabeli 3.4, nie wraca; (b) została w źródle z tą samą treścią →
dalej Rozstrzygnięte, nie wraca i nie dubluje się (klucz = `id_pozycji`); (c) agent zmienił treść (np. dopisał
`status`) → dla skill-scouta pole `status`/`decyzja` wyklucza pozycję z puli już u źródła (3.1), dla pozostałych zmiana
odcisku oznacza „treść zmieniona po decyzji” — pozycja wraca do „Do decyzji” z tym znacznikiem, żeby człowiek zauważył
rozjazd. Dlatego `pobierz_decyzje.py` podaje agentowi dokładną zmianę minimalną (3.9), a nie dowolną edycję.

### 3.8 API (wszystko za logowaniem; POST przez `straz_zrodla`)

| Adres | Metoda | Działanie |
|---|---|---|
| `/?przesluchania=wybor` | POST | `{id_pozycji, wybor, uwaga}` — serwer znajduje pozycję w bieżącym skanie (klient nie podaje tekstu ani odcisku), sprawdza, czy `wybor` należy do jej wariantów, liczy odcisk, zapisuje rekord. Brak pozycji w źródle → 404 z komunikatem; zły wybór → 400; błąd zapisu → 500 `BŁĄD zapisu stanu` (jak dziś). **Idempotencja:** ostatni rekord tej pozycji z tym samym `wybor`, `uwaga` i odciskiem → zwraca ten rekord (`powtorzenie: true`), nie tworzy nowego `id`; inny wybór tej samej pozycji = nowy rekord (ostatni obowiązuje). Sprawdzenie i zapis pod jedną blokadą `Magazyn` (dwa jednoczesne kliknięcia dają jeden rekord) |
| `/?przesluchania=eksport` | GET | rozszerzony: stare pola bez zmian + `wybory` (nieprzeniesione, z `przeniesione`, `kotwica`) + `ostatnie_id` jako max obu list. **Wybrano rozszerzenie istniejącego adresu**, nie nowy `/?decyzje.json` — jeden adres, jedna ochrona, jedno potwierdzenie |
| `/?przesluchania=potwierdz` | POST | bez zmian (idempotentne) |
| `/?przesluchania=licznik` | GET | `do_decyzji` = notatki + pozycje (kafelek `jarvis-start` — inne repozytorium, nie zmieniamy — pokaże sumę); dodatkowe pola: `do_decyzji_notatki`, `do_decyzji_pozycje`, `raporty_nieprzeczytane`, `zrodla_problemy`, `nieprzeniesione` (łącznie) |
| `/?przesluchania=ui` | POST | rozszerzony o `zwiniete`, `zrodlo` |

Eksport Markdown (`przesluchania-eksport.md`) dostaje sekcję „## Wybory w pozycjach” (źródło, pozycja, wybór, uwaga, miejsce
naniesienia).

> PRZEGLĄD: (ustalenie 8) Brakująca trasa: „przeczytane” dla raportu. `Magazyn.ustaw_odsluch` woła dziś tylko
> `notatka_post` (adres pliku notatki). Dochodzi `POST /?przesluchania=przeczytane {id_pozycji}` — przyjmuje tylko
> identyfikatory `raport:` obecne w bieżącym skanie (inaczej 404), zapisuje `odsluch[id_pozycji].odsluchane`, nie tworzy
> rekordu wyboru i nie trafia do eksportu ani do `pobierz_decyzje.py`. Test w porcji B (nowy nr 32).
>
> PRZEGLĄD: (ustalenie 12) Linia procedury w `eksport_markdown` liczy dziś `{"do": …}` z `nowe[-1]["id"]` (tylko decyzje
> notatek). Po zmianie musi to być maksimum `id` z obu list — inaczej agent potwierdzi za mało albo pominie wybory.

### 3.9 Skrypt `narzedzia/pobierz_decyzje.py` (laptop)

- Wywołanie: `python narzedzia/pobierz_decyzje.py [--adres URL] [--potwierdz] [--json] [--limit-s N]`.
  Adres domyślnie `http://100.67.174.47:8790` (nadpisywalny zmienną `ANBERFILES_ADRES`); `--limit-s` domyślnie 20,
  stosowany do KAŻDEGO żądania (`urllib` z `timeout`).
- Uwierzytelnienie: hasło z zmiennej `ANBERFILES_HASLO` albo `getpass` (pytanie na terminalu); nigdy z argumentu,
  nigdy zapisywane, nigdy w wydruku. Logowanie `POST /__anberfiles/zaloguj` (`haslo=…`, bez `Origin`) → ciasteczko
  w pamięci procesu (`http.cookiejar`), potem `GET /?przesluchania=eksport`.
- Wydruk „co naniesić gdzie”, po jednym bloku na wybór, z kotwicą:

| Źródło | Zatwierdź | Odrzuć | Inne |
|---|---|---|---|
| skill-scout | w elemencie `slug` w `_proposed.json` dopisz `"status": "zatwierdzone"` i `"decyzja_czas"`; utworzenie skilla to osobna sprawa agenta | dopisz `"status": "odrzucone"` (element zostaje, żeby skill-scout nie zaproponował go ponownie) | — |
| reflect | w sekcji o nagłówku `…` zmień `- [ ] ✅ Zatwierdź tę zmianę` na `- [x]` (potem `/reflect apply`) | usuń sekcję z pliku albo przenieś pod `### Odrzucone` (brak znacznika odrzucenia w formacie) | — |
| NOW.md | linię `- [ ] **…**` zmień na `- [x] … — decyzja: <wybór> (<data>)` | jw. z `decyzja: odrzucone` | wariant A/B: nazwa wariantu w treści |
| NOW-pending | usuń wpis `### …` do następnego `###` | — | `przyjete`: usuń wpis |

- Kody wyjścia: 0 — dane pobrane (także „brak nowych decyzji”, wypisane wprost); 2 — zły argument; 3 — odmowa logowania
  (401) albo brak hasła; 4 — błąd sieci albo przekroczony limit czasu; 5 — odpowiedź niezgodna ze schematem; 6 — `--potwierdz`
  odrzucone. `--potwierdz` wysyła `POST …=potwierdz {"do": ostatnie_id}` z nagłówkiem `X-AnberFiles: 1` dopiero po wypisaniu
  zmian i tylko na wyraźne polecenie; skrypt nie zmienia vaulta.
- Skrypt dostaje się do `ruff` i `py_compile` w CI. Zgodny z Python 3.10, bez zależności.

> PRZEGLĄD: odpowiedź 429 („Za dużo prób”, `LIMIT_PROB = 10` na minutę z adresu) → kod wyjścia 3 z osobnym napisem.
> Logowanie sprawdzone: udane → 303 z ciasteczkiem `anberfiles_sesja` bez flagi `Secure` po HTTP (więc `http.cookiejar`
> je odeśle), złe hasło → 401 po 1 s, brak sesji przy `GET` bez `Accept: text/html` → 401. Token jest bezstanowy (HMAC),
> więc każde uruchomienie skryptu nie zostawia śladu w katalogu danych.

### 3.10 Dokumentacja

README: sekcja „Lista Do przesłuchania” (nowe źródła, trzy stany, wybory, adresy, licznik, `pobierz_decyzje.py`); procedura
wdrożenia odsyła do `docs/odtworzenie-po-awarii.md` (kroki o `git pull` jako `anberfiles`, `chown -R root:root`).

### 3.11 Porcjowanie 98c (push i zielone CI po każdej)

| Porcja | Zakres | Testy | Gdy bieg jest za długi, zostaje jako lista |
|---|---|---|---|
| A | `app/pozycje.py` (cztery źródła + raporty), `Magazyn` v2 (`wybory`, migracja, idempotencja), ci.yml (lint) | nr 1–19, 27 | kolejność wewnątrz A: (1) skill-scout i reflect, (2) NOW.md i NOW-pending, (3) raporty, (4) migracja i magazyn; każdy krok osobny commit |
| B | trasa `wybor`, interfejs (sekcje, przyciski ≥ 44 px, paski stanów), licznik, eksport JSON/Markdown, stan UI, tekst dla lektora, `narzedzia/pobierz_decyzje.py`, README | nr 20–26, 28–30 | kolejność: (1) trasa i licznik, (2) interfejs, (3) `pobierz_decyzje.py`, (4) lektor, (5) README |
| C | wdrożenie na Jarvisie (bramka lektora i Pulsa, `git pull` jako `anberfiles`, `chown`/`install`, restart), odbiór z zrzutem ekranu, sumy kontrolne vaulta | kryteria 6.2–6.8 | wdrożenie bez odbioru nie jest ukończone — porcja C zostaje w całości, jeśli bramka zamknięta |

> PRZEGLĄD: (ustalenie 14) Testy 16 i 17 mówią o kodach HTTP i równoległych żądaniach, czyli o trasie z porcji B.
> W porcji A: `Magazyn.dodaj_wybor` (idempotencja, dwa wątki → jeden rekord, zły wybór → `ValueError`) i funkcja zakładki
> z tabeli 3.4 w `app/pozycje.py` (test 18 na funkcji). W porcji B: te same przypadki przez trasę (400, 404, `powtorzenie`).
> Porcja B jest największa; gdy bieg się nie mieści, punkt podziału to B1 = (1)+(2), B2 = (3)+(4)+(5).
>
> PRZEGLĄD: (ustalenie 4 i 5) Porcja C w tej postaci jest niewykonalna bez Karola i niezgodna ze stanem serwera:
> - Każdy widok listy, eksport i licznik są za logowaniem; hasła Karola Wykonawca nie zna i nie wolno mu czytać
>   `dane/auth`. Katalog `/srv/anberfiles/dane` ma prawa `drwxr-x--- anberfiles`, więc bramka „kolejka lektora pusta”
>   (`dane/lektor_queue.json` albo `/?lektorqj` za logowaniem) też wymaga albo sesji, albo `sudo -u anberfiles`.
> - Odbiór funkcjonalny (kliknięcia, zrzuty, restart, F5) robi 98c na INSTANCJI TESTOWEJ: klon repozytorium w
>   `/tmp/jarwis-wykonawca/`, własny plik ustawień (port spoza 8790, katalog danych i lektora w `/tmp`, własne hasło
>   ustawione tokenem startowym jak w CI), `przesluchania_zakres = /srv/anberfiles/vault` — konto `claude` czyta klon,
>   ale nie może w nim pisać (pliki `anberfiles`, prawa 0644/0755), więc zapis jest wykluczony uprawnieniami.
>   Dowód restartu = zatrzymanie i ponowne uruchomienie tej instancji.
> - Na produkcji 98c sprawdza bez sesji: wdrożony SHA, `systemctl is-active anberfiles`, 401 dla `/?przesluchania=eksport`
>   bez ciasteczka. Zrzut listy produkcyjnej i kafelek `jarvis-start` (6.8) potwierdza Karol — w meldunku jako „czego nie zrobiłem”.
> - Restart i bramka lektora tylko wtedy, gdy prompt 98c wprost pozwala na `sudo` (ten prompt 98b zakazuje); inaczej C kończy
>   się `BŁĄD: brak uprawnień do wdrożenia` bez restartu, a instancja testowa i tak przechodzi cały odbiór.
> - Procedura: `docs/odtworzenie-po-awarii.md` każe robić `git pull` jako `anberfiles`, ale `/srv/anberfiles/kod` należy do
>   roota, a reflog pokazuje faktyczny ostatni krok `checkout: moving from main to a567a59` (odłączony HEAD, pliki roota,
>   gałąź `main` stoi na `421a318`). 98c zapisuje SHA sprzed wdrożenia, wdraża tak samo (`git fetch` + `git checkout
>   --detach <sha>` jako root, potem `chown -R root:root` i `install` wg kroku 12 dokumentu) i zgłasza rozjazd dokumentu
>   z praktyką w meldunku (poprawa dokumentu poza zakresem 98).

## 4. Inwentarz plików

| Plik | Zmiana |
|---|---|
| `app/pozycje.py` | nowy: parsery, stany źródeł, identyfikatory, odciski, warianty |
| `app/przesluchania.py` | `WERSJA_STANU = 2`, `wybory`, `dodaj_wybor` (idempotentny), eksport JSON/Markdown |
| `app/server.py` | `_prz_pozycje`, trasa `wybor`, licznik, render sekcji i przycisków, pasek stanów źródeł, zapis tekstu dla lektora |
| `narzedzia/pobierz_decyzje.py` | nowy |
| `tests/test_pozycje_parsery.py` | nowy (porcja A) |
| `tests/test_pozycje_magazyn.py` | nowy (porcja A) |
| `tests/test_pozycje_serwer.py` | nowy (porcja B) |
| `tests/test_pobierz_decyzje.py` | nowy (porcja B) |
| `tests/dane/przesluchania_v1.py` | PRZEGLĄD: nowy (porcja A) — zamrożona kopia `app/przesluchania.py` z `a567a59` dla testu 14 |
| `tests/test_przesluchania.py` | dopiski: licznik, regresja eksportu starego stanu |
| `.github/workflows/ci.yml` | `app/pozycje.py` i `narzedzia/pobierz_decyzje.py` do `py_compile` i do `ruff` |
| `README.md` | sekcja listy, `pobierz_decyzje.py` |
| `docs/plany/2026-10-10-98-decyzje-w-liscie-odsluchu-plan.md` | ten plik (98a) |

Bez zmian: `requirements*.txt`, `konfiguracja/*` (ścieżki źródeł są stałymi względem `przesluchania_zakres`; bez nowych
kluczy), `tools/*`, inne repozytoria.

## 5. Testy RED

Zasada: wszystkie testy na własnych drzewach w `tmp_path`; próbki źródeł budowane w testach według kształtu z 1.4 (nie
czytane z vaulta Karola); najpierw test czerwony na HEAD, potem zielony po zmianie. Mutanty uruchamiane skryptem na
KOPII modułu w `/tmp/jarwis-wykonawca/` (nie `git checkout`), z kontrolą `count(stare) == 1` i różnicą po przywróceniu.

### 5.1 Lista testów i mutantów

| # | Test | Mutant, który ma go zapalić |
|---|---|---|
| 1 | skill-scout: poprawny plik → 87 pozycji w próbce 3-elementowej, kolejność wg `priority` | usunięcie sortowania po `priority` |
| 2 | skill-scout: element bez pola `what`, element bez `slug`, element-nie-obiekt, `evidence` nie-lista | `e['what']` zamiast `e.get('what')` |
| 3 | skill-scout: pusty `proposed` → stan `ok`, 0 pozycji (różne od `brak`) | zwracanie stanu `brak` przy pustej liście |
| 4 | skill-scout: uszkodzony JSON → stan `nieczytelne` z powodem, brak wyjątku; brak pliku → `brak` | połknięcie wyjątku i zwrot pustej listy bez stanu |
| 5 | skill-scout: element ze `status: zatwierdzone` nie jest pozycją | usunięcie warunku statusu |
| 6 | odcisk skill-scouta nie zmienia się przy zmianie `priority`/`freq`, zmienia przy zmianie `what` | dołączenie `priority` do odcisku |
| 7 | reflect: próbka 3 sekcji (jedna `- [x]`) → 2 pozycje; `### Odrzucone` kończy ostatnią sekcję i nie jest pozycją | wyrównanie do `startswith('##')` (łapie `###`) |
| 8 | reflect: sekcja bez checkboxa → pozycja z ostrzeżeniem; dwa takie same nagłówki → rozróżnione licznikiem | pominięcie sekcji bez checkboxa |
| 9 | NOW.md: `- [ ]` pozycje, `- [x]` pominięte, meta-wpis „Kolejki propozycji” pominięty; wzorzec `A … · B …` → dwa warianty; „X czy Y” → dwa; zwykły → Zatwierdź/Odrzuć | zamiana kolejności wariantów / usunięcie wzorca „czy” |
| 10 | NOW-pending: tylko sekcja `## Decyzje do briefingu` (próbka z drugą sekcją `## Otwarte decyzje` w kopii NOW wyżej nie jest czytana); wpisy `### Wykonawca:` pominięte | rozszerzenie zakresu na cały plik |
| 11 | raporty: najnowszy `data/*.json` wybierany po dacie z nazwy (przyrostek `_jarvis` ignorowany przy porównaniu), brak katalogu → `brak`; reflect bez pliku → komunikat „nie da się ustalić” | wybór leksykograficznie ostatniego pliku z przyrostkiem |
| 12 | vault tylko do odczytu: AST `app/pozycje.py` bez wywołań zapisu (`open` w trybie ≠ `r`, `write_text`, `write_bytes`, `unlink`, `rename`, `replace`, `mkdir`, `rmtree`, `touch`); zapis tekstu dla lektora wskazuje ścieżkę POZA `przesluchania_zakres`; sumy kontrolne drzewa testowego przed/po całym scenariuszu (skan, wybór, eksport, potwierdzenie) | dodanie `write_text` do `pozycje.py`; zapis tekstu lektora do katalogu zakresu |
| 13 | migracja v1 → v2: plik v1 (rekordy w `decyzje`) ładuje się, `nastepne_id` kontynuowany, po pierwszym zapisie `wersja: 2`; plik `wersja: 3` odkładany jako uszkodzony | pozostawienie `WERSJA_STANU = 1` |
| 14 | rollback: stan z `wybory` przeżywa zapis starej ścieżki (nieznany klucz zachowany) | filtrowanie klucza przy zapisie |
| 15 | rekord wyboru: wszystkie pola schematu, `czas` ISO z przesunięciem, `odcisk` liczony po stronie serwera (klient nie wpływa) | przyjęcie odcisku z żądania |
| 16 | idempotencja: drugie identyczne kliknięcie → ten sam `id`, `powtorzenie: true`; inny wybór → nowy rekord; dwa równoległe żądania → jeden rekord | usunięcie sprawdzenia ostatniego rekordu |
| 17 | wybór spoza wariantów pozycji → 400; pozycja nieobecna w źródle → 404 | brak walidacji wariantu |
| 18 | znikanie: tabela 3.4 w 5 wierszach (wybór → Rozstrzygnięte; źródło odświeżone bez pozycji → z migawki; ta sama treść → nie wraca; zmieniona treść → wraca ze znacznikiem) | porównanie odcisku pominięte (zawsze „zgodny") |
| 19 | wybory nie dublują się po odświeżeniu źródła (ten sam `id_pozycji` po przeczytaniu pliku drugi raz) | `id` z numeru wiersza zamiast treści |
| 20 | XSS: tytuł `<script>alert(1)</script>` w każdym z czterech źródeł → w HTML tylko `&lt;script&gt;`; uwaga z `<img onerror>` w eksporcie JSON jako dane | usunięcie `_esc` przy tytule pozycji |
| 21 | logowanie: `GET eksport` bez sesji → 401 (w trybie `formularz`), `POST wybor` bez sesji → 401, bez nagłówka `X-AnberFiles` → 403 | pominięcie sprawdzenia nagłówka w nowej trasie |
| 22 | trzy stany na liście: brak pliku → czerwony pasek „źródło X: brak …”, uszkodzony → „nieczytelne — powód”, poprawny pusty → „0 pozycji”; wpis w rejestrze zdarzeń przy zmianie stanu, nie przy każdym żądaniu | zamiana komunikatu `brak` na pustą listę |
| 23 | licznik: `do_decyzji` = notatki + pozycje, pola dodatkowe, `zrodla_problemy`; CORS jak dotąd | `do_decyzji` tylko notatki |
| 24 | eksport JSON i Markdown zawierają `wybory`; stary stan bez wyborów daje eksport identyczny bajt w bajt | pominięcie `wybory` w Markdown |
| 25 | stan UI przeżywa F5: `zwiniete`/`zrodlo` zapisane przez `ui`, odczytane przy nowym żądaniu i po ponownym utworzeniu `Magazyn` z pliku; wartość spoza listy → 400 | zapis bez walidacji |
| 26 | determinizm: dwa kolejne odczyty tego samego drzewa → identyczna lista i kolejność; skan nie blokuje pętli (źródło odczytywane przez `run_in_executor` — test z atrapą wolnego odczytu i równoległym żądaniem) | `os.listdir` bez sortowania / odczyt synchroniczny w handlerze |
| 27 | limity: plik źródła > 2 MiB → `nieczytelne — za duży`; tytuł i tekst obcinane do limitów z „…” na granicy słowa | usunięcie limitu rozmiaru |
| 28 | interfejs: HTML pozycji zawiera `min-height:44px` dla przycisków decyzji; ten sam tekst w widoku i w pliku dla lektora (porównanie napisów); przycisk „generuj” wskazuje plik w `katalog_lektora` | zmiana 44px → 30px; plik lektora z innym tekstem |
| 29 | `pobierz_decyzje.py` z atrapą serwera HTTP w wątku testu (adres `127.0.0.1`, port wolny): logowanie, pobranie, wydruk per źródło, `--potwierdz` wysyła `do=ostatnie_id` z nagłówkiem; hasło nie pojawia się w wydruku ani w argv | wydruk hasła; brak nagłówka w `--potwierdz` |
| 30 | `pobierz_decyzje.py`: kody wyjścia (401 → 3, serwer zawiesza odpowiedź dłużej niż `--limit-s` → 4 w czasie ≤ limit + margines, zły JSON → 5, brak nowych decyzji → 0 z napisem) | usunięcie `timeout` z `urlopen` (test zawisa → musi być zabity limitem czasu testu) |

| 31 | PRZEGLĄD: skill-scout: element `type: new` ze slugiem, dla którego w drzewie testowym jest `.claude/skills/<slug>/SKILL.md`, nie jest pozycją i jest liczony w `juz_skillem`; `type: update` z takim slugiem zostaje pozycją; katalog skilla BEZ `SKILL.md` nie wyklucza | usunięcie warunku `is_file()` (sprawdzanie samego katalogu) / usunięcie wykluczenia |
| 32 | PRZEGLĄD: `POST /?przesluchania=przeczytane`: raport znika z sekcji raportów, `raporty_nieprzeczytane` maleje, eksport bez zmian; identyfikator spoza `raport:` albo nieobecny → 404 | zapis rekordu wyboru zamiast `odsluch` |
| 33 | PRZEGLĄD: plik tekstu pozycji: dwa skany bez zmiany treści → czas modyfikacji pliku bez zmian; zmiana odcisku → plik nadpisany; plik pozycji nieobecnej w skanie i starszej niż `wiek_nagran_dni` → usunięty, młodszy → zostaje | zapis bez porównania treści / brak sprzątania |

> PRZEGLĄD: poprawki tabeli 5.1:
> - (ustalenie 13) Test 1 jest wewnętrznie sprzeczny („87 pozycji w próbce 3-elementowej”): obowiązuje „próbka
>   3-elementowa o RÓŻNYCH priorytetach → 3 pozycje w kolejności malejącego `priority`”.
> - Test 9 i 10 — zakres z poprawek 3.1 (cztery przypadki „czy”; dwie sekcje briefingu z powtórzonym wpisem).
> - Test 11 — remis dat (`<data>.json` i `<data>_jarvis.json`) i linia „Wygenerowane: <d1>, uzupełnione <d2>” → klucz z d2.
> - Test 13 — wersja nowsza: wczytanie bez utraty, zapis → `BladStanu`, plik bajt w bajt bez zmian (ustalenie 10).
> - Test 14 — zamrożona kopia modułu v1 (ustalenie 11).
> - (ustalenie 12) Test 24 „bajt w bajt”: `eksport_markdown` wstawia `_teraz_iso()` bez parametru czasu, więc test podmienia
>   `przesluchania._teraz_iso` (monkeypatch) na stałą — bez tego porównanie zależy od zegara. Mutant dodatkowy: `do` w linii
>   procedury liczone tylko z decyzji notatek.
> - (ustalenie 15) Test 30: `requirements-dev.txt` nie ma `pytest-timeout`. Skrypt uruchamiany przez
>   `subprocess.run([...], timeout=limit_s + 10)`; `TimeoutExpired` = czerwień. Bez tego mutant zawiesza całą suitę CI.
> - Testów jest 33; kryterium 6.1 („30”) czytać jako 33.

### 5.2 Kontrole rozróżniające

Każdy mutant uruchamiany z kontrolą bez mutacji (wszystkie testy zielone) i z mutacją (dokładnie wskazane testy czerwone);
tabela wyników mutacji trafia do meldunku 98c. Testy o „kolejności” i „odcisku” używają wejść, w których zmieniana cecha
faktycznie występuje (priorytety różne, dwa takie same nagłówki), żeby nie przechodziły z definicji.

## 6. Kryteria odbioru

Ustalone przed implementacją (dowodem jest działanie na Jarvisie, nie deklaracja):

1. `python -m pytest tests` zielone w Pythonie 3.10 (macierz `anbernic`) i 3.13 (`jarvis`) na CI; `lint` zielony z nowymi
   plikami; testy z tabeli 5.1 (30) czerwone na HEAD przed zmianą i zielone po niej; każdy mutant zapala wskazany test.
2. Lista `/?przesluchania=1` na Jarvisie po wdrożeniu pokazuje: sekcję „Raporty do przeczytania” (skill-scout, pamięć,
   reflect), 87 (lub aktualną liczbę) propozycji skill-scouta jako osobne wiersze z przyciskami Zatwierdź/Odrzuć, 27 sekcji
   reflect osobno, pozycje z `Otwarte decyzje` z przyciskami wariantów tam, gdzie tekst je podaje, oraz pasek stanów źródeł.
3. Kliknięcie na Jarvisie: pozycja znika z „Do decyzji”, jest w „Rozstrzygnięte”, `GET /?przesluchania=eksport` (po
   zalogowaniu) zawiera rekord z kotwicą; powtórne kliknięcie nie dodaje rekordu; `pobierz_decyzje.py` na Pi 5 (z atrapą
   albo z prawdziwym adresem i hasłem podanym przez Karola) wypisuje zmianę do naniesienia.
4. Dowód nienaruszalności: sumy kontrolne drzewa `/srv/anberfiles/vault` (poza `.git`) przed i po odbiorze identyczne;
   `git -C /srv/anberfiles/vault status` czysty.
   PRZEGLĄD: (ustalenie 16) Klon odświeża timer co 15 minut, więc push Karola w trakcie odbioru zmienia sumy bez winy
   AnberFiles — kontrola nierozróżniająca. Obowiązuje: zapis `git rev-parse HEAD` i sum przed i po; przy tym samym HEAD sumy
   identyczne; przy innym HEAD `git status --porcelain` pusty, a pliki o innej sumie są podzbiorem
   `git diff --name-only <HEAD przed> <HEAD po>`.
5. Dowód trzech stanów: po tymczasowym zasłonięciu źródła w KOPII testowej (nie w vaulcie Karola) lista pokazuje komunikat;
   bez dotykania produkcji — w teście, nie na Jarvisie.
6. Dowód restartu: po `sudo systemctl restart anberfiles` stan wyborów (plik `przesluchania.json`) i `ui` odczytane bez
   strat; F5 zachowuje zakładkę i rozwinięcia.
7. Zrzut ekranu listy bez okna przeglądarki (narzędzie ustalone w 98c) pokazujący pozycje z każdego źródła i przyciski, plus
   zrzut w szerokości telefonu (np. 390 px) z przyciskami ≥ 44 px.
8. Kafelek `jarvis-start` pokazuje sumę `do_decyzji` bez zmian w tamtym repozytorium.

PRZEGLĄD: (ustalenia 1, 4, 18) Kryterium 2 bez liczb na sztywno: liczba propozycji skill-scouta = elementy pliku minus
`juz_skillem` minus rozstrzygnięte, liczba sekcji reflect = liczba sekcji `## ` z `- [ ]`, obie zmierzone w klonie w chwili
odbioru i porównane z listą. Kryteria 2, 3, 6 i 7 dowodzi instancja testowa (porcja C, poprawka w 3.11); na produkcji —
SHA, aktywność usługi, 401 bez sesji. Kryterium 8 (kafelek) potwierdza Karol; `jarvis-start/app.js` czyta tylko liczbę
całkowitą `do_decyzji`, więc suma nie wymaga zmian po tamtej stronie (sprawdzone odczytem).

## 7. Ryzyka i cofnięcie

| Ryzyko | Skutek | Zapobieganie / cofnięcie |
|---|---|---|
| Format źródła zmieni się (np. „plan porządku vaulta” przeniesie pliki) | cicha pusta lista | trzy stany (3.5), test 22; paski i wpis w rejestrze |
| Skill-scout nie rozumie pól `status`/`decyzja` w `_proposed.json` | agent laptopa dopisze pole, a skill-scout je skasuje przy kolejnym przebiegu → pozycja wraca | założenie do potwierdzenia (8.1); odcisk i rekord u AnberFiles utrzymują decyzję do czasu zmiany treści |
| Przepisanie sekcji reflect (jak „pozycja 1 przepisana 10.10”) po decyzji | odcisk inny → pozycja wraca z znacznikiem | zamierzone (3.4), znacznik informuje człowieka |
| 87 powiadomień Wykonawcy w `NOW-pending` | zaśmiecenie | domyślny filtr `### Wykonawca:` (3.1); do potwierdzenia |
| Zapis tekstu dla lektora w `korzen/lektor` | pliki rosną | tylko nagłówek+krótki tekst (≤ 750 znaków) na pozycję, nazwa z SHA, nadpisywanie zamiast przyrostu; sprzątanie razem z nagraniami (`wiek_nagran_dni`) |
| Obciążenie Pi 5 przy skanie 116-kilobajtowych plików | wolniejsza odpowiedź | pamięć podręczna 30 s, odczyt w wątku, limity rozmiaru; pomiar czasu skanu w odbiorze (cel: < 200 ms na Pi 5, do zmierzenia i zapisania) |
| Strefa czasowa Jarvisa nie Warsaw | zły `czas` w rekordach | 98c sprawdza `timedatectl` przed wdrożeniem; `czas` z `datetime.now().astimezone()` (nie wymaga `tzdata`) |
| Restart usługi przerywa pracę lektora lub Pulsa | stracone nagrania | BRAMKA przed restartem: `kolejka_lektora` pusta i Puls (`http://100.67.174.47:7777`) bez biegu innego niż własny; inaczej porcja C czeka (kod `BŁĄD` w meldunku, bez restartu) |

Cofnięcie: `git revert` commitów porcji B i A (kolejność odwrotna), `git pull` jako `anberfiles`, `chown -R root:root`, restart.

> PRZEGLĄD: (ustalenie 5) Cofnięcie = `git checkout --detach <SHA sprzed wdrożenia>` w `/srv/anberfiles/kod` (jak faktyczny
> stan: odłączony HEAD), `chown -R root:root`, restart — bez publikowania commitów. `git revert` na `main` tylko na decyzję
> Karola. (ustalenie 6) Wiersz „Zapis tekstu dla lektora” nieprawdziwy co do sprzątania: `sprzataj` nie usuwa plików `.md`
> — obowiązuje własne sprzątanie z poprawki w 3.6.
Stan `przesluchania.json` po rollbacku zachowuje klucz `wybory` (test 14); nic nie ginie, wybory są po prostu niewidoczne dla
starego kodu. Brak operacji sieciowych dotykających łączności urządzenia — zasady `lacznosc.md` nie są naruszone.

## 8. Założenia do potwierdzenia

1. **Nanoszenie decyzji w `_proposed.json`**: dopisanie pola `status` do elementu; skill-scout musi je znosić i nie proponować
   elementu ponownie (potwierdzić z autorem skilla `skill-scout`, 98b sprawdza przez przegląd definicji skilla na laptopie —
   Pi 5 jej nie widzi).
   PRZEGLĄD: definicji skilla `skill-scout` nie ma w klonie (`.claude/skills` ma 41 skilli, żadnego `*scout*`) — nie da się
   ustalić z Pi 5. Ustalone odczytem: notatka `Zasoby/Jarwis-vault-i-skille/2026-08-12-skill-scout-jarvis-i-laptop.md`
   nazywa `_proposed.json` „pamięcią scouta — już wytypowane”, wspólną dla obu maszyn; `combine-raport.mjs` tylko czyta
   `proposed[]` (zapisuje wyłącznie HTML). Ryzyko z 7 zostaje; rekord u AnberFiles i odcisk je łagodzą.
2. **`NOW-pending` — co jest „decyzją”.** Plan traktuje wpisy jako powiadomienia („Przyjąłem do wiadomości”) i filtruje
   87 wpisów Wykonawcy. Karol mówił „wszystko co wymaga decyzji zwrotnej usera” — czy powiadomienia o biegach mają być
   w ogóle widoczne?
3. **Raporty daily/memory-update**: jedna wspólna pozycja z `NOW.md`, nie dwie (brak osobnych raportów, 1.4).
4. **Wspólny licznik `do_decyzji`** w `?przesluchania=licznik` (kafelek bez zmian w innym repozytorium).
5. **Pozycje złączone w NOW.md** (`**A** · **B**`) zostają jedną pozycją.
6. **Zapis tekstu pozycji w `katalog_lektora/_pozycje/`** jest dopuszczalny (jedyny zapisywalny katalog poza `dane`); alternatywa
   bez lektora dla pozycji odrzucona, bo łamie zasadę „ten sam tekst do czytania i do odsłuchu”.
7. **Czas rekordu = czas systemowy Jarvisa (Europe/Warsaw)**; weryfikacja `timedatectl` przed wdrożeniem.
8. **Moduł `przesluchania` zostaje włączony** (`[moduly] przesluchania = tak` w `/etc/anberfiles/jarvis.conf` już jest);
   bez nowych kluczy konfiguracji.
9. **Dopuszczalne opóźnienie widoczności naniesionej decyzji**: do 30 minut (push laptopa + timer 15 min).

## 9. Pytanie kontrolne

Gdyby klon vaulta na Jarvisie zniknął albo wszystkie cztery źródła stały się nieczytelne, a Karol kliknął „Zatwierdź”
przy pozycji, którą widział chwilę wcześniej: co zobaczy na liście, co zostanie zapisane i czy cokolwiek da się
naprawić bez dotykania vaulta? Odpowiedź planu: kliknięcie dla pozycji nieobecnej w skanie kończy się 404 z komunikatem
(rekord nie powstaje, bo serwer nie ma treści do policzenia odcisku); lista pokazuje czerwone paski czterech źródeł i wpisy
w rejestrze; wcześniej zapisane wybory zostają w „Rozstrzygnięte” z migawek; po powrocie klonu pozycje wracają bez zmian,
a wybory z zgodnym odciskiem dalej obowiązują. Żadna ścieżka nie wymaga zapisu do vaulta.
