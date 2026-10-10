# Decyzje przy pozycjach listy „Do przesłuchania”

Plan: `docs/plany/2026-10-10-98-decyzje-w-liscie-odsluchu-plan.md` (z poprawkami przeglądu 98b).
Kod: `app/pozycje.py` (odczyt źródeł), `app/przesluchania.py` (`Magazyn`, eksport), `app/server.py`
(trasy i widok), `narzedzia/pobierz_decyzje.py` (laptop).

## Droga decyzji

1. Karol klika przycisk przy pozycji (Zatwierdź / Odrzuć / wariant A, B / Przyjąłem do wiadomości),
   opcjonalnie z uwagą. Przeglądarka wysyła `POST /?przesluchania=wybor` z nagłówkiem
   `X-AnberFiles: 1` (za logowaniem).
2. Serwer szuka pozycji w bieżącym odczycie źródła (klient nie podaje treści ani odcisku), sprawdza,
   czy wybór należy do wariantów pozycji, i dopisuje rekord do `katalog_danych/przesluchania.json`.
   Vault na Jarvisie jest tylko do odczytu — serwer nigdy go nie zapisuje.
3. Agent laptopa uruchamia `python narzedzia/pobierz_decyzje.py` i nanosi wypisane zmiany w vaulcie,
   wypycha vault, potem `python narzedzia/pobierz_decyzje.py --potwierdz`.
4. Po odświeżeniu klonu vaulta na Jarvisie (timer co 15 minut) pozycja znika ze źródła; do tego
   czasu jest w zakładce „Rozstrzygnięte” ze znacznikiem „czeka na przeniesienie” albo
   „przeniesione, czeka na odświeżenie vaulta”.

## Rekord wyboru (`wybory` w `przesluchania.json`, wersja stanu 2)

| Pole | Znaczenie |
|---|---|
| `id` | numer ze wspólnego licznika z decyzjami notatek |
| `czas` | ISO 8601 z przesunięciem strefy, czas Jarvisa |
| `zrodlo` | `skill-scout`, `reflect`, `now`, `now-pending` |
| `id_pozycji` | `skill-scout:<slug>`, `reflect:<12 znaków SHA-256 nagłówka>`, `now:<12 znaków SHA-256 tytułu>`, `now-pending:<12 znaków SHA-256 nagłówka>` |
| `wybor` | `zatwierdz`, `odrzuc`, `przyjete`, `A`, `B`, `C` |
| `uwaga` | tekst do 4000 znaków |
| `odcisk` | SHA-256 treści pozycji w chwili wyboru |
| `tytul`, `tekst` | migawka widoczna w „Rozstrzygnięte”, gdy źródło już pozycji nie ma |
| `kotwica` | gdzie nanieść: `plik` oraz `slug`, `naglowek` albo `linia` |

Obowiązuje ostatni rekord pozycji. To samo kliknięcie drugi raz (ten sam wybór, uwaga i treść) zwraca
istniejący rekord (`powtorzenie: true`). Plik stanu z wersją wyższą niż zna kod jest tylko do odczytu
(zapis kończy się błędem 500, plik nietknięty), a kod wersji 1 zachowuje klucz `wybory` przy zapisie.

## Adresy (wszystkie za logowaniem)

| Adres | Metoda | Działanie |
|---|---|---|
| `/?przesluchania=wybor` | POST | `{id_pozycji, wybor, uwaga}` → rekord; 400 zły wybór, 404 pozycji nie ma w źródle |
| `/?przesluchania=przeczytane` | POST | `{id_pozycji}` raportu automatu → znika z „Raporty do przeczytania” (bez rekordu wyboru) |
| `/?przesluchania=eksport` | GET | JSON: stare pola + `wybory` (nieprzeniesione, z `kotwica` i `przeniesione`); `ostatnie_id` = największy id obu list |
| `/?przesluchania=potwierdz` | POST | `{"do": ostatnie_id}` — przeniesione do vaulta (idempotentne) |
| `/?przesluchania=licznik` | GET | `do_decyzji` (notatki + pozycje) i pola szczegółowe |
| `/?przesluchania=ui` | POST | `{zakladka}`, `{tylko_nagrane}`, `{zwiniete: [...]}`, `{zrodlo: ...}` |

## Co nanosi agent laptopa

| Źródło | Zatwierdź | Odrzuć | Inne |
|---|---|---|---|
| skill-scout | w elemencie `slug` w `_proposed.json` dopisz `"status": "zatwierdzone"` i `"decyzja_czas"` | dopisz `"status": "odrzucone"` (element zostaje) | — |
| reflect | w sekcji zmień `- [ ] ✅ Zatwierdź tę zmianę` na `- [x]`, potem `/reflect apply` | usuń sekcję albo przenieś pod `### Odrzucone w tym przebiegu (świadomie)` | — |
| NOW.md | linię `- [ ] **…**` zmień na `- [x] … — decyzja: zatwierdzone (<data>)` | jw. z `decyzja: odrzucone` | wariant A/B/C: `decyzja: wariant <litera>` |
| NOW-pending | — | — | `przyjete`: usuń wpis `###` |

Element `_proposed.json` z polem `status` albo `decyzja` równym `zatwierdzone`/`odrzucone` nie jest
już pozycją. Dla pozostałych źródeł zmiana treści pozycji po decyzji przywraca ją do „Do decyzji” ze
znacznikiem „treść zmieniona po decyzji” — dlatego agent nanosi dokładnie zmianę minimalną z wydruku.

`pobierz_decyzje.py`: hasło ze zmiennej `ANBERFILES_HASLO` albo z terminala (nigdy argument),
adres `--adres` / `ANBERFILES_ADRES` (domyślnie `http://100.67.174.47:8790`), limit czasu
`--limit-s` (20 s) na każde żądanie. Kody wyjścia: 0 pobrane, 2 zły argument, 3 logowanie
(także 429 — za dużo prób), 4 sieć / limit czasu, 5 niezgodny schemat, 6 potwierdzenie odrzucone.

## Wdrożenie na Jarvisie

`docs/odtworzenie-po-awarii.md` każe robić `git pull` jako `anberfiles`, ale `/srv/anberfiles/kod` należy do
roota i stoi na odłączonym HEAD (stan 10.10.2026: `a567a59`, reflog `checkout: moving from main to a567a59`).
Wdrożenie zgodne z praktyką (wymaga `sudo`): zapisz SHA sprzed wdrożenia, potem jako root
`git -C /srv/anberfiles/kod fetch` i `git -C /srv/anberfiles/kod checkout --detach <SHA>`, `chown -R root:root`,
`systemctl restart anberfiles`. Przed restartem bramka: kolejka lektora (`kolejka_lektora` z
`/etc/anberfiles/jarvis.conf`) pusta i Puls (`http://127.0.0.1:7777/api/status`) bez biegu innego niż własny.
Cofnięcie: `checkout --detach <SHA sprzed wdrożenia>` i restart — plik stanu zachowuje `wybory` (test
`test_cofniecie_kodu_do_v1_zachowuje_wybory`).

## Odbiór 98c (10.10.2026)

Na instancji testowej (kopia klonu vaulta w `/tmp`, tylko do odczytu, port 8798 na 127.0.0.1, własny katalog
danych, decyzja kliknięta wyłącznie na pozycji testowej dopisanej do kopii `NOW.md`): lista, wybór,
powtórzenie bez nowego rekordu, eksport, `pobierz_decyzje.py`, restart z zachowaniem stanu, nagranie atrapą
lektora. Zrzuty Chromium bez okna (pakiety `chromium` rozpakowane bez roota, `--headless=new --screenshot`):
`docs/odbior/2026-10-10-98-lista.png` (sklejone: raporty, każde źródło osobnym filtrem, „Rozstrzygnięte”) i
`docs/odbior/2026-10-10-98-lista-telefon.png` (szerokość 390 px). Puste kwadraty w miejscu emoji: Chromium bez
okna na Jarvisie nie ma kroju emoji.
