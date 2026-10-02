"""Nagrania lektora odświeżane samoczynnie po zmianie dokumentu.

Karol 02.10: „Przy okazji automatycznie powinien odtworzyć nowy plik audio po
jego modyfikacjach.”

Odbiór: zmiana treści → zadanie w kolejce; brak zmiany → nic; dokument bez
nagrania → nic; dwa przeglądy → jedno zadanie; trwający bieg Pulsa → czeka;
nagranie zlecone ręcznie ma pierwszeństwo. Testy budują własne drzewo
w tmp_path; Puls podstawiony (bez sieci)."""
import asyncio
import json
import os
import time

import pytest

from conftest import czekaj_na_lektora, uruchom, wczytaj
from conftest import zbuduj_jarvis as _zbuduj_jarvis

import nagrania as _nagr
import odswiezanie as _odsw

STARY = '# Notatka\n\nPierwsza wersja treści.\n'
NOWY = '# Notatka\n\nDruga wersja treści — po redakcji.\n'


def zbuduj_jarvis(tmp_path, **nadpisz):
    nadpisz.setdefault('logowanie', 'basic')
    nadpisz.setdefault('lektor_auto_odswiezanie', 'tak')
    return _zbuduj_jarvis(tmp_path, **nadpisz)


@pytest.fixture
def puls(monkeypatch):
    """Podstawiony Puls: stan['bieg'] = True → trwający bieg."""
    import server
    stan = {'bieg': False, 'zapytania': 0}

    def status():
        stan['zapytania'] += 1
        return {'current_run': {'id': 1} if stan['bieg'] else None,
                'current_runs': [1] if stan['bieg'] else []}
    monkeypatch.setattr(server, '_puls_status', status)
    monkeypatch.setattr(server, 'ODSW_CZEKAJ_S', 0.05)
    monkeypatch.setattr(server, 'ODSW_CO_S', 3600)       # przegląd startowy + ręczne wywołania
    return stan


def _korzen(tmp_path):
    return tmp_path / 'srv' / 'korzen'


def _dokument(tmp_path, tekst, nazwa='notatka', katalog='a'):
    d = _korzen(tmp_path) / 'vault' / katalog
    d.mkdir(parents=True, exist_ok=True)
    p = d / f'{nazwa}.md'
    p.write_bytes(tekst.encode('utf-8'))
    return p


def _nagranie(tmp_path, tekst_nagrany, nazwa='notatka', katalog='a', metryka=True,
              wiek_s=3600.0):
    """Nagranie w katalogu lektora; metryka z odciskiem tekst_nagrany."""
    kl = _korzen(tmp_path) / 'lektor' / 'vault' / katalog
    kl.mkdir(parents=True, exist_ok=True)
    aud = kl / f'{nazwa}_lektor.mp3'
    aud.write_bytes(b'ID3' + bytes(64) + b'STARE')
    (kl / f'{nazwa}_lektor.cues.json').write_text('[]', encoding='utf-8')
    if metryka:
        _odsw.zapisz_metryke(aud, f'vault/{katalog}/{nazwa}.md',
                             _odsw.odcisk_tekstu(tekst_nagrany), 'piper')
    t = time.time() - wiek_s
    os.utime(aud, (t, t))
    return aud


def _rejestr(tmp_path):
    p = tmp_path / 'srv' / 'dane' / 'anberfiles-events.log'
    return p.read_text(encoding='utf-8') if p.exists() else ''


async def _zadania(cl, auth):
    return (await (await cl.get('/?lektorqj=1', auth=auth)).json())['jobs']


async def _czekaj_na_zadanie(cl, auth, limit=10.0, czeka=None):
    """Zadanie w kolejce; czeka='puls' — także ustalony powód czekania (zadanie
    ustala go w swoim pierwszym kroku, chwilę po dopisaniu do kolejki)."""
    t = 0.0
    while t < limit:
        j = await _zadania(cl, auth)
        if j and (czeka is None or all(x['czeka'] == czeka for x in j)):
            return j
        await asyncio.sleep(0.05)
        t += 0.05
    raise AssertionError('brak zadania w kolejce')


# ── odcisk treści (czysta logika) ───────────────────────────────────────────

def test_odcisk_ignoruje_konce_linii_i_status_w_naglowku():
    a = _odsw.odcisk_tekstu('---\nstatus: do-akceptacji\n---\n# T\n\nZdanie.\n')
    assert a == _odsw.odcisk_tekstu('---\r\nstatus: zaakceptowane\r\n---\r\n# T\r\n\r\nZdanie.\r\n')
    assert a != _odsw.odcisk_tekstu('# T\n\nZdanie!\n')
    assert len(a) == 64


def test_metryka_usuwana_razem_z_nagraniem(tmp_path):
    aud = _nagranie(tmp_path, STARY)
    assert _odsw.plik_metryki(aud).exists()
    _nagr.usun_nagranie(aud)
    assert not _odsw.plik_metryki(aud).exists()


# ── zmiana treści → zadanie w kolejce, nowe nagranie, wpis w rejestrze ─────

def test_zmiana_tresci_daje_zadanie_i_nowe_nagranie(tmp_path, auth, puls):
    doc = _dokument(tmp_path, NOWY)
    aud = _nagranie(tmp_path, STARY, wiek_s=2 * 86400)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True                          # zatrzymuje zadanie w kolejce

    async def sc(cl):
        jobs = await _czekaj_na_zadanie(cl, auth, czeka='puls')
        lista = await (await cl.get('/?przesluchania=1', auth=auth)).text()
        sluchaj = await (await cl.get('/vault/a/notatka.md?sluchaj=1', auth=auth)).text()
        stary_bajt = aud.read_bytes()            # stare nagranie nadal na miejscu
        puls['bieg'] = False
        await czekaj_na_lektora(cl, auth)
        return jobs, lista, sluchaj, stary_bajt
    jobs, lista, sluchaj, stary_bajt = uruchom(k, sc)
    assert len(jobs) == 1 and jobs[0]['auto'] is True and jobs[0]['czeka'] == 'puls'
    assert jobs[0]['out'] == 'notatka_lektor.mp3'
    assert stary_bajt.endswith(b'STARE')
    assert '🔄 odświeżane po zmianie dokumentu' in sluchaj
    assert 'nieaktualne — nowe w przygotowaniu' in sluchaj
    argv = (tmp_path / 'srv' / 'eksport' / 'ostatnie_argv.txt').read_text(encoding='utf-8')
    assert '--silnik piper' in argv and str(doc) in argv
    assert not aud.read_bytes().endswith(b'STARE')           # zastąpione
    met = json.loads(_odsw.plik_metryki(aud).read_text(encoding='utf-8'))
    assert met['sha256'] == _odsw.odcisk_tekstu(NOWY)
    assert met['zrodlo'] == 'vault/a/notatka.md'
    rej = _rejestr(tmp_path)
    assert 'nagranie nieaktualne: vault/a/notatka.md' in rej
    assert 'nagranie odświeżone: vault/a/notatka.md (zmiana treści' in rej
    # wygasanie liczone od NOWEGO nagrania (3 dni w przykładzie Jarvisa)
    assert _nagr.pozostalo_s(aud, 3) > 3 * 86400 - 600


def test_lista_do_przesluchania_pokazuje_odswiezanie(tmp_path, auth, puls):
    (_korzen(tmp_path) / 'vault' / 'a').mkdir(parents=True)
    _dokument(tmp_path, '---\nstatus: do-akceptacji\n---\n' + NOWY)
    _nagranie(tmp_path, STARY)
    k = wczytaj(zbuduj_jarvis(tmp_path, przesluchania_zakres=(
        _korzen(tmp_path) / 'vault').as_posix()))
    puls['bieg'] = True

    async def sc(cl):
        await _czekaj_na_zadanie(cl, auth, czeka='puls')
        return await (await cl.get('/?przesluchania=1', auth=auth)).text()
    html = uruchom(k, sc)
    assert '🔄 odświeżane po zmianie dokumentu · ⏳ czeka na koniec biegu Pulsa' in html
    assert 'stare nagranie: nieaktualne — nowe w przygotowaniu' in html


# ── brak zmiany / brak nagrania / dwa przeglądy → bez nowych zadań ─────────

def test_brak_zmiany_nic(tmp_path, auth, puls):
    doc = _dokument(tmp_path, STARY.replace('\n', '\r\n'))   # inne końce linii
    _nagranie(tmp_path, STARY)
    t = time.time()
    os.utime(doc, (t, t))                                    # nowszy czas pliku
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        import server
        await asyncio.sleep(0.3)
        dod = server.odswiez_nagrania()
        return dod, await _zadania(cl, auth)
    dod, jobs = uruchom(k, sc)
    assert dod == [] and jobs == []
    assert 'nagranie nieaktualne' not in _rejestr(tmp_path)


def test_dokument_bez_nagrania_nic(tmp_path, auth, puls):
    _dokument(tmp_path, NOWY)
    _dokument(tmp_path, NOWY, nazwa='inna')
    _nagranie(tmp_path, NOWY, nazwa='inna')                  # aktualne
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        import server
        await asyncio.sleep(0.3)
        return server.odswiez_nagrania(), await _zadania(cl, auth)
    dod, jobs = uruchom(k, sc)
    assert dod == [] and jobs == []
    assert not (_korzen(tmp_path) / 'lektor' / 'vault' / 'a' / 'notatka_lektor.mp3').exists()


def test_dwa_przeglady_jedno_zadanie(tmp_path, auth, puls):
    _dokument(tmp_path, NOWY)
    _nagranie(tmp_path, STARY)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True

    async def sc(cl):
        import server
        await _czekaj_na_zadanie(cl, auth)                   # przegląd przy starcie
        d1 = server.odswiez_nagrania()                       # odświeżenie klonu nr 1
        d2 = server.odswiez_nagrania()                       # odświeżenie klonu nr 2
        return d1, d2, await _zadania(cl, auth)
    d1, d2, jobs = uruchom(k, sc)
    assert d1 == [] and d2 == []
    assert len(jobs) == 1


# ── obciążenie Jarvisa: Puls i nagrania ręczne mają pierwszeństwo ──────────

def test_trwajacy_bieg_pulsa_wstrzymuje_generowanie(tmp_path, auth, puls):
    _dokument(tmp_path, NOWY)
    aud = _nagranie(tmp_path, STARY)
    argv = tmp_path / 'srv' / 'eksport' / 'ostatnie_argv.txt'
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True

    async def sc(cl):
        await _czekaj_na_zadanie(cl, auth)
        await asyncio.sleep(0.5)                             # 10 cykli czekania
        w_trakcie = (argv.exists(), (await _zadania(cl, auth))[0])
        puls['bieg'] = False
        await czekaj_na_lektora(cl, auth)
        return w_trakcie
    (uruchomiony, job) = uruchom(k, sc)
    assert not uruchomiony, 'lektor ruszył mimo trwającego biegu Pulsa'
    assert job['state'] == 'queued' and job['czeka'] == 'puls'
    assert argv.exists() and not aud.read_bytes().endswith(b'STARE')
    assert puls['zapytania'] >= 2


def test_nagranie_reczne_ma_pierwszenstwo(tmp_path, auth, puls):
    _dokument(tmp_path, NOWY)
    _nagranie(tmp_path, STARY)
    reczny = _dokument(tmp_path, '# Inny\n\nRęcznie zlecony.\n', nazwa='reczny', katalog='b')
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True
    argv = tmp_path / 'srv' / 'eksport' / 'ostatnie_argv.txt'
    kolejnosc = []

    async def sc(cl):
        await _czekaj_na_zadanie(cl, auth)                   # automatyczne czeka (Puls)
        r = await cl.post('/vault/b/reczny.md?lektor=1&fmt=mp3&queue=1', auth=auth)
        assert r.status == 202, await r.text()
        puls['bieg'] = False                                 # Puls wolny, ręczne w kolejce
        for _ in range(400):
            if argv.exists():
                s = argv.read_text(encoding='utf-8')
                if not kolejnosc or kolejnosc[-1] != s:
                    kolejnosc.append(s)
            if not await _zadania(cl, auth):
                break
            await asyncio.sleep(0.02)
        await czekaj_na_lektora(cl, auth)
        kolejnosc.append(argv.read_text(encoding='utf-8'))
    uruchom(k, sc)
    assert str(reczny) in kolejnosc[0], f'pierwsze generowanie nie było ręczne: {kolejnosc}'
    assert 'notatka.md' in kolejnosc[-1]


# ── nagrania sprzed odcisków (stan wyjściowy po wdrożeniu) ─────────────────

def test_nagranie_bez_metryki_dokument_starszy_dostaje_metryke(tmp_path, auth, puls):
    doc = _dokument(tmp_path, STARY)
    t = time.time() - 7200
    os.utime(doc, (t, t))
    aud = _nagranie(tmp_path, STARY, metryka=False, wiek_s=3600)
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        import server
        await asyncio.sleep(0.3)
        return server.odswiez_nagrania(), await _zadania(cl, auth)
    dod, jobs = uruchom(k, sc)
    assert dod == [] and jobs == []
    assert _odsw.czytaj_metryke(aud)['sha256'] == _odsw.odcisk_tekstu(STARY)


def test_nagranie_bez_metryki_dokument_zmieniony_po_nagraniu(tmp_path, auth, puls):
    _dokument(tmp_path, NOWY)                                 # czas: teraz
    _nagranie(tmp_path, STARY, metryka=False, wiek_s=3600)    # godzinę temu
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True

    async def sc(cl):
        return await _czekaj_na_zadanie(cl, auth)
    jobs = uruchom(k, sc)
    assert len(jobs) == 1 and jobs[0]['auto']


# ── ustawienie instancji: Anbernic bez zmian ───────────────────────────────

def test_domyslnie_wylaczone(tmp_path, auth, puls):
    _dokument(tmp_path, NOWY)
    _nagranie(tmp_path, STARY)
    k = wczytaj(zbuduj_jarvis(tmp_path, lektor_auto_odswiezanie='nie'))
    assert k.lektor_auto_odswiezanie is False

    async def sc(cl):
        import server
        await asyncio.sleep(0.3)
        return server.odswiez_nagrania(), await _zadania(cl, auth)
    dod, jobs = uruchom(k, sc)
    assert dod == [] and jobs == []


def test_anbernic_domyslnie_nie():
    import konfiguracja
    assert konfiguracja.domyslna().lektor_auto_odswiezanie is False


def test_puls_zajety_z_odpowiedzi_api_status():
    assert _odsw.puls_zajety({'current_run': {'id': 5}, 'current_runs': []})
    assert _odsw.puls_zajety({'current_run': None, 'current_runs': [{'id': 5}]})
    assert not _odsw.puls_zajety({'current_run': None, 'current_runs': [],
                                  'queue_length': 4})
    assert not _odsw.puls_zajety(None)


def test_zmiana_tylko_w_zalaczniku_nic(tmp_path, auth, puls):
    """Załącznik za <!-- lektor: koniec --> nie jest czytany — jego zmiana nie
    wymusza nowego nagrania; zmiana przed znacznikiem — wymusza."""
    znacznik = '\n<!-- lektor: koniec -->\n## Załącznik\n\n'
    _dokument(tmp_path, STARY + znacznik + 'Źródło B.\n')
    _nagranie(tmp_path, STARY)
    _dokument(tmp_path, NOWY + znacznik + 'Źródło A.\n', nazwa='druga')
    _nagranie(tmp_path, STARY, nazwa='druga')
    k = wczytaj(zbuduj_jarvis(tmp_path))
    puls['bieg'] = True

    async def sc(cl):
        await _czekaj_na_zadanie(cl, auth)
        await asyncio.sleep(0.2)
        return await _zadania(cl, auth)
    jobs = uruchom(k, sc)
    assert [j['out'] for j in jobs] == ['druga_lektor.mp3']
