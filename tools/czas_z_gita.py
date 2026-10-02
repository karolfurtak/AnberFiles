#!/usr/bin/env python3
"""Przywraca czasy plików klonu git z historii i zapisuje bufor dat.

Użycie: czas_z_gita.py <katalog klonu> [--bufor <plik.json>]

Git nadaje plikom czas modyfikacji (mtime) = chwila pobrania, więc w listingu
wszystkie pliki mają tę samą datę. Skrypt dla każdego śledzonego pliku ustala:
  - zmianę treści — czas OSTATNIEGO commita, który zmienił TREŚĆ pliku;
    czysta zmiana nazwy albo położenia (git mv, rename R100) się nie liczy
    (02.10.2026: po przeniesieniu całego vaulta 01.10 wieczorem wszystkie
    pliki miały datę przeniesienia),
  - powstanie — czas NAJSTARSZEGO commita, który dodał plik, z pójściem
    wstecz przez zmiany nazwy i położenia.
mtime i atime pliku = zmiana treści; katalog: mtime = najnowsza zmiana treści
w nim (rekurencyjnie). --bufor zapisuje oba czasy (pliki i katalogi; katalog:
powstanie = najstarsze powstanie zawartości) do pliku JSON, który czyta lista
katalogów AnberFiles — bez liczenia historii przy każdym żądaniu.

Jeden przebieg `git log -M --name-status` przez całą historię (od najnowszego
do najstarszego commita). Tylko biblioteka standardowa.
Kod wyjścia: 0 = sukces, 1 = błąd (opis na stderr).
"""
import json
import os
import subprocess
import sys
import threading
import time

LIMIT_GIT_S = 120  # limit czasu na każde wywołanie gita
WERSJA_BUFORA = 1


def _git(katalog):
    return ["git", "-C", katalog, "-c", "core.quotePath=false",
            "-c", "diff.renameLimit=100000"]


def sledzone(katalog):
    wynik = subprocess.run(_git(katalog) + ["ls-files", "-z"], capture_output=True,
                           timeout=LIMIT_GIT_S)
    if wynik.returncode != 0:
        raise RuntimeError("git ls-files: " + wynik.stderr.decode("utf-8", "replace").strip())
    return {p for p in wynik.stdout.decode("utf-8", "surrogateescape").split("\0") if p}


def _tokeny(proc):
    reszta = b""
    while True:
        kawalek = proc.stdout.read1(65536)
        if not kawalek:
            break
        *tokeny, reszta = (reszta + kawalek).split(b"\0")
        for t in tokeny:
            yield t.decode("utf-8", "surrogateescape")
    if reszta:
        yield reszta.decode("utf-8", "surrogateescape")


def czasy_z_historii(katalog, pliki):
    """Zwraca {plik: (zmiana treści, powstanie)} — czasy uniksowe commitów.

    Idzie od najnowszego commita; `nazwa` mapuje ścieżkę w danym punkcie
    historii na plik bieżący (zmiana nazwy R przesuwa mapowanie na starą
    nazwę). Zmiana treści: pierwsze (najnowsze) M/T/A albo R z podobieństwem
    < 100 %. Powstanie: ostatnie (najstarsze) A w łańcuchu nazw."""
    nazwa = {p: p for p in pliki}
    zmiana, powstanie = {}, {}
    if not nazwa:
        return {}
    proc = subprocess.Popen(
        _git(katalog) + ["log", "-M", "--name-status", "-z", "--format=%x01%ct"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    uciety_limitem = []
    zegar = threading.Timer(LIMIT_GIT_S, lambda: (uciety_limitem.append(1), proc.kill()))
    zegar.start()
    try:
        czas, status, sciezki = None, None, []
        for t in _tokeny(proc):
            t = t.lstrip("\n")
            if not t:
                continue
            if t.startswith("\x01"):
                czas, status = int(t[1:].strip()), None
                continue
            if status is None:
                status, sciezki = t, []
                continue
            sciezki.append(t)
            if status[0] in "RC" and len(sciezki) < 2:
                continue
            rodzaj = status[0]
            if rodzaj == "R":
                stara, nowa = sciezki
                f = nazwa.pop(nowa, None)
                if f is not None:
                    if status != "R100":
                        zmiana.setdefault(f, czas)
                    nazwa[stara] = f
            elif rodzaj in "AC":
                f = nazwa.pop(sciezki[-1], None)
                if f is not None:
                    zmiana.setdefault(f, czas)
                    powstanie[f] = czas
            elif rodzaj in "MT":
                f = nazwa.get(sciezki[0])
                if f is not None:
                    zmiana.setdefault(f, czas)
            status = None
    finally:
        zegar.cancel()
        if proc.poll() is None:
            proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.wait()
    if uciety_limitem:
        raise RuntimeError("git log przekroczył limit %d s" % LIMIT_GIT_S)
    if proc.returncode != 0:
        raise RuntimeError("git log: " + (err or "kod %s" % proc.returncode))
    wynik = {}
    for f, z in zmiana.items():
        wynik[f] = (z, powstanie.get(f, z))   # płytki klon: brak A → najstarsza znana
    return wynik


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


def zapisz_bufor(plik, glowa, pliki, katalogi):
    """JSON zapisywany atomowo (plik tymczasowy + zamiana)."""
    os.makedirs(os.path.dirname(os.path.abspath(plik)), exist_ok=True)
    tmp = "%s.%d.tmp" % (plik, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"wersja": WERSJA_BUFORA, "head": glowa, "utworzono": int(time.time()),
                   "pliki": {p: list(c) for p, c in pliki.items()},
                   "katalogi": {d: list(c) for d, c in katalogi.items()}},
                  f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, plik)


def main(argv):
    for strumien in (sys.stdout, sys.stderr):
        strumien.reconfigure(encoding="utf-8", errors="replace")
    arg = argv[1:]
    bufor = None
    if "--bufor" in arg:
        i = arg.index("--bufor")
        if i + 1 >= len(arg):
            print("czas_z_gita: --bufor wymaga ścieżki pliku", file=sys.stderr)
            return 1
        bufor = arg[i + 1]
        del arg[i:i + 2]
    if len(arg) != 1:
        print("Użycie: czas_z_gita.py <katalog klonu> [--bufor <plik.json>]", file=sys.stderr)
        return 1
    katalog = os.path.abspath(arg[0])
    start = time.monotonic()
    try:
        pliki = sledzone(katalog)
        czasy = czasy_z_historii(katalog, pliki)
        glowa = subprocess.run(_git(katalog) + ["rev-parse", "HEAD"], capture_output=True,
                               text=True, timeout=LIMIT_GIT_S).stdout.strip()
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        print("czas_z_gita: BŁĄD — %s" % e, file=sys.stderr)
        return 1
    zmienione = 0
    katalogi = {}  # katalog -> (najnowsza zmiana treści, najstarsze powstanie)
    for p, (czas, pow_) in czasy.items():
        sc = os.path.join(katalog, p)
        if not os.path.lexists(sc):
            continue
        zmienione += ustaw(sc, czas)
        d = os.path.dirname(p)
        while True:
            z0, p0 = katalogi.get(d, (0, pow_))
            katalogi[d] = (max(z0, czas), min(p0, pow_))
            if not d:
                break
            d = os.path.dirname(d)
    for d in sorted(katalogi, key=lambda x: -x.count("/")):
        sc = os.path.join(katalog, d) if d else katalog
        if os.path.isdir(sc) and not os.path.islink(sc):
            zmienione += ustaw(sc, katalogi[d][0])
    if bufor:
        try:
            zapisz_bufor(bufor, glowa, czasy, katalogi)
        except OSError as e:
            print("czas_z_gita: BŁĄD zapisu bufora %s — %s" % (bufor, e), file=sys.stderr)
            return 1
    print("czas_z_gita: plików %d, zmienionych (z katalogami) %d, %.2f s%s"
          % (len(czasy), zmienione, time.monotonic() - start,
             (", bufor %s" % bufor) if bufor else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
