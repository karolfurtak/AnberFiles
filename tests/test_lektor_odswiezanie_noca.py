"""Odświeżanie nagrań lektora w trybie „nocą” — sztuczny zegar.

Karol 02.10: „C — nocą plus przycisk »nagraj teraz«.” Przy kilku poprawkach
z rzędu tryb natychmiastowy nagrywał ten sam dokument kilka razy.

Odbiór: zmiana treści w dzień → tylko oznaczenie (przycisk „🔄 nagraj teraz”
i napis „nowe nocą o 03:00” w podglądzie, widoku słuchania i na liście);
03:00 → zlecenie raz na dokument; po 06:30 → zadanie nie zaczyna się
i przechodzi na kolejną noc; „nagraj teraz” → zlecenie od razu; trwający
bieg Pulsa → przebieg nocny czeka. Testy budują własne drzewo w tmp_path;
Puls i zegar podstawione."""
import asyncio
import json
import re
from datetime import datetime

import pytest

from conftest import czekaj_na_lektora, uruchom, wczytaj
from conftest import zbuduj_jarvis as _zbuduj_jarvis
from test_lektor_odswiezanie import (NOWY, STARY, _czekaj_na_zadanie, _dokument,
                                     _korzen, _nagranie, _rejestr, _zadania)

import odswiezanie as _odsw

NAGLOWEK = '---\nstatus: do-akceptacji\n---\n'
DZIEN = datetime(2026, 10, 2, 10, 0)
NOC = datetime(2026, 10, 3, 3, 0)


def zbuduj_jarvis(tmp_path, **nadpisz):
    nadpisz.setdefault('logowanie', 'basic')
    nadpisz.setdefault('lektor_auto_odswiezanie', 'nocą')
    nadpisz.setdefault('przesluchania_zakres', (_korzen(tmp_path) / 'vault').as_posix())
    return _zbuduj_jarvis(tmp_path, **nadpisz)


@pytest.fixture
def zegar(monkeypatch):
    import server
    stan = {'t': DZIEN}
    monkeypatch.setattr(server, '_zegar', lambda: stan['t'])
    return stan


def _argv(tmp_path):
    return tmp_path / 'srv' / 'eksport' / 'ostatnie_argv.txt'


async def _czekaj(warunek, limit=10.0):
    t = 0.0
    while t < limit:
        if await warunek():
            return
        await asyncio.sleep(0.05)
        t += 0.05
    raise AssertionError('warunek niespełniony w czasie')


# ── w dzień: tylko oznaczenie, przycisk „nagraj teraz” ──────────────────────

def test_zmiana_w_dzien_tylko_oznacza(tmp_path, auth, puls, zegar):
    doc = _dokument(tmp_path, NAGLOWEK + NOWY)
    aud = _nagranie(tmp_path, NAGLOWEK + STARY)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    assert k.lektor_auto_odswiezanie == 'noca'

    async def sc(cl):
        import server
        await asyncio.sleep(0.3)                             # przegląd przy starcie
        dod = server.odswiez_nagrania()                      # kolejne przeglądy
        dod += server.odswiez_nagrania()
        jobs = await _zadania(cl, auth)
        lista = await (await cl.get('/?przesluchania=1', auth=auth)).text()
        sluchaj = await (await cl.get('/vault/a/notatka.md?sluchaj=1', auth=auth)).text()
        podglad = await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
        return dod, jobs, lista, sluchaj, podglad, server._odsw_nieaktualne(doc)
    dod, jobs, lista, sluchaj, podglad, nieakt = uruchom(k, sc)
    assert dod == [] and jobs == [], 'w dzień zmiana nie może zlecać nagrania'
    assert nieakt
    assert not _argv(tmp_path).exists()
    assert aud.read_bytes().endswith(b'STARE')               # stare nagranie zostaje
    for nazwa, html in (('lista', lista), ('słuchanie', sluchaj), ('podgląd', podglad)):
        assert 'nagranie nieaktualne — nowe nocą o 03:00' in html, nazwa
        assert 'class="nagrajteraz"' in html and '🔄 nagraj teraz' in html, nazwa
    assert '<audio id="au"' in sluchaj                       # stare do odtworzenia
    rej = _rejestr(tmp_path)
    assert rej.count('nagranie nieaktualne: vault/a/notatka.md') == 1   # raz, nie co minutę
    assert 'nowe nocą o 03:00' in rej
    assert 'tryb nocą — przebieg codziennie o 03:00, nowych nagrań nie zaczyna od 06:30' in rej


def test_nagraj_teraz_zleca_od_razu(tmp_path, auth, puls, zegar):
    doc = _dokument(tmp_path, NAGLOWEK + NOWY)
    aud = _nagranie(tmp_path, NAGLOWEK + STARY)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True               # ręczne zlecenie nie czeka na Puls (jak inne ręczne)

    async def sc(cl):
        await asyncio.sleep(0.3)
        lista = await (await cl.get('/?przesluchania=1', auth=auth)).text()
        u = re.search(r'class="nagrajteraz" data-u="([^"]+)"', lista).group(1)
        u = u.replace('&amp;', '&')
        r = await cl.post(u, auth=auth)
        assert r.status == 202, await r.text()
        jobs = await _zadania(cl, auth)
        await czekaj_na_lektora(cl, auth)
        import server
        server.odswiez_nagrania()
        po = await (await cl.get('/?przesluchania=1', auth=auth)).text()
        return u, jobs, po, server._odsw_nieaktualne(doc)
    u, jobs, po, nieakt = uruchom(k, sc)
    assert 'fmt=mp3' in u and 'silnik=piper' in u
    assert len(jobs) == 1 and jobs[0]['auto'] is False
    argv = _argv(tmp_path).read_text(encoding='utf-8')
    assert '--silnik piper' in argv and '--format mp3' in argv
    assert not aud.read_bytes().endswith(b'STARE')
    assert _odsw.czytaj_metryke(aud)['sha256'] == _odsw.odcisk_tekstu(NAGLOWEK + NOWY)
    assert not nieakt and 'nagrajteraz" data-u' not in po
    assert 'nagranie odświeżone przyciskiem „nagraj teraz”: vault/a/notatka.md' in _rejestr(tmp_path)


# ── nocą: jeden przebieg o 03:00, raz na dokument ───────────────────────────

def test_o_trzeciej_zlecenie_raz(tmp_path, auth, puls, zegar):
    _dokument(tmp_path, NOWY)
    aud = _nagranie(tmp_path, STARY)
    _dokument(tmp_path, NOWY, nazwa='druga')
    aud2 = _nagranie(tmp_path, STARY, nazwa='druga')
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True

    async def sc(cl):
        import server
        await asyncio.sleep(0.3)
        assert server.odswiez_nagrania() == []               # 10:00
        zegar['t'] = datetime(2026, 10, 3, 2, 59)
        assert server.odswiez_nagrania() == []               # przed oknem
        zegar['t'] = NOC
        d1 = server.odswiez_nagrania()
        zegar['t'] = datetime(2026, 10, 3, 3, 1)
        d2 = server.odswiez_nagrania()
        jobs = await _czekaj_na_zadanie(cl, auth, czeka='puls')
        puls['bieg'] = False
        await czekaj_na_lektora(cl, auth)
        server.odswiez_nagrania()                            # bilans przebiegu
        zegar['t'] = datetime(2026, 10, 3, 4, 0)
        d3 = server.odswiez_nagrania()
        return d1, d2, d3, jobs
    d1, d2, d3, jobs = uruchom(k, sc)
    assert len(d1) == 2 and all(j['auto'] and j.get('do') for j in d1)
    assert d2 == [] and d3 == []
    assert sorted(j['out'] for j in jobs) == ['druga_lektor.mp3', 'notatka_lektor.mp3']
    assert not aud.read_bytes().endswith(b'STARE') and not aud2.read_bytes().endswith(b'STARE')
    assert '--silnik piper' in _argv(tmp_path).read_text(encoding='utf-8')
    rej = _rejestr(tmp_path)
    assert ('przebieg nocny 2026-10-03 03:00 (nowych nagrań nie zaczyna od 06:30): '
            'nieaktualnych 2, zleconych 2') in rej
    assert rej.count('przebieg nocny 2026-10-03 03:00') == 1
    assert 'nagranie odświeżone: vault/a/notatka.md (zmiana treści' in rej
    assert ('przebieg nocny 2026-10-03 zakończony: zleconych 2, nagranych 2, '
            'przeniesionych na kolejną noc 0, nieudanych 0') in rej


def test_trwajacy_bieg_pulsa_przebieg_nocny_czeka(tmp_path, auth, puls, zegar):
    _dokument(tmp_path, NOWY)
    aud = _nagranie(tmp_path, STARY)
    zegar['t'] = NOC                                         # start usługi w oknie
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True

    async def sc(cl):
        await _czekaj_na_zadanie(cl, auth, czeka='puls')
        await asyncio.sleep(0.5)                             # 10 cykli czekania
        w_trakcie = (_argv(tmp_path).exists(), (await _zadania(cl, auth))[0])
        puls['bieg'] = False
        await czekaj_na_lektora(cl, auth)
        return w_trakcie
    uruchomiony, job = uruchom(k, sc)
    assert not uruchomiony, 'lektor ruszył mimo trwającego biegu Pulsa'
    assert job['state'] == 'queued' and job['czeka'] == 'puls' and job['auto']
    assert not aud.read_bytes().endswith(b'STARE')


def test_po_szostej_trzydziesci_nie_zaczyna(tmp_path, auth, puls, zegar):
    doc = _dokument(tmp_path, NOWY)
    aud = _nagranie(tmp_path, STARY)
    zegar['t'] = NOC
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True                                      # Puls pracuje do rana

    async def sc(cl):
        import server
        await _czekaj_na_zadanie(cl, auth, czeka='puls')
        zegar['t'] = datetime(2026, 10, 3, 6, 31)
        puls['bieg'] = False                                 # Puls wolny, ale okno zamknięte

        async def pusto():
            return not await _zadania(cl, auth)
        await _czekaj(pusto)
        await asyncio.sleep(0.2)
        po_oknie = (_argv(tmp_path).exists(), server._odsw_nieaktualne(doc))
        server.odswiez_nagrania()                            # bilans
        zegar['t'] = datetime(2026, 10, 3, 6, 45)
        d_rano = server.odswiez_nagrania()
        zegar['t'] = datetime(2026, 10, 4, 3, 0)             # kolejna noc
        d_noc2 = server.odswiez_nagrania()
        await czekaj_na_lektora(cl, auth)
        return po_oknie, d_rano, d_noc2
    (uruchomiony, nieakt), d_rano, d_noc2 = uruchom(k, sc)
    assert not uruchomiony, 'lektor zaczął nagranie po 06:30'
    assert nieakt, 'nagranie musi zostać nieaktualne do kolejnej nocy'
    assert d_rano == []
    assert len(d_noc2) == 1                                  # kolejna noc — zlecone
    assert not aud.read_bytes().endswith(b'STARE')
    rej = _rejestr(tmp_path)
    assert ('okno nocne zamknięte (06:30): vault/a/notatka.md nie rozpoczęte — '
            'przechodzi na kolejną noc') in rej
    assert 'zleconych 1, nagranych 0, przeniesionych na kolejną noc 1, nieudanych 0' in rej
    assert 'przebieg nocny 2026-10-04 03:00' in rej


def test_okno_przez_polnoc(tmp_path, auth, puls, zegar):
    _dokument(tmp_path, NOWY)
    _nagranie(tmp_path, STARY)
    k = wczytaj(zbuduj_jarvis(tmp_path, lektor_godzina_nocna='23:30',
                              lektor_nocne_okno_do='01:00'))
    puls['bieg'] = True

    async def sc(cl):
        import server
        await asyncio.sleep(0.3)
        zegar['t'] = datetime(2026, 10, 3, 0, 30)
        d = server.odswiez_nagrania()
        return d
    d = uruchom(k, sc)
    assert len(d) == 1
    assert d[0]['do'] == datetime(2026, 10, 3, 1, 0).timestamp()
    assert 'przebieg nocny 2026-10-02 23:30' in _rejestr(tmp_path)


def test_kolejka_po_restarcie_zachowuje_termin_okna(tmp_path, auth, puls, zegar):
    _dokument(tmp_path, NOWY)
    _nagranie(tmp_path, STARY)
    zegar['t'] = NOC
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True

    async def sc(cl):
        await _czekaj_na_zadanie(cl, auth, czeka='puls')
    uruchom(k, sc)
    zad = json.loads(k.kolejka_lektora.read_text(encoding='utf-8'))['jobs']
    assert zad and zad[0]['do'] == datetime(2026, 10, 3, 6, 30).timestamp()


# ── ustawienia ──────────────────────────────────────────────────────────────

def test_ustawienia_trybu_i_godzin(tmp_path):
    import konfiguracja
    for wart, oczek in (('tak', 'natychmiast'), ('natychmiast', 'natychmiast'),
                        ('nocą', 'noca'), ('noca', 'noca'), ('nie', 'nie')):
        assert wczytaj(zbuduj_jarvis(tmp_path, lektor_auto_odswiezanie=wart)
                       ).lektor_auto_odswiezanie == oczek, wart
    k = wczytaj(zbuduj_jarvis(tmp_path, lektor_godzina_nocna='2:15',
                              lektor_nocne_okno_do='05:45'))
    assert k.lektor_godzina_nocna == (2, 15) and k.lektor_nocne_okno_do == (5, 45)
    for klucz, wart in (('lektor_auto_odswiezanie', 'czasem'),
                        ('lektor_godzina_nocna', '24:00'),
                        ('lektor_nocne_okno_do', '6.30')):
        with pytest.raises(konfiguracja.BladKonfiguracji):
            wczytaj(zbuduj_jarvis(tmp_path, **{klucz: wart}))
    d = konfiguracja.domyslna()
    assert d.lektor_auto_odswiezanie == 'nie'
    assert d.lektor_godzina_nocna == (3, 0) and d.lektor_nocne_okno_do == (6, 30)
