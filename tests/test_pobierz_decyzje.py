"""narzedzia/pobierz_decyzje.py — skrypt agenta laptopa (plan 98, porcja B):
logowanie, pobranie eksportu, wydruk zmian per źródło, potwierdzenie, kody
wyjścia. Atrapa serwera HTTP w wątku testu na 127.0.0.1 (bez sieci);
skrypt uruchamiany jako podproces z limitem czasu (bez pytest-timeout)."""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

SKRYPT = Path(__file__).resolve().parents[1] / 'narzedzia' / 'pobierz_decyzje.py'
HASLO = 'tajne-haslo-atrapy-987'
CIASTKO = 'anberfiles_sesja=token-atrapy'

WYBORY = [
    {'id': 5, 'czas': '2026-10-10T21:04:12+02:00', 'zrodlo': 'skill-scout',
     'id_pozycji': 'skill-scout:audyt-skilli', 'wybor': 'zatwierdz', 'uwaga': 'pilne',
     'odcisk': 'o', 'tytul': 'Audyt skilli', 'tekst': 't', 'przeniesione': False,
     'kotwica': {'plik': 'Zasoby/raporty/skill-scout/_proposed.json', 'slug': 'audyt-skilli'}},
    {'id': 6, 'czas': '2026-10-10T21:05:00+02:00', 'zrodlo': 'reflect',
     'id_pozycji': 'reflect:abc', 'wybor': 'zatwierdz', 'uwaga': '', 'odcisk': 'o',
     'tytul': 'Reguła', 'tekst': 't', 'przeniesione': False,
     'kotwica': {'plik': 'Zasoby/Jarwis-vault-i-skille/_reflect-pending.md',
                 'naglowek': '## persona.md → § 7 — reguła'}},
    {'id': 7, 'czas': '2026-10-10T21:06:00+02:00', 'zrodlo': 'now', 'id_pozycji': 'now:x',
     'wybor': 'B', 'uwaga': '', 'odcisk': 'o', 'tytul': 'Nocna pętla', 'tekst': 't',
     'przeniesione': False,
     'kotwica': {'plik': '.claude/rules/NOW.md', 'linia': '- [ ] **Nocna pętla:** A · B'}},
    {'id': 8, 'czas': '2026-10-10T21:07:00+02:00', 'zrodlo': 'now-pending',
     'id_pozycji': 'now-pending:y', 'wybor': 'przyjete', 'uwaga': '', 'odcisk': 'o',
     'tytul': 'ALARM', 'tekst': 't', 'przeniesione': False,
     'kotwica': {'plik': 'NOW-pending.md', 'naglowek': '### ALARM automaty'}},
    {'id': 9, 'czas': '2026-10-10T21:08:00+02:00', 'zrodlo': 'skill-scout',
     'id_pozycji': 'skill-scout:zbedny', 'wybor': 'odrzuc', 'uwaga': '', 'odcisk': 'o',
     'tytul': 'Zbędny', 'tekst': 't', 'przeniesione': False,
     'kotwica': {'plik': 'Zasoby/raporty/skill-scout/_proposed.json', 'slug': 'zbedny'}},
]


class Atrapa:
    """Serwer udający AnberFiles: tryb = ok | pusty | zle_haslo | limit | wisi | zly_json."""

    def __init__(self, tryb='ok'):
        self.tryb, self.zadania = tryb, []
        atrapa = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _odp(self, kod, tresc=b'', naglowki=()):
                self.send_response(kod)
                for k, v in naglowki:
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(tresc)))
                self.end_headers()
                self.wfile.write(tresc)

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                cialo = self.rfile.read(n).decode()
                atrapa.zadania.append(('POST', self.path, {k.lower(): v for k, v in self.headers.items()}, cialo))
                if self.path == '/__anberfiles/zaloguj':
                    if atrapa.tryb == 'limit':
                        return self._odp(429, 'Za dużo prób'.encode())
                    if parse_qs(cialo).get('haslo') != [HASLO] or atrapa.tryb == 'zle_haslo':
                        return self._odp(401, b'zle')
                    return self._odp(303, b'', [('Location', '/'),
                                                ('Set-Cookie', CIASTKO + '; HttpOnly; Path=/')])
                if self.path == '/?przesluchania=potwierdz':
                    if self.headers.get('Cookie') != CIASTKO:
                        return self._odp(401)
                    if self.headers.get('X-AnberFiles') != '1':
                        return self._odp(403)
                    return self._odp(200, json.dumps({'potwierdzono_do': json.loads(cialo)['do']}).encode())
                self._odp(404)

            def do_GET(self):
                atrapa.zadania.append(('GET', self.path, {k.lower(): v for k, v in self.headers.items()}, ''))
                if self.headers.get('Cookie') != CIASTKO:
                    return self._odp(401)
                if atrapa.tryb == 'wisi':
                    time.sleep(30)
                    return self._odp(200, b'{}')
                if atrapa.tryb == 'zly_json':
                    return self._odp(200, b'<html>to nie JSON')
                pusty = atrapa.tryb == 'pusty'
                d = {'potwierdzono_do': 4 if pusty else 2, 'ostatnie_id': 4 if pusty else 9,
                     'decyzje': [] if pusty else [{'id': 3, 'sciezka': 'Zasoby/a.md'}],
                     'statusy': {} if pusty else {'Zasoby/a.md': 'odrzucone'},
                     'do_kolejki': [], 'wybory': [] if pusty else WYBORY}
                self._odp(200, json.dumps(d).encode(), [('Content-Type', 'application/json')])

        self.serwer = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.serwer.daemon_threads = True
        self.adres = f'http://127.0.0.1:{self.serwer.server_address[1]}'
        threading.Thread(target=self.serwer.serve_forever, daemon=True).start()

    def zamknij(self):
        self.serwer.shutdown()
        self.serwer.server_close()


@pytest.fixture
def atrapa():
    zrobione = []

    def zrob(tryb='ok'):
        a = Atrapa(tryb)
        zrobione.append(a)
        return a
    yield zrob
    for a in zrobione:
        a.zamknij()


def _uruchom(adres, *arg, haslo=HASLO, limit_s=2):
    env = {k: v for k, v in os.environ.items() if not k.startswith('ANBERFILES_')}
    if haslo is not None:
        env['ANBERFILES_HASLO'] = haslo
    argv = [sys.executable, str(SKRYPT), '--adres', adres, '--limit-s', str(limit_s), *arg]
    r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=limit_s + 10,
                       stdin=subprocess.DEVNULL)
    return r, argv


def test_wydruk_zmian_per_zrodlo_i_potwierdzenie(atrapa):
    """Test 29: logowanie, pobranie, wydruk „co nanieść gdzie” dla każdego
    źródła, --potwierdz wysyła do=ostatnie_id z nagłówkiem X-AnberFiles;
    hasło nie pojawia się w wydruku ani w argumentach procesu."""
    a = atrapa()
    r, argv = _uruchom(a.adres, '--potwierdz')
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert 'w elemencie "slug": "audyt-skilli" dopisz "status": "zatwierdzone"' in out
    assert 'uwaga:  pilne' in out
    assert 'zmień „- [ ] ✅ Zatwierdź tę zmianę” na „- [x] ✅ Zatwierdź tę zmianę”' in out
    assert '„— decyzja: wariant B (2026-10-10)”' in out
    assert 'usuń wpis „### ALARM automaty”' in out
    assert 'NOTATKA Zasoby/a.md: w nagłówku ustaw status: odrzucone' in out
    assert 'w elemencie "slug": "zbedny" dopisz "status": "odrzucone"' in out
    assert 'Potwierdzono przeniesienie do id 9.' in out
    assert HASLO not in out + r.stderr and HASLO not in ' '.join(argv)
    (pot,) = [z for z in a.zadania if z[1] == '/?przesluchania=potwierdz']
    assert json.loads(pot[3]) == {'do': 9} and pot[2].get('x-anberfiles') == '1'


def test_bez_potwierdz_tylko_czyta_i_json(atrapa):
    a = atrapa()
    r, _ = _uruchom(a.adres, '--json')
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert [w['id'] for w in d['wybory']] == [5, 6, 7, 8, 9]
    assert 'dopisz "status": "zatwierdzone"' in d['wybory'][0]['zmiana']
    assert not any(z[0] == 'POST' and 'potwierdz' in z[1] for z in a.zadania)


def test_kody_wyjscia(atrapa):
    """Test 30: 401 → 3, 429 → 3, brak hasła → 3, zawieszony serwer → 4
    w czasie ≤ limit + margines, odpowiedź nie-JSON → 5, brak nowych → 0
    z napisem, nieznany argument (np. hasło w argv) → 2."""
    r = _uruchom(atrapa('zle_haslo').adres)[0]
    assert r.returncode == 3 and HASLO not in r.stdout + r.stderr
    r = _uruchom(atrapa('limit').adres)[0]
    assert r.returncode == 3 and 'za dużo prób' in r.stderr
    assert HASLO not in r.stdout + r.stderr
    assert _uruchom(atrapa().adres, haslo=None)[0].returncode == 3
    t = time.monotonic()
    r = _uruchom(atrapa('wisi').adres, limit_s=1)[0]
    assert r.returncode == 4 and time.monotonic() - t < 1 + 8
    assert 'BŁĄD sieci' in r.stderr and HASLO not in r.stdout + r.stderr
    assert _uruchom(atrapa('zly_json').adres)[0].returncode == 5
    r = _uruchom(atrapa('pusty').adres, '--potwierdz')[0]
    assert r.returncode == 0 and 'Brak nowych decyzji do naniesienia' in r.stdout
    assert _uruchom(atrapa().adres, '--haslo', HASLO)[0].returncode == 2
    assert _uruchom('http://127.0.0.1:9', limit_s=1)[0].returncode == 4
