"""Narzędzia planu ujednolicenia: porownaj_wersje.py (załącznik A) i
zaleznosci_eksportera.py (zależności eksportera DOCX → requirements.in).

T2b na Wykonawcy: zmienna AF_KATALOG_EKSPORTU=/mnt/data/sprawozdania/EXPORT
uruchamia sprawdzenie importów w interpreterze usługi (czerwone przy braku
którejkolwiek zależności, także opcjonalnej) i eksport próbnego .md
prawdziwym skryptem (równanie, tabela, obraz SVG) bez komunikatów o pominięciu.
"""
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import REPO

NARZ = REPO / 'narzedzia'
ENV = dict(os.environ, PYTHONIOENCODING='utf-8')


def _uruchom(skrypt, *arg):
    return subprocess.run([sys.executable, str(NARZ / skrypt), *map(str, arg)],
                          capture_output=True, text=True, encoding='utf-8', env=ENV,
                          timeout=120, stdin=subprocess.DEVNULL)


# ── porownaj_wersje.py ───────────────────────────────────────────────────────

STARY = '''
def wspolna(a):
    """opis starej"""
    return a + 1   # komentarz

def zmieniona(x):
    y = x * 2
    return y

def tylko_stara():
    pass
'''
NOWY = '''
def wspolna(a):
    """inny opis"""
    return a + 1

def zmieniona(x):
    y = x * 3
    z = y
    return z

async def tylko_nowa():
    pass
'''


def test_porownaj_wersje_tabela_i_diff(tmp_path):
    (tmp_path / 's.py').write_text(STARY, encoding='utf-8')
    (tmp_path / 'n.py').write_text(NOWY, encoding='utf-8')
    r = _uruchom('porownaj_wersje.py', tmp_path / 's.py', tmp_path / 'n.py', '--diff',
                 tmp_path / 'd')
    assert r.returncode == 0, r.stderr
    assert 'Tylko stary (1): tylko_stara' in r.stdout
    assert '| `wspolna` | 3 | 3 | 0 (identyczne) |' in r.stdout     # docstring/komentarz pominięte
    assert '| `zmieniona` |' in r.stdout
    assert (tmp_path / 'd' / 'zmieniona.diff').is_file()
    assert not (tmp_path / 'd' / 'wspolna.diff').exists()
    assert r.stderr.strip().splitlines()[-1].startswith('SUKCES')


def test_porownaj_wersje_bledne_wejscie(tmp_path):
    (tmp_path / 'zly.py').write_text('def (:\n', encoding='utf-8')
    r = _uruchom('porownaj_wersje.py', tmp_path / 'zly.py', tmp_path / 'brak.py')
    assert r.returncode == 2 and 'BŁĄD' in r.stderr


# ── zaleznosci_eksportera.py ─────────────────────────────────────────────────

EKSPORTER = '''
import os, re
import typografia
from pathlib import Path
try:
    import latex2mathml.converter
    HAS_LATEX2MATHML = True
except ImportError:
    HAS_LATEX2MATHML = False
from docx import Document

def svg():
    try:
        import cairosvg
    except ImportError:
        return None
    from fontTools.ttLib import TTFont
'''


def test_zaleznosci_z_importow_takze_opcjonalnych(tmp_path):
    (tmp_path / 'export_to_docx.py').write_text(EKSPORTER, encoding='utf-8')
    (tmp_path / 'typografia.py').write_text('import re\n', encoding='utf-8')
    r = _uruchom('zaleznosci_eksportera.py', tmp_path / 'export_to_docx.py',
                 tmp_path / 'typografia.py')
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ['cairosvg', 'fonttools', 'latex2mathml', 'python-docx']


def test_zaleznosci_sprawdz_requirements(tmp_path):
    (tmp_path / 'export_to_docx.py').write_text(EKSPORTER, encoding='utf-8')
    (tmp_path / 'typografia.py').write_text('', encoding='utf-8')
    req = tmp_path / 'requirements.in'
    req.write_text('python-docx\nlatex2mathml  # MIT\n', encoding='utf-8')
    r = _uruchom('zaleznosci_eksportera.py', tmp_path / 'export_to_docx.py',
                 '--sprawdz-requirements', req)
    assert r.returncode == 1
    assert 'cairosvg' in r.stderr and 'fonttools' in r.stderr
    assert 'python-docx (brak' not in r.stderr


def test_zaleznosci_importuj_czerwone_przy_braku(tmp_path):
    (tmp_path / 'e.py').write_text('try:\n    import modul_ktorego_nie_ma_xyz\n'
                                   'except ImportError:\n    pass\n', encoding='utf-8')
    r = _uruchom('zaleznosci_eksportera.py', tmp_path / 'e.py', '--importuj')
    assert r.returncode == 1 and 'modul_ktorego_nie_ma_xyz' in r.stderr


def test_zaleznosci_bledne_wejscie(tmp_path):
    r = _uruchom('zaleznosci_eksportera.py', tmp_path / 'brak.py')
    assert r.returncode == 2 and 'BŁĄD' in r.stderr


def test_requirements_in_zawiera_zaleznosci_eksportera():
    """Lista w requirements.in obejmuje zależności eksportera wyprowadzone skryptem
    (stan EXPORT z 19.11.2026; na Wykonawcy sprawdza to T2b na żywych plikach)."""
    from conftest import REPO as R
    tekst = (R / 'requirements.in').read_text(encoding='utf-8').lower()
    for p in ('python-docx', 'markdown', 'pillow', 'beautifulsoup4', 'cairosvg', 'fonttools',
              'latex2mathml', 'lxml', 'edge-tts', 'num2words'):
        assert f'\n{p}' in tekst, p
    txt = (R / 'requirements.txt').read_text(encoding='utf-8').lower()
    for p in ('beautifulsoup4==', 'cairosvg==', 'fonttools==', 'latex2mathml=='):
        assert p in txt, p


# ── T2b: tylko na Wykonawcy, z prawdziwym katalogiem EXPORT ──────────────────

EKSPORT = os.environ.get('AF_KATALOG_EKSPORTU', '')
na_wykonawcy = pytest.mark.skipif(not EKSPORT, reason='T2b: ustaw AF_KATALOG_EKSPORTU '
                                  '(Wykonawca, /mnt/data/sprawozdania/EXPORT)')

PROBNY_MD = '''# Próba eksportu

Wzór: $E = m c^2$ oraz $$\\int_0^1 x\\,dx = \\tfrac{1}{2}$$

| a | b |
|---|---|
| 1 | 2 |

![rysunek](rys.svg)
'''
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
       '<rect width="40" height="20" fill="#36c"/></svg>')


@na_wykonawcy
def test_t2b_importy_eksportera_w_interpreterze_uslugi():
    pliki = [Path(EKSPORT) / n for n in ('export_to_docx.py', 'typografia.py', 'lint_styl.py',
                                         'czytaj_tts.py')]
    r = _uruchom('zaleznosci_eksportera.py', *pliki, '--importuj',
                 '--sprawdz-requirements', REPO / 'requirements.in')
    assert r.returncode == 0, r.stdout + r.stderr


@na_wykonawcy
def test_t2b_eksport_prawdziwym_skryptem_bez_pominiec(tmp_path):
    (tmp_path / 'p.md').write_text(PROBNY_MD, encoding='utf-8')
    (tmp_path / 'rys.svg').write_text(SVG, encoding='utf-8')
    out = tmp_path / 'p.docx'
    r = subprocess.run([sys.executable, str(Path(EKSPORT) / 'export_to_docx.py'),
                        str(tmp_path / 'p.md'), '-o', str(out), '--base-dir', str(tmp_path)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace',
                       cwd=EKSPORT, env=ENV, timeout=240)
    assert r.returncode == 0, r.stderr[-2000:]
    log = (r.stdout + r.stderr).lower()
    for zle in ('pomijane', 'pomijam', 'fallback nie zadziała', 'brak latex2mathml',
                'brak cairosvg'):
        assert zle not in log, (zle, log[-1500:])
    with zipfile.ZipFile(out) as z:
        dok = z.read('word/document.xml').decode('utf-8')
    assert '<m:oMath' in dok and '<w:tbl>' in dok
