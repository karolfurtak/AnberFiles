"""B9: ochrona przed żądaniami z obcych stron (DNS rebinding, CSRF).

(a) nagłówek Host: literał IP, localhost, *.local i dozwolone_hosty → przepuść;
    inna nazwa → 421 (rebinding wymaga nazwy domenowej napastnika);
(b) POST/PUT/PATCH/DELETE wymagają X-AnberFiles: 1 (dokładają go skrypty stron;
    formularz z obcej domeny nie doda nagłówka) → brak = 403;
    formularze logowania i „Ustaw hasło": zamiast nagłówka zgodność Origin z Host.
"""
import re

import aiohttp
import pytest

from conftest import HASLO, REPO, uruchom, wczytaj, zbuduj_anbernic, zbuduj_jarvis

AU = aiohttp.BasicAuth('anbernic', HASLO)


def _anbernic(tmp_path):
    conf = zbuduj_anbernic(tmp_path)
    (tmp_path / 'sprawozdania' / 'a.txt').write_text('A', encoding='utf-8')
    return wczytaj(conf)


# ── (b) nagłówek X-AnberFiles dla metod zmieniających stan ──────────────────

def test_post_bez_naglowka_403_z_naglowkiem_jak_dotad(tmp_path):
    k = _anbernic(tmp_path)

    async def sc(cl):
        bez = await cl.post('/?mkdir=obcy', auth=AU)
        z = await cl.post('/?mkdir=swoj', auth=AU, headers={'X-AnberFiles': '1'})
        return bez.status, z.status
    bez, z = uruchom(k, sc, naglowek=False)
    assert bez == 403
    assert z == 200
    assert not (tmp_path / 'sprawozdania' / 'obcy').exists()
    assert (tmp_path / 'sprawozdania' / 'swoj').is_dir()


@pytest.mark.parametrize('metoda,adres', [
    ('DELETE', '/a.txt'),
    ('POST', '/?lektorqshutdown=1&force=1'),
    ('POST', '/a.txt?rename=b.txt'),
    ('POST', '/'),                        # wgrywanie (przeciągnij-upuść)
    ('PUT', '/a.txt'),
    ('PATCH', '/a.txt'),
])
def test_metody_zmieniajace_stan_bez_naglowka_403(tmp_path, metoda, adres):
    k = _anbernic(tmp_path)

    async def sc(cl):
        return (await cl.request(metoda, adres, auth=AU)).status
    assert uruchom(k, sc, naglowek=False) == 403
    assert (tmp_path / 'sprawozdania' / 'a.txt').exists()


def test_zla_wartosc_naglowka_403(tmp_path):
    k = _anbernic(tmp_path)

    async def sc(cl):
        return (await cl.delete('/a.txt', auth=AU, headers={'X-AnberFiles': '0'})).status
    assert uruchom(k, sc, naglowek=False) == 403


def test_get_bez_naglowka_dziala(tmp_path):
    """Kontrola: odczyt (GET) nie wymaga nagłówka — linki i pasek adresu."""
    k = _anbernic(tmp_path)

    async def sc(cl):
        return (await cl.get('/a.txt', auth=AU)).status
    assert uruchom(k, sc, naglowek=False) == 200


def test_eksport_docx_tylko_post(tmp_path):
    """?docx=1 zapisuje plik w exports/ — GET zmieniający stan zamieniony na POST."""
    k = _anbernic(tmp_path)
    (tmp_path / 'sprawozdania' / 'd.md').write_text('# D\n', encoding='utf-8')

    async def sc(cl):
        return (await cl.get('/d.md?docx=1', auth=AU)).status
    assert uruchom(k, sc) == 405


# ── (a) nagłówek Host ───────────────────────────────────────────────────────

@pytest.mark.parametrize('host,oczekiwany', [
    ('evil.example', 421),
    ('evil.example:8765', 421),
    ('konsola.local.evil.example', 421),
    ('192.0.2.10:8000', 200),
    ('192.0.2.10', 200),
    ('[2001:db8::1]:8765', 200),
    ('localhost:8765', 200),
    ('konsola.local', 200),
    ('KONSOLA.LOCAL:8765', 200),
])
def test_host(tmp_path, host, oczekiwany):
    k = _anbernic(tmp_path)

    async def sc(cl):
        return (await cl.get('/', auth=AU, headers={'Host': host})).status
    assert uruchom(k, sc) == oczekiwany


def test_dozwolone_hosty_nazwa_i_sufiks(tmp_path):
    conf = zbuduj_jarvis(tmp_path, logowanie='basic',
                         dozwolone_hosty='jarvis.example.org, .ts.net')
    k = wczytaj(conf)
    assert k.dozwolone_hosty == ('jarvis.example.org', '.ts.net')
    au = aiohttp.BasicAuth(k.uzytkownik_www, HASLO)

    async def sc(cl):
        wyn = []
        for h in ('jarvis.example.org', 'pi.tailnet-abc.ts.net:8790', 'ts.net.evil.example',
                  'inny.example.org'):
            wyn.append((await cl.get('/', auth=au, headers={'Host': h})).status)
        return wyn
    assert uruchom(k, sc) == [200, 200, 421, 421]


def test_host_sprawdzany_przed_logowaniem(tmp_path):
    """Rebinding nie może nawet zobaczyć ekranu logowania."""
    conf = zbuduj_jarvis(tmp_path, logowanie='formularz')
    k = wczytaj(conf, haslo='')

    async def sc(cl):
        return (await cl.get('/', headers={'Host': 'evil.example',
                                           'Accept': 'text/html'})).status
    assert uruchom(k, sc) == 421


# ── formularze logowania: Origin zamiast nagłówka ───────────────────────────

def _formularz(tmp_path):
    import logowanie
    conf = zbuduj_jarvis(tmp_path, logowanie='formularz')
    k = wczytaj(conf, haslo='')
    logowanie.ustaw_haslo(k.katalog_auth, 'dobre-haslo-1234')
    return k


def test_logowanie_z_obcego_origin_403(tmp_path):
    import logowanie
    k = _formularz(tmp_path)

    async def sc(cl):
        host = f'127.0.0.1:{cl.port}'
        dane = {'haslo': 'dobre-haslo-1234', 'dalej': '/'}
        obcy = await cl.post(logowanie.SCIEZKA_LOGOWANIA, data=dane, allow_redirects=False,
                             headers={'Origin': 'http://evil.example'})
        null = await cl.post(logowanie.SCIEZKA_LOGOWANIA, data=dane, allow_redirects=False,
                             headers={'Origin': 'null'})
        swoj = await cl.post(logowanie.SCIEZKA_LOGOWANIA, data=dane, allow_redirects=False,
                             headers={'Origin': f'http://{host}'})
        brak = await cl.post(logowanie.SCIEZKA_LOGOWANIA, data=dane, allow_redirects=False)
        return obcy.status, null.status, swoj.status, brak.status
    # formularz przeglądarki nie dokłada X-AnberFiles — klient bez nagłówka
    assert uruchom(k, sc, naglowek=False) == (403, 403, 303, 303)


def test_ustaw_haslo_z_obcego_origin_403(tmp_path):
    import logowanie
    conf = zbuduj_jarvis(tmp_path, logowanie='formularz',
                         ustaw_haslo_bez_tokenu='127.0.0.0/8')
    k = wczytaj(conf, haslo='')

    async def sc(cl):
        dane = {'haslo': 'dobre-haslo-1234', 'powtorz': 'dobre-haslo-1234'}
        obcy = await cl.post(logowanie.SCIEZKA_USTAWIENIA, data=dane, allow_redirects=False,
                             headers={'Origin': 'http://evil.example'})
        ustawione_po_obcym = logowanie.haslo_ustawione(k.katalog_auth)
        swoj = await cl.post(logowanie.SCIEZKA_USTAWIENIA, data=dane, allow_redirects=False,
                             headers={'Origin': f'http://127.0.0.1:{cl.port}'})
        return obcy.status, ustawione_po_obcym, swoj.status
    assert uruchom(k, sc, naglowek=False) == (403, False, 303)


# ── formularze z Origin: null (zgłoszenie Karola 02.10.2026) ────────────────
# Chrome/Edge przy wysyłce <form method=post> ze strony z Referrer-Policy:
# no-referrer wysyłają „Origin: null". Dziennik Jarvisa 02.10 17:29–18:07:
# „formularz /__anberfiles/zaloguj z obcego originu 'null' od 100.97.34.18"
# → Karol nie mógł się zalogować (403). Odtworzenie: najpierw GET strony
# logowania (jak przeglądarka), potem POST z tym, co strona dała, i Origin: null.

def _pole(html: str, nazwa: str) -> str:
    m = re.search(rf'name="{nazwa}" value="([^"]*)"', html)
    return m.group(1) if m else ''


def test_logowanie_formularzem_przegladarki_origin_null_dziala(tmp_path):
    import logowanie
    k = _formularz(tmp_path)

    async def sc(cl):
        strona = await cl.get('/', headers={'Accept': 'text/html'})
        html = await strona.text()
        token = _pole(html, logowanie.POLE_FORMULARZA)
        dane = {'haslo': 'dobre-haslo-1234', 'dalej': _pole(html, 'dalej') or '/',
                logowanie.POLE_FORMULARZA: token}
        wyslij = await cl.post(logowanie.SCIEZKA_LOGOWANIA, data=dane,
                               allow_redirects=False, headers={'Origin': 'null'})
        lista = await cl.get('/', headers={'Accept': 'text/html'})
        return strona.status, bool(token), wyslij.status, lista.status
    # formularz przeglądarki nie dokłada X-AnberFiles — klient bez nagłówka
    assert uruchom(k, sc, naglowek=False) == (401, True, 303, 200)


def test_origin_null_bez_ciasteczka_formularza_403(tmp_path):
    """Obca strona (sandbox, data:) też wysyła Origin: null — ale nie ma
    ciasteczka SameSite=Strict ani nie policzy HMAC bez sekretu instancji."""
    import logowanie
    k = _formularz(tmp_path)

    async def sc(cl):
        html = await (await cl.get('/', headers={'Accept': 'text/html'})).text()
        token = _pole(html, logowanie.POLE_FORMULARZA)
        cl.session.cookie_jar.clear()              # żądanie z obcej strony: bez ciasteczka
        bez_ciastka = await cl.post(logowanie.SCIEZKA_LOGOWANIA, allow_redirects=False,
                                    data={'haslo': 'dobre-haslo-1234',
                                          logowanie.POLE_FORMULARZA: token},
                                    headers={'Origin': 'null'})
        await cl.get('/', headers={'Accept': 'text/html'})
        zly_token = await cl.post(logowanie.SCIEZKA_LOGOWANIA, allow_redirects=False,
                                  data={'haslo': 'dobre-haslo-1234',
                                        logowanie.POLE_FORMULARZA: '0' * 64},
                                  headers={'Origin': 'null'})
        zalogowany = await cl.get('/', headers={'Accept': 'text/html'})
        return bez_ciastka.status, zly_token.status, zalogowany.status
    assert uruchom(k, sc, naglowek=False) == (403, 403, 401)


def test_ustaw_haslo_formularzem_przegladarki_origin_null_dziala(tmp_path):
    import logowanie
    conf = zbuduj_jarvis(tmp_path, logowanie='formularz',
                         ustaw_haslo_bez_tokenu='127.0.0.0/8')
    k = wczytaj(conf, haslo='')

    async def sc(cl):
        html = await (await cl.get('/', headers={'Accept': 'text/html'})).text()
        dane = {'haslo': 'dobre-haslo-1234', 'powtorz': 'dobre-haslo-1234',
                logowanie.POLE_FORMULARZA: _pole(html, logowanie.POLE_FORMULARZA)}
        r = await cl.post(logowanie.SCIEZKA_USTAWIENIA, data=dane, allow_redirects=False,
                          headers={'Origin': 'null'})
        return r.status, logowanie.haslo_ustawione(k.katalog_auth)
    assert uruchom(k, sc, naglowek=False) == (303, True)


def test_ciasteczko_formularza_bez_secure_przez_http(tmp_path):
    """Przez HTTP (Tailscale) ciasteczko z flagą Secure nie zostałoby zapisane."""
    k = _formularz(tmp_path)

    async def sc(cl):
        r = await cl.get('/', headers={'Accept': 'text/html'})
        return r.headers.getall('Set-Cookie', [])
    ciastka = uruchom(k, sc, naglowek=False)
    assert ciastka and all('secure' not in c.lower() for c in ciastka)


# ── strony: każdy fetch/XHR zmieniający stan idzie przez wspólną funkcję ─────

def test_kod_stron_bez_golych_zadan_zmieniajacych_stan():
    """Statyczny strażnik: w kodzie stron nie ma fetch(…{method:"POST"/"DELETE"})
    ani XMLHttpRequest z POST poza wspólną funkcją JS_ZAPIS."""
    import server
    zrodlo = (REPO / 'app' / 'server.py').read_text(encoding='utf-8')
    zrodlo = zrodlo.replace(server.JS_ZAPIS, '')
    gole = re.findall(r'\bfetch\([^;]*?method:\s*\\?"(?:POST|DELETE|PUT|PATCH)', zrodlo)
    assert gole == []
    assert 'xhr.open("POST"' not in zrodlo or 'afXhr(xhr)' in zrodlo


def test_strony_maja_wspolna_funkcje_zapisu(tmp_path):
    """Listing (wgrywanie, usuwanie, lektor) i podgląd .md (druk, eksport, lektor)
    zawierają definicję funkcji dokładającej nagłówek."""
    import server
    k = _anbernic(tmp_path)
    (tmp_path / 'sprawozdania' / 'd.md').write_text('# D\n', encoding='utf-8')

    async def sc(cl):
        lst = await (await cl.get('/', auth=AU)).text()
        md = await (await cl.get('/d.md?view=1', auth=AU)).text()
        kol = await (await cl.get('/?lektorq=1', auth=AU)).text()
        return lst, md, kol
    for html in uruchom(k, sc):
        assert server.JS_ZAPIS in html
        assert "'X-AnberFiles':'1'" in server.JS_ZAPIS
