"""Jeden zły wpis katalogu (EPERM/EACCES/EIO przy stat — np. dowiązanie
z bezwzględnym celem na innym komputerze, zdalny sshfs) nie może wywracać
listingu, drzewa eksploratora ani list rodzeństwa.

Testy budują własne katalogi w tmp_path."""
import errno
import sys
from pathlib import Path

import aiohttp
import pytest

from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic

AU = aiohttp.BasicAuth('anbernic', HASLO)
ZLY = 'gpiod.cpython-311-aarch64-linux-gnu.so'


def _konf(tmp_path):
    return wczytaj(zbuduj_anbernic(tmp_path))


def _drzewo(tmp_path):
    root = tmp_path / 'sprawozdania'
    (root / 'maly' / 'podkatalog').mkdir(parents=True)
    (root / 'maly' / 'plik.txt').write_text('x', encoding='utf-8')
    (root / 'maly' / 'a.mp3').write_bytes(b'ID3')
    (root / 'maly' / 'b.mp3').write_bytes(b'ID3')
    (root / 'maly' / 'a.png').write_bytes(b'x')
    (root / 'maly' / 'b.png').write_bytes(b'x')
    (root / 'maly' / ZLY).write_text('x', encoding='utf-8')
    return root


def _zepsuj(monkeypatch, nazwa=ZLY):
    """stat()/is_dir()/is_file() wpisu o danej nazwie rzuca EPERM (jak
    dowiązanie do ścieżki z innego komputera); reszta działa normalnie."""
    stat0, isdir0, isfile0 = Path.stat, Path.is_dir, Path.is_file

    def _blad():
        return PermissionError(errno.EPERM, 'Operation not permitted')

    def stat(self, *a, **kw):
        if self.name == nazwa:
            raise _blad()
        return stat0(self, *a, **kw)

    def is_dir(self, *a, **kw):
        if self.name == nazwa:
            raise _blad()
        return isdir0(self, *a, **kw)

    def is_file(self, *a, **kw):
        if self.name == nazwa:
            raise _blad()
        return isfile0(self, *a, **kw)

    monkeypatch.setattr(Path, 'stat', stat)
    monkeypatch.setattr(Path, 'is_dir', is_dir)
    monkeypatch.setattr(Path, 'is_file', is_file)


def test_listing_z_zlym_wpisem_daje_200_i_reszte_listy(tmp_path, monkeypatch):
    _drzewo(tmp_path)
    k = _konf(tmp_path)
    _zepsuj(monkeypatch)

    async def sc(cl):
        r = await cl.get('/maly/', auth=AU)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 200
    assert 'plik.txt' in html and 'podkatalog' in html and 'a.mp3' in html


def test_eksplorator_zly_wpis_nie_kasuje_sasiednich_katalogow(tmp_path, monkeypatch):
    root = _drzewo(tmp_path)
    (root / 'maly' / 'drugi-katalog').mkdir()
    (root / ZLY).write_text('x', encoding='utf-8')     # zły wpis na poziomie korzenia
    k = _konf(tmp_path)
    _zepsuj(monkeypatch)

    async def sc(cl):
        r = await cl.get('/?explorer=1', auth=AU)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 200
    assert 'maly' in html and 'podkatalog' in html and 'drugi-katalog' in html


def test_dir_children_zly_wpis_pominiety_reszta_zostaje(tmp_path, monkeypatch):
    import server
    root = _drzewo(tmp_path)
    (root / 'maly' / 'drugi-katalog').mkdir()
    _zepsuj(monkeypatch, nazwa='podkatalog')
    nazwy = [d.name for d in server._dir_children(root / 'maly')]
    assert nazwy == ['drugi-katalog']


def test_rodzenstwo_audio_i_zdjec_ze_zlym_wpisem(tmp_path, monkeypatch):
    _drzewo(tmp_path)
    k = _konf(tmp_path)
    _zepsuj(monkeypatch)

    async def sc(cl):
        r1 = await cl.get('/maly/a.mp3?siblings=1', auth=AU)
        r2 = await cl.get('/maly/a.png?view=1', auth=AU)
        return r1.status, await r1.json(), r2.status
    st1, sibs, st2 = uruchom(k, sc)
    assert st1 == 200 and sibs == ['a.mp3', 'b.mp3']
    assert st2 == 200


@pytest.mark.skipif(sys.platform == 'win32', reason='dowiązania i EPERM tylko na Linuksie')
def test_dowiazanie_do_sciezki_z_bledem_innym_niz_enoent(tmp_path, monkeypatch):
    root = _drzewo(tmp_path)
    (root / 'maly' / ZLY).unlink()
    (root / 'maly' / ZLY).symlink_to('/usr/lib/python3/dist-packages/gpiod.so')
    import os
    stat0 = os.stat

    def stat(p, *a, **kw):
        if str(p).endswith(ZLY):
            raise PermissionError(errno.EPERM, 'Operation not permitted')
        return stat0(p, *a, **kw)
    monkeypatch.setattr(os, 'stat', stat)
    k = _konf(tmp_path)

    async def sc(cl):
        r = await cl.get('/maly/', auth=AU)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 200 and 'plik.txt' in html
