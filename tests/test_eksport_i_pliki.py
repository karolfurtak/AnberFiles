"""Eksport DOCX tylko skryptami z katalogu eksportu (B5), pliki z kropką
niedostępne po adresie (C1), ZIP folderu bez dowiązań i z limitem (C2).

Testy budują własne drzewo w tmp_path (ustawienia konsoli Anbernic)."""
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
