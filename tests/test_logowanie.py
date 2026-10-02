"""Logowanie formularzem z zapamiętaniem urządzenia (instancja Jarvis):
ekran „ustaw hasło" przy pierwszym uruchomieniu (tylko z sieci prywatnej
i Tailscale), trwałe ciasteczko podpisane HMAC-SHA256, wylogowanie, limit
prób, unieważnienie zmianą sekretu, reset poleceniem administracyjnym."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO, uruchom, wczytaj, zbuduj_jarvis

HASLO_F = 'dobre-haslo-1234'
HTML = {'Accept': 'text/html,application/xhtml+xml'}
API = {'Accept': '*/*'}
RESET = REPO / 'narzedzia' / 'anberfiles-reset-hasla'


def _instancja(tmp_path, haslo=HASLO_F, **nadpisz):
    """Jarvis w trybie formularz; haslo=None → pierwsze uruchomienie."""
    korzen = tmp_path / 'srv' / 'korzen'
    (korzen / 'vault').mkdir(parents=True, exist_ok=True)
    (korzen / 'vault' / 'a.md').write_text('# A\n', encoding='utf-8')
    nadpisz.setdefault('logowanie', 'formularz')
    conf = zbuduj_jarvis(tmp_path, **nadpisz)
    k = wczytaj(conf, haslo='')                 # SERVER_PASS pusty: nie potrzebny
    if haslo is not None:
        import logowanie
        logowanie.ustaw_haslo(k.katalog_auth, haslo)
    return conf, k


def _token(odp) -> str:
    """Wartość ciasteczka sesji z nagłówka Set-Cookie ('' = brak)."""
    import logowanie
    for h in odp.headers.getall('Set-Cookie', []):
        if h.startswith(logowanie.CIASTECZKO + '='):
            return h.split(';', 1)[0].split('=', 1)[1]
    return ''


def _naglowek_ciasteczka(odp) -> str:
    import logowanie
    for h in odp.headers.getall('Set-Cookie', []):
        if h.startswith(logowanie.CIASTECZKO + '='):
            return h
    return ''


async def _zaloguj(cl, haslo=HASLO_F, **kw):
    import logowanie
    r = await cl.post(logowanie.SCIEZKA_LOGOWANIA, data={'haslo': haslo, 'dalej': '/'},
                      allow_redirects=False, **kw)
    cl.session.cookie_jar.clear()               # ciasteczko podajemy jawnie
    return r


def _c(token):
    import logowanie
    return {'Cookie': f'{logowanie.CIASTECZKO}={token}'}


@pytest.fixture(autouse=True)
def _bez_opoznienia(monkeypatch):
    import logowanie
    monkeypatch.setattr(logowanie, 'OPOZNIENIE_ZLE_HASLO', 0.01)


# ── bez ciasteczka ──────────────────────────────────────────────────────────

def test_bez_ciasteczka_formularz_html_a_api_401(tmp_path):
    _, k = _instancja(tmp_path)

    async def sc(cl):
        r1 = await cl.get('/', headers=HTML)
        r2 = await cl.get('/?lektorqj=1', headers=API)
        r3 = await cl.get('/vault/a.md', headers=API)
        return (r1.status, r1.content_type, await r1.text(),
                r2.status, r2.content_type, r3.status, await r3.text())
    s1, ct1, html, s2, ct2, s3, t3 = uruchom(k, sc)
    assert s1 == 401 and ct1 == 'text/html'
    assert '<form' in html and 'type="password"' in html
    assert 'WWW-Authenticate' not in html
    assert s2 == 401 and ct2 != 'text/html'
    assert s3 == 401 and '# A' not in t3


def test_brak_okna_basic_w_trybie_formularz(tmp_path):
    _, k = _instancja(tmp_path)

    async def sc(cl):
        return (await cl.get('/', headers=HTML)).headers.get('WWW-Authenticate')
    assert uruchom(k, sc) is None


def test_haslo_basic_nie_wpuszcza_w_trybie_formularz(tmp_path):
    import aiohttp
    _, k = _instancja(tmp_path)

    async def sc(cl):
        return (await cl.get('/', headers=API,
                             auth=aiohttp.BasicAuth('karol', HASLO_F))).status
    assert uruchom(k, sc) == 401


# ── logowanie ───────────────────────────────────────────────────────────────

def test_zle_haslo_odmowa_bez_ciasteczka(tmp_path):
    _, k = _instancja(tmp_path)

    async def sc(cl):
        r = await _zaloguj(cl, 'zle-haslo-0000')
        return r.status, _token(r), await r.text()
    st, tok, html = uruchom(k, sc)
    assert st == 401
    assert tok == ''
    assert 'type="password"' in html


def test_poprawne_haslo_trwale_ciasteczko_i_listing(tmp_path):
    _, k = _instancja(tmp_path)

    async def sc(cl):
        r = await _zaloguj(cl)
        tok = _token(r)
        lst = await cl.get('/', headers={**HTML, **_c(tok)})
        return r.status, r.headers.get('Location'), _naglowek_ciasteczka(r), \
            lst.status, await lst.text()
    st, loc, ciast, st_l, html = uruchom(k, sc)
    assert st == 303 and loc == '/'
    atr = [a.strip().lower() for a in ciast.split(';')]
    assert 'httponly' in atr
    assert 'samesite=strict' in atr
    assert 'path=/' in atr
    assert 'secure' not in atr                      # żądanie przez HTTP
    max_age = [a for a in atr if a.startswith('max-age=')]
    assert max_age and int(max_age[0].split('=')[1]) >= 5 * 365 * 24 * 3600
    assert st_l == 200
    assert 'vault/' in html
    assert 'wyloguj' in html.lower()


def test_secure_przy_https(tmp_path):
    _, k = _instancja(tmp_path)

    async def sc(cl):
        r = await _zaloguj(cl, headers={'X-Test-Scheme': 'https'})
        return _naglowek_ciasteczka(r)
    atr = [a.strip().lower() for a in uruchom(k, sc).split(';')]
    assert 'secure' in atr


def test_dalej_tylko_sciezka_lokalna(tmp_path):
    import logowanie
    _, k = _instancja(tmp_path)

    async def sc(cl):
        r = await cl.post(logowanie.SCIEZKA_LOGOWANIA,
                          data={'haslo': HASLO_F, 'dalej': '//zly.example/x'},
                          allow_redirects=False)
        return r.headers.get('Location')
    assert uruchom(k, sc) == '/'


@pytest.mark.parametrize('zmiana', ['ostatni_znak', 'obcy', 'pusty', 'smieci'])
def test_podrobione_ciasteczko_odmowa(tmp_path, zmiana):
    _, k = _instancja(tmp_path)

    async def sc(cl):
        tok = _token(await _zaloguj(cl))
        zly = {'ostatni_znak': tok[:-1] + ('0' if tok[-1] != '0' else '1'),
               'obcy': 'v1.' + 'a' * 16 + '.1700000000.' + 'b' * 32 + '.' + 'c' * 64,
               'pusty': '',
               'smieci': 'abc'}[zmiana]
        return (await cl.get('/', headers={**API, **_c(zly)})).status
    assert uruchom(k, sc) == 401


def test_zmiana_sekretu_uniewaznia_ciasteczka(tmp_path):
    import logowanie
    _, k = _instancja(tmp_path)

    async def sc(cl):
        tok = _token(await _zaloguj(cl))
        przed = (await cl.get('/', headers={**API, **_c(tok)})).status
        logowanie.zmien_sekret(k.katalog_auth)
        po = (await cl.get('/', headers={**API, **_c(tok)})).status
        return przed, po
    assert uruchom(k, sc) == (200, 401)


def test_zmiana_hasla_uniewaznia_ciasteczka(tmp_path):
    import logowanie
    _, k = _instancja(tmp_path)

    async def sc(cl):
        tok = _token(await _zaloguj(cl))
        (k.katalog_auth / logowanie.PLIK_HASLA).unlink()
        logowanie.ustaw_haslo(k.katalog_auth, HASLO_F)   # to samo hasło, nowa sól
        return (await cl.get('/', headers={**API, **_c(tok)})).status
    assert uruchom(k, sc) == 401


def test_wylogowanie_usuwa_ciasteczko(tmp_path):
    import logowanie
    _, k = _instancja(tmp_path)

    async def sc(cl):
        tok = _token(await _zaloguj(cl))
        r = await cl.get(logowanie.SCIEZKA_WYLOGOWANIA, headers={**HTML, **_c(tok)},
                         allow_redirects=False)
        return r.status, _naglowek_ciasteczka(r)
    st, ciast = uruchom(k, sc)
    assert st == 303
    atr = [a.strip().lower() for a in ciast.split(';')]
    assert ciast.startswith(logowanie.CIASTECZKO + '=;') or \
        ciast.startswith(logowanie.CIASTECZKO + '=""')
    assert 'max-age=0' in atr


def test_limit_prob_na_adres(tmp_path):
    import logowanie
    _, k = _instancja(tmp_path)

    async def sc(cl):
        h = {'X-Test-Remote': '100.64.7.7'}
        zle = [(await _zaloguj(cl, 'zle-haslo-0000', headers=h)).status
               for _ in range(logowanie.LIMIT_PROB)]
        nadmiar = await _zaloguj(cl, HASLO_F, headers=h)
        inny = await _zaloguj(cl, HASLO_F, headers={'X-Test-Remote': '100.64.8.8'})
        return zle, nadmiar.status, _token(nadmiar), inny.status
    zle, st_n, tok_n, st_i = uruchom(k, sc)
    assert zle == [401] * len(zle)
    assert st_n == 429 and tok_n == ''              # nawet dobre hasło odrzucone
    assert st_i == 303                              # inny adres bez blokady


# ── pierwsze uruchomienie ───────────────────────────────────────────────────

@pytest.mark.parametrize('adres', ['127.0.0.1', '100.64.1.2', '100.127.255.1',
                                   '192.168.1.10', '10.1.2.3', '172.16.0.5', '::1',
                                   'fd7a:115c:a1e0::1'])
def test_ekran_ustawienia_z_sieci_prywatnej(tmp_path, adres):
    _, k = _instancja(tmp_path, haslo=None)

    async def sc(cl):
        r = await cl.get('/', headers={**HTML, 'X-Test-Remote': adres})
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 200
    assert 'Ustaw hasło' in html and 'name="powtorz"' in html


@pytest.mark.parametrize('adres', ['203.0.113.5', '8.8.8.8', '100.128.0.1',
                                   '2001:db8::1'])
def test_ekran_ustawienia_z_adresu_publicznego_403(tmp_path, adres):
    import logowanie
    _, k = _instancja(tmp_path, haslo=None)

    async def sc(cl):
        h = {'X-Test-Remote': adres}
        g = await cl.get('/', headers={**HTML, **h})
        p = await cl.post(logowanie.SCIEZKA_USTAWIENIA, headers=h, allow_redirects=False,
                          data={'haslo': HASLO_F, 'powtorz': HASLO_F})
        return g.status, await g.text(), p.status, _token(p)
    sg, html, sp, tok = uruchom(k, sc)
    assert sg == 403 and sp == 403 and tok == ''
    assert 'Ustaw hasło' not in html or 'type="password"' not in html
    assert not (k.katalog_auth / logowanie.PLIK_HASLA).exists()


def test_naglowek_x_forwarded_for_ignorowany(tmp_path):
    import logowanie
    _, k = _instancja(tmp_path, haslo=None)

    async def sc(cl):
        h = {'X-Test-Remote': '203.0.113.5', 'X-Forwarded-For': '127.0.0.1'}
        return (await cl.post(logowanie.SCIEZKA_USTAWIENIA, headers=h,
                              data={'haslo': HASLO_F, 'powtorz': HASLO_F})).status
    assert uruchom(k, sc) == 403


@pytest.mark.parametrize('haslo,powtorz', [('krotkie', 'krotkie'),
                                           (HASLO_F, HASLO_F + 'x'),
                                           ('', '')])
def test_ustawienie_hasla_walidacja(tmp_path, haslo, powtorz):
    import logowanie
    _, k = _instancja(tmp_path, haslo=None)

    async def sc(cl):
        r = await cl.post(logowanie.SCIEZKA_USTAWIENIA, allow_redirects=False,
                          data={'haslo': haslo, 'powtorz': powtorz})
        return r.status, _token(r)
    st, tok = uruchom(k, sc)
    assert st == 400 and tok == ''
    assert not (k.katalog_auth / logowanie.PLIK_HASLA).exists()


def test_ustawienie_hasla_skrot_ciasteczko_i_ekran_znika(tmp_path):
    import logowanie
    _, k = _instancja(tmp_path, haslo=None)

    async def sc(cl):
        r = await cl.post(logowanie.SCIEZKA_USTAWIENIA, allow_redirects=False,
                          data={'haslo': HASLO_F, 'powtorz': HASLO_F})
        cl.session.cookie_jar.clear()
        tok = _token(r)
        lst = await cl.get('/', headers={**HTML, **_c(tok)})
        bez = await cl.get('/', headers=HTML)
        drugi = await cl.post(logowanie.SCIEZKA_USTAWIENIA, allow_redirects=False,
                              data={'haslo': 'inne-haslo-9999', 'powtorz': 'inne-haslo-9999'})
        return r.status, tok, lst.status, bez.status, await bez.text(), drugi.status
    st, tok, st_l, st_b, html_b, st_d = uruchom(k, sc)
    assert st == 303 and tok
    assert st_l == 200
    assert st_b == 401 and 'name="powtorz"' not in html_b   # już logowanie, nie ustawianie
    assert st_d == 403
    plik = k.katalog_auth / logowanie.PLIK_HASLA
    tekst = plik.read_text(encoding='utf-8')
    assert HASLO_F not in tekst
    dane = json.loads(tekst)
    assert dane['algorytm'] == 'scrypt' and dane['sol'] and dane['skrot']
    assert logowanie.sprawdz_haslo(k.katalog_auth, HASLO_F)
    assert not logowanie.sprawdz_haslo(k.katalog_auth, 'inne-haslo-9999')
    if sys.platform != 'win32':
        assert plik.stat().st_mode & 0o777 == 0o600


def test_sekret_tworzony_przy_starcie_0600(tmp_path):
    import logowanie
    _, k = _instancja(tmp_path, haslo=None)

    async def sc(cl):
        return None
    uruchom(k, sc)
    s = k.katalog_auth / logowanie.PLIK_SEKRETU
    assert s.is_file() and len(s.read_bytes()) == 32
    if sys.platform != 'win32':
        assert s.stat().st_mode & 0o777 == 0o600
        assert k.katalog_auth.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('adres,ok', [
    ('127.0.0.1', True), ('127.5.5.5', True), ('::1', True), ('10.0.0.1', True),
    ('172.31.255.255', True), ('172.32.0.1', False), ('192.168.0.1', True),
    ('100.64.0.1', True), ('100.127.255.254', True), ('100.63.255.255', False),
    ('fd7a:115c:a1e0::5', True), ('fd7a:115c:a1e1::5', False),
    ('203.0.113.5', False), ('::ffff:127.0.0.1', True), ('::ffff:8.8.8.8', False),
    ('', False), (None, False), ('nie-adres', False)])
def test_adresy_dozwolone_do_ustawienia(adres, ok):
    import logowanie
    assert logowanie.adres_dozwolony_do_ustawienia(adres) is ok


# ── reset poleceniem administracyjnym ───────────────────────────────────────

def _reset(conf, **env_extra):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', **env_extra)
    env.pop('ANBERFILES_CONF', None)
    return subprocess.run([sys.executable, str(RESET), '--conf', str(conf)], env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=60)


def test_reset_hasla_bez_restartu(tmp_path):
    import logowanie
    conf, k = _instancja(tmp_path)
    sekret = k.katalog_auth / logowanie.PLIK_SEKRETU
    wyniki = {}

    async def sc(cl):
        tok = _token(await _zaloguj(cl))
        wyniki['sekret_przed'] = sekret.read_bytes()
        wyniki['przed'] = (await cl.get('/', headers={**API, **_c(tok)})).status
        wyniki['reset'] = _reset(conf)
        wyniki['po'] = (await cl.get('/', headers={**API, **_c(tok)})).status
        r = await cl.get('/', headers=HTML)
        wyniki['ekran'] = (r.status, await r.text())
    uruchom(k, sc)
    r = wyniki['reset']
    assert r.returncode == 0, r.stderr
    assert 'ustaw nowe hasło' in r.stdout.lower()
    assert not (k.katalog_auth / logowanie.PLIK_HASLA).exists()
    assert sekret.read_bytes() != wyniki['sekret_przed'] and len(sekret.read_bytes()) == 32
    if sys.platform != 'win32':
        assert sekret.stat().st_mode & 0o777 == 0o600
    assert wyniki['przed'] == 200 and wyniki['po'] == 401
    st, html = wyniki['ekran']
    assert st == 200 and 'name="powtorz"' in html          # ekran ustawienia wrócił


def test_reset_brak_pliku_ustawien_czytelny_blad(tmp_path):
    r = _reset(tmp_path / 'nie-ma.conf')
    assert r.returncode != 0
    assert 'nie-ma.conf' in r.stderr
    assert 'Traceback' not in r.stderr


def _modul_resetu():
    loader = importlib.machinery.SourceFileLoader('reset_hasla', str(RESET))
    spec = importlib.util.spec_from_loader('reset_hasla', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.mark.skipif(sys.platform == 'win32', reason='uprawnienia uniksowe')
def test_reset_bez_roota_czytelny_blad(tmp_path):
    _, k = _instancja(tmp_path)
    mod = _modul_resetu()
    obcy = os.geteuid() + 4242
    blad = mod.sprawdz_uprawnienia(k.katalog_auth, euid=obcy)
    assert blad and 'sudo' in blad
    assert mod.sprawdz_uprawnienia(k.katalog_auth, euid=os.geteuid()) is None
    assert mod.sprawdz_uprawnienia(k.katalog_auth, euid=0) is None


# ── tryb basic i ustawienia ─────────────────────────────────────────────────

def test_logowanie_zla_wartosc_to_blad(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path, logowanie='oauth')
    with pytest.raises(konfiguracja.BladKonfiguracji):
        konfiguracja.wczytaj(conf, env={})


def test_formularz_bez_server_pass_startuje(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path, logowanie='formularz')
    k = konfiguracja.wczytaj(conf, env={})
    assert k.haslo == ''
    assert konfiguracja.sprawdz_przy_starcie(k) == []


def test_basic_bez_server_pass_nadal_odmowa(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path, logowanie='basic')
    k = konfiguracja.wczytaj(conf, env={})
    assert any('Brak hasła' in b for b in konfiguracja.sprawdz_przy_starcie(k))


def test_basic_nie_tworzy_katalogu_auth(tmp_path):
    k = wczytaj(zbuduj_jarvis(tmp_path, logowanie='basic'))

    async def sc(cl):
        return (await cl.get('/', headers=HTML)).headers.get('WWW-Authenticate')
    assert uruchom(k, sc) is not None                       # okno Basic jak dotąd
    assert not k.katalog_auth.exists()


def test_formularz_logowanie_nie_pisze_hasla_do_rejestru(tmp_path):
    _, k = _instancja(tmp_path)

    async def sc(cl):
        await _zaloguj(cl, 'zle-haslo-ZZZZ')
        await _zaloguj(cl)
    uruchom(k, sc)
    rej = k.rejestr_zdarzen
    tekst = rej.read_text(encoding='utf-8') if rej.exists() else ''
    assert HASLO_F not in tekst and 'zle-haslo-ZZZZ' not in tekst


def test_skrypt_resetu_uzywa_modulu_konfiguracji():
    tekst = RESET.read_text(encoding='utf-8')
    assert 'konfiguracja' in tekst and 'configparser' not in tekst
    assert Path(RESET).read_text(encoding='utf-8').startswith('#!/usr/bin/env python3')
