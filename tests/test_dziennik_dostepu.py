"""D2: dziennik dostępu (adres, metoda, ścieżka, kod, bajty, czas) do
katalog_danych/access.log z rotacją 5 × 5 MB; bez ciasteczek i nagłówków,
wartość parametru token= (i haslo=) zamaskowana. Anbernic (bez pliku
ustawień) — w dotychczasowym katalogu danych (/mnt/data)."""
import logging.handlers

import aiohttp

from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic, zbuduj_jarvis

AU = aiohttp.BasicAuth('anbernic', HASLO)


def _linie(k):
    p = k.katalog_danych / 'access.log'
    return p.read_text(encoding='utf-8').splitlines() if p.exists() else []


def test_zadanie_zapisane_z_kodem_i_bajtami(tmp_path):
    k = wczytaj(zbuduj_anbernic(tmp_path))
    (tmp_path / 'sprawozdania' / 'a.txt').write_text('ABCDE', encoding='utf-8')

    async def sc(cl):
        r = await cl.get('/a.txt', auth=AU)
        await r.read()
        r = await cl.get('/nie-ma.txt', auth=AU)
        await r.read()
    uruchom(k, sc)
    linie = _linie(k)
    a = [ln for ln in linie if '/a.txt' in ln]
    assert len(a) == 1
    pola = a[0].split('\t')
    # czas, adres, metoda, ścieżka, kod, bajty, czas obsługi
    assert pola[1] == '127.0.0.1' and pola[2] == 'GET' and pola[3] == '/a.txt'
    assert pola[4] == '200' and pola[5] == '5' and pola[6].endswith('ms')
    assert any('/nie-ma.txt\t404' in ln for ln in linie)


def test_token_zamaskowany_bez_ciasteczek_i_naglowkow(tmp_path):
    k = wczytaj(zbuduj_jarvis(tmp_path, logowanie='formularz'), haslo='')

    async def sc(cl):
        r = await cl.get('/__anberfiles/ustaw-haslo?token=SEKRET-TOKEN-123&x=1',
                         headers={'Accept': 'text/html', 'Cookie': 'af=CIASTKO-SEKRET',
                                  'Authorization': 'Basic U0VLUkVULUJBU0lD'})
        await r.read()
        r = await cl.get('/?haslo=HASLO-SEKRET', headers={'Accept': 'text/html'})
        await r.read()
    uruchom(k, sc)
    tekst = '\n'.join(_linie(k))
    assert 'token=***&x=1' in tekst
    assert 'haslo=***' in tekst
    for sekret in ('SEKRET-TOKEN-123', 'CIASTKO-SEKRET', 'U0VLUkVULUJBU0lD', 'HASLO-SEKRET'):
        assert sekret not in tekst


def test_rotacja_5_x_5_mb(tmp_path):
    import server
    k = wczytaj(zbuduj_anbernic(tmp_path))
    server.zastosuj_konfiguracje(k)
    opcje = server.opcje_dziennika_dostepu()
    uchwyty = opcje['access_log'].handlers
    assert len(uchwyty) == 1
    h = uchwyty[0]
    assert isinstance(h, logging.handlers.RotatingFileHandler)
    assert h.maxBytes == 5 * 1024 * 1024 and h.backupCount == 5
    assert h.baseFilename == str(k.katalog_danych / 'access.log')
    server.zamknij_dziennik_dostepu()


def test_anbernic_domyslnie_w_katalogu_danych():
    import konfiguracja
    k = konfiguracja.domyslna()
    assert str(k.dziennik_dostepu).replace('\\', '/') == '/mnt/data/access.log'
