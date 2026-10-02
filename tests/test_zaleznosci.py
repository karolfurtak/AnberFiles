"""B7: zależności przypięte (==) i ze skrótami sha256 — także przechodnie.

CI instaluje `pip install --require-hashes -r requirements.txt`: wpis bez skrótu
albo plik o innym skrócie = błąd instalacji. Ten test pilnuje kształtu pliku
(wpis bez == albo bez --hash, zależność z requirements.in nieobecna w .txt)."""
import re

from conftest import REPO


def _wpisy(tekst: str) -> dict:
    """requirements.txt → {nazwa: (wersja, liczba skrótów)} (wiersze z \\ złączone)."""
    tekst = re.sub(r'\\\s*\n', ' ', tekst)
    wynik = {}
    for linia in tekst.splitlines():
        linia = linia.split('#', 1)[0].strip()
        if not linia or linia.startswith('-'):
            continue
        m = re.match(r'([A-Za-z0-9_.-]+)\s*(==\s*([^\s;]+))?', linia)
        assert m, linia
        nazwa = m.group(1).lower().replace('_', '-')
        wynik[nazwa] = (m.group(3), len(re.findall(r'--hash=sha256:[0-9a-f]{64}', linia)))
    return wynik


def _sprawdz(tekst: str) -> list:
    bledy = []
    for nazwa, (wersja, skroty) in _wpisy(tekst).items():
        if not wersja:
            bledy.append(f'{nazwa}: brak ==wersja')
        if skroty == 0:
            bledy.append(f'{nazwa}: brak --hash=sha256')
    return bledy


def test_kazda_zaleznosc_przypieta_ze_skrotem():
    tekst = (REPO / 'requirements.txt').read_text(encoding='utf-8')
    assert _wpisy(tekst), 'pusty requirements.txt'
    assert _sprawdz(tekst) == []


def test_wejscie_pokryte_przez_plik_skompilowany():
    wejscie = (REPO / 'requirements.in').read_text(encoding='utf-8')
    nazwy = {re.match(r'[A-Za-z0-9_.-]+', ln.strip()).group(0).lower().replace('_', '-')
             for ln in wejscie.splitlines()
             if ln.strip() and not ln.strip().startswith('#')}
    wpisy = _wpisy((REPO / 'requirements.txt').read_text(encoding='utf-8'))
    assert nazwy and nazwy <= set(wpisy), nazwy - set(wpisy)


def test_kontrola_wpis_bez_skrotu_wykryty():
    """Kontrola rozróżniająca: dawny kształt pliku (same nazwy) → błędy."""
    assert _sprawdz('aiohttp\nmarkdown==3.10.3\n') == [
        'aiohttp: brak ==wersja', 'aiohttp: brak --hash=sha256',
        'markdown: brak --hash=sha256']


def test_narzedzia_dev_bez_wlaczenia_listy_glownej():
    """requirements-dev.txt instalowany drugim poleceniem bez --require-hashes —
    nie może dociągać requirements.txt (pip wymusiłby skróty dla wszystkiego)."""
    dev = (REPO / 'requirements-dev.txt').read_text(encoding='utf-8')
    wiersze = [ln.split('#', 1)[0].strip() for ln in dev.splitlines()]
    assert not any(w.startswith(('-r', '--requirement', '-c')) for w in wiersze)
