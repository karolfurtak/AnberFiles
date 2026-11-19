# DŁUG — świadomie odłożone prace (AnberFiles)

Wpisy z planu ujednolicenia instancji (19.11.2026, wersja 3). Każdy: co, dlaczego
odłożone (pomiar), warunek powrotu.

## 1. Podgląd STEP→GLB, rozbiór części, pole rzutu, podgląd `.3dm`
- Było w `sprawozdania-server.py` (`step_to_glb`, `_decompose_*`, `projected_area`,
  `?glb=1`, `?area=1`; `.3dm` przez rhino3dm z CDN).
- Pomiar 19.11.2026: w `/mnt/data/sprawozdania` brak plików `.step/.stp/.glb`;
  w dzienniku usługi od 30.10 zero wywołań; `cascadio` i `trimesh` niezainstalowane
  (funkcja od przeniesienia na Wykonawcę nie działała). `.3dm`: 3 pliki, użycia
  niezmierzone; rhino3dm pobiera WebAssembly z CDN, a CSP (`connect-src 'self'`,
  audyt A3) to blokuje.
- Powrót: realne użycie + sprawdzone licencje cascadio/trimesh/rhino3dm, lokalna kopia
  bibliotek (bez CDN), moduł `app/model3d.py` z testami.

## 2. Eksport DOCX jako zadanie w tle z postępem
- Dziś: jedno żądanie z limitem 240 s (504 po przekroczeniu), wynik buforowany
  w katalogu wyniku. Twarde przerwanie po limicie to wyjątek od reguły „bez limitu
  czasu, wznawianie z punktu kontrolnego” — eksport nie ma stanu do wznowienia.
- Powrót: dokumenty, których eksport przekracza 240 s, albo potrzeba eksportu wielu
  plików naraz.

## 3. Jeden silnik lektora (`czytaj_tts.py`)
- Dwa programy: repozytorium `tools/czytaj_tts.py` (972 linie, Piper lokalnie,
  `--silnik`) i `EXPORT/czytaj_tts.py` bota (2534 linie, edge, `--lang`, `--bilingual`);
  2594 linie różnic (pomiar 19.11.2026). Serwer dopasowuje okno lektora do opcji
  skryptu instancji (`_lektor_opcje`).
- Powrót: osobne zadanie (to_do „jeden silnik lektora”) z testami obu trybów;
  zepsucie lektora kosztowałoby obie instancje.

## 4. Podatne zależności na Pythonie 3.10 (konsola Anbernic)
- pip-audit 19.11.2026: pillow 12.3.0 i soupsieve 2.10 mają znane podatności, a ich
  poprawki (pillow 13, soupsieve ≥ 3.0.2) wymagają Pythona ≥ 3.11 (soupsieve ≥ 3.11.5).
  requirements.txt rozdziela wersje znacznikami `python_full_version`; Jarvis/Wykonawca
  (3.13) mają wersje poprawione, konsola 3.10 nie.
- Powrót: Python ≥ 3.11.5 na konsoli Anbernic albo usunięcie zależności z jej instalacji.
