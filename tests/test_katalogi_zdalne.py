"""Duże i zdalne katalogi: eksplorator nie rozwija wskazanych poddrzew
(bez_drzewa), a odczyt katalogu ma limit czasu (limit_odczytu_katalogu_s),
żeby zawieszony zdalny system plików nie blokował serwera.

Testy budują własne katalogi w tmp_path."""
import asyncio
import dataclasses
import errno
import time

import aiohttp
import pytest

from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic

AU = aiohttp.BasicAuth('anbernic', HASLO)


def _konf(tmp_path, serwer: str = '', katalogi: str = ''):
    conf = zbuduj_anbernic(tmp_path)
    tekst = conf.read_text(encoding='utf-8')
    tekst = tekst.replace('[katalogi]\n', '[katalogi]\n' + katalogi + '\n', 1)
    conf.write_text('[serwer]\n' + serwer + '\n' + tekst, encoding='utf-8')
    return wczytaj(conf)


def _drzewo(tmp_path):
    root = tmp_path / 'sprawozdania'
    (root / 'duzy' / 'wnuk-ukryty').mkdir(parents=True)
    (root / 'maly' / 'wnuk-widoczny').mkdir(parents=True)
    (root / 'maly' / 'plik.txt').write_text('x', encoding='utf-8')
    return root


# ── ustawienia ───────────────────────────────────────────────────────────────

def test_ustawienia_domyslne_nowych_kluczy(tmp_path):
    k = _konf(tmp_path)
    assert k.bez_drzewa == ()
    assert k.limit_odczytu_katalogu_s == 3


def test_ustawienia_wczytanie_nowych_kluczy(tmp_path):
    k = _konf(tmp_path, serwer='limit_odczytu_katalogu_s = 0.5',
              katalogi='bez_drzewa = a, b c')
    assert k.limit_odczytu_katalogu_s == 0.5
    assert k.bez_drzewa == ('a', 'b c')


@pytest.mark.parametrize('zla', ['../x', 'a/b', r'a\b', '..'])
def test_bez_drzewa_odrzuca_sciezki(tmp_path, zla):
    import konfiguracja
    with pytest.raises(konfiguracja.BladKonfiguracji):
        _konf(tmp_path, katalogi=f'bez_drzewa = ok, {zla}')


@pytest.mark.parametrize('zly', ['0', '-1', 'abc', 'nan', 'inf'])
def test_limit_odczytu_wymaga_liczby_dodatniej(tmp_path, zly):
    import konfiguracja
    with pytest.raises(konfiguracja.BladKonfiguracji):
        _konf(tmp_path, serwer=f'limit_odczytu_katalogu_s = {zly}')


def test_przyklad_jarvis_ma_nowe_klucze():
    import konfiguracja
    assert 'bez_drzewa' in konfiguracja.KLUCZE['katalogi']
    assert 'limit_odczytu_katalogu_s' in konfiguracja.KLUCZE['serwer']


# ── bez_drzewa w eksploratorze ───────────────────────────────────────────────

def test_eksplorator_nie_rozwija_katalogu_z_listy(tmp_path, monkeypatch):
    import server
    _drzewo(tmp_path)
    k = _konf(tmp_path, katalogi='bez_drzewa = duzy')
    wolane = []
    oryginal = server._dir_children

    def liczone(p):
        wolane.append(p.name)
        return oryginal(p)
    monkeypatch.setattr(server, '_dir_children', liczone)

    async def sc(cl):
        r = await cl.get('/?explorer=1', auth=AU)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 200
    assert 'data-p="/duzy/"' in html            # sam wpis zostaje
    assert 'wnuk-ukryty' not in html            # bez dzieci
    assert 'duzy' not in wolane                 # iterdir na nim nie wołany
    assert 'wnuk-widoczny' in html              # spoza listy rozwija się jak dotąd
    assert 'maly' in wolane


def test_listing_katalogu_z_listy_dziala_normalnie(tmp_path):
    _drzewo(tmp_path)
    k = _konf(tmp_path, katalogi='bez_drzewa = duzy')

    async def sc(cl):
        r = await cl.get('/duzy/', auth=AU)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 200 and 'wnuk-ukryty' in html


# ── limit czasu odczytu katalogu ─────────────────────────────────────────────

def test_zwykly_katalog_200(tmp_path):
    _drzewo(tmp_path)
    k = _konf(tmp_path)

    async def sc(cl):
        r = await cl.get('/maly/', auth=AU)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 200 and 'plik.txt' in html


def test_katalog_nie_odpowiada_504_a_serwer_zyje(tmp_path, monkeypatch):
    import server
    _drzewo(tmp_path)
    k = dataclasses.replace(_konf(tmp_path), limit_odczytu_katalogu_s=0.3)
    oryginal = server._proba_katalogu

    def zawieszona(p):
        if p.name == 'duzy':
            time.sleep(2)
        return oryginal(p)
    monkeypatch.setattr(server, '_proba_katalogu', zawieszona)

    async def sc(cl):
        t0 = time.monotonic()
        wolne = asyncio.ensure_future(cl.get('/duzy/', auth=AU))
        await asyncio.sleep(0.1)
        t1 = time.monotonic()
        inny = await cl.get('/maly/', auth=AU)      # w trakcie zawieszenia
        czas_innego = time.monotonic() - t1
        r = await wolne
        return (r.status, await r.text(), time.monotonic() - t0,
                inny.status, czas_innego)
    st, html, czas, st_inny, czas_innego = uruchom(k, sc)
    assert st == 504
    assert czas < 0.3 + 1
    assert 'Katalog nie odpowiada w 0.3 s' in html
    assert 'Spróbuj ponownie za chwilę' in html
    assert 'href="/"' in html                       # katalog nadrzędny
    assert st_inny == 200 and czas_innego < 0.25    # pętla nie jest zablokowana


def test_504_trafia_do_rejestru_zdarzen(tmp_path, monkeypatch):
    import server
    _drzewo(tmp_path)
    k = dataclasses.replace(_konf(tmp_path), limit_odczytu_katalogu_s=0.2)
    monkeypatch.setattr(server, '_proba_katalogu', lambda p: time.sleep(1))

    async def sc(cl):
        return (await cl.get('/maly/', auth=AU)).status
    assert uruchom(k, sc) == 504
    wpisy = k.rejestr_zdarzen.read_text(encoding='utf-8')
    assert '\twarn\t' in wpisy and 'maly' in wpisy


def test_oserror_503_z_przyczyna_bez_sladu_stosu(tmp_path, monkeypatch):
    import server
    _drzewo(tmp_path)
    k = _konf(tmp_path)

    def blad(p):
        raise OSError(errno.ENOTCONN, 'Transport endpoint is not connected')
    monkeypatch.setattr(server, '_proba_katalogu', blad)

    async def sc(cl):
        r = await cl.get('/maly/', auth=AU)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 503
    assert 'not connected' in html
    assert 'Traceback' not in html
    assert 'Spróbuj ponownie za chwilę' in html
    assert 'href="/"' in html


def test_zawieszone_rozwiazywanie_sciezki_tez_ma_limit(tmp_path, monkeypatch):
    """resolve()/exists() leżą w tej samej próbie — zawieszenie zdalnego dysku
    na etapie ustalania ścieżki też kończy się 504, nie wiszącym serwerem."""
    import server
    _drzewo(tmp_path)
    k = dataclasses.replace(_konf(tmp_path), limit_odczytu_katalogu_s=0.3)
    monkeypatch.setattr(server, '_zbadaj_sciezke', lambda raw: time.sleep(2))

    async def sc(cl):
        t0 = time.monotonic()
        r = await cl.get('/maly/', auth=AU)
        return r.status, time.monotonic() - t0
    st, czas = uruchom(k, sc)
    assert st == 504 and czas < 1.3
