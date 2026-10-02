"""C10: ?lektorqdel=ext / ?lektorqpause=ext / ?lektorqresume sygnałują WYŁĄCZNIE
proces zapisany przez czytaj_tts.py w pliku PID (obok rygla), i tylko gdy jego
linia poleceń (/proc/<pid>/cmdline) nadal zawiera czytaj_tts.py. Dawniej:
każdy proces z „czytaj_tts.py" w linii poleceń (pgrep -f).

Bez prawdziwych sygnałów: funkcja wysyłania sygnału i katalog /proc podstawione."""
import sys

import aiohttp
import pytest

from conftest import HASLO, REPO, uruchom, wczytaj, zbuduj_anbernic

AU = aiohttp.BasicAuth('anbernic', HASLO)
sys.path.insert(0, str(REPO / 'tools'))


@pytest.fixture
def atrapa(tmp_path, monkeypatch):
    """Podstawiony /proc, plik PID i rejestrator sygnałów."""
    import server
    proc = tmp_path / 'proc'
    proc.mkdir()

    def proces(pid, *argv, comm='python3'):
        d = proc / str(pid)
        d.mkdir()
        (d / 'cmdline').write_bytes(b'\x00'.join(a.encode() for a in argv) + b'\x00')
        (d / 'comm').write_text(comm + '\n')
    wyslane = []
    monkeypatch.setattr(server, 'KATALOG_PROC', proc, raising=False)
    monkeypatch.setattr(server, 'LEKTOR_PID_PLIK', tmp_path / 'lektor.pid', raising=False)
    monkeypatch.setattr(server, '_wyslij_sygnal', lambda pid, sig: wyslane.append((pid, sig)),
                        raising=False)
    return proces, tmp_path / 'lektor.pid', wyslane


def _k(tmp_path):
    return wczytaj(zbuduj_anbernic(tmp_path))


def test_przerwanie_ext_tylko_pid_z_pliku(tmp_path, atrapa):
    import server
    proces, pid_plik, wyslane = atrapa
    proces(4242, 'python3', '/x/czytaj_tts.py', 'a.md')       # lektor z pliku PID
    proces(4343, 'python3', '/y/czytaj_tts.py', 'b.md')       # inny „czytaj_tts.py"
    proces(4444, 'bash', '-c', 'ssh agent czytaj_tts.py', comm='bash')
    pid_plik.write_text('4242\n')

    async def sc(cl):
        r = await cl.post('/?lektorqdel=ext', auth=AU)
        return r.status, await r.json()
    st, j = uruchom(_k(tmp_path), sc)
    assert st == 200 and j['pids'] == [4242]
    assert wyslane == [(4242, server.SYGNAL_KILL)]


def test_pid_z_pliku_bez_czytaj_tts_w_cmdline_nie_dostaje_sygnalu(tmp_path, atrapa):
    """PID ponownie użyty przez inny program (lektor dawno skończył) — bez sygnału."""
    proces, pid_plik, wyslane = atrapa
    proces(5151, '/usr/sbin/sshd', '-D')
    pid_plik.write_text('5151')

    async def sc(cl):
        await cl.post('/?lektorqdel=ext', auth=AU)
        await cl.post('/?lektorqpause=ext&mode=hold', auth=AU)
        await cl.post('/?lektorqresume=1', auth=AU)
        return None
    uruchom(_k(tmp_path), sc)
    assert wyslane == []


def test_brak_pliku_pid_brak_sygnalow(tmp_path, atrapa):
    proces, _, wyslane = atrapa
    proces(6161, 'python3', '/x/czytaj_tts.py', 'a.md')       # działa, ale bez pliku PID

    async def sc(cl):
        await cl.post('/?lektorqdel=ext', auth=AU)
        await cl.post('/?lektorqpause=ext&mode=hold', auth=AU)
        return None
    uruchom(_k(tmp_path), sc)
    assert wyslane == []


@pytest.mark.parametrize('zawartosc', ['', 'abc', '-5', '1', '0'])
def test_zly_plik_pid(tmp_path, atrapa, zawartosc):
    proces, pid_plik, wyslane = atrapa
    proces(1, 'python3', '/x/czytaj_tts.py')
    pid_plik.write_text(zawartosc)

    async def sc(cl):
        return (await cl.post('/?lektorqdel=ext', auth=AU)).status
    assert uruchom(_k(tmp_path), sc) == 200
    assert wyslane == []


def test_pauza_i_wznowienie_ext(tmp_path, atrapa):
    import server
    proces, pid_plik, wyslane = atrapa
    proces(7272, 'python3', '/x/czytaj_tts.py', 'a.md')
    pid_plik.write_text('7272')

    async def sc(cl):
        await cl.post('/?lektorqpause=ext&mode=hold', auth=AU)
        await cl.post('/?lektorqresume=1', auth=AU)
        return None
    uruchom(_k(tmp_path), sc)
    assert wyslane == [(7272, server.SYGNAL_STOP), (7272, server.SYGNAL_CONT)]


# ── czytaj_tts.py: plik PID obok rygla ──────────────────────────────────────

def test_czytaj_tts_zapisuje_i_sprzata_plik_pid(tmp_path, monkeypatch):
    import os
    import czytaj_tts
    plik = tmp_path / 'lektor.pid'
    monkeypatch.setattr(czytaj_tts, 'PID_FILE', plik, raising=False)
    czytaj_tts._zapisz_pid()
    assert plik.read_text().strip() == str(os.getpid())
    czytaj_tts._usun_pid()
    assert not plik.exists()


def test_czytaj_tts_nie_kasuje_cudzego_pliku_pid(tmp_path, monkeypatch):
    import czytaj_tts
    plik = tmp_path / 'lektor.pid'
    monkeypatch.setattr(czytaj_tts, 'PID_FILE', plik, raising=False)
    plik.write_text('999999')                 # inny lektor zdążył zapisać swój
    czytaj_tts._usun_pid()
    assert plik.read_text() == '999999'
