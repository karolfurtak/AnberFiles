"""Limity rozmiaru żądań i obserwacja pamięci (audyt bezpieczeństwa, B3).

Testy budują własne katalogi w tmp_path; pomiar pamięci dostaje atrapę /proc
(katalog z plikami status), więc działa także na Windows."""
import io
import json
import re

import aiohttp
import pytest

from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic, zbuduj_jarvis

HASLO_F = 'dobre-haslo-1234'
MB = 1024 * 1024


def _jarvis_formularz(tmp_path, haslo=HASLO_F):
    conf = zbuduj_jarvis(tmp_path, logowanie='formularz')
    k = wczytaj(conf, haslo='')
    if haslo is not None:
        import logowanie
        logowanie.ustaw_haslo(k.katalog_auth, haslo)
    return k


def _anbernic(tmp_path, serwer: str = ''):
    """Ustawienia konsoli; serwer = dodatkowe linie sekcji [serwer]."""
    conf = zbuduj_anbernic(tmp_path)
    if serwer:
        conf.write_text('[serwer]\n' + serwer + '\n' + conf.read_text(encoding='utf-8'),
                        encoding='utf-8')
    return wczytaj(conf)


AU = aiohttp.BasicAuth('anbernic', HASLO)


# ── logowanie: formularz ograniczony do 4 KiB, PRZED wczytaniem ──────────────

def test_logowanie_duze_cialo_413(tmp_path):
    import logowanie
    k = _jarvis_formularz(tmp_path)

    async def sc(cl):
        r = await cl.post(logowanie.SCIEZKA_LOGOWANIA, allow_redirects=False,
                          data={'haslo': 'x' * MB, 'dalej': '/'})
        return r.status
    assert uruchom(k, sc) == 413


def test_logowanie_male_cialo_dziala(tmp_path):
    """Kontrola rozróżniająca: zwykły formularz przechodzi limit (303)."""
    import logowanie
    k = _jarvis_formularz(tmp_path)

    async def sc(cl):
        r = await cl.post(logowanie.SCIEZKA_LOGOWANIA, allow_redirects=False,
                          data={'haslo': HASLO_F, 'dalej': '/'})
        return r.status
    assert uruchom(k, sc) == 303


def test_logowanie_chunked_ponad_limit_413(tmp_path):
    """Bez Content-Length (chunked): czytanie urywa się po limicie."""
    import logowanie
    k = _jarvis_formularz(tmp_path)

    async def cialo():
        yield b'haslo='
        for _ in range(64):
            yield b'x' * 1024

    async def sc(cl):
        r = await cl.post(logowanie.SCIEZKA_LOGOWANIA, allow_redirects=False, data=cialo(),
                          headers={'Content-Type': 'application/x-www-form-urlencoded'})
        return r.status
    assert uruchom(k, sc) == 413


def test_ustawienie_hasla_duze_cialo_413(tmp_path):
    import logowanie
    k = _jarvis_formularz(tmp_path, haslo=None)

    async def sc(cl):
        r = await cl.post(logowanie.SCIEZKA_USTAWIENIA, allow_redirects=False,
                          data={'haslo': 'x' * MB, 'powtorz': 'x' * MB})
        return r.status
    assert uruchom(k, sc) == 413
    assert not (k.katalog_auth / logowanie.PLIK_HASLA).exists()


# ── pozostałe żądania: ciało czytane do pamięci najwyżej 64 KiB ──────────────

def test_kadrowanie_duze_cialo_json_413(tmp_path):
    from PIL import Image
    k = _anbernic(tmp_path)
    Image.new('RGB', (20, 20), 'red').save(tmp_path / 'sprawozdania' / 'o.png')

    async def sc(cl):
        r = await cl.post('/o.png?crop', auth=AU,
                          json={'x': 0, 'y': 0, 'w': 5, 'h': 5, 'mode': 'copy',
                                'wypelnienie': 'x' * (200 * 1024)})
        return r.status
    assert uruchom(k, sc) == 413
    assert not (tmp_path / 'sprawozdania' / 'o_crop.png').exists()


# ── wgrywanie: strumieniowo, własny licznik bajtów ───────────────────────────

def _fd(nazwa, dane):
    fd = aiohttp.FormData()
    fd.add_field('file', io.BytesIO(dane), filename=nazwa)
    return fd


def test_wgrywanie_2mb_na_anbernicu_dziala(tmp_path):
    """Kontrola rozróżniająca: domyślny limit wgrywania (512 MiB) zostaje."""
    k = _anbernic(tmp_path)
    dane = b'a' * (2 * MB)

    async def sc(cl):
        r = await cl.post('/', data=_fd('duzy.bin', dane), auth=AU)
        return r.status
    assert uruchom(k, sc) == 200
    assert (tmp_path / 'sprawozdania' / 'duzy.bin').read_bytes() == dane


def test_wgrywanie_ponad_limit_413(tmp_path):
    k = _anbernic(tmp_path, 'limit_wgrywania_mb = 1')
    assert k.limit_wgrywania_mb == 1

    async def sc(cl):
        r = await cl.post('/', data=_fd('duzy.bin', b'a' * (2 * MB)), auth=AU)
        return r.status
    assert uruchom(k, sc) == 413
    korzen = tmp_path / 'sprawozdania'
    assert not (korzen / 'duzy.bin').exists()
    assert not list(korzen.glob('*.part'))


def test_wgrywanie_chunked_ponad_limit_413_bez_odpadkow(tmp_path):
    """Bez Content-Length: przerywa licznik bajtów, .part usunięty."""
    k = _anbernic(tmp_path, 'limit_wgrywania_mb = 1')
    granica = 'GRANICA0123456789'

    async def cialo():
        yield (f'--{granica}\r\nContent-Disposition: form-data; name="file"; '
               'filename="duzy.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
               ).encode()
        for _ in range(48):
            yield b'a' * (64 * 1024)
        yield f'\r\n--{granica}--\r\n'.encode()

    async def sc(cl):
        r = await cl.post('/', data=cialo(), auth=AU, headers={
            'Content-Type': f'multipart/form-data; boundary={granica}'})
        return r.status
    assert uruchom(k, sc) == 413
    korzen = tmp_path / 'sprawozdania'
    assert not (korzen / 'duzy.bin').exists()
    assert not list(korzen.glob('*.part'))


def test_limity_domyslne_w_ustawieniach():
    import konfiguracja
    k = konfiguracja.wczytaj(env={})
    assert k.limit_wgrywania_mb == 512
    assert k.prog_pamieci_mb == 400


@pytest.mark.parametrize('wartosc', ['0', '-5', 'duzo'])
def test_limit_zla_wartosc_to_blad(tmp_path, wartosc):
    import konfiguracja
    with pytest.raises(konfiguracja.BladKonfiguracji):
        _anbernic(tmp_path, f'limit_wgrywania_mb = {wartosc}')


# ── obserwacja pamięci ───────────────────────────────────────────────────────

def _proc(tmp_path, procesy):
    """Atrapa /proc: procesy = {pid: (ppid, rss_kB)}."""
    proc = tmp_path / 'proc'
    for pid, (ppid, rss) in procesy.items():
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / 'status').write_text(f'Name:\tx\nPPid:\t{ppid}\nVmRSS:\t {rss} kB\n',
                                  encoding='utf-8')
    (proc / 'self').mkdir()                      # katalog nienumeryczny — pomijany
    return proc


def test_pomiar_pamieci_sumuje_potomkow(tmp_path):
    import server
    proc = _proc(tmp_path, {100: (1, 50 * 1024),          # serwer
                            200: (100, 30 * 1024),        # soffice (dziecko)
                            201: (200, 20 * 1024),        # soffice.bin (wnuk)
                            300: (1, 999 * 1024)})        # obcy proces
    m = server.zmierz_pamiec(100, proc)
    assert m == {'serwer_mb': 50.0, 'potomne_mb': 50.0, 'razem_mb': 100.0}


def test_pomiar_pamieci_bez_proc_zwraca_none(tmp_path):
    import server
    assert server.zmierz_pamiec(100, tmp_path / 'nie-ma') is None


def _obserwator(prog, odczyty, zegar):
    import server
    wpisy = []
    it = iter(odczyty)

    def pomiar():
        v = next(it)
        return {'serwer_mb': v, 'potomne_mb': 0.0, 'razem_mb': v}
    obs = server.ObserwatorPamieci(
        prog_mb=prog, pomiar=pomiar,
        rejestr=lambda kind, msg, level='info': wpisy.append((level, kind, msg)),
        zegar=lambda: zegar[0])
    return obs, wpisy


def test_obserwator_przekroczenie_progu_wpis_warn(tmp_path):
    zegar = [0.0]
    obs, wpisy = _obserwator(400, [450.0, 460.0, 470.0], zegar)
    obs.krok()
    zegar[0] = 60
    obs.krok()                                   # w ciągu 10 min — bez drugiego
    assert [w for w in wpisy if w[0] == 'warn'] and \
        len([w for w in wpisy if w[0] == 'warn']) == 1
    zegar[0] = 601
    obs.krok()                                   # po 10 min — kolejne ostrzeżenie
    assert len([w for w in wpisy if w[0] == 'warn']) == 2
    assert all(w[1] == 'pamiec' for w in wpisy)


def test_obserwator_ponizej_progu_bez_warn(tmp_path):
    """Kontrola rozróżniająca: te same kroki poniżej progu — zero ostrzeżeń."""
    zegar = [0.0]
    obs, wpisy = _obserwator(400, [100.0, 105.0, 300.0], zegar)
    for t in (0, 60, 601):
        zegar[0] = t
        obs.krok()
    assert not [w for w in wpisy if w[0] == 'warn']
    # szczyt: pierwszy zapis, +5 % pominięte, +185 % zapisane
    szczyty = [w for w in wpisy if 'szczyt' in w[2]]
    assert len(szczyty) == 2
    assert obs.szczyt_mb == 300.0 and obs.teraz_mb == 300.0


def test_rejestr_zdarzen_pokazuje_pamiec(tmp_path):
    import server
    k = _anbernic(tmp_path, 'prog_pamieci_mb = 100')

    async def sc(cl):
        server._PAMIEC.pomiar = lambda: {'serwer_mb': 150.0, 'potomne_mb': 10.0,
                                         'razem_mb': 160.0}
        server._PAMIEC.krok()
        r = await cl.get('/?events=1', auth=AU)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 200
    assert re.search(r'Pamięć: teraz 160 MB, szczyt 160 MB, próg 100 MB', html)
    assert 'pamiec-przekroczona' in html
    wpisy = (tmp_path / 'dane' / 'anberfiles-events.log').read_text(encoding='utf-8')
    assert '\twarn\tpamiec\t' in wpisy
    json.dumps(server._PAMIEC.stan())            # stan da się podać jako JSON
