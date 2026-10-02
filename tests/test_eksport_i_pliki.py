"""Eksport DOCX tylko skryptami z katalogu eksportu (B5), pliki z kropką
niedostępne po adresie (C1), ZIP folderu bez dowiązań i z limitem (C2).

Testy budują własne drzewo w tmp_path (ustawienia konsoli Anbernic)."""
import io
import os
import sys
import zipfile

import aiohttp
import pytest

from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic

AU = aiohttp.BasicAuth('anbernic', HASLO)

# Atrapa skryptu eksportu: argumenty jak export_to_docx.py (md -o out --base-dir b);
# zapisuje wynik i znacznik uruchomienia obok siebie.
ATRAPA_EKSPORTU = r'''
import argparse
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument('md'); ap.add_argument('-o'); ap.add_argument('--base-dir')
a = ap.parse_args()
Path(a.o).write_bytes(b'PK-atrapa-docx')
Path(__file__).with_suffix('.uruchomiony').write_text(Path(__file__).name, encoding='utf-8')
'''


def _anbernic(tmp_path, serwer: str = ''):
    conf = zbuduj_anbernic(tmp_path)
    if serwer:
        conf.write_text('[serwer]\n' + serwer + '\n' + conf.read_text(encoding='utf-8'),
                        encoding='utf-8')
    return wczytaj(conf)


# ── B5: eksport DOCX ─────────────────────────────────────────────────────────

def _projekt(tmp_path, naglowek: str = ''):
    korzen = tmp_path / 'sprawozdania'
    proj = korzen / 'projekty' / 'p1'
    (proj / 'processed').mkdir(parents=True)
    (proj / 'szablon').mkdir()
    (proj / 'processed' / 'doc.md').write_text(f'{naglowek}\n# Tytuł\n\nTreść.\n',
                                               encoding='utf-8')
    eksport = korzen / 'EXPORT'
    eksport.mkdir(parents=True, exist_ok=True)
    (eksport / 'export_to_docx.py').write_text(ATRAPA_EKSPORTU, encoding='utf-8')
    (eksport / 'export_inny.py').write_text(ATRAPA_EKSPORTU, encoding='utf-8')
    (proj / 'szablon' / 'zly.py').write_text(ATRAPA_EKSPORTU, encoding='utf-8')
    (proj / 'szablon' / 'export_zly.py').write_text(ATRAPA_EKSPORTU, encoding='utf-8')
    return korzen, proj, eksport


def _eksport(k):
    async def sc(cl):
        r = await cl.get('/projekty/p1/processed/doc.md?docx=1', auth=AU)
        return r.status, await r.read()
    return uruchom(k, sc)


@pytest.mark.parametrize('dyrektywa', ['zly', 'zly.py', 'szablon/zly.py',
                                       '../szablon/zly.py', 'export_zly.py'])
def test_eksport_dyrektywa_ze_skryptem_projektu_400(tmp_path, dyrektywa):
    _, proj, eksport = _projekt(tmp_path, f'<!-- eksporter: {dyrektywa} -->')
    k = _anbernic(tmp_path)
    st, _ = _eksport(k)
    assert st == 400
    assert not list(proj.rglob('*.uruchomiony'))          # skrypt projektu NIE ruszył
    assert not list(eksport.glob('*.uruchomiony'))
    assert not (proj / 'exports' / 'doc.docx').exists()


def test_eksport_konfiguracja_szablonu_ze_skryptem_projektu_400(tmp_path):
    _, proj, eksport = _projekt(tmp_path)
    (proj / 'szablon' / 'eksporter.conf').write_text('eksporter = zly\n', encoding='utf-8')
    k = _anbernic(tmp_path)
    st, _ = _eksport(k)
    assert st == 400
    assert not list(proj.rglob('*.uruchomiony'))
    assert not list(eksport.glob('*.uruchomiony'))


@pytest.mark.parametrize('dyrektywa', ['inny', 'export_inny.py'])
def test_eksport_nazwa_z_katalogu_eksportu_uruchomiona(tmp_path, dyrektywa):
    """Kontrola rozróżniająca: nazwany eksporter z katalogu eksportu działa."""
    _, proj, eksport = _projekt(tmp_path, f'<!-- eksporter: {dyrektywa} -->')
    k = _anbernic(tmp_path)
    st, tresc = _eksport(k)
    assert st == 200 and tresc == b'PK-atrapa-docx'
    assert (eksport / 'export_inny.uruchomiony').exists()
    assert not (eksport / 'export_to_docx.uruchomiony').exists()


def test_eksport_domyslny_bez_dyrektywy(tmp_path):
    _, proj, eksport = _projekt(tmp_path)
    k = _anbernic(tmp_path)
    st, tresc = _eksport(k)
    assert st == 200 and tresc == b'PK-atrapa-docx'
    assert (eksport / 'export_to_docx.uruchomiony').exists()
    assert (proj / 'exports' / 'doc.docx').exists()


def test_eksport_konfiguracja_szablonu_z_nazwa_z_listy(tmp_path):
    _, proj, eksport = _projekt(tmp_path)
    (proj / 'szablon' / 'eksporter.conf').write_text('# szablon\neksporter = inny\n',
                                                     encoding='utf-8')
    k = _anbernic(tmp_path)
    st, _ = _eksport(k)
    assert st == 200
    assert (eksport / 'export_inny.uruchomiony').exists()


# ── C1: pliki i katalogi z kropką ────────────────────────────────────────────

def _ukryte(tmp_path):
    korzen = tmp_path / 'sprawozdania'
    (korzen / '.kosz').mkdir(parents=True, exist_ok=True)
    (korzen / '.kosz' / 'usuniety.txt').write_text('usunięte', encoding='utf-8')
    (korzen / 'x' / '.git').mkdir(parents=True)
    (korzen / 'x' / '.git' / 'config').write_text('[core]', encoding='utf-8')
    (korzen / 'x' / 'jawny.txt').write_text('jawny', encoding='utf-8')
    (korzen / 'x' / '.ukryty.txt').write_text('ukryty', encoding='utf-8')
    return korzen


@pytest.mark.parametrize('adres', ['/.kosz/', '/.kosz/usuniety.txt', '/x/.git/config',
                                   '/x/.ukryty.txt', '/x/.git/config?dl=1',
                                   '/x/.git/?zip=1', '/x/%2Egit/config'])
def test_kropka_w_sciezce_403(tmp_path, adres):
    from yarl import URL
    _ukryte(tmp_path)
    k = _anbernic(tmp_path)

    async def sc(cl):
        r = await cl.get(URL(adres, encoded=True), auth=AU)
        return r.status, await r.read()
    st, tresc = uruchom(k, sc)
    assert st == 403, (adres, st)
    assert b'[core]' not in tresc and 'usunięte'.encode() not in tresc


@pytest.mark.parametrize('metoda,adres', [
    ('POST', '/x/.ukryty.txt?rename=widoczny.txt'),
    ('DELETE', '/x/.ukryty.txt'),
    ('POST', '/x/.git/?mkdir=nowy'),
    ('POST', '/x/.ukryty.txt?lektor=1')])
def test_kropka_w_sciezce_403_zapis(tmp_path, metoda, adres):
    korzen = _ukryte(tmp_path)
    k = _anbernic(tmp_path)

    async def sc(cl):
        return (await cl.request(metoda, adres, auth=AU)).status
    assert uruchom(k, sc) == 403
    assert (korzen / 'x' / '.ukryty.txt').exists()
    assert not (korzen / 'x' / '.git' / 'nowy').exists()


def test_zwykly_plik_200(tmp_path):
    """Kontrola rozróżniająca: zwykły plik i katalog obok ukrytych — 200."""
    _ukryte(tmp_path)
    k = _anbernic(tmp_path)

    async def sc(cl):
        a = await cl.get('/x/jawny.txt', auth=AU)
        b = await cl.get('/x/', auth=AU)
        return a.status, await a.text(), b.status, await b.text()
    st_a, tresc, st_b, lst = uruchom(k, sc)
    assert st_a == 200 and tresc == 'jawny'
    assert st_b == 200 and 'jawny.txt' in lst and '.ukryty' not in lst


# ── C2: ZIP folderu ──────────────────────────────────────────────────────────

def _symlink_albo_pomin(cel, link, katalog=False):
    try:
        os.symlink(cel, link, target_is_directory=katalog)
    except (OSError, NotImplementedError) as e:
        if sys.platform == 'win32':
            pytest.skip(f'brak uprawnień do dowiązań na Windows: {e}')
        raise


def _zip(k, adres='/paczka/?zip=1'):
    async def sc(cl):
        r = await cl.get(adres, auth=AU)
        return r.status, await r.read()
    return uruchom(k, sc)


def test_zip_pomija_dowiazania_poza_katalog(tmp_path):
    korzen = tmp_path / 'sprawozdania'
    (korzen / 'paczka').mkdir(parents=True)
    (korzen / 'paczka' / 'zwykly.txt').write_text('zwykly', encoding='utf-8')
    sekret = tmp_path / 'sekret.txt'
    sekret.write_text('TAJNE-POZA-KORZENIEM', encoding='utf-8')
    (tmp_path / 'obcy').mkdir()
    (tmp_path / 'obcy' / 'plik.txt').write_text('TAJNE-KATALOG', encoding='utf-8')
    _symlink_albo_pomin(sekret, korzen / 'paczka' / 'link.txt')
    _symlink_albo_pomin(tmp_path / 'obcy', korzen / 'paczka' / 'linkdir', katalog=True)
    k = _anbernic(tmp_path)
    st, dane = _zip(k)
    assert st == 200
    with zipfile.ZipFile(io.BytesIO(dane)) as zf:
        nazwy = zf.namelist()
        tresci = b''.join(zf.read(n) for n in nazwy)
    assert 'paczka/zwykly.txt' in nazwy                 # kontrola: zwykły plik jest
    assert not any('link' in n for n in nazwy), nazwy
    assert b'TAJNE' not in tresci


def test_zip_ponad_limit_413(tmp_path):
    korzen = tmp_path / 'sprawozdania'
    (korzen / 'paczka').mkdir(parents=True)
    (korzen / 'paczka' / 'duzy.bin').write_bytes(b'a' * (2 * 1024 * 1024))
    k = _anbernic(tmp_path, 'limit_zip_mb = 1')
    st, _ = _zip(k)
    assert st == 413
    assert not list(k.katalog_zip_tmp.glob('*.zip'))     # plik tymczasowy sprzątnięty


def test_zip_ponizej_limitu_200(tmp_path):
    """Kontrola rozróżniająca: ten sam katalog przy domyślnym limicie (2 GiB)."""
    korzen = tmp_path / 'sprawozdania'
    (korzen / 'paczka').mkdir(parents=True)
    (korzen / 'paczka' / 'duzy.bin').write_bytes(b'a' * (2 * 1024 * 1024))
    k = _anbernic(tmp_path)
    assert k.limit_zip_mb == 2048
    st, dane = _zip(k)
    assert st == 200
    with zipfile.ZipFile(io.BytesIO(dane)) as zf:
        assert zf.read('paczka/duzy.bin') == b'a' * (2 * 1024 * 1024)
