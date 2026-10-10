# Przegląd planu 98a — decyzje i raporty automatów na liście „Do przesłuchania”

Zlecenie 98b, 10.10.2026, model `claude-opus-5-5`. Przedmiot: `docs/plany/2026-10-10-98-decyzje-w-liscie-odsluchu-plan.md`
(commit `69ff4ae`). Plan sprawdzony wobec kodu na HEAD (klon `/tmp/jarwis-wykonawca/anberfiles`) oraz wobec faktycznych plików
klonu vaulta `/srv/anberfiles/vault` (tylko odczyt, konto `claude`, vault na HEAD `916bdde` z 10.10 19:46). Poprawki naniesione
w planie w miejscu, każda oznaczona `PRZEGLĄD:` z numerem ustalenia. Kod i testy produktu bez zmian.

Wynik ogólny: rdzeń planu jest poprawny i zgodny z kodem — rozszerza istniejący `Magazyn` (wspólny licznik, wspólne
potwierdzenie, jeden eksport), nie tworzy drugiej drogi zapisu, zapis tylko w katalogu danych i katalogu lektora, ochrona
`straz_zrodla` i logowanie bez zmian. Wady dotyczą kształtu źródeł (plan przeoczył stan zapisany w samym vaulcie i drugą
sekcję briefingu), wykonalności odbioru na produkcji bez hasła Karola oraz kilku testów, które w opisanej postaci byłyby
pozorne albo zależne od zegara. Wszystkie dają się poprawić w miejscu.

## Ustalenia

| Nr | Waga | Co | Poprawka (miejsce w planie) |
|---|---|---|---|
| 1 | wysoka | skill-scout: 18 elementów `type: new` w `_proposed.json` ma już skill `.claude/skills/<slug>/SKILL.md` w klonie; plan pokazałby je jako „do decyzji”, a kryterium 6.2 żąda „87” | wykluczenie w parserze, licznik `juz_skillem`, test 31; 6.2 bez liczby (1.4, 3.1, 5.1, 6) |
| 2 | wysoka | `NOW-pending.md` ma dwie sekcje `## Decyzje do briefingu` (linia 130 i 1304); plan zna jedną i czyta „tylko do końca swojej sekcji”; wpisów spoza Wykonawcy jest 15, nie 5 | wszystkie sekcje, deduplikacja po `id`, cały plik w limicie 2 MiB, test 10 (1.4, 3.1) |
| 3 | średnia | Reguła „X czy Y” na 15 prawdziwych liniach `Otwarte decyzje`: 4 dopasowania, 3 z błędnymi napisami przycisków | reguła tokenowa (słowo przed i po „czy”), cztery przypadki w teście 9 (3.1) |
| 4 | wysoka | Porcja C wymaga sesji (hasło Karola, którego nie wolno czytać) i odczytu `dane/` (`drwxr-x--- anberfiles`) do bramki lektora; odbiór na produkcji w opisanej postaci niewykonalny | odbiór funkcjonalny na instancji testowej czytającej klon (zapis wykluczony uprawnieniami), na produkcji SHA, aktywność, 401; sudo tylko za zgodą promptu 98c; zrzut produkcji i kafelek — Karol (3.11, 6) |
| 5 | średnia | Wdrożenie i cofnięcie skopiowane z `docs/odtworzenie-po-awarii.md` („`git pull` jako `anberfiles`”), a kod należy do roota i stoi na odłączonym HEAD `a567a59` (reflog); cofnięcie przez `git revert` publikuje commity | wdrożenie jak faktycznie (`fetch` + `checkout --detach` jako root, krok 12 dokumentu), cofnięcie = checkout poprzedniego SHA + restart; rozjazd dokumentu do meldunku 98c (3.11, 7) |
| 6 | średnia | Pliki tekstu pozycji w katalogu lektora: `nagrania.sprzataj` usuwa tylko nagrania (wbrew wierszowi ryzyk), zapis co skan zmienia czas modyfikacji i może oznaczać nagrania jako nieaktualne; mp3 trafia do `korzen/lektor/lektor/_pozycje/…` | zapis tylko przy zmianie treści, własne sprzątanie, test 33, test 28 na faktycznej ścieżce (3.6, 7) |
| 7 | średnia | Brak drogi do odsłuchu pozycji: `?sluchaj=1` przyjmuje tylko pliki pod `przesluchania_zakres` | odtwarzacz w wierszu przez `_lektor_audio_for`, stan nagrania, odnośnik raportu do źródła (3.6) |
| 8 | średnia | Brak trasy „przeczytane” dla raportów — `ustaw_odsluch` woła dziś tylko `notatka_post` | `POST /?przesluchania=przeczytane`, bez rekordu wyboru i eksportu, test 32 (3.8) |
| 9 | średnia | Klucz raportu reflect z „Wygenerowane: 2026-09-28, uzupełnione 2026-10-10” — pierwsza data stoi, nowy przebieg nie wróciłby; skill-scout: pary plików tej samej daty, remis nieokreślony, mutant testu 11 rozróżnia tylko przy remisie | klucz z ostatniej daty; remis → plik bez przyrostka; remis w teście 11 (3.1, 5.1) |
| 10 | średnia | Stan z nowszą `wersja` odkładany jako uszkodzony → po cofnięciu kodu decyzje znikają z widoku | wczytanie tylko do odczytu, zapis → `BladStanu` 500, test 13 (3.2) |
| 11 | średnia | Test 14 (cofnięcie) pozorny: stara ścieżka zapisu znika z kodu, a CI ma checkout głębokości 1 (`git show a567a59:…` nie zadziała) | zamrożona kopia modułu `tests/dane/przesluchania_v1.py` (3.2, 4, 5.1) |
| 12 | średnia | Test 24 „bajt w bajt” zależny od zegara (`eksport_markdown` wstawia `_teraz_iso()` bez parametru); linia procedury w eksporcie liczy `do` tylko z decyzji notatek | monkeypatch `_teraz_iso`; `do` = maksimum obu list, mutant (3.8, 5.1) |
| 13 | niska | Test 1 wewnętrznie sprzeczny („87 pozycji w próbce 3-elementowej”) | 3 pozycje, różne priorytety (5.1) |
| 14 | niska | Testy 16–17 (kody HTTP, żądania równoległe) przypisane porcji A, a trasa powstaje w B | A na poziomie `Magazyn`/funkcji, B przez trasę; punkt podziału B1/B2 (3.11) |
| 15 | niska | Mutant testu 30 („brak timeout”) zawiesza suitę — `requirements-dev.txt` nie ma `pytest-timeout` | `subprocess.run(..., timeout=limit_s + 10)` (5.1) |
| 16 | niska | Kryterium 6.4 (sumy vaulta przed/po) nierozróżniające — timer odświeża klon co 15 minut | HEAD przed/po; przy różnym HEAD zmiany ⊆ `git diff --name-only` (6) |
| 17 | niska | Niejasne, czy raporty liczą się do `do_decyzji`; brak kodu dla 429; treść wpisu rejestru wyboru nieokreślona | raporty poza `do_decyzji`, 429 → kod 3, wpis rejestru bez treści uwagi (3.4, 3.9) |
| 18 | niska | Źródło reflect zmieniło się po planie: 24 sekcje zamiast 27, nowa sekcja H3 `### Rozstrzygnięte przez CEO 10.10.2026` | reguła końca sekcji obsługuje to bez zmian; liczby w kryteriach mierzone przy odbiorze (1.4, 6) |

Liczba ustaleń według wagi:

| Waga | Liczba ustaleń |
|---|---|
| wysoka | 3 |
| średnia | 9 |
| niska | 6 |

## Sprawdzone fakty

| Polecenie | Wynik |
|---|---|
| `git -c safe.directory='*' pull --ff-only` w `/tmp/jarwis-wykonawca/anberfiles`; `git log --oneline -1` | „Already up to date”, HEAD `69ff4ae` (commit planu na `a567a59`) |
| `systemctl cat anberfiles` | `BindReadOnlyPaths=/srv/anberfiles/vault:/srv/anberfiles/korzen/vault` (oraz `wykonawca`), `ReadWritePaths=/srv/anberfiles/dane /srv/anberfiles/korzen/lektor`, `ProtectSystem=strict`, `User=anberfiles` — zgodne z 1.2 planu |
| `grep przesluch /etc/anberfiles/jarvis.conf` (bez linii z hasłem) | `przesluchania_zakres = /srv/anberfiles/korzen/vault`, `katalog_lektora = /srv/anberfiles/korzen/lektor`, `tylko_odczyt = tak`, `[moduly] przesluchania = tak`, `wiek_nagran_dni = 3` |
| `ls -la /srv/anberfiles /srv/anberfiles/vault` | klon z `.git`, `Zasoby/`, `.claude/`, `NOW-pending.md` (115 872 bajty); `dane` ma prawa `drwxr-x--- anberfiles`; `kod` należy do roota |
| `git -C /srv/anberfiles/kod reflog -5`; `cat .git/HEAD` | `a567a59 HEAD@{0}: checkout: moving from main to a567a59`; HEAD odłączony, `main` na `421a318`, pliki roota |
| odczyt `app/przesluchania.py` | `WERSJA_STANU = 1`; `Magazyn` (jeden plik JSON, zapis atomowy, `threading.Lock`), `_wczytaj` uzupełnia klucze `setdefault` i odkłada nieczytelny plik jako `.uszkodzony-<czas>`; `_zapisz` zapisuje cały `_stan`; `dodaj_decyzje` tylko `zakres='notatka'` (`ZAKRESY = ('notatka',)`); `potwierdz` idempotentny z kontrolą zakresu; `eksport_json` (`ostatnie_id` z `nowe[-1]`), `eksport_markdown` z `_teraz_iso()` bez parametru; `ZAKLADKI` pięć zakładek — wszystko zgodne z 1.3 planu |
| odczyt `app/server.py` 5059–5260, 6001–6007 | `przesluchania_get` (5188, `eksport`, `licznik`, lista), `przesluchania_post` (5222, `ui`, `potwierdz`), `_prz_notatki` w `run_in_executor` z `PRZ_SKAN_TTL_S = 30`, `_prz_licznik`, `_cors_strony_startowej`, inicjalizacja `Magazyn` (6004); `ustaw_odsluch` wołane tylko z `notatka_post` (5295) |
| odczyt `straz_zrodla` (2363) i `origin_zgodny` (2349) | POST bez `X-AnberFiles: 1` → 403; brak `Origin` = zgodny, token formularza wymagany tylko przy `Origin: null` |
| odczyt `app/logowanie.py` (`obsluz`, `nowy_token`) | udane logowanie → 303 z ciasteczkiem `HttpOnly`, `SameSite=Strict`, `Secure` tylko przy HTTPS; złe hasło → 401 po 1 s; limit 10 prób na minutę → 429; brak sesji → 401; token bezstanowy (HMAC) |
| odczyt `lektor_item` (4401) i `_lektor_dir_for` (386) | plik pod `ROOT`, `.md/.docx/.txt`; katalog wyniku = `katalog_lektora / <katalog względem ROOT>`; dozwolone w trybie tylko do odczytu |
| odczyt `app/nagrania.py` (`sprzataj`, `wygasle`, `usun_nagranie`) | usuwane wyłącznie pliki, dla których `jest_nagraniem_lektora` jest prawdą — pliki `.md` nie |
| odczyt `.github/workflows/ci.yml` | `lint`: ruff `E9,F` na `app/ tools/ tests/ narzedzia/anberfiles-reset-hasla` i jawna lista `py_compile`; `testy`: macierz `anbernic` Python 3.10 i `jarvis` Python 3.13; `actions/checkout@v4` bez `fetch-depth` |
| `grep -v hash requirements-dev.txt` | `pytest`, `ruff`, `pip-audit` — bez `pytest-timeout` |
| Python: `json.load(_proposed.json)` | `{"proposed": [...]}`, 87 elementów (73 `new`, 14 `update`), 87 unikalnych `slug`, wszystkie 11 pól w każdym, `machine` w 10; najdłuższe `what` 626 znaków, `title` 133 — zgodne z planem |
| Python: `slug` wobec `.claude/skills/*/SKILL.md` | 41 skilli w klonie; 18 elementów `new` i 2 `update` mają slug istniejącego skilla; 55 `new` i 12 `update` pozostałych |
| `ls Zasoby/raporty/skill-scout/data` | 21 plików; pary tej samej daty: `2026-09-03.json` i `2026-09-03_jarvis.json`; najnowszy `2026-10-10.json` (`date`, `window_days` 7, `stats`, 2 kandydatów) |
| `grep -E "^#{1,3} " _reflect-pending.md`; `grep -c "^- \[ \] ✅ Zatwierdź tę zmianę"` | 24 sekcje `## `, 24 linie checkboxa, każda ostatnią niepustą linią sekcji; H3 `### Odrzucone w tym przebiegu (świadomie)` (306) i `### Rozstrzygnięte przez CEO 10.10.2026 (usunięte z listy)` (337); linia 3: `*Wygenerowane: 2026-09-28, uzupełnione 2026-10-10 \| …*` |
| `awk` sekcji `## Otwarte decyzje` w `.claude/rules/NOW.md` | linie 53–69, 15 pozycji `- [ ]`, żadnego `- [x]`; ostatnia to metawpis „Kolejki propozycji … 68 kandydatów skill-scout” |
| Python w `/tmp/jarwis-wykonawca/98b`: reguły wariantów na tych 15 liniach | `A … · B …`: 3 dopasowania, 3 poprawne; „X czy Y” w wersji planu: 4 dopasowania, 3 błędne; reguła tokenowa: 3 poprawne, 1 odrzucone do przycisków domyślnych |
| `grep -n "^## " NOW-pending.md`; `awk` zliczający `### ` w sekcjach | `## Decyzje do briefingu` w liniach 130 i 1304; pierwsza: 92 wpisy (87 Wykonawcy, 5 innych), druga: 10 wpisów (0 Wykonawcy); kopia `NOW.md` w bloku `NOW:START` (linie 11–117) z własnym `## Otwarte decyzje` (54) |
| `grep _proposed Zasoby/raporty/skill-scout/scripts/combine-raport.mjs` | `_proposed.json` tylko czytany (`loadHistory`); zapis tylko `raport-zbiorczy.html` |
| `ls .claude/skills \| grep scout` | brak definicji skilla `skill-scout` w klonie — założenie 8.1 nie da się ustalić z Pi 5 |
| `grep do_decyzji /home/claude/jarvis-start/app.js` | kafelek czyta `dane.do_decyzji` jako liczbę całkowitą — suma notatek i pozycji nie wymaga zmian w tamtym repozytorium |
| `ls /tmp/jarwis-wykonawca/chromium`; `which chromium google-chrome` | brak — zgodne z 1.3 planu (narzędzie zrzutów ustala 98c) |

Nie sprawdzano: uprawnień `sudo` konta `claude` (zakaz `sudo` w tym biegu; `/etc/sudoers.d` nieczytelny) — dlatego poprawka 3.11
uzależnia restart od zgody w prompcie 98c. Nie czytano `dane/auth`, kolejki lektora ani nagrań.

## Werdykt

Plan po poprawkach spełnia kryteria zlecenia: fakty potwierdzone odczytem, rozszerzenie istniejącego `Magazyn` zamiast dubletu,
wstecznie bezpieczna migracja z testem na zamrożonej kopii, vault wyłącznie do odczytu (test strukturalny i uprawnienia),
trzy stany źródeł, bezpieczeństwo, wszystkie powierzchnie (lista, odsłuch, licznik, kafelek, eksport, README), 33 testy
z mutantami, wdrożenie z bramką i cofnięciem zgodnym ze stanem serwera, porcjowanie A/B/C z punktem podziału B. Żadna
wada nie wymaga przebudowy planu — wszystkie poprawki naniesiono w miejscu. Porcja C na produkcji zależy od zgody na `sudo`
w prompcie 98c; bez niej odbiór przechodzi na instancji testowej, a wdrożenie zostaje do decyzji Karola.

WERDYKT: ZATWIERDZONY
