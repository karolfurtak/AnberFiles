# AnberFiles

Lekki serwer plików HTTP (aiohttp, jeden plik) dla **Anbernic RG40XX V** — przeglądarkowy
menedżer katalogu projektów/sprawozdań działający bezpośrednio na konsoli. Port **8765**,
HTTP Basic Auth, zero JS-frameworków (vanilla, ~wszystko inline).

Powstał jako `sprawozdania-server` do pracy z pipeline'em sprawozdań (AnbernBot),
ale serwuje dowolne drzewo katalogów.

## Możliwości

**Listing katalogu (sortowalna tabela):**
- kolumny: Nazwa, Rozmiar, Modyfikacja, Powstanie (klony git: z bufora `.git/anberfiles-czasy.json` —
  ostatnia zmiana treści i najstarsze dodanie pliku, przez zmiany nazwy; poza gitem:
  czas powstania z systemu plików, a gdy go brak — czas modyfikacji z „≈”)
- **sortowanie naturalne** — `plik_10` po `plik_9`, nie po `plik_1`
- klik w nagłówek sortuje; wybór **zapamiętywany** (localStorage) i przywracany
  po odświeżeniu oraz w innych folderach; wiersz `..` zawsze przypięty na górze
- **auto-odświeżanie co 4 s** — nowy plik (np. świeżo wygenerowany DOCX) pojawia
  się sam, bez F5; podmiana tylko przy realnej zmianie (bez migotania)
- **⬇ pobieranie** jednym kliknięciem przy każdym pliku (`?dl=1`,
  `Content-Disposition: attachment` z nazwą UTF-8)
- ikony wg rozszerzenia (📕 pdf, 📘 docx, 📊 xlsx, 🖼 obrazy…)

**Breadcrumb z nawigacją po drzewie:**
- każdy segment ścieżki klikalny (skok na dowolny poziom)
- **najechanie na segment rozwija listę folderów-rodzeństwa** z tego poziomu —
  przeskok w inną gałąź drzewa bez przechodzenia przez `..`; bieżący folder
  wyróżniony

**Upload drag & drop:**
- upuszczenie plików na listing wgrywa je do bieżącego katalogu
  (POST multipart, limit 512 MB — `limit_wgrywania_mb`, wiele plików naraz)
- duplikaty nazw dostają sufiks z timestampem — nic nie jest nadpisywane

**Akcje przy pliku:**
- **🗑 usuwanie** do kosza `ROOT/.kosz/` (timestamp_nazwa — nic nie znika trwale)
- **✎ zmiana nazwy** (bez nadpisywania — 409 przy konflikcie)
- **⧉ kopiowanie nazwy** do schowka
- **🔊 lektor** (przy `.md`/`.docx`/`.txt`) — patrz niżej

**Przeglądarka zdjęć (`?view=1`):**
- nawigacja poprzednie/następne (przyciski + strzałki ←/→)
- zoom kółkiem myszy wokół kursora (0,2×–20×), przesuwanie przeciąganiem
- dwuklik: dopasuj do okna ⇄ 1:1; przyciski „⊡ dopasuj" i „1:1"; klawisze `0`/`1`/`+`/`−`
- ciemny motyw, licznik pozycji i % powiększenia

**Podgląd dokumentów (`?view=1`):**
- **.md** — zakładki Render (markdown + MathJax dla `$…$`) / Kod; resolver
  obrazków (gołe nazwy → `raw/`, `processed/`, `szablony/`); nawigacja ←/→
  po plikach .md; auto-odświeżanie treści (poll mtime co 3 s)
- **.docx** — konwersja LibreOffice→PDF z cache per wersja pliku (pierwsze
  otwarcie ~20 s na A53, kolejne natychmiast); PDF inline w przeglądarce

**Odtwarzacz audio (`?view=1` dla mp3/flac/wav/ogg):**
- natywny `<audio>` z autostartem, poprzedni/następny (też strzałkami),
  **auto-playlista po katalogu**, **tempo 0,75–2×** (zapamiętywane),
  spacja = pauza; poprawny MIME dla `.flac`

**Lektor TTS (🔊 / `POST ?lektor=1[&fmt=][&queue=1]`):**
- silnik: `tools/czytaj_tts.py` (edge-tts pl-PL 96 kbps + pełna polska
  normalizacja liczb/jednostek/dat/symboli; wstawki „(z ang. …)" czyta
  głos angielski) — wynik `<nazwa>_lektor.<mp3|flac|wav>`
- silnik mowy: `silnik` w `lektor-ustawienia.conf`; bez tej linii lokalny Piper, gdy
  jego usługa odpowiada, inaczej edge. Edge wysyła tekst do Microsoftu — okno lektora
  pokazuje „tekst opuszcza urządzenie (usługa Microsoft)", lektor wpisuje to do dziennika
- wybór formatu radiobuttonami; **wspólna kolejka** dla przycisku 🔊
  i zadań spoza serwera (wykrywanie po procesie)
- **widok kolejki** (`/?lektorq=1`, link w listingu): podgląd na żywo,
  pasek postępu (chunk n/N), usuwanie ✕ (też przerwanie trwającej
  generacji), **pauza ⏸** (następny plik / wstrzymanie całej kolejki,
  SIGSTOP) i ▶ wznowienie
- **trwałość**: rejestr kolejki na dysku + checkpoint per chunk — kolejka
  i postęp przeżywają restart serwera i **reboot urządzenia** (wznowienie
  od ostatniego ukończonego chunka)
- **🗑 nagranie** w pasku podglądu: usuwa nagranie razem z `.cues.json`
  i `.chapters.json` — w katalogu lektora także przy instancji tylko do odczytu
  (inne pliki dalej 403); wpis w rejestrze zdarzeń
- **wygasanie**: `wiek_nagran_dni` (`[serwer]`, domyślnie 0 = wyłączone) — nagrania
  w katalogu lektora starsze niż N dni serwer usuwa przy starcie i co godzinę,
  każde z wpisem w rejestrze; pasek podglądu pokazuje „⏳ zostało …”
- **załącznik nieczytany**: linia `<!-- lektor: koniec -->` — od niej do końca pliku
  lektor nie czyta (szczegóły, źródła); podgląd pokazuje całość, widok słuchania —
  załącznik pod napisem „Dalej: załącznik — tylko do czytania”; brak znacznika = całość
- **odświeżanie po zmianie dokumentu**: `lektor_auto_odswiezanie` (`[serwer]`):
  `nie` (domyślnie, Anbernic) | `natychmiast` (dawne `tak`) | `nocą` (Jarvis). Każde nagranie
  ma `<nazwa>_lektor.zrodlo.json` (dokument, odcisk SHA-256 części czytanej bez nagłówka
  Obsidiana, silnik). Przy starcie i co minutę serwer porównuje odcisk TYLKO dokumentów
  z nagraniem. `natychmiast`: inny odcisk → zadanie w kolejce od razu. `nocą`: inny odcisk
  tylko oznacza nagranie („⚠ nagranie nieaktualne — nowe nocą o 03:00” i przycisk
  „🔄 nagraj teraz” w podglądzie, widoku słuchania i na liście „Do przesłuchania”; stare
  nagranie do odtworzenia); o `lektor_godzina_nocna` (03:00) jeden przebieg zleca wszystkie
  nieaktualne, raz na dokument, tym samym głosem; od `lektor_nocne_okno_do` (06:30) nowych
  nie zaczyna — pozostałe przechodzą na kolejną noc. Rejestr: ustawienie przy starcie,
  „przebieg nocny … zleconych N”, „okno nocne zamknięte …”, bilans przebiegu. Zadania
  automatyczne ustępują zleconym ręcznie i czekają, gdy Puls (`puls_adres`, `/api/status`)
  ma trwający bieg; „nagraj teraz” to zlecenie ręczne (nie czeka)

**Lista „Do przesłuchania” (moduł `przesluchania`, domyślnie wyłączony):**
- `/?przesluchania=1` — notatki `.md` z `przesluchania_zakres` ze `status:` w nagłówku
  (`do-akceptacji`, `zaakceptowane-niewdrozone`, …) albo z tabeli propozycji
  `przesluchania_kandydaci` (wiersze tabeli: ścieżka w odwróconych apostrofach, rodzaj, status); zakładki: do decyzji,
  zaakceptowane niewdrożone, rozstrzygnięte; zakładka zapamiętana na serwerze
- `<notatka>.md?sluchaj=1` — tekst + lektor (zdania podświetlane, gdy jest nagranie
  z czasami zdań) + decyzja „akceptuję / do poprawy / odrzucam” z uwagą tekstową;
  pozycja odtwarzania i „odsłuchane” (95 % nagrania) zapisywane na serwerze
- stan: `katalog_danych/przesluchania.json` (zapis atomowy, dziennik decyzji tylko
  dopisywany); vault nigdy nie jest zapisywany — decyzja obowiązuje, dopóki treść
  notatki (bez nagłówka) się nie zmieni
- eksport dla agenta przenoszącego decyzje do vaulta: `katalog_danych/przesluchania-eksport.md`
  i `GET /?przesluchania=eksport` (JSON); potwierdzenie `POST /?przesluchania=potwierdz`
  `{"do": id}` (powtarzalne)
- `GET /?przesluchania=licznik` — liczba notatek do decyzji dla kafelka strony startowej;
  zgoda CORS z ciasteczkiem tylko dla originu z `strona_startowa`

**Bezpieczeństwo:**
- HTTP Basic Auth (konfigurowany przez env; pusty `SERVER_PASS` = open access,
  tylko do sieci prywatnych!; przy `haslo_wymagane = tak` pusty `SERVER_PASS`
  zatrzymuje start)
- guard na path traversal (żądania nie wyjdą poza katalog główny)
- pliki i katalogi z kropką (`.kosz`, `.git`, `.opisy_cache`, `.part`) — ukryte w listingu
  i niedostępne po bezpośrednim adresie: 403 dla każdej metody (jeden strażnik w pośredniku)

## Wymagania

- Python 3.10+ (sprawdzane w CI na 3.10 i 3.13),
  `pip install --require-hashes -r requirements.txt` — wersje przypięte (`==`) ze skrótami
  sha256 wszystkich plików dystrybucji (Linux x86_64 i aarch64, Python 3.10 i 3.13), także
  zależności przechodnie; wejście: `requirements.in` (polecenie kompilacji w nagłówku obu
  plików). Narzędzia (pytest, ruff, pip-audit): `pip install -r requirements-dev.txt`.
  CI uruchamia `pip-audit` na `requirements.txt`.
- testowane na stock firmware Anbernic RG40XX V (Ubuntu 22.04, build 20251225) —
  ale działa na dowolnym Linuksie

## Konfiguracja (env)

| Zmienna | Domyślnie | Rola |
|---|---|---|
| `SERVER_PORT` | `8765` | port HTTP |
| `SERVER_HOST` | `0.0.0.0` | `127.0.0.1` = tylko lokalnie/tunel SSH |
| `SERVER_USER` | `anbernic` | login Basic Auth |
| `SERVER_PASS` | *(puste)* | hasło; **puste wyłącza auth** |

Zmienna środowiska wygrywa z plikiem ustawień, plik z wartością domyślną.

## Ustawienia instancji (`ANBERFILES_CONF`)

Jeden kod obsługuje kilka instancji (konsola Anbernic, Jarvis). Wszystkie ścieżki
i przełączniki są w `app/konfiguracja.py`; plik ustawień instancji leży **poza
repozytorium**, a wskazuje go zmienna `ANBERFILES_CONF`.

- **Brak zmiennej = ustawienia konsoli Anbernic** (katalog `/mnt/data/sprawozdania`,
  dane w `/mnt/data`, port 8765, wszystkie moduły włączone) — zachowanie jak dotąd.
- Zmienna ustawiona, a pliku brak, nieznany klucz albo zła wartość = serwer nie startuje.
- Sekcje: `[serwer]` (`nazwa_instancji`, `host`, `port`, `uzytkownik_www`,
  `haslo_wymagane`, `tylko_odczyt`, `logowanie`), `[katalogi]` (`katalog_glowny`, `katalog_danych`,
  `katalog_lektora`, `katalog_eksportu`, pliki rejestru i błędów, pamięć podręczna
  podglądu DOCX, katalog ZIP, favikona, kosz), `[moduly]` (`podglad_docx`,
  `eksport_docx`, `lektor`, `lektor_opisy_ai`, `wylaczanie`, `druk`, `bateria`,
  `kadrowanie`, `przesluchania` — `tak`/`nie`; `przesluchania` bez wpisu = `nie`).
- Eksport DOCX (POST `?docx=1` na `.md`, przycisk w podglądzie): skrypt wyłącznie z `katalog_eksportu` — domyślnie
  `export_to_docx.py`; dyrektywa `<!-- eksporter: X -->` w dokumencie albo
  `<projekt>/szablon/eksporter.conf` wybiera tylko NAZWĘ z białej listy plików
  `export_*.py` w katalogu eksportu (`X` → `export_X.py`). Ścieżka, `/`, `\`, `..`
  albo skrypt z katalogu projektu = 400, nic nie jest uruchamiane.
- Hasło **tylko** w zmiennej `SERVER_PASS`, nigdy w pliku.
- Przy starcie: brak hasła przy `haslo_wymagane = tak`, brak katalogu głównego albo
  nieudana próba zapisu w katalogu danych = komunikat na stderr i kod wyjścia 2.
- Limity i pamięć (`[serwer]`): `limit_wgrywania_mb` (domyślnie 512) — łączny rozmiar
  jednego wgrywania, ponad → 413 (licznik bajtów w trakcie strumienia, `.part` usuwany);
  pozostałe ciała żądań czytane do pamięci najwyżej 64 KiB, formularz logowania 4 KiB
  (sprawdzane przed wczytaniem). `prog_pamieci_mb` (domyślnie 400) — co 60 s pomiar RSS
  serwera i jego procesów potomnych (soffice, lektor) z `/proc`; nowy szczyt (wzrost
  o co najmniej 10 %) i przekroczenie progu (najwyżej raz na 10 min, poziom `warn`)
  trafiają do rejestru zdarzeń; stan „Pamięć: teraz / szczyt / próg" w nagłówku `/?events=1`.
  `limit_zip_mb` (domyślnie 2048) — łączny rozmiar plików folderu pobieranego jako ZIP
  (`?zip=1`), ponad → 413 przed zapisem archiwum; ZIP pomija dowiązania symboliczne
  i pliki, których rzeczywista ścieżka wychodzi poza pakowany katalog.
- `tylko_odczyt = tak` → 403 na wgrywanie, usuwanie, zmianę nazwy, nowy katalog,
  kadrowanie i eksport DOCX; lektor i jego kolejka działają. Przyciski tych akcji
  znikają z interfejsu, podobnie przyciski wyłączonych modułów.
- Ochrona przed żądaniami z obcych stron (DNS rebinding, CSRF):
  - nagłówek `Host` musi być adresem IP (v4/v6, z portem lub bez), `localhost`, nazwą
    `*.local` albo nazwą z `dozwolone_hosty` (`[serwer]`, lista po przecinku; wpis
    z kropką na początku = sufiks, np. `.ts.net`) — inaczej 421;
  - POST, PUT, PATCH i DELETE (wgrywanie, usuwanie, zmiana nazwy, kolejka lektora,
    druk, wyłączanie, eksport DOCX) wymagają nagłówka `X-AnberFiles: 1`, który dokładają
    skrypty stron (jedna funkcja `afFetch`/`afXhr`) — bez niego 403. Formularz z obcej
    strony nie doda nagłówka, a `fetch` z obcego originu z własnym nagłówkiem wymaga
    zgody CORS, której serwer nie daje. Skrypty i narzędzia wołające API (curl) muszą
    dodać `-H 'X-AnberFiles: 1'`;
  - formularze logowania i „Ustaw hasło" (zwykły formularz HTML, działa bez JS
    i z menedżerem haseł) zamiast nagłówka sprawdzają `Origin`: obecny musi wskazywać
    ten sam host:port co `Host`, inaczej 403.
- Druk (moduł `druk`): `limit_druku_na_godzine` (`[serwer]`, domyślnie 20) — zadań na
  godzinę w oknie przesuwnym, ponad → 429 z `Retry-After`; druk `.md` bez
  `export_to_docx.py` w katalogu eksportu → 400 z przyczyną (zamiast cichej awarii
  w `print_errors.log`).
- Duże i zdalne katalogi: `limit_odczytu_katalogu_s` (`[serwer]`, domyślnie 3, wartość
  ułamkowa dozwolona) — czas na odczyt katalogu; po nim 504 ze stroną po polsku i wpisem
  w rejestrze zdarzeń (błąd systemu plików → 503), a reszta serwera odpowiada normalnie.
  `bez_drzewa` (`[katalogi]`, domyślnie puste) — nazwy katalogów pierwszego poziomu
  katalogu głównego, których poddrzewa eksplorator (`?explorer=1`) nie rozwija.
- Dziennik dostępu: `katalog_danych/access.log` (klucz `[katalogi] dziennik_dostepu`;
  na konsoli `/mnt/data/access.log`) — linia na żądanie: czas, adres, metoda, ścieżka
  z zapytaniem, kod, bajty treści, czas obsługi; rotacja 5 × 5 MB (`access.log.1`…`.5`).
  Bez nagłówków i ciasteczek; wartości `token=`, `haslo=`, `password=` jako `***`.
- `soffice_bez_sieci` (`[serwer]`, `auto`/`tak`/`nie`, domyślnie `auto`) — LibreOffice
  (podgląd DOCX, druk) uruchamiany w piaskownicy bez sieci (`bwrap --unshare-net`, a gdy
  go brak — `unshare -n`), żeby pole `INCLUDEPICTURE http://…` w dokumencie nie kazało
  serwerowi pobierać adresów. Wykrycie raz na start (próbne `true` w piaskownicy);
  `auto` bez działającej piaskownicy = zwykły `soffice` i jedno ostrzeżenie w rejestrze
  zdarzeń, `tak` = podgląd DOCX i druk odmawiają konwersji. Wymaga `bubblewrap`
  (`apt install bubblewrap`); jednostka systemd z `RestrictNamespaces=yes` blokuje
  przestrzenie nazw — dla bwrap potrzebne `RestrictNamespaces=user net mnt`.
- `katalog_lektora` (musi leżeć w katalogu głównym): nagrania trafiają do
  `katalog_lektora/<ścieżka katalogu źródła>/<nazwa>_lektor.<fmt>` zamiast obok
  dokumentu; podgląd `.md`, `?read` i ikona 🎧 w listingu je odnajdują.

### Logowanie (`[serwer] logowanie`)

- `basic` (domyślne, konsola Anbernic) — okno HTTP Basic, hasło z `SERVER_PASS`.
- `formularz` (Jarvis) — strona logowania; poprawne hasło zapamiętuje urządzenie
  trwałym ciasteczkiem (HMAC-SHA256, 10 lat, `HttpOnly`, `SameSite=Strict`), link
  „wyloguj" w listingu. Pierwsze uruchomienie: ekran **„Ustaw hasło"** (min. 10 znaków),
  dostępny tylko z sieci lokalnej i Tailscale i tylko z **jednorazowym tokenem
  startowym**: usługa drukuje do dziennika (`journalctl -u anberfiles | grep token`)
  gotowy adres `…/__anberfiles/ustaw-haslo?token=…`; token żyje wyłącznie w pamięci
  procesu (restart wydaje nowy, ustawienie hasła go unieważnia), porównanie stałoczasowe.
  `ustaw_haslo_bez_tokenu` (lista adresów/sieci CIDR, domyślnie pusta) zwalnia wskazane
  adresy z tokenu — nie z ograniczenia do sieci prywatnych. Skrót hasła (scrypt) i sekret ciasteczek:
  `katalog_danych/auth/` (0600). `SERVER_PASS` niepotrzebny. Złe hasło: 1 s opóźnienia,
  limit 10 prób na minutę na adres. Resetu przez WWW nie ma:
  `sudo anberfiles-reset-hasla` (bez restartu usługi; wszystkie urządzenia wylogowane).

Instancja Jarvis: jednostki systemd `konfiguracja/*.service.przyklad`, `*.timer.przyklad`,
narzędzia w `narzedzia/` (reset hasła, kopia plików instancji, odświeżanie klonów),
procedura odtworzenia: [`docs/odtworzenie-po-awarii.md`](docs/odtworzenie-po-awarii.md).

Przykłady: [`konfiguracja/jarvis.conf.przyklad`](konfiguracja/jarvis.conf.przyklad),
[`konfiguracja/lektor-ustawienia.conf.przyklad`](konfiguracja/lektor-ustawienia.conf.przyklad).

## Instalacja jako usługa (systemd)

```bash
scp app/server.py root@KONSOLA:/usr/local/bin/sprawozdania-server.py
scp app/konfiguracja.py root@KONSOLA:/usr/local/bin/konfiguracja.py   # obok serwera
# app/logowanie.py — tylko dla logowanie = formularz (konsola go nie potrzebuje)

cat > /etc/sprawozdania-server.env <<EOF
SERVER_PASS=twoje_haslo
EOF

cat > /etc/systemd/system/sprawozdania-server.service <<EOF
[Unit]
Description=AnberFiles file server
After=network-online.target

[Service]
EnvironmentFile=/etc/sprawozdania-server.env
ExecStart=/usr/bin/python3 /usr/local/bin/sprawozdania-server.py
Restart=on-failure
# Zalecane: dedykowany użytkownik zamiast root (np. useradd -r -s /usr/sbin/nologin
# anberfiles; prawa zapisu do katalogu głównego i katalogu danych). Uwaga: moduł
# „wylaczanie" (wyłączenie konsoli po kolejce lektora) wymaga wtedy uprawnienia
# do poweroff (np. reguła polkit albo sudoers dla tego jednego polecenia).
#User=anberfiles
#Group=anberfiles

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now sprawozdania-server
```

Otwórz `http://IP_KONSOLI:8765/` w przeglądarce.

## Uwagi

- Linki do plików z polskimi znakami wymagają URL-encode — listing robi to sam
  (`urllib.parse.quote`).
- Auto-odświeżanie i sortowanie nie gryzą się: po podmianie tabeli przywracany
  jest zapamiętany porządek.

---

## Licencja

Copyright (c) 2026 Karol Furtak. **Wszelkie prawa zastrzeżone.** Użycie komercyjne, kopiowanie, rozpowszechnianie i modyfikowanie wyłącznie za pisemną zgodą autora — szczegóły w pliku [LICENSE](LICENSE).
