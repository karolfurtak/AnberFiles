"""Link do strony startowej serwera („🏠 <nazwa instancji>”) — ustawienie
strona_startowa: przycisk na KAŻDEJ stronie AnberFiles (lista, drzewo, podglądy,
rejestr zdarzeń, kolejka lektora, ekran logowania i ustawienia hasła).
02.10 wieczór Karol: „muszę mieć możliwość dostać się szybko do strony
startowej" — drobny napis w wierszu pod nagłówkiem listy był niewidoczny.
Adres w testach wyłącznie dokumentacyjny (192.0.2.0/24, RFC 5737)."""
import pytest

from conftest import uruchom, wczytaj, zbuduj_anbernic, zbuduj_jarvis

ADRES = 'http://192.0.2.10:8080/'
HREF = f'href="{ADRES}"'
PRZYCISK = 'class="af-start"'


def _tresc(tmp_path):
    proj = tmp_path / 'srv' / 'korzen' / 'proj'
    proj.mkdir(parents=True)
    (proj / 'a.md').write_text('# Tytuł\n\nTekst.\n', encoding='utf-8')
    (proj / 'b.csv').write_text('x;y\n1;2\n', encoding='utf-8')


def _strony(k, auth):
    async def sc(cl):
        out = {}
        for nazwa, url in (('lista', '/proj/'), ('md', '/proj/a.md?view=1'),
                           ('csv', '/proj/b.csv?view=1'),
                           ('drzewo', '/?explorer=1'), ('zdarzenia', '/?events=1'),
                           ('kolejka_lektora', '/?lektorq=1')):
            r = await cl.get(url, auth=auth)
            assert r.status == 200, (url, r.status)
            out[nazwa] = await r.text()
        return out
    return uruchom(k, sc)


def test_link_startowy_w_liscie_i_podgladach(tmp_path, auth):
    _tresc(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path, logowanie='basic', strona_startowa=ADRES))
    assert k.strona_startowa == ADRES
    for nazwa, html in _strony(k, auth).items():
        assert HREF in html, nazwa
        assert PRZYCISK in html and '🏠 Jarvis</a>' in html, nazwa
        assert 'target="_top"' in html, nazwa          # z panelu drzewa: całe okno


def test_przycisk_na_liscie_przed_naglowkiem(tmp_path, auth):
    """Lista katalogów: przycisk na samej górze (przed ścieżką), nie w wierszu
    drobnych odnośników pod nią."""
    _tresc(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path, logowanie='basic', strona_startowa=ADRES))
    html = _strony(k, auth)['lista']
    assert html.index(PRZYCISK) < html.index('<h2>')
    assert html.count(PRZYCISK) == 1


def test_przycisk_na_ekranie_logowania_i_ustawienia_hasla(tmp_path):
    import logowanie
    k = wczytaj(zbuduj_jarvis(tmp_path, logowanie='formularz', strona_startowa=ADRES,
                              ustaw_haslo_bez_tokenu='127.0.0.0/8'), haslo='')

    async def sc(cl):
        ustaw = await (await cl.get('/', headers={'Accept': 'text/html'})).text()
        logowanie.ustaw_haslo(k.katalog_auth, 'dobre-haslo-1234')
        zaloguj = await (await cl.get('/', headers={'Accept': 'text/html'})).text()
        return ustaw, zaloguj
    ustaw, zaloguj = uruchom(k, sc, naglowek=False)
    assert 'Ustaw hasło' in ustaw and PRZYCISK in ustaw and HREF in ustaw
    assert 'name="haslo"' in zaloguj and PRZYCISK in zaloguj and HREF in zaloguj


def test_bez_ustawienia_brak_linku(tmp_path, auth):
    """Kontrola: instancja bez strona_startowa (jak Anbernic) nie ma linku."""
    _tresc(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path, logowanie='basic'))
    assert k.strona_startowa == ''
    for nazwa, html in _strony(k, auth).items():
        assert 'Strona startowa' not in html and '🏠' not in html, nazwa


def test_anbernic_domyslnie_bez_linku(tmp_path):
    assert wczytaj(zbuduj_anbernic(tmp_path)).strona_startowa == ''


def test_adres_jest_escapowany(tmp_path, auth):
    _tresc(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path, logowanie='basic',
                              strona_startowa='http://192.0.2.10/?a=1&b=2'))
    html = _strony(k, auth)['lista']
    assert 'href="http://192.0.2.10/?a=1&amp;b=2"' in html


@pytest.mark.parametrize('zly', ['javascript:alert(1)', 'data:text/html,x',
                                 'ftp://192.0.2.10/', '192.0.2.10:8080',
                                 'http://192.0.2.10/"onmouseover="x'])
def test_obcy_schemat_to_blad_ustawien(tmp_path, zly):
    import konfiguracja
    with pytest.raises(konfiguracja.BladKonfiguracji, match='strona_startowa'):
        wczytaj(zbuduj_jarvis(tmp_path, strona_startowa=zly))
