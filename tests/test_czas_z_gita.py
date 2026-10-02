"""Testy tools/czas_z_gita.py — czas modyfikacji plików klonu z historii git."""
import calendar
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKRYPT = Path(__file__).resolve().parent.parent / "tools" / "czas_z_gita.py"
DATA1 = "2024-01-01T12:00:00+0000"
DATA2 = "2025-06-15T12:00:00+0000"
CZAS1 = calendar.timegm((2024, 1, 1, 12, 0, 0))
CZAS2 = calendar.timegm((2025, 6, 15, 12, 0, 0))

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="brak git w PATH")


def _git(repo, *arg, data=None):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    if data:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = data
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "core.autocrlf=false", *arg],
                   check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "klon"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "a.txt").write_text("a")
    (r / "wspolny.txt").write_text("v1")
    (r / "podkatalog").mkdir()
    (r / "podkatalog" / "stary.txt").write_text("s")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "pierwszy", data=DATA1)
    (r / "b.txt").write_text("b")
    (r / "wspolny.txt").write_text("v2")
    (r / "zażółć gęślą jaźń.txt").write_text("pl")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "drugi", data=DATA2)
    teraz = time.time()
    for p in r.rglob("*"):
        if ".git" not in p.parts:
            os.utime(p, (teraz, teraz))
    return r


def _uruchom(repo):
    return subprocess.run([sys.executable, str(SKRYPT), str(repo)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_czasy_z_commitow(repo):
    wynik = _uruchom(repo)
    assert wynik.returncode == 0, wynik.stderr
    assert int(os.stat(repo / "a.txt").st_mtime) == CZAS1
    assert int(os.stat(repo / "b.txt").st_mtime) == CZAS2
    assert int(os.stat(repo / "zażółć gęślą jaźń.txt").st_mtime) == CZAS2
    assert int(os.stat(repo / "podkatalog" / "stary.txt").st_mtime) == CZAS1
    assert int(os.stat(repo / "a.txt").st_atime) == CZAS1


def test_plik_zmieniony_w_obu_commitach_dostaje_nowszy(repo):
    assert _uruchom(repo).returncode == 0
    assert int(os.stat(repo / "wspolny.txt").st_mtime) == CZAS2


def test_katalog_ma_najnowszy_czas_zawartosci(repo):
    assert _uruchom(repo).returncode == 0
    assert int(os.stat(repo / "podkatalog").st_mtime) == CZAS1
    assert int(os.stat(repo).st_mtime) == CZAS2


def test_brakujacy_plik_jest_pomijany(repo):
    (repo / "b.txt").unlink()
    wynik = _uruchom(repo)
    assert wynik.returncode == 0, wynik.stderr
    assert int(os.stat(repo / "a.txt").st_mtime) == CZAS1


def test_nie_klon_konczy_sie_bledem(tmp_path):
    wynik = _uruchom(tmp_path)
    assert wynik.returncode != 0
    assert wynik.stderr.strip()


# ── zmiany nazwy i położenia (zgłoszenie Karola 02.10: „Wciąż wszędzie są te
# same daty”) — 01.10 wieczorem cały vault przeniesiono `git mv`, a poprzednia
# wersja brała czas ostatniego commita, czyli przeniesienia.

D_DODANIE = "2024-01-01T12:00:00+0000"
D_ZMIANA = "2024-01-03T12:00:00+0000"
D_PRZENIESIENIE = "2024-01-05T12:00:00+0000"
D_ZMIANA_NAZWY_Z_TRESCIA = "2024-01-07T12:00:00+0000"
T_DODANIE = calendar.timegm((2024, 1, 1, 12, 0, 0))
T_ZMIANA = calendar.timegm((2024, 1, 3, 12, 0, 0))

TRESC = "".join(f"wiersz {i} z dłuższą treścią notatki\n" for i in range(40))


@pytest.fixture
def repo_przeniesienia(tmp_path):
    r = tmp_path / "vault"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "notatka.md").write_text(TRESC, encoding="utf-8")
    (r / "nietknieta.md").write_text(TRESC + "inna\n", encoding="utf-8")
    (r / "przemianowana.md").write_text(TRESC + "trzecia\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "dodanie", data=D_DODANIE)
    (r / "notatka.md").write_text(TRESC + "dopisek\n", encoding="utf-8")
    _git(r, "commit", "-q", "-am", "zmiana treści", data=D_ZMIANA)
    (r / "Projekt" / "Zasoby").mkdir(parents=True)
    _git(r, "mv", "notatka.md", "Projekt/Zasoby/notatka.md")
    _git(r, "mv", "nietknieta.md", "Projekt/Zasoby/nietknieta.md")
    _git(r, "commit", "-q", "-m", "Porządek vaulta: przeniesienie", data=D_PRZENIESIENIE)
    _git(r, "mv", "przemianowana.md", "Projekt/nowa-nazwa.md")
    (r / "Projekt" / "nowa-nazwa.md").write_text(TRESC + "trzecia\npoprawka\n", encoding="utf-8")
    _git(r, "commit", "-q", "-am", "zmiana nazwy z poprawką", data=D_ZMIANA_NAZWY_Z_TRESCIA)
    teraz = time.time()
    for p in r.rglob("*"):
        if ".git" not in p.parts:
            os.utime(p, (teraz, teraz))
    return r


def test_przeniesienie_nie_jest_zmiana_tresci(repo_przeniesienia, tmp_path):
    """Plik zmieniony 3.01, przeniesiony 5.01 → data 3.01, powstanie 1.01."""
    r = repo_przeniesienia
    bufor = tmp_path / "dane" / "czasy-git" / "vault.json"
    wynik = subprocess.run([sys.executable, str(SKRYPT), str(r), "--bufor", str(bufor)],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert wynik.returncode == 0, wynik.stderr
    assert int(os.stat(r / "Projekt" / "Zasoby" / "notatka.md").st_mtime) == T_ZMIANA
    # nigdy nie zmieniana treść → data dodania, mimo przeniesienia
    assert int(os.stat(r / "Projekt" / "Zasoby" / "nietknieta.md").st_mtime) == T_DODANIE
    # przeniesienie z poprawką odwołań w tym samym commicie (R<100, jak porządek
    # vaulta 01.10: R095–R099) to nadal przeniesienie, nie praca nad treścią
    assert int(os.stat(r / "Projekt" / "nowa-nazwa.md").st_mtime) == T_DODANIE
    import json
    b = json.loads(bufor.read_text(encoding="utf-8"))
    assert b["pliki"]["Projekt/Zasoby/notatka.md"] == [T_ZMIANA, T_DODANIE]
    assert b["pliki"]["Projekt/Zasoby/nietknieta.md"] == [T_DODANIE, T_DODANIE]
    assert b["pliki"]["Projekt/nowa-nazwa.md"] == [T_DODANIE, T_DODANIE]
    assert b["katalogi"]["Projekt/Zasoby"] == [T_ZMIANA, T_DODANIE]
    assert len(b["head"]) == 40


def test_lista_katalogow_pokazuje_daty_z_bufora(repo_przeniesienia, tmp_path):
    """Lista AnberFiles: Modyfikacja = zmiana treści, Powstanie = dodanie —
    z bufora w .git klonu (zapisuje go anberfiles-odswiez), nie z czasu pobrania."""
    from datetime import datetime
    from conftest import HASLO, uruchom, wczytaj, zbuduj_jarvis
    import aiohttp
    k = wczytaj(zbuduj_jarvis(tmp_path, logowanie='basic'))
    vault = tmp_path / "srv" / "korzen" / "vault"
    shutil.move(str(repo_przeniesienia), str(vault))
    wynik = subprocess.run([sys.executable, str(SKRYPT), str(vault), "--bufor",
                            str(vault / ".git" / "anberfiles-czasy.json")],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert wynik.returncode == 0, wynik.stderr
    teraz = time.time()
    for p in vault.rglob("*"):                 # np. ponowne pobranie: mtime = teraz
        if ".git" not in p.parts:
            os.utime(p, (teraz, teraz))

    async def sc(cl):
        r = await cl.get("/vault/Projekt/Zasoby/", auth=aiohttp.BasicAuth("admin", HASLO))
        return await r.text()
    html = uruchom(k, sc)

    def f(t):
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    wiersz = html[html.index('data-name="notatka.md"'):]
    wiersz = wiersz[:wiersz.index("</tr>")]
    assert f'data-sort="{T_ZMIANA}">{f(T_ZMIANA)}</td>' in wiersz
    assert f'data-sort="{T_DODANIE}">{f(T_DODANIE)}</td>' in wiersz
    assert ">Powstanie</th>" in html


def test_komorka_powstania_bez_daty_z_systemu_plikow_ma_adnotacje():
    import server
    assert "≈" in server._komorka_powstania(1.0e9, False)
    assert "≈" not in server._komorka_powstania(1.0e9, True)


def test_plik_dodany_na_dwoch_galeziach_powstanie_najstarsze(tmp_path):
    """Vault 03.09: ten sam plik dodany w schowku (27.08) i w migawce Jarvisa
    (03.09), potem scalenie — powstanie i data = najstarsze dodanie."""
    r = tmp_path / "v"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "baza.txt").write_text("b")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "baza", data=DATA1)
    _git(r, "checkout", "-q", "-b", "boczna")
    (r / "spis.md").write_text("spis\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "starsze dodanie", data="2024-02-01T12:00:00+0000")
    _git(r, "checkout", "-q", "main")
    (r / "spis.md").write_text("spis\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "nowsze dodanie", data="2024-03-01T12:00:00+0000")
    _git(r, "merge", "-q", "--no-edit", "boczna", data="2024-03-02T12:00:00+0000")
    assert _uruchom(r).returncode == 0
    assert int(os.stat(r / "spis.md").st_mtime) == calendar.timegm((2024, 2, 1, 12, 0, 0))
