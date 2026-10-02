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
