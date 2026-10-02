"""Logowanie formularzem z zapamiętaniem urządzenia (ustawienie
[serwer] logowanie = formularz).

Przebieg:
  - pierwsze uruchomienie (brak pliku skrótu): ekran „Ustaw hasło", dostępny
    WYŁĄCZNIE z sieci lokalnej i Tailscale (adres z request.remote, nagłówki
    X-Forwarded-* są ignorowane) i tylko z jednorazowym tokenem startowym
    z dziennika usługi (klasa Bramka; zwolnienie: ustaw_haslo_bez_tokenu);
    po ustawieniu ekran znika na stałe,
  - poprawne hasło → trwałe ciasteczko (podpis HMAC-SHA256 sekretem instancji,
    ważne 10 lat, HttpOnly, SameSite=Strict, Secure przy HTTPS),
  - złe hasło → odmowa po opóźnieniu, limit prób na adres,
  - wylogowanie usuwa ciasteczko z przeglądarki,
  - formularze logowania i „Ustaw hasło" niosą ukryte pole `formularz`
    (HMAC sekretem instancji z losowej wartości ciasteczka `anberfiles_formularz`,
    SameSite=Strict). Wymagane, gdy przeglądarka wysyła `Origin: null` — tak
    robi Chrome/Edge przy wysyłce formularza ze strony z Referrer-Policy:
    no-referrer (02.10.2026: logowanie na Jarvisie kończyło się 403).

Pliki (katalog_danych/auth/, prawa 0600, katalog 0700):
  haslo.json — skrót hashlib.scrypt (sól losowa, parametry zapisane w pliku),
  sekret     — 32 bajty losowe, klucz podpisu ciasteczek.
Ciasteczko zawiera znacznik wersji skrótu hasła: zmiana hasła albo sekretu
unieważnia wszystkie urządzenia. Oba pliki są czytane przy każdym żądaniu
(z pamięcią podręczną zależną od czasu modyfikacji i numeru i-węzła), więc
reset poleceniem `anberfiles-reset-hasla` działa bez restartu usługi.
Resetu przez WWW nie ma.

Tylko biblioteka standardowa + aiohttp; Python 3.10–3.13.
"""
import asyncio
import hashlib
import hmac
import html as _html
import ipaddress
import json
import os
import re
import secrets
import sys
import time
from collections import deque
from urllib.parse import parse_qsl
from datetime import datetime
from pathlib import Path

from aiohttp import web

PLIK_HASLA = 'haslo.json'
PLIK_SEKRETU = 'sekret'
CIASTECZKO = 'anberfiles_sesja'
CIASTECZKO_FORMULARZA = 'anberfiles_formularz'   # wartość losowa do tokenu formularza
POLE_FORMULARZA = 'formularz'                    # ukryte pole z tokenem formularza
LINK_STARTOWY = ''      # przycisk „🏠 strona startowa” (HTML z server._link_startowy)
WAZNOSC_S = 10 * 365 * 24 * 3600          # „do odwołania" — 10 lat
MIN_DLUGOSC_HASLA = 10
LIMIT_PROB = 10                           # prób logowania na adres …
OKNO_PROB_S = 60.0                        # … w oknie tylu sekund
OPOZNIENIE_ZLE_HASLO = 1.0                # sekundy przed odpowiedzią na złe hasło
LIMIT_FORMULARZA = 4096                   # bajtów ciała formularza logowania/ustawienia

SCIEZKA_LOGOWANIA = '/__anberfiles/zaloguj'
SCIEZKA_USTAWIENIA = '/__anberfiles/ustaw-haslo'
SCIEZKA_WYLOGOWANIA = '/__anberfiles/wyloguj'
# klucz żądania aiohttp: strażnik źródła (server.py) zaznacza wysyłkę z Origin: null
WYMAGA_TOKENU = 'anberfiles_wymaga_tokenu_formularza'

# scrypt: 16 MiB pamięci na próbę (n·r·128 B, niezależnie od p); p=5 → ok. 0,5 s
# na Raspberry Pi 5 (p=1 do 02.10.2026 — stare pliki weryfikowane parametrami z pliku)
SCRYPT = {'n': 2 ** 14, 'r': 8, 'p': 5, 'dlugosc': 32}
_SCRYPT_MAXMEM = 128 * 1024 ** 2

# Sieci, z których wolno ustawić hasło przy pierwszym uruchomieniu
SIECI_USTAWIENIA = tuple(ipaddress.ip_network(s) for s in (
    '127.0.0.0/8', '::1/128',
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '100.64.0.0/10',                       # Tailscale (CGNAT)
    'fd7a:115c:a1e0::/48',                 # Tailscale IPv6
))


# ── pliki: skrót hasła i sekret ─────────────────────────────────────────────

_PAMIEC = {}


def _czytaj(sciezka: Path):
    """Zawartość pliku (bytes) albo None; ponowny odczyt tylko po zmianie
    pliku (czas modyfikacji, rozmiar, i-węzeł — podmiana przez rename też)."""
    try:
        st = os.stat(sciezka)
    except FileNotFoundError:
        _PAMIEC.pop(str(sciezka), None)
        return None
    klucz = (st.st_mtime_ns, st.st_size, st.st_ino)
    zapis = _PAMIEC.get(str(sciezka))
    if zapis and zapis[0] == klucz:
        return zapis[1]
    with open(sciezka, 'rb') as f:
        dane = f.read()
    _PAMIEC[str(sciezka)] = (klucz, dane)
    return dane


def _zapisz_wylacznie(sciezka: Path, dane: bytes, wlasciciel=None) -> bool:
    """Zapis 0600 tylko gdy pliku NIE ma (atomowo: plik tymczasowy + link).
    False = plik już istniał (nic nie zmieniono)."""
    tmp = sciezka.with_name(f'.{sciezka.name}.{os.getpid()}.{secrets.token_hex(4)}')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0),
                 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(dane)
            f.flush()
            os.fsync(f.fileno())
        _prawa(tmp, 0o600, wlasciciel)
        try:
            os.link(tmp, sciezka)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _zapisz_podmien(sciezka: Path, dane: bytes, wlasciciel=None) -> None:
    """Zapis 0600 z podmianą istniejącego pliku (atomowo: rename)."""
    tmp = sciezka.with_name(f'.{sciezka.name}.{os.getpid()}.{secrets.token_hex(4)}')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0),
                 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(dane)
            f.flush()
            os.fsync(f.fileno())
        _prawa(tmp, 0o600, wlasciciel)
        os.replace(tmp, sciezka)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prawa(sciezka: Path, tryb: int, wlasciciel=None) -> None:
    if os.name != 'posix':
        return
    if wlasciciel is not None:
        os.chown(sciezka, *wlasciciel)
    os.chmod(sciezka, tryb)


def przygotuj_katalog(katalog: Path) -> None:
    katalog = Path(katalog)
    katalog.mkdir(mode=0o700, parents=True, exist_ok=True)
    _prawa(katalog, 0o700)


def zapewnij_sekret(katalog: Path) -> bytes:
    """Sekret ciasteczek; tworzony (32 bajty losowe, 0600) przy pierwszym starcie."""
    katalog = Path(katalog)
    s = _czytaj(katalog / PLIK_SEKRETU)
    if s is None:
        przygotuj_katalog(katalog)
        _zapisz_wylacznie(katalog / PLIK_SEKRETU, secrets.token_bytes(32))
        s = _czytaj(katalog / PLIK_SEKRETU)
    if not s or len(s) < 32:
        raise OSError(f'{katalog / PLIK_SEKRETU}: sekret krótszy niż 32 bajty')
    return s


def zmien_sekret(katalog: Path, wlasciciel=None) -> None:
    """Nowy losowy sekret — unieważnia ciasteczka wszystkich urządzeń."""
    _zapisz_podmien(Path(katalog) / PLIK_SEKRETU, secrets.token_bytes(32), wlasciciel)


def haslo_ustawione(katalog: Path) -> bool:
    return _czytaj(Path(katalog) / PLIK_HASLA) is not None


def _skrot(haslo: str, sol: bytes, n: int, r: int, p: int, dlugosc: int) -> bytes:
    return hashlib.scrypt(haslo.encode('utf-8'), salt=sol, n=n, r=r, p=p,
                          dklen=dlugosc, maxmem=_SCRYPT_MAXMEM)


def ustaw_haslo(katalog: Path, haslo: str) -> bool:
    """Zapisuje skrót hasła. False = hasło już było ustawione (bez zmian)."""
    katalog = Path(katalog)
    przygotuj_katalog(katalog)
    sol = secrets.token_bytes(16)
    dane = {'algorytm': 'scrypt', **SCRYPT, 'sol': sol.hex(),
            'skrot': _skrot(haslo, sol, **SCRYPT).hex(),
            'utworzono': datetime.now().astimezone().isoformat(timespec='seconds')}
    return _zapisz_wylacznie(katalog / PLIK_HASLA,
                             json.dumps(dane, indent=1).encode('utf-8'))


def usun_haslo(katalog: Path) -> bool:
    try:
        (Path(katalog) / PLIK_HASLA).unlink()
        return True
    except FileNotFoundError:
        return False


def sprawdz_haslo(katalog: Path, haslo: str) -> bool:
    surowe = _czytaj(Path(katalog) / PLIK_HASLA)
    if surowe is None:
        return False
    try:
        d = json.loads(surowe.decode('utf-8'))
        if d.get('algorytm') != 'scrypt':
            return False
        oczek = bytes.fromhex(d['skrot'])
        wynik = _skrot(haslo, bytes.fromhex(d['sol']), int(d['n']), int(d['r']),
                       int(d['p']), int(d['dlugosc']))
    except (ValueError, KeyError, TypeError):
        return False                       # uszkodzony plik: nikt nie wejdzie (reset)
    return hmac.compare_digest(wynik, oczek)


def _wersja_hasla(katalog: Path):
    surowe = _czytaj(Path(katalog) / PLIK_HASLA)
    return None if surowe is None else hashlib.sha256(surowe).hexdigest()[:16]


# ── ciasteczko ──────────────────────────────────────────────────────────────

def _podpis(sekret: bytes, tresc: str) -> str:
    return hmac.new(sekret, tresc.encode('ascii'), hashlib.sha256).hexdigest()


def nowy_token(katalog: Path) -> str:
    """v1.<wersja skrótu hasła>.<czas wydania>.<losowe>.<HMAC-SHA256>"""
    wersja = _wersja_hasla(katalog)
    if wersja is None:
        raise ValueError('hasło nieustawione')
    tresc = f'v1.{wersja}.{int(time.time())}.{secrets.token_hex(16)}'
    return f'{tresc}.{_podpis(zapewnij_sekret(katalog), tresc)}'


def token_wazny(katalog: Path, token: str) -> bool:
    if not token or len(token) > 256:
        return False
    czesci = token.split('.')
    if len(czesci) != 5 or czesci[0] != 'v1':
        return False
    wersja = _wersja_hasla(katalog)
    sekret = _czytaj(Path(katalog) / PLIK_SEKRETU)
    if wersja is None or not sekret:
        return False
    tresc = '.'.join(czesci[:4])
    try:
        podpis_ok = hmac.compare_digest(_podpis(sekret, tresc).encode('ascii'),
                                        czesci[4].encode('ascii'))
        wydany = int(czesci[2])
    except (ValueError, UnicodeEncodeError):
        return False
    if not podpis_ok or not hmac.compare_digest(czesci[1].encode('ascii', 'replace'),
                                                wersja.encode('ascii')):
        return False
    teraz = time.time()
    return wydany <= teraz + 300 and teraz - wydany < WAZNOSC_S


# ── token formularza (CSRF przy Origin: null) ──────────────────────────────

def token_formularza(katalog: Path, losowe: str) -> str:
    """Token ukrytego pola: HMAC-SHA256(sekret instancji, losowe z ciasteczka).
    Obca strona nie zna sekretu, a ciasteczka SameSite=Strict nie dostanie."""
    return _podpis(zapewnij_sekret(katalog), f'formularz.{losowe}')


def _losowe_formularza(request) -> str:
    """Wartość z ciasteczka (wiele kart = ten sam token) albo nowa."""
    w = request.cookies.get(CIASTECZKO_FORMULARZA, '')
    return w if re.fullmatch(r'[0-9a-f]{32}', w) else secrets.token_hex(16)


def token_formularza_poprawny(katalog: Path, request, form: dict) -> bool:
    w = request.cookies.get(CIASTECZKO_FORMULARZA, '')
    pole = str(form.get(POLE_FORMULARZA, ''))
    if not re.fullmatch(r'[0-9a-f]{32}', w) or not pole:
        return False
    return hmac.compare_digest(token_formularza(katalog, w).encode('ascii'),
                               pole.encode('ascii', 'replace'))


def _formularz(request, katalog: Path, strona, *arg, **kw) -> web.Response:
    """Strona z formularzem + ukryte pole tokenu + ciasteczko z wartością losową."""
    losowe = _losowe_formularza(request)
    odp = strona(*arg, token_form=token_formularza(katalog, losowe), **kw)
    odp.set_cookie(CIASTECZKO_FORMULARZA, losowe, path='/', httponly=True,
                   samesite='Strict', secure=True if request.secure else None)
    return odp


def _pole_formularza(token_form: str) -> str:
    return (f'<input type="hidden" name="{POLE_FORMULARZA}" '
            f'value="{_html.escape(token_form, quote=True)}">' if token_form else '')


def _odmowa_formularza(request, bramka, adres) -> web.Response:
    bramka.rejestr('ochrona', f'formularz {request.path} z Origin '
                   f'{request.headers.get("Origin")!r} bez ważnego tokenu formularza '
                   f'od {adres}', level='warn')
    return web.Response(status=403, text='403 — formularz wysłany z innej strony albo '
                        'nieaktualny. Odśwież stronę logowania (F5) i spróbuj ponownie.')


# ── adresy i limit prób ─────────────────────────────────────────────────────

def adres_w_sieciach(adres, sieci) -> bool:
    """Czy adres klienta (request.remote) należy do którejś z sieci."""
    if not adres:
        return False
    try:
        ip = ipaddress.ip_address(str(adres).split('%', 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == s.version and ip in s for s in sieci)


def adres_dozwolony_do_ustawienia(adres) -> bool:
    """Czy z adresu klienta wolno ustawić hasło (sieć lokalna, Tailscale)."""
    return adres_w_sieciach(adres, SIECI_USTAWIENIA)


def _oglos_na_stderr(token: str) -> None:
    print('AnberFiles: hasło NIEUSTAWIONE — jednorazowy token startowy ekranu '
          f'„Ustaw hasło": {SCIEZKA_USTAWIENIA}?token={token}', file=sys.stderr, flush=True)


class Bramka:
    """Stan logowania w pamięci procesu: limit prób logowania i ustawienia
    hasła na adres (okno przesuwne) oraz jednorazowy token startowy.

    Token startowy (A1): przy nieustawionym haśle ekran „Ustaw hasło" przyjmuje
    hasło tylko z tokenem — losowym, wydrukowanym do dziennika usługi (stdout/
    stderr → journal) razem z gotowym adresem. Trzymany WYŁĄCZNIE w pamięci:
    nie leży na dysku (kopia plików instancji go nie zabierze), restart usługi
    wydaje nowy, ustawienie hasła go unieważnia. Adresy z bez_tokenu (ustawienie
    ustaw_haslo_bez_tokenu) ustawiają hasło bez tokenu; ograniczenie do sieci
    prywatnych i Tailscale obowiązuje zawsze."""

    def __init__(self, rejestr=None, bez_tokenu=(), oglos=None):
        self._proby = {}
        self.rejestr = rejestr or (lambda *a, **k: None)
        self.bez_tokenu = tuple(bez_tokenu)
        self.oglos = oglos or _oglos_na_stderr
        self.token_startowy = None

    def zapewnij_token(self) -> str:
        """Token startowy; nowy (i ogłoszony w dzienniku usługi), gdy brak."""
        if self.token_startowy is None:
            self.token_startowy = secrets.token_urlsafe(32)
            self.oglos(self.token_startowy)
            self.rejestr('logowanie', 'hasło nieustawione: wydano token startowy '
                                      '(adres w dzienniku usługi)')
        return self.token_startowy

    def uniewaznij_token(self) -> None:
        self.token_startowy = None

    def token_poprawny(self, token: str) -> bool:
        wzor = self.token_startowy
        if not wzor or not token:
            return False
        return hmac.compare_digest(str(token).encode('utf-8'), wzor.encode('utf-8'))

    def zwolniony_z_tokenu(self, adres) -> bool:
        return adres_w_sieciach(adres, self.bez_tokenu)

    def wolno(self, adres) -> bool:
        teraz = time.monotonic()
        kolejka = self._proby.setdefault(str(adres), deque())
        while kolejka and teraz - kolejka[0] > OKNO_PROB_S:
            kolejka.popleft()
        if len(kolejka) >= LIMIT_PROB:
            return False
        kolejka.append(teraz)
        if len(self._proby) > 10000:          # sprzątanie po wielu adresach
            for a in [a for a, q in self._proby.items()
                      if not q or teraz - q[-1] > OKNO_PROB_S]:
                del self._proby[a]
        return True


# ── strony ──────────────────────────────────────────────────────────────────

_STYL = ('body{font-family:system-ui,sans-serif;max-width:24rem;margin:12vh auto;'
         'padding:0 16px;color:#222}h1{font-size:1.3rem}input{display:block;'
         'width:100%;box-sizing:border-box;font-size:1rem;padding:.5rem;margin:.4rem 0 .8rem}'
         'button{font-size:1rem;padding:.5rem 1.2rem}.blad{color:#b00020}'
         '.muted{color:#666;font-size:.9rem}')


def _strona(tytul: str, tresc: str, status: int) -> web.Response:
    return web.Response(
        status=status, content_type='text/html', charset='utf-8',
        headers={'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY'},
        text=('<!doctype html><meta charset=utf-8>'
              '<meta name="viewport" content="width=device-width,initial-scale=1">'
              f'<title>{_html.escape(tytul)}</title><style>{_STYL}</style>'
              + (f'<div>{LINK_STARTOWY}</div>' if LINK_STARTOWY else '') + tresc))


def _strona_logowania(instancja: str, dalej: str, blad: str = '', status: int = 401,
                      token_form: str = ''):
    b = f'<p class="blad">{_html.escape(blad)}</p>' if blad else ''
    return _strona(f'AnberFiles — logowanie ({instancja})',
                   f'<h1>AnberFiles · {_html.escape(instancja)}</h1>{b}'
                   f'<form method="post" action="{SCIEZKA_LOGOWANIA}">'
                   '<label for="haslo">Hasło</label>'
                   '<input id="haslo" name="haslo" type="password" autofocus required '
                   'autocomplete="current-password">'
                   f'<input type="hidden" name="dalej" value="{_html.escape(dalej)}">'
                   + _pole_formularza(token_form) +
                   '<button type="submit">Zaloguj</button></form>'
                   '<p class="muted">To urządzenie zostanie zapamiętane — następnym '
                   'razem hasło nie będzie potrzebne.</p>', status)


def _pole_tokenu(token: str, wymagany: bool) -> str:
    """Token startowy z adresu (?token=) → pole ukryte; brak tokenu, a adres
    nie jest zwolniony → pole do wklejenia z podpowiedzią, skąd go wziąć."""
    if token:
        return (f'<input type="hidden" name="token" '
                f'value="{_html.escape(token, quote=True)}">')
    if not wymagany:
        return ''
    return ('<label for="token">Token startowy</label>'
            '<input id="token" name="token" type="text" required autocomplete="off" '
            'spellcheck="false">'
            '<p class="muted">Jednorazowy token jest w dzienniku usługi na serwerze: '
            '<code>sudo journalctl -u anberfiles -n 50 | grep token</code> — '
            'tam też gotowy adres z tokenem.</p>')


def _strona_ustawienia(instancja: str, blad: str = '', status: int = 200,
                       token: str = '', token_wymagany: bool = True, token_form: str = ''):
    b = f'<p class="blad">{_html.escape(blad)}</p>' if blad else ''
    return _strona(f'AnberFiles — ustaw hasło ({instancja})',
                   f'<h1>Ustaw hasło · {_html.escape(instancja)}</h1>{b}'
                   '<p class="muted">Pierwsze uruchomienie. Hasło (co najmniej '
                   f'{MIN_DLUGOSC_HASLA} znaków) chroni dostęp z każdego urządzenia. '
                   'Zmiana później tylko poleceniem administracyjnym na serwerze.</p>'
                   f'<form method="post" action="{SCIEZKA_USTAWIENIA}">'
                   + _pole_tokenu(token, token_wymagany) + _pole_formularza(token_form) +
                   '<label for="haslo">Hasło</label>'
                   '<input id="haslo" name="haslo" type="password" autofocus required '
                   f'minlength="{MIN_DLUGOSC_HASLA}" autocomplete="new-password">'
                   '<label for="powtorz">Powtórz hasło</label>'
                   '<input id="powtorz" name="powtorz" type="password" required '
                   f'minlength="{MIN_DLUGOSC_HASLA}" autocomplete="new-password">'
                   '<button type="submit">Ustaw hasło</button></form>', status)


def _chce_html(request) -> bool:
    if request.method not in ('GET', 'HEAD'):
        return False
    return ('text/html' in request.headers.get('Accept', '')
            or request.headers.get('Sec-Fetch-Mode') == 'navigate')


def _bezpieczne_dalej(dalej: str) -> str:
    if (not dalej or not dalej.startswith('/') or dalej.startswith('//')
            or dalej.startswith('/\\') or dalej.startswith('/__anberfiles/')
            or any(c in dalej for c in '\r\n')):
        return '/'
    return dalej


def _z_ciasteczkiem(request, odp: web.StreamResponse, katalog: Path):
    odp.set_cookie(CIASTECZKO, nowy_token(katalog), max_age=WAZNOSC_S, path='/',
                   httponly=True, samesite='Strict', secure=True if request.secure else None)
    return odp


def _przekieruj(cel: str) -> web.Response:
    return web.Response(status=303, headers={'Location': cel, 'Cache-Control': 'no-store'})


class FormularzZaDuzy(Exception):
    """Ciało formularza przekracza LIMIT_FORMULARZA (odpowiedź 413)."""


async def czytaj_formularz(request, limit: int = LIMIT_FORMULARZA) -> dict:
    """Pola formularza (application/x-www-form-urlencoded) czytane z limitem
    PRZED uwierzytelnieniem: Content-Length ponad limit → odmowa bez czytania;
    bez Content-Length (chunked) czytanie urywa się po limicie + 1 bajt.
    W pamięci nigdy więcej niż limit + jeden kawałek strumienia."""
    dl = request.content_length
    if dl is not None and dl > limit:
        raise FormularzZaDuzy()
    cialo = bytearray()
    while True:
        kawalek = await request.content.read(limit + 1 - len(cialo))
        if not kawalek:
            break
        cialo += kawalek
        if len(cialo) > limit:
            raise FormularzZaDuzy()
    return dict(parse_qsl(cialo.decode('utf-8', 'replace'), keep_blank_values=True))


def _za_duzy() -> web.Response:
    return web.Response(status=413, text=f'413 — formularz większy niż '
                                         f'{LIMIT_FORMULARZA} bajtów.')


async def _w_tle(funkcja, *arg):
    return await asyncio.get_running_loop().run_in_executor(None, funkcja, *arg)


# ── pośrednik żądań ─────────────────────────────────────────────────────────

async def obsluz(request, handler, katalog: Path, bramka: Bramka, instancja: str):
    """Pośrednik aiohttp trybu „formularz" (woła go middleware serwera)."""
    katalog = Path(katalog)
    sciezka = request.path
    adres = request.remote

    if sciezka == SCIEZKA_WYLOGOWANIA:
        odp = _przekieruj('/')
        odp.del_cookie(CIASTECZKO, path='/')
        bramka.rejestr('logowanie', f'wylogowanie z {adres}')
        return odp

    if not haslo_ustawione(katalog):
        return await _pierwsze_uruchomienie(request, katalog, bramka, instancja, adres)
    bramka.uniewaznij_token()            # hasło jest — token startowy przestaje działać

    if sciezka == SCIEZKA_USTAWIENIA:
        return web.Response(status=403, text='Hasło jest już ustawione. Zmiana tylko '
                                             'poleceniem anberfiles-reset-hasla na serwerze.')

    if sciezka == SCIEZKA_LOGOWANIA and request.method == 'POST':
        if not bramka.wolno(adres):
            bramka.rejestr('logowanie', f'limit prób przekroczony z {adres}', level='warn')
            return _formularz(request, katalog, _strona_logowania, instancja, '/',
                              'Za dużo prób. Odczekaj minutę.', 429)
        try:
            form = await czytaj_formularz(request)
        except FormularzZaDuzy:
            bramka.rejestr('logowanie', f'za duży formularz logowania z {adres}', level='warn')
            return _za_duzy()
        if request.get(WYMAGA_TOKENU) and not token_formularza_poprawny(katalog, request, form):
            return _odmowa_formularza(request, bramka, adres)
        dalej = _bezpieczne_dalej(str(form.get('dalej', '/')))
        if await _w_tle(sprawdz_haslo, katalog, str(form.get('haslo', ''))):
            bramka.rejestr('logowanie', f'zalogowano urządzenie z {adres}')
            return _z_ciasteczkiem(request, _przekieruj(dalej), katalog)
        await asyncio.sleep(OPOZNIENIE_ZLE_HASLO)
        bramka.rejestr('logowanie', f'złe hasło z {adres}', level='warn')
        return _formularz(request, katalog, _strona_logowania, instancja, dalej, 'Złe hasło.')

    if token_wazny(katalog, request.cookies.get(CIASTECZKO, '')):
        if sciezka == SCIEZKA_LOGOWANIA:
            return _przekieruj('/')
        return await handler(request)

    if _chce_html(request):
        dalej = '/' if sciezka == SCIEZKA_LOGOWANIA else _bezpieczne_dalej(request.path_qs)
        return _formularz(request, katalog, _strona_logowania, instancja, dalej)
    return web.Response(status=401, text='401 — wymagane zalogowanie (ciasteczko '
                                         'urządzenia nieważne albo brak).')


async def _pierwsze_uruchomienie(request, katalog, bramka, instancja, adres):
    if not adres_dozwolony_do_ustawienia(adres):
        bramka.rejestr('logowanie', f'odmowa ustawienia hasła z {adres}', level='warn')
        return web.Response(
            status=403, text='403 — hasło nie jest jeszcze ustawione, a ustawić je można '
                             'wyłącznie z sieci lokalnej albo Tailscale. Adres '
                             f'{adres} jest spoza tych sieci.')
    bramka.zapewnij_token()              # np. po resecie hasła bez restartu usługi
    zwolniony = bramka.zwolniony_z_tokenu(adres)
    if request.path == SCIEZKA_USTAWIENIA and request.method == 'POST':
        if not bramka.wolno(adres):
            return _formularz(request, katalog, _strona_ustawienia, instancja,
                              'Za dużo prób. Odczekaj minutę.', 429)
        try:
            form = await czytaj_formularz(request)
        except FormularzZaDuzy:
            bramka.rejestr('logowanie', f'za duży formularz ustawienia hasła z {adres}',
                           level='warn')
            return _za_duzy()
        if request.get(WYMAGA_TOKENU) and not token_formularza_poprawny(katalog, request, form):
            return _odmowa_formularza(request, bramka, adres)
        if not zwolniony and not bramka.token_poprawny(form.get('token', '')):
            bramka.rejestr('logowanie', f'ustawienie hasła bez ważnego tokenu startowego '
                                        f'z {adres}', level='warn')
            return _formularz(
                request, katalog, _strona_ustawienia,
                instancja, 'Brak albo zły token startowy. Weź adres z tokenem z dziennika '
                'usługi (token zmienia się przy każdym restarcie).', 403)
        haslo, powtorz = str(form.get('haslo', '')), str(form.get('powtorz', ''))
        tok = '' if zwolniony else str(form.get('token', ''))
        if len(haslo) < MIN_DLUGOSC_HASLA:
            return _formularz(
                request, katalog, _strona_ustawienia,
                instancja, f'Hasło musi mieć co najmniej {MIN_DLUGOSC_HASLA} znaków.', 400,
                tok, not zwolniony)
        if not hmac.compare_digest(haslo.encode('utf-8'), powtorz.encode('utf-8')):
            return _formularz(request, katalog, _strona_ustawienia, instancja,
                              'Hasła się różnią.', 400, tok, not zwolniony)
        if not await _w_tle(ustaw_haslo, katalog, haslo):
            return web.Response(status=403, text='Hasło zostało już ustawione.')
        bramka.uniewaznij_token()
        bramka.rejestr('logowanie', f'ustawiono hasło z {adres}'
                       + (' (adres zwolniony z tokenu)' if zwolniony else ' (token startowy)'))
        return _z_ciasteczkiem(request, _przekieruj('/'), katalog)
    if _chce_html(request):
        return _formularz(request, katalog, _strona_ustawienia, instancja,
                          token=request.query.get('token', '')[:200],
                          token_wymagany=not zwolniony)
    return web.Response(status=401, text='401 — hasło nieustawione: otwórz AnberFiles '
                                         'w przeglądarce i ustaw hasło.')
