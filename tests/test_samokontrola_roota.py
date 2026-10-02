"""Samokontrola poleceń uruchamianych jako root (audyt A2): root nie wykona
kodu, który może podmienić nieuprzywilejowany użytkownik. Testy nie działają
jako root — euid i metadane plików podajemy jako parametry."""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conftest import REPO

RESET = REPO / 'narzedzia' / 'anberfiles-reset-hasla'
KOPIA = REPO / 'narzedzia' / 'anberfiles-kopia-plikow'

tylko_linux = pytest.mark.skipif(sys.platform == 'win32',
                                 reason='uprawnienia uniksowe')


def _modul():
    loader = importlib.machinery.SourceFileLoader('reset_hasla_a2', str(RESET))
    spec = importlib.util.spec_from_loader('reset_hasla_a2', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _atrapa_lstat(wpisy, domyslny=(0, 0o40755)):
    """lstat zwracający uid i tryb z tabeli {ścieżka: (uid, tryb)}."""
    def lstat(p):
        uid, tryb = wpisy.get(str(p), domyslny)
        return SimpleNamespace(st_uid=uid, st_mode=tryb)
    return lstat


@tylko_linux
def test_root_odmawia_pliku_nalezacego_do_nie_roota():
    m = _modul()
    lstat = _atrapa_lstat({'/srv/anberfiles/kod/app/logowanie.py': (1001, 0o100644)})
    powod = m.sprawdz_zaufanie_roota(['/srv/anberfiles/kod/app/logowanie.py'],
                                     euid=0, lstat_fn=lstat, realpath_fn=lambda p: p)
    assert powod and 'logowanie.py' in powod and '1001' in powod
    assert 'root nie wykona kodu' in powod


@tylko_linux
def test_root_zgoda_dla_plikow_roota_bez_zapisu_innych():
    m = _modul()
    lstat = _atrapa_lstat({'/srv/anberfiles/kod/app/logowanie.py': (0, 0o100755)})
    assert m.sprawdz_zaufanie_roota(['/srv/anberfiles/kod/app/logowanie.py'],
                                    euid=0, lstat_fn=lstat,
                                    realpath_fn=lambda p: p) is None


@tylko_linux
@pytest.mark.parametrize('tryb', [0o100775, 0o100757])
def test_root_odmawia_pliku_z_zapisem_grupy_lub_innych(tryb):
    m = _modul()
    lstat = _atrapa_lstat({'/srv/anberfiles/kod/app/logowanie.py': (0, tryb)})
    powod = m.sprawdz_zaufanie_roota(['/srv/anberfiles/kod/app/logowanie.py'],
                                     euid=0, lstat_fn=lstat, realpath_fn=lambda p: p)
    assert powod and 'zapis' in powod


@tylko_linux
def test_root_odmawia_gdy_katalog_nadrzedny_nalezy_do_uzytkownika():
    m = _modul()
    lstat = _atrapa_lstat({'/srv/anberfiles': (1001, 0o40755)})
    powod = m.sprawdz_zaufanie_roota(['/srv/anberfiles/kod/narzedzia/x'],
                                     euid=0, lstat_fn=lstat, realpath_fn=lambda p: p)
    assert powod and '/srv/anberfiles' in powod


@tylko_linux
def test_nie_root_nie_podlega_kontroli_a_furtki_srodowiskowej_brak():
    m = _modul()
    lstat = _atrapa_lstat({'/x': (1001, 0o100777)})
    assert m.sprawdz_zaufanie_roota(['/x'], euid=1000, lstat_fn=lstat) is None
    # kontrola rozróżniająca: ta sama ścieżka dla roota jest odrzucona
    assert m.sprawdz_zaufanie_roota(['/x'], euid=0, lstat_fn=lstat,
                                    realpath_fn=lambda p: p)
    zrodlo = RESET.read_text(encoding='utf-8')
    assert 'ANBERFILES_POMIN' not in zrodlo and 'SKIP' not in zrodlo.upper().replace(
        'SKIPPED', '')


@tylko_linux
def test_dowiazanie_nalezace_do_uzytkownika_jest_odrzucone():
    m = _modul()
    lstat = _atrapa_lstat({'/usr/local/sbin/anberfiles-reset-hasla': (1001, 0o120777)})
    powod = m.sprawdz_zaufanie_roota(['/usr/local/sbin/anberfiles-reset-hasla'],
                                     euid=0, lstat_fn=lstat, realpath_fn=lambda p: p)
    assert powod and '1001' in powod


# ── skrypt sh: funkcja plik_zaufany wyjęta ze skryptu i uruchomiona na plikach ──

def _funkcja_sh():
    tekst = KOPIA.read_text(encoding='utf-8').splitlines()
    i = next(n for n, l in enumerate(tekst) if l.startswith('# >>> zaufanie'))
    j = next(n for n, l in enumerate(tekst) if l.startswith('# <<< zaufanie'))
    return '\n'.join(tekst[i:j + 1])


def _plik_zaufany(plik, uid):
    r = subprocess.run(['sh', '-c', _funkcja_sh() + '\nplik_zaufany "$1" "$2"',
                        'sh', str(plik), str(uid)],
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stderr


@tylko_linux
def test_sh_plik_zaufany_zgoda_i_odmowy(tmp_path):
    p = tmp_path / 'skrypt'
    p.write_text('#!/bin/sh\n')
    p.chmod(0o755)
    wlasny = os.getuid()
    # katalogi nadrzędne tmp_path bywają zapisywalne przez grupę — kontrola
    # dotyczy tu samego pliku, więc łańcuch obcinamy do tmp_path
    rc, _ = _plik_zaufany(p, wlasny + 4242)
    assert rc != 0                                      # inny właściciel → odmowa
    p.chmod(0o775)
    rc, err = _plik_zaufany(p, wlasny)
    assert rc != 0 and 'zapis' in err                   # zapis grupy → odmowa
    p.chmod(0o757)
    rc, err = _plik_zaufany(p, wlasny)
    assert rc != 0 and 'zapis' in err                   # zapis innych → odmowa


@tylko_linux
def test_sh_plik_zaufany_zgoda_dla_pliku_roota():
    rc, err = _plik_zaufany('/bin/sh', 0)
    assert rc == 0, err
