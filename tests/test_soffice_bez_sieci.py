"""C9: LibreOffice (soffice --headless) konwertuje dokumenty bez dostępu do sieci
(SSRF przez INCLUDEPICTURE http://…): bwrap --unshare-net, a gdy go brak —
unshare -n (działa bez roota rzadko, na konsoli usługa jest rootem). Ustawienie
soffice_bez_sieci = tak|nie|auto (domyślnie auto). Piaskownica, której nie da
się uruchomić (np. RestrictNamespaces=yes w systemd), = zwykły soffice
i ostrzeżenie w rejestrze RAZ na start — podgląd DOCX działa dalej."""
import re

import pytest

from conftest import REPO, uruchom, wczytaj, zbuduj_anbernic, zbuduj_jarvis

BWRAP = ['bwrap', '--unshare-net', '--dev-bind', '/', '/']


def _which(*dostepne):
    return lambda nazwa: f'/usr/bin/{nazwa}' if nazwa in dostepne else None


@pytest.fixture
def srv(tmp_path):
    import server
    server.zastosuj_konfiguracje(wczytaj(zbuduj_anbernic(tmp_path)))
    return server


def _ostrzezenia(server):
    try:
        tekst = server.EVENT_LOG.read_text(encoding='utf-8')
    except OSError:
        return []
    return [ln for ln in tekst.splitlines() if '\twarn\tsoffice\t' in ln]


def test_bwrap_dostepny_polecenie_bez_sieci(srv):
    srv._wykryj_piaskownice(which=_which('bwrap', 'unshare'), proba=lambda cmd: True)
    assert srv.polecenie_soffice('--convert-to', 'pdf', 'a.docx') == \
        BWRAP + ['soffice', '--convert-to', 'pdf', 'a.docx']
    assert _ostrzezenia(srv) == []


def test_brak_bwrap_unshare_dziala(srv):
    srv._wykryj_piaskownice(which=_which('unshare'), proba=lambda cmd: True)
    assert srv.polecenie_soffice('x') == ['unshare', '-n', 'soffice', 'x']


def test_brak_narzedzi_zwykly_soffice_i_jedno_ostrzezenie(srv):
    srv._wykryj_piaskownice(which=_which(), proba=lambda cmd: True)
    assert srv.polecenie_soffice('x') == ['soffice', 'x']
    assert srv.polecenie_soffice('y') == ['soffice', 'y']
    assert len(_ostrzezenia(srv)) == 1


def test_bwrap_zablokowany_powrot_do_zwyklego_soffice(srv):
    """RestrictNamespaces=yes: bwrap jest, ale się nie uruchamia (próba ≠ 0);
    unshare -n bez roota też nie — zwykły soffice + ostrzeżenie z przyczyną."""
    proby = []

    def proba(cmd):
        proby.append(cmd)
        return False
    srv._wykryj_piaskownice(which=_which('bwrap', 'unshare'), proba=proba)
    assert srv.polecenie_soffice('x') == ['soffice', 'x']
    assert proby == [BWRAP + ['true'], ['unshare', '-n', 'true']]
    ostrz = _ostrzezenia(srv)
    assert len(ostrz) == 1 and 'bwrap' in ostrz[0]


def test_tryb_nie_bez_piaskownicy_mimo_bwrap(tmp_path):
    import server
    server.zastosuj_konfiguracje(wczytaj(zbuduj_jarvis(tmp_path, soffice_bez_sieci='nie')))
    server._wykryj_piaskownice(which=_which('bwrap'), proba=lambda cmd: True)
    assert server.polecenie_soffice('x') == ['soffice', 'x']
    assert _ostrzezenia(server) == []


def test_tryb_tak_bez_piaskownicy_odmowa(tmp_path):
    """tak = wymagane: bez działającej piaskownicy konwersji nie ma (None)."""
    import server
    server.zastosuj_konfiguracje(wczytaj(zbuduj_jarvis(tmp_path, soffice_bez_sieci='tak')))
    server._wykryj_piaskownice(which=_which('bwrap'), proba=lambda cmd: False)
    assert server.polecenie_soffice('x') is None


def test_zla_wartosc_ustawienia(tmp_path):
    import konfiguracja
    with pytest.raises(konfiguracja.BladKonfiguracji):
        wczytaj(zbuduj_jarvis(tmp_path, soffice_bez_sieci='moze'))


def test_domyslnie_auto(tmp_path):
    assert wczytaj(zbuduj_anbernic(tmp_path)).soffice_bez_sieci == 'auto'


def test_kazde_uruchomienie_soffice_przez_wspolna_funkcje():
    """Statycznie: literał 'soffice' jako program pojawia się w server.py tylko
    w polecenie_soffice (podgląd DOCX i druk idą przez nią)."""
    zrodlo = (REPO / 'app' / 'server.py').read_text(encoding='utf-8')
    assert len(re.findall(r"\['soffice'", zrodlo)) == 1           # baza w polecenie_soffice
    assert not re.search(r"(exec|_polecenie)\(\s*'soffice'", zrodlo)


def test_wykrycie_przy_starcie_serwera(tmp_path, monkeypatch):
    """Start aplikacji wykrywa piaskownicę raz (on_startup) — bez sieci na
    maszynie testowej: brak narzędzi → zwykły soffice, podgląd działa dalej."""
    import server
    k = wczytaj(zbuduj_anbernic(tmp_path))
    monkeypatch.setattr(server.shutil, 'which', _which())

    async def sc(cl):
        return server.polecenie_soffice('x')
    assert uruchom(k, sc) == ['soffice', 'x']
    assert len(_ostrzezenia(server)) == 1
