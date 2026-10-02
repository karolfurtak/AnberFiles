"""Wspólne narzędzia testów AnberFiles.

Testy budują WŁASNE katalogi w tmp_path — nigdy nie dotykają /mnt ani danych
użytkownika. Instancja „jarvis" = plik konfiguracja/jarvis.conf.przyklad
z podmienionym przedrostkiem /srv/anberfiles na katalog tymczasowy.
"""
import asyncio
import re
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP = REPO / 'app'
PRZYKLAD_JARVIS = REPO / 'konfiguracja' / 'jarvis.conf.przyklad'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

HASLO = 'test-haslo-123'

# Atrapa lektora: zamiast syntezy mowy (internet) zapisuje mały plik „mp3"
# i sidecar zdań; argumenty wywołania odkłada do ostatnie_argv.txt.
ATRAPA_TTS = r'''
import argparse, json, sys
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument('input'); ap.add_argument('-o', '--output')
ap.add_argument('--format'); ap.add_argument('--opisy'); ap.add_argument('--silnik')
a = ap.parse_args()
(Path(__file__).parent / 'ostatnie_argv.txt').write_text(' '.join(sys.argv[1:]), encoding='utf-8')
out = Path(a.output)
out.write_bytes(b'ID3' + bytes(64))
out.with_name(out.stem + '.cues.json').write_text(
    json.dumps([{"t": 0.0, "text": "Pierwsze zdanie."}]), encoding='utf-8')
'''

ATRAPA_TTS_BLAD = r'''
import sys
sys.stderr.write('ATRAPA: synteza nieudana\n')
sys.exit(3)
'''


def wolny_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def zbuduj_jarvis(tmp_path: Path, **nadpisz) -> Path:
    """Plik ustawień Jarvisa z katalogami w tmp_path; nadpisz=klucz→wartość."""
    baza = (tmp_path / 'srv').as_posix()
    tekst = PRZYKLAD_JARVIS.read_text(encoding='utf-8').replace('/srv/anberfiles', baza)
    for klucz, wart in nadpisz.items():
        tekst, n = re.subn(rf'(?m)^{klucz}\s*=.*$', f'{klucz} = {wart}', tekst)
        assert n == 1, f'brak klucza {klucz} w przykładzie'
    for d in ('korzen', 'korzen/lektor', 'dane', 'eksport'):
        (tmp_path / 'srv' / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / 'srv' / 'eksport' / 'czytaj_tts.py').write_text(ATRAPA_TTS, encoding='utf-8')
    conf = tmp_path / 'jarvis.conf'
    conf.write_text(tekst, encoding='utf-8')
    return conf


def zbuduj_anbernic(tmp_path: Path) -> Path:
    """Ustawienia jak na konsoli (moduły i tryb domyślne), tylko katalogi w tmp_path."""
    for d in ('dane', 'sprawozdania', 'sprawozdania/EXPORT'):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / 'sprawozdania' / 'EXPORT' / 'czytaj_tts.py').write_text(
        ATRAPA_TTS, encoding='utf-8')
    conf = tmp_path / 'anbernic.conf'
    conf.write_text(
        '[katalogi]\n'
        f'katalog_glowny = {(tmp_path / "sprawozdania").as_posix()}\n'
        f'katalog_danych = {(tmp_path / "dane").as_posix()}\n', encoding='utf-8')
    return conf


def wczytaj(conf: Path, haslo: str = HASLO):
    import konfiguracja
    return konfiguracja.wczytaj(conf, env={'SERVER_PASS': haslo})


# Nagłówek, który dokładają skrypty stron do żądań zmieniających stan (B9);
# klient testowy wysyła go domyślnie, bo testy symulują stronę AnberFiles.
NAGLOWEK_STRONY = {'X-AnberFiles': '1'}


def uruchom(k, scenariusz, naglowek: bool = True):
    """Serwer z ustawieniami k w pętli testowej; scenariusz(klient) → wynik.

    naglowek=True: klient dokłada do KAŻDEGO żądania X-AnberFiles: 1 (jak
    skrypty stron); False — klient „obcej strony" bez nagłówka.

    Nagłówki X-Test-Remote i X-Test-Scheme w żądaniu testowym podstawiają
    request.remote (adres klienta) i schemat (http/https) — pośrednik
    dokładany WYŁĄCZNIE tutaj, w testach; serwer nagłówków X-* nie czyta."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    import server

    @web.middleware
    async def podstaw_adres(request, handler):
        adres = request.headers.get('X-Test-Remote')
        if adres:
            request = request.clone(remote=adres)
        schemat = request.headers.get('X-Test-Scheme')
        if schemat:
            request = request.clone(scheme=schemat)
        return await handler(request)

    async def _run():
        app = server.utworz_aplikacje(k)
        app.middlewares.insert(0, podstaw_adres)
        async with TestClient(TestServer(app),
                              headers=NAGLOWEK_STRONY if naglowek else None) as cl:
            return await scenariusz(cl)
    return asyncio.run(_run())


async def czekaj_na_lektora(cl, auth, plik: Path = None, limit: float = 30.0):
    """Czeka, aż kolejka lektora opustoszeje (i plik powstanie, jeśli podany)."""
    t = 0.0
    while t < limit:
        r = await cl.get('/?lektorqj=1', auth=auth)
        j = await r.json()
        if not j['jobs'] and (plik is None or plik.exists()):
            return j
        await asyncio.sleep(0.2)
        t += 0.2
    raise AssertionError(f'lektor nie skończył w {limit} s: {j}')


@pytest.fixture
def auth():
    import aiohttp
    return aiohttp.BasicAuth('karol', HASLO)
