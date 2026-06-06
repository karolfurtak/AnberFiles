# AnberFiles

Lekki serwer plików HTTP (aiohttp, jeden plik) dla **Anbernic RG40XX V** — przeglądarkowy
menedżer katalogu projektów/sprawozdań działający bezpośrednio na konsoli. Port **8765**,
HTTP Basic Auth, zero JS-frameworków (vanilla, ~wszystko inline).

Powstał jako `sprawozdania-server` do pracy z pipeline'em sprawozdań (AnbernBot),
ale serwuje dowolne drzewo katalogów.

## Możliwości

**Listing katalogu (sortowalna tabela):**
- kolumny: Nazwa, Rozmiar, Modyfikacja, Utworzono (crtime/ctime)
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
  (POST multipart, limit 512 MB, wiele plików naraz)
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
- wybór formatu radiobuttonami; **wspólna kolejka** dla przycisku 🔊
  i zadań spoza serwera (wykrywanie po procesie)
- **widok kolejki** (`/?lektorq=1`, link w listingu): podgląd na żywo,
  pasek postępu (chunk n/N), usuwanie ✕ (też przerwanie trwającej
  generacji), **pauza ⏸** (następny plik / wstrzymanie całej kolejki,
  SIGSTOP) i ▶ wznowienie
- **trwałość**: rejestr kolejki na dysku + checkpoint per chunk — kolejka
  i postęp przeżywają restart serwera i **reboot urządzenia** (wznowienie
  od ostatniego ukończonego chunka)

**Bezpieczeństwo:**
- HTTP Basic Auth (konfigurowany przez env; pusty `SERVER_PASS` = open access,
  tylko do sieci prywatnych!)
- guard na path traversal (żądania nie wyjdą poza katalog główny)

## Wymagania

- Python 3.10+, `aiohttp` (`pip install aiohttp`)
- testowane na stock firmware Anbernic RG40XX V (Ubuntu 22.04, build 20251225) —
  ale działa na dowolnym Linuksie

## Konfiguracja (env)

| Zmienna | Domyślnie | Rola |
|---|---|---|
| `SERVER_PORT` | `8765` | port HTTP |
| `SERVER_HOST` | `0.0.0.0` | `127.0.0.1` = tylko lokalnie/tunel SSH |
| `SERVER_USER` | `anbernic` | login Basic Auth |
| `SERVER_PASS` | *(puste)* | hasło; **puste wyłącza auth** |

Katalog główny: stała `ROOT` w `app/server.py` (domyślnie `/mnt/data/sprawozdania`).

## Instalacja jako usługa (systemd)

```bash
scp app/server.py root@KONSOLA:/usr/local/bin/sprawozdania-server.py

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
