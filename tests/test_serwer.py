"""Serwer AnberFiles na żywo (aiohttp test client): hasło, tylko odczyt,
lektor i katalog lektora, przełączniki modułów, wyjście poza katalog."""
import io
import os
import subprocess
import sys
from pathlib import Path

import aiohttp
import pytest
from yarl import URL

from conftest import (ATRAPA_TTS_BLAD, HASLO, czekaj_na_lektora, uruchom,
                      wczytaj, zbuduj_anbernic, zbuduj_jarvis)


def _drzewo(tmp_path):
    """Treść testowa w katalogu głównym Jarvisa."""
    korzen = tmp_path / 'srv' / 'korzen'
    proj = korzen / 'vault' / 'proj'
    proj.mkdir(parents=True)
    (proj / 'a.md').write_text('# Tytuł\n\nPierwsze zdanie.\n', encoding='utf-8')
    (proj / 'b.txt').write_text('tekst', encoding='utf-8')
    from PIL import Image
    Image.new('RGB', (20, 20), 'red').save(proj / 'obraz.png')
    return korzen, proj


def _migawka(katalog: Path) -> dict:
    return {p.relative_to(katalog).as_posix(): (p.stat().st_size if p.is_file() else -1)
            for p in sorted(katalog.rglob('*'))}


# ── hasło ───────────────────────────────────────────────────────────────────

def test_bez_hasla_401_z_haslem_200(tmp_path, auth):
    _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        r1 = await cl.get('/')
        r2 = await cl.get('/', auth=aiohttp.BasicAuth('karol', 'zle'))
        r3 = await cl.get('/', auth=auth)
        return r1.status, r2.status, r3.status, await r3.text()
    s1, s2, s3, html = uruchom(k, sc)
    assert (s1, s2, s3) == (401, 401, 200)
    assert 'vault/' in html


# ── tylko odczyt ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('kadrowanie', ['nie', 'tak'])
def test_tylko_odczyt_odrzuca_zapis(tmp_path, auth, kadrowanie):
    korzen, proj = _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path, kadrowanie=kadrowanie, eksport_docx='tak'))
    przed = _migawka(korzen)

    async def sc(cl):
        st = {}
        fd = aiohttp.FormData()
        fd.add_field('file', io.BytesIO(b'xyz'), filename='nowy.txt')
        st['wgrywanie'] = (await cl.post('/vault/proj/', data=fd, auth=auth)).status
        st['mkdir'] = (await cl.post('/vault/proj/?mkdir=nowy', auth=auth)).status
        st['usuwanie'] = (await cl.delete('/vault/proj/b.txt', auth=auth)).status
        st['zmiana_nazwy'] = (await cl.post('/vault/proj/b.txt?rename=c.txt',
                                            auth=auth)).status
        st['kadrowanie'] = (await cl.post('/vault/proj/obraz.png?crop', auth=auth,
                                          json={'x': 0, 'y': 0, 'w': 5, 'h': 5,
                                                'mode': 'overwrite'})).status
        st['eksport_docx'] = (await cl.get('/vault/proj/a.md?docx=1', auth=auth)).status
        return st
    st = uruchom(k, sc)
    assert st == {n: 403 for n in st}, st
    assert _migawka(korzen) == przed


def test_bez_tylko_odczytu_zapis_dziala(tmp_path):
    """Kontrola czułości: ten sam zestaw na ustawieniach konsoli przechodzi."""
    k = wczytaj(zbuduj_anbernic(tmp_path))
    korzen = tmp_path / 'sprawozdania'
    (korzen / 'b.txt').write_text('tekst', encoding='utf-8')
    au = aiohttp.BasicAuth('anbernic', HASLO)

    async def sc(cl):
        fd = aiohttp.FormData()
        fd.add_field('file', io.BytesIO(b'xyz'), filename='nowy.txt')
        return ((await cl.post('/', data=fd, auth=au)).status,
                (await cl.post('/?mkdir=nowy', auth=au)).status,
                (await cl.post('/b.txt?rename=c.txt', auth=au)).status,
                (await cl.delete('/c.txt', auth=au)).status)
    assert uruchom(k, sc) == (200, 200, 200, 200)
    assert (korzen / 'nowy.txt').read_bytes() == b'xyz'
    assert (korzen / 'nowy').is_dir()
    assert len(list((korzen / '.kosz').iterdir())) == 1


def test_interfejs_ukrywa_akcje_wylaczone(tmp_path, auth):
    _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        lst = await (await cl.get('/vault/proj/', auth=auth)).text()
        md = await (await cl.get('/vault/proj/a.md?view=1', auth=auth)).text()
        img = await (await cl.get('/vault/proj/obraz.png?view=1', auth=auth)).text()
        return lst, md, img
    lst, md, img = uruchom(k, sc)
    assert 'class="dl del"' not in lst and 'class="dl ren"' not in lst
    assert 'id="mkd"' not in lst
    assert 'class="dl lek"' in lst                  # lektor włączony
    assert 'id="docxbtn"' not in md                 # eksport DOCX wyłączony
    assert 'id="prn"' in md                         # druk włączony
    assert 'id="cropb"' not in img                  # kadrowanie wyłączone


# ── lektor ──────────────────────────────────────────────────────────────────

def test_lektor_przy_tylko_odczycie_do_katalogu_lektora(tmp_path, auth):
    korzen, proj = _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    cel = korzen / 'lektor' / 'vault' / 'proj' / 'a_lektor.mp3'

    async def sc(cl):
        r = await cl.post('/vault/proj/a.md?lektor=1&fmt=mp3', auth=auth)
        assert r.status == 202, await r.text()
        await czekaj_na_lektora(cl, auth, cel)
        read = await cl.get('/vault/proj/a.md?read=1', auth=auth)
        view = await (await cl.get('/vault/proj/a.md?view=1', auth=auth)).text()
        lst = await (await cl.get('/vault/proj/', auth=auth)).text()
        return read.status, await read.text(), view, lst
    st_read, read, view, lst = uruchom(k, sc)
    assert cel.exists()
    assert not (proj / 'a_lektor.mp3').exists()       # nic obok źródła
    assert st_read == 200
    assert '/lektor/vault/proj/a_lektor.mp3' in read
    assert 'Pierwsze zdanie.' in read
    assert '/lektor/vault/proj/a_lektor.mp3' in view
    assert '/lektor/vault/proj/a_lektor.mp3' in lst    # ikona audio przy .md
    argv = (tmp_path / 'srv' / 'eksport' / 'ostatnie_argv.txt').read_text(encoding='utf-8')
    assert '--opisy nie' in argv                       # opisy AI wyłączone


def test_lektor_bez_katalogu_lektora_obok_zrodla(tmp_path):
    """Anbernic: audio obok .md, jak dotąd."""
    k = wczytaj(zbuduj_anbernic(tmp_path))
    korzen = tmp_path / 'sprawozdania'
    (korzen / 'x.md').write_text('Tekst.', encoding='utf-8')
    au = aiohttp.BasicAuth('anbernic', HASLO)

    async def sc(cl):
        r = await cl.post('/x.md?lektor=1&fmt=mp3', auth=au)
        assert r.status == 202
        await czekaj_na_lektora(cl, au, korzen / 'x_lektor.mp3')
    uruchom(k, sc)
    assert (korzen / 'x_lektor.mp3').exists()


def test_lektor_blad_programu_widoczny(tmp_path, auth):
    _drzewo(tmp_path)
    conf = zbuduj_jarvis(tmp_path)
    (tmp_path / 'srv' / 'eksport' / 'czytaj_tts.py').write_text(ATRAPA_TTS_BLAD,
                                                                encoding='utf-8')
    k = wczytaj(conf)

    async def sc(cl):
        r = await cl.post('/vault/proj/a.md?lektor=1&fmt=mp3', auth=auth)
        assert r.status == 202
        return await czekaj_na_lektora(cl, auth)
    j = uruchom(k, sc)
    assert j['bledy'], j
    assert j['bledy'][-1]['state'] == 'failed'
    log = (tmp_path / 'srv' / 'dane' / 'lektor_errors.log').read_text(encoding='utf-8')
    assert 'ATRAPA: synteza nieudana' in log


@pytest.mark.skipif(sys.platform == 'win32' or (hasattr(os, 'geteuid') and os.geteuid() == 0),
                    reason='chmod nie odbiera prawa zapisu na Windows ani administratorowi')
def test_katalog_lektora_tylko_do_odczytu_blad_widoczny(tmp_path, auth):
    _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    kl = tmp_path / 'srv' / 'korzen' / 'lektor'
    kl.chmod(0o555)

    async def sc(cl):
        r = await cl.post('/vault/proj/a.md?lektor=1&fmt=mp3', auth=auth)
        return r.status, await r.json(), await czekaj_na_lektora(cl, auth)
    try:
        st, odp, j = uruchom(k, sc)
    finally:
        kl.chmod(0o755)
    assert st >= 500 and odp['status'] == 'failed', odp
    assert odp.get('blad')
    assert j['bledy'] and j['bledy'][-1]['state'] == 'failed'
    log = (tmp_path / 'srv' / 'dane' / 'lektor_errors.log').read_text(encoding='utf-8')
    assert 'a_lektor.mp3' in log


def test_lektor_wylaczony_403(tmp_path, auth):
    _drzewo(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path, lektor='nie'))

    async def sc(cl):
        r = await cl.post('/vault/proj/a.md?lektor=1', auth=auth)
        lst = await (await cl.get('/vault/proj/', auth=auth)).text()
        return r.status, lst
    st, lst = uruchom(k, sc)
    assert st == 403
    assert 'class="dl lek"' not in lst


# ── wyłączanie urządzenia ───────────────────────────────────────────────────

def _atrapa_run(wywolania):
    def run(cmd, *a, **kw):
        wywolania.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, stdout='', stderr='')
    return run


def test_wylaczanie_nie_nigdy_nie_wola_shutdown(tmp_path, auth, monkeypatch):
    import server
    wyw = []
    monkeypatch.setattr(subprocess, 'run', _atrapa_run(wyw))
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        r = await cl.post('/?lektorqshutdown=1&force=1', auth=auth)
        server._LEKTOR_SHUTDOWN = True        # nawet z flagą odziedziczoną z kolejki
        server._lektor_maybe_shutdown()
        j = await (await cl.get('/?lektorqj=1', auth=auth)).json()
        return r.status, j
    st, j = uruchom(k, sc)
    assert st == 403
    assert j['wylaczanie'] is False
    assert not any(c and c[0] == 'shutdown' for c in wyw), wyw


def test_wylaczanie_tak_wola_shutdown(tmp_path, monkeypatch):
    """Kontrola czułości atrapy: na ustawieniach konsoli shutdown JEST wołany."""
    import server
    wyw = []
    monkeypatch.setattr(subprocess, 'run', _atrapa_run(wyw))
    k = wczytaj(zbuduj_anbernic(tmp_path))

    async def sc(cl):
        server._LEKTOR_SHUTDOWN = True
        server._lektor_maybe_shutdown()
    uruchom(k, sc)
    assert any(c and c[0] == 'shutdown' for c in wyw), wyw


# ── wyjście poza katalog ────────────────────────────────────────────────────

def test_wyjscie_poza_katalog_403(tmp_path, auth):
    _drzewo(tmp_path)
    (tmp_path / 'srv' / 'sekret.txt').write_text('tajne', encoding='utf-8')
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        out = []
        for u in ('/..%2Fsekret.txt', '/vault/..%2F..%2Fsekret.txt',
                  '/%2E%2E/sekret.txt'):
            r = await cl.get(URL(u, encoded=True), auth=auth)
            out.append((u, r.status, await r.text()))
        return out
    for u, st, txt in uruchom(k, sc):
        assert st == 403, (u, st)
        assert 'tajne' not in txt
