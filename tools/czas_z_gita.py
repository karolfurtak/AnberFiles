#!/usr/bin/env python3
"""Przywraca czasy modyfikacji plików klonu git z historii.

Użycie: czas_z_gita.py <katalog klonu>

Git nadaje plikom czas modyfikacji (mtime) = chwila pobrania, więc w listingu
wszystkie pliki mają tę samą datę. Skrypt ustawia mtime i atime każdego
śledzonego pliku na czas OSTATNIEGO commita, który go zmienił, a mtime katalogu
na najnowszy czas pliku w nim (rekurencyjnie).

Jeden przebieg `git log --name-only` od najnowszego commita; czytanie kończy się,
gdy wszystkie śledzone pliki mają czas. Tylko biblioteka standardowa.
Kod wyjścia: 0 = sukces, 1 = błąd (opis na stderr).
"""
import os
import subprocess
import sys
import threading
import time

LIMIT_GIT_S = 120  # limit czasu na każde wywołanie gita


def _git(katalog):
    return ["git", "-C", katalog, "-c", "core.quotePath=false"]


def sledzone(katalog):
    wynik = subprocess.run(_git(katalog) + ["ls-files", "-z"], capture_output=True,
                           timeout=LIMIT_GIT_S)
    if wynik.returncode != 0:
        raise RuntimeError("git ls-files: " + wynik.stderr.decode("utf-8", "replace").strip())
    return {p for p in wynik.stdout.decode("utf-8", "surrogateescape").split("\0") if p}


def czasy_z_historii(katalog, pliki):
    """Zwraca {plik: czas ostatniego commita}, czytając log od najnowszego."""
    pozostale = set(pliki)
    czasy = {}
    if not pozostale:
        return czasy
    proc = subprocess.Popen(
        _git(katalog) + ["log", "--no-renames", "--name-only", "-z", "--format=%x01%ct"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    uciety_limitem = []
    zegar = threading.Timer(LIMIT_GIT_S, lambda: (uciety_limitem.append(1), proc.kill()))
    zegar.start()
    obcieto = False
    try:
        czas = None
        reszta = b""
        while pozostale and not obcieto:
            kawalek = proc.stdout.read1(65536)
            if not kawalek:
                break
            *tokeny, reszta = (reszta + kawalek).split(b"\0")
            for t in tokeny:
                t = t.decode("utf-8", "surrogateescape")
                if t.startswith("\x01"):
                    czas = int(t[1:].strip())
                    continue
                t = t.lstrip("\n")
                if t in pozostale and czas is not None:
                    czasy[t] = czas
                    pozostale.discard(t)
                    if not pozostale:
                        obcieto = True
                        break
    finally:
        zegar.cancel()
        if obcieto or proc.poll() is None:
            proc.kill()
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.wait()
    if uciety_limitem:
        raise RuntimeError("git log przekroczył limit %d s" % LIMIT_GIT_S)
    if proc.returncode != 0 and not obcieto:
        raise RuntimeError("git log: " + (err or "kod %s" % proc.returncode))
    return czasy


def ustaw(sciezka, czas):
    """Ustawia mtime i atime; zwraca True, gdy coś zmieniono."""
    try:
        st = os.lstat(sciezka)
    except OSError:
        return False
    if int(st.st_mtime) == czas and int(st.st_atime) == czas:
        return False
    sym = os.path.islink(sciezka)
    if sym and os.utime not in os.supports_follow_symlinks:
        return False
    try:
        os.utime(sciezka, (czas, czas), follow_symlinks=False) if os.utime in os.supports_follow_symlinks \
            else os.utime(sciezka, (czas, czas))
    except OSError:
        return False
    return True


def main(argv):
    for strumien in (sys.stdout, sys.stderr):
        strumien.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) != 2:
        print("Użycie: czas_z_gita.py <katalog klonu>", file=sys.stderr)
        return 1
    katalog = os.path.abspath(argv[1])
    start = time.monotonic()
    try:
        pliki = sledzone(katalog)
        czasy = czasy_z_historii(katalog, pliki)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        print("czas_z_gita: BŁĄD — %s" % e, file=sys.stderr)
        return 1
    zmienione = 0
    katalogi = {}  # katalog -> najnowszy czas zawartości
    for p, czas in czasy.items():
        sc = os.path.join(katalog, p)
        if not os.path.lexists(sc):
            continue
        zmienione += ustaw(sc, czas)
        d = os.path.dirname(p)
        while True:
            katalogi[d] = max(katalogi.get(d, 0), czas)
            if not d:
                break
            d = os.path.dirname(d)
    for d in sorted(katalogi, key=lambda x: -x.count("/")):
        sc = os.path.join(katalog, d) if d else katalog
        if os.path.isdir(sc) and not os.path.islink(sc):
            zmienione += ustaw(sc, katalogi[d])
    print("czas_z_gita: plików %d, zmienionych (z katalogami) %d, %.2f s"
          % (len(czasy), zmienione, time.monotonic() - start))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
