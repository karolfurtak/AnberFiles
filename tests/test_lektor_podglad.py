"""Lektor w PODGLĄDZIE dokumentu (?view=1): przycisk generowania z wyborem
silnika, odtwarzacz po nagraniu, nagrania bez kolizji nazw.

Zgłoszenie z tabletu (02.10): podgląd .md na Jarvisie (katalog tylko do
odczytu) miał pasek bez lektora — przycisk był wyłącznie w liście folderu.
Testy budują własne drzewo w tmp_path."""
import os
import stat
import sys

import aiohttp
import pytest

from conftest import (HASLO, czekaj_na_lektora, uruchom, wczytaj,
                      zbuduj_anbernic)
from conftest import zbuduj_jarvis as _zbuduj_jarvis


def zbuduj_jarvis(tmp_path, **nadpisz):
    nadpisz.setdefault('logowanie', 'basic')
    return _zbuduj_jarvis(tmp_path, **nadpisz)


def _drzewo(tmp_path):
    """Dwa pliki notatka.md w różnych katalogach vaulta + .docx i .txt."""
    korzen = tmp_path / 'srv' / 'korzen'
    a = korzen / 'vault' / 'a'
    b = korzen / 'vault' / 'b'
    for d in (a, b):
        d.mkdir(parents=True)
    (a / 'notatka.md').write_text('# A\n\nZdanie z katalogu a.\n', encoding='utf-8')
    (b / 'notatka.md').write_text('# B\n\nZdanie z katalogu b.\n', encoding='utf-8')
    (a / 'tekst.txt').write_text('Tekst.', encoding='utf-8')
    return korzen, a, b


def _tylko_do_odczytu(*katalogi):
    """Katalog źródeł także fizycznie bez prawa zapisu (tam, gdzie chmod działa)."""
    if sys.platform == 'win32' or (hasattr(os, 'geteuid') and os.geteuid() == 0):
        return []
    for d in katalogi:
        d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return list(katalogi)


def _przywroc(katalogi):
    for d in katalogi:
        d.chmod(0o755)


# (a) przycisk lektora w podglądzie .md w katalogu tylko do odczytu
def test_podglad_md_tylko_odczyt_ma_przycisk_lektora(tmp_path, auth):
    korzen, a, b = _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    ro = _tylko_do_odczytu(a, b)

    async def sc(cl):
        return await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
    try:
        html = uruchom(k, sc)
    finally:
        _przywroc(ro)
    assert 'id="lekgen"' in html, 'brak przycisku lektora w podglądzie .md'
    assert 'data-n="notatka.md"' in html
    # wybór silnika: domyślny z ustawień lektora (brak pliku → wybór czytaj_tts.py)
    assert 'window._LEK_SILNIK=' in html
    assert "opuszcza urządzenie" in html      # napis przy wyborze edge (B8)


# (b) dwa pliki o tej samej nazwie w różnych katalogach → różne nagrania
def test_ta_sama_nazwa_rozne_katalogi_rozne_nagrania(tmp_path, auth):
    korzen, a, b = _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    ro = _tylko_do_odczytu(a, b)

    async def sc(cl):
        out = []
        for d in ('a', 'b'):
            r = await cl.post(f'/vault/{d}/notatka.md?lektor=1&fmt=mp3&queue=1',
                              auth=auth)
            assert r.status == 202, await r.text()
            out.append((await r.json())['out'])
        await czekaj_na_lektora(cl, auth)
        va = await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
        vb = await (await cl.get('/vault/b/notatka.md?view=1', auth=auth)).text()
        ra = await (await cl.get('/vault/a/notatka.md?read=1', auth=auth)).text()
        return va, vb, ra
    try:
        va, vb, ra = uruchom(k, sc)
    finally:
        _przywroc(ro)
    na = korzen / 'lektor' / 'vault' / 'a' / 'notatka_lektor.mp3'
    nb = korzen / 'lektor' / 'vault' / 'b' / 'notatka_lektor.mp3'
    assert na.exists() and nb.exists() and na != nb
    assert not (a / 'notatka_lektor.mp3').exists()
    assert '/lektor/vault/a/notatka_lektor.mp3' in va
    assert '/lektor/vault/b/notatka_lektor.mp3' in vb
    assert '/lektor/vault/b/' not in va
    assert '/lektor/vault/a/notatka_lektor.mp3' in ra      # ?read znajduje nagranie


# (c) podłożone nagranie w katalogu lektora → odtwarzacz w podglądzie
def test_podlozone_nagranie_daje_odtwarzacz(tmp_path, auth):
    korzen, a, b = _drzewo(tmp_path)
    cel = korzen / 'lektor' / 'vault' / 'a' / 'notatka_lektor.mp3'
    cel.parent.mkdir(parents=True)
    cel.write_bytes(b'ID3' + bytes(64))
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        va = await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
        vb = await (await cl.get('/vault/b/notatka.md?view=1', auth=auth)).text()
        return va, vb
    va, vb = uruchom(k, sc)
    assert 'id="lek"' in va and '/lektor/vault/a/notatka_lektor.mp3' in va
    assert 'id="lekgen"' in va                # nadal można wygenerować ponownie
    assert 'id="lek"' not in vb               # b nie ma nagrania — bez odtwarzacza


# wybór silnika trafia do programu lektora (--silnik), domyślnie bez parametru
def test_wybrany_silnik_trafia_do_lektora(tmp_path, auth):
    korzen, a, b = _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    argv = tmp_path / 'srv' / 'eksport' / 'ostatnie_argv.txt'

    async def sc(cl):
        r = await cl.post('/vault/a/notatka.md?lektor=1&fmt=mp3&silnik=piper', auth=auth)
        assert r.status == 202, await r.text()
        await czekaj_na_lektora(cl, auth)
        s1 = argv.read_text(encoding='utf-8')
        r = await cl.post('/vault/b/notatka.md?lektor=1&fmt=mp3&silnik=zly', auth=auth)
        assert r.status == 202, await r.text()
        await czekaj_na_lektora(cl, auth)
        return s1, argv.read_text(encoding='utf-8')
    s1, s2 = uruchom(k, sc)
    assert '--silnik piper' in s1
    assert '--silnik' not in s2               # nieznana wartość → wg ustawień lektora


def test_domyslny_silnik_z_ustawien_lektora(tmp_path, auth):
    _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    (tmp_path / 'srv' / 'eksport' / 'lektor-ustawienia.conf').write_text(
        'format = mp3\nsilnik = piper   # lokalnie\n', encoding='utf-8')

    async def sc(cl):
        return await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
    assert 'window._LEK_SILNIK="piper"' in uruchom(k, sc)


# (d) kontrola rozróżniająca: moduł lektora wyłączony → brak przycisku
def test_lektor_wylaczony_brak_przycisku_w_podgladzie(tmp_path, auth):
    _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path, lektor='nie'))

    async def sc(cl):
        return await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
    html = uruchom(k, sc)
    assert 'id="lekgen"' not in html
    assert '#e-lektor' not in html


# (d) Anbernic: przycisk w podglądzie, nagranie obok dokumentu jak dotąd
def test_anbernic_przycisk_i_nagranie_obok_dokumentu(tmp_path):
    k = wczytaj(zbuduj_anbernic(tmp_path))
    korzen = tmp_path / 'sprawozdania'
    (korzen / 'x.md').write_text('Tekst.', encoding='utf-8')
    au = aiohttp.BasicAuth('anbernic', HASLO)

    async def sc(cl):
        v1 = await (await cl.get('/x.md?view=1', auth=au)).text()
        r = await cl.post('/x.md?lektor=1&fmt=mp3', auth=au)
        assert r.status == 202
        await czekaj_na_lektora(cl, au, korzen / 'x_lektor.mp3')
        v2 = await (await cl.get('/x.md?view=1', auth=au)).text()
        return v1, v2
    v1, v2 = uruchom(k, sc)
    assert 'id="lekgen"' in v1
    assert (korzen / 'x_lektor.mp3').exists()
    assert 'id="lek"' in v2 and '/x_lektor.mp3' in v2


@pytest.mark.parametrize('nazwa', ['tekst.txt'])
def test_podglad_txt_nie_psuje_sie(tmp_path, auth, nazwa):
    """.txt otwiera się wprost (bez strony podglądu) — lektor zostaje w liście."""
    _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        lst = await (await cl.get('/vault/a/', auth=auth)).text()
        return lst
    lst = uruchom(k, sc)
    assert f'class="dl lek" data-n="{nazwa}"' in lst


def test_podglad_docx_ma_przycisk_lektora(tmp_path, auth):
    """Podgląd .docx (strona-opakowanie) też ma przycisk; .doc — nie (lektor go nie czyta)."""
    korzen, a, b = _drzewo(tmp_path)
    (a / 'raport.docx').write_bytes(b'PK\x03\x04atrapa')
    (a / 'stary.doc').write_bytes(b'atrapa')
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        d1 = await (await cl.get('/vault/a/raport.docx?view=1', auth=auth)).text()
        d2 = await (await cl.get('/vault/a/stary.doc?view=1', auth=auth)).text()
        return d1, d2
    d1, d2 = uruchom(k, sc)
    assert 'id="lekgen"' in d1 and 'data-n="raport.docx"' in d1
    assert 'id="lekgen"' not in d2


# (f) F5 w trakcie generowania (zgłoszenie Karola 02.10: „jeśli pomyłkowo
# odświeżę, znika pasek postępu") — stan zadania TEGO pliku jest w HTML od
# pierwszego wyświetlenia, a ponowne zlecenie nie dubluje pracy.

ATRAPA_WOLNA = r"""
import argparse, time
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument('input'); ap.add_argument('-o', '--output')
ap.add_argument('--format'); ap.add_argument('--opisy'); ap.add_argument('--silnik')
a = ap.parse_args()
time.sleep(4)
Path(a.output).write_bytes(b'ID3' + bytes(64))
"""


def test_po_odswiezeniu_podglad_pokazuje_postep_zadania_tego_pliku(tmp_path, auth, monkeypatch):
    import server
    korzen, a, b = _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    monkeypatch.setattr(server, '_lektor_progress',
                        lambda: {'pct': 42, 'chunk': 3, 'chunks': 7, 'out': 'x'})

    async def sc(cl):
        cel = (a / 'notatka.md').resolve()
        server._LEKTOR_QUEUE.append({
            'id': 77, 'out': str(korzen / 'lektor' / 'notatka_lektor.mp3'),
            'src': str(cel), 'plik': str(cel), 'fmt': 'mp3', 'state': 'running',
            'cancelled': False, 'proc': None, 'started': 0})
        try:
            ten = await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
            inny = await (await cl.get('/vault/b/notatka.md?view=1', auth=auth)).text()
            kol = await (await cl.get('/?lektorqj=1', auth=auth)).json()
        finally:
            server._LEKTOR_QUEUE.clear()
        return ten, inny, kol
    ten, inny, kol = uruchom(k, sc)
    assert 'data-job="77"' in ten
    assert '<progress id="lekpb" max="100" value="42"' in ten
    assert 'część 3/7 · 42%' in ten
    assert 'if(b.dataset.job)sledz(' in ten              # śledzenie rusza samo po F5
    # plik o tej samej nazwie w innym katalogu: bez cudzego paska
    assert 'data-job=' not in inny and 'część 3/7' not in inny
    assert kol['jobs'][0]['plik'] == 'vault/a/notatka.md'


def test_ponowne_zlecenie_pliku_w_toku_nie_dubluje_zadania(tmp_path, auth):
    korzen, a, b = _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    (tmp_path / 'srv' / 'eksport' / 'czytaj_tts.py').write_text(ATRAPA_WOLNA,
                                                                encoding='utf-8')

    async def sc(cl):
        u = '/vault/a/notatka.md?lektor=1'
        r1 = await cl.post(u + '&fmt=mp3', auth=auth)
        r2 = await cl.post(u + '&fmt=mp3&queue=1', auth=auth)    # F5 + ponowny klik
        r3 = await cl.post(u + '&fmt=wav&queue=1', auth=auth)    # inny format, ten plik
        j1, j2, j3 = await r1.json(), await r2.json(), await r3.json()
        kol = await (await cl.get('/?lektorqj=1', auth=auth)).json()
        await czekaj_na_lektora(cl, auth)
        return r1.status, j1, j2, j3, kol
    st, j1, j2, j3, kol = uruchom(k, sc)
    assert st == 202
    assert j2['status'] == 'duplikat' and j2['id'] == j1['id']
    assert j3['status'] == 'duplikat' and j3['id'] == j1['id']
    assert len(kol['jobs']) == 1
