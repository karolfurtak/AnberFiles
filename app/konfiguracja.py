"""Ustawienia instancji AnberFiles — JEDYNE miejsce ze ścieżkami i przełącznikami.

Plik ustawień (format configparser: sekcje [serwer], [katalogi], [moduly])
wskazuje zmienna środowiska ANBERFILES_CONF. Brak zmiennej = wartości
domyślne konsoli Anbernic RG40XX V (zachowanie sprzed wprowadzenia ustawień).
Zmienna ustawiona, a pliku brak = błąd (serwer nie startuje) — literówka
w ścieżce nie może po cichu przełączyć instancji na ustawienia konsoli.

Priorytet wartości [serwer]: zmienna środowiska > plik ustawień > domyślne.
  SERVER_HOST → host, SERVER_PORT → port, SERVER_USER → uzytkownik_www.
Hasło WYŁĄCZNIE ze zmiennej SERVER_PASS (nigdy z pliku — plik może trafić
do repozytorium, hasło nie) — dotyczy logowania „basic" (HTTP Basic).
Logowanie „formularz" (app/logowanie.py) trzyma skrót hasła i sekret ciasteczek
w katalog_danych/auth/; SERVER_PASS nie jest wtedy potrzebne.

Moduł działa na Pythonie 3.10 (Anbernic) i 3.13 (Jarvis); tylko biblioteka
standardowa.
"""
import configparser
import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── wartości domyślne instancji Anbernic (dzisiejsze zachowanie konsoli) ─────
DOMYSLNY_KATALOG_GLOWNY = '/mnt/data/sprawozdania'
DOMYSLNY_KATALOG_DANYCH = '/mnt/data'
# czujnik baterii konsoli (PMIC axp2202) — tylko gdy moduł „bateria" włączony
SCIEZKA_BATERII = Path('/sys/class/power_supply/axp2202-battery')
# podpowiedź w komunikacie o braku hasła (instancja Jarvis, usługa systemd)
PLIK_HASLA_PODPOWIEDZ = '/etc/anberfiles/haslo.env'

LOGOWANIE = ('basic', 'formularz')

MODULY = ('podglad_docx', 'eksport_docx', 'lektor', 'lektor_opisy_ai',
          'wylaczanie', 'druk', 'bateria', 'kadrowanie')

KLUCZE = {
    'serwer': ('nazwa_instancji', 'host', 'port', 'uzytkownik_www',
               'haslo_wymagane', 'tylko_odczyt', 'logowanie',
               'limit_wgrywania_mb', 'prog_pamieci_mb', 'ustaw_haslo_bez_tokenu',
               'limit_zip_mb', 'dozwolone_hosty'),
    'katalogi': ('katalog_glowny', 'katalog_danych', 'rejestr_zdarzen',
                 'kolejka_lektora', 'bledy_lektora', 'bledy_druku',
                 'katalog_lektora', 'pamiec_podr_docx', 'katalog_zip_tmp',
                 'katalog_eksportu', 'favikona', 'kosz'),
    'moduly': MODULY,
}

_TAK = {'tak', 'yes', 'true', 'on', '1'}
_NIE = {'nie', 'no', 'false', 'off', '0'}


class BladKonfiguracji(Exception):
    """Ustawienia nieczytelne lub sprzeczne — serwer nie może wystartować."""


@dataclass(frozen=True)
class Konfiguracja:
    nazwa_instancji: str
    host: str
    port: int
    uzytkownik_www: str
    haslo: str
    haslo_wymagane: bool
    tylko_odczyt: bool
    katalog_glowny: Path
    katalog_danych: Path
    rejestr_zdarzen: Path
    kolejka_lektora: Path
    bledy_lektora: Path
    bledy_druku: Path
    katalog_lektora: 'Path | None'
    pamiec_podr_docx: Path
    katalog_zip_tmp: Path
    katalog_eksportu: Path
    favikona: Path
    kosz: Path
    moduly: dict = field(default_factory=dict)
    zrodlo: str = 'domyślne (Anbernic)'
    logowanie: str = 'basic'
    limit_wgrywania_mb: int = 512        # łączny rozmiar jednego wgrywania (413 ponad)
    limit_zip_mb: int = 2048             # łączny rozmiar plików folderu w ?zip=1 (413 ponad)
    prog_pamieci_mb: int = 400           # serwer + procesy potomne: ostrzeżenie w rejestrze
    # sieci, z których ekran „Ustaw hasło" przyjmuje hasło BEZ tokenu startowego
    # (domyślnie żadne — token wymagany od wszystkich)
    ustaw_haslo_bez_tokenu: tuple = ()
    # nazwy w nagłówku Host przyjmowane oprócz literałów IP, localhost i *.local
    # (B9, DNS rebinding); wpis z kropką na początku = sufiks (np. .ts.net)
    dozwolone_hosty: tuple = ()

    @property
    def katalog_auth(self) -> Path:
        """Skrót hasła i sekret ciasteczek (logowanie „formularz")."""
        return self.katalog_danych / 'auth'

    def modul(self, nazwa: str) -> bool:
        if nazwa not in MODULY:
            raise KeyError(f'nieznany moduł: {nazwa}')
        return bool(self.moduly.get(nazwa, True))

    @property
    def czytaj_tts(self) -> Path:
        return self.katalog_eksportu / 'czytaj_tts.py'

    @property
    def lektor_conf(self) -> Path:
        return self.katalog_eksportu / 'lektor-ustawienia.conf'

    @property
    def skrypt_eksportu_docx(self) -> Path:
        return self.katalog_eksportu / 'export_to_docx.py'


def _bool(sekcja: str, klucz: str, tekst: str) -> bool:
    t = tekst.strip().lower()
    if t in _TAK:
        return True
    if t in _NIE:
        return False
    raise BladKonfiguracji(f'[{sekcja}] {klucz} = {tekst!r}: dozwolone „tak" albo „nie"')


def _port(zrodlo: str, tekst: str) -> int:
    try:
        p = int(str(tekst).strip())
    except ValueError:
        raise BladKonfiguracji(f'{zrodlo}: port {tekst!r} nie jest liczbą') from None
    if not 1 <= p <= 65535:
        raise BladKonfiguracji(f'{zrodlo}: port {p} poza zakresem 1–65535')
    return p


def _dodatnia(klucz: str, tekst: str) -> int:
    try:
        v = int(str(tekst).strip())
    except ValueError:
        raise BladKonfiguracji(f'[serwer] {klucz} = {tekst!r}: wymagana liczba '
                               'całkowita') from None
    if v < 1:
        raise BladKonfiguracji(f'[serwer] {klucz} = {v}: wymagana liczba dodatnia')
    return v


def _sieci(klucz: str, tekst: str) -> tuple:
    """Lista adresów/sieci CIDR oddzielonych przecinkami lub spacjami."""
    wynik = []
    for s in re.split(r'[\s,]+', tekst.strip()):
        if not s:
            continue
        try:
            wynik.append(ipaddress.ip_network(s, strict=False))
        except ValueError:
            raise BladKonfiguracji(f'[serwer] {klucz}: {s!r} nie jest adresem '
                                   'ani siecią (np. 192.168.0.0/16)') from None
    return tuple(wynik)


def _hosty(tekst: str) -> tuple:
    """Lista nazw hostów (po przecinku lub spacji), małymi literami, bez kropki
    końcowej; wpis z kropką na początku zostaje sufiksem (np. .ts.net)."""
    wynik = []
    for s in re.split(r'[\s,]+', tekst.strip().lower()):
        if not s:
            continue
        if not re.fullmatch(r'\.?[a-z0-9_-]+(\.[a-z0-9_-]+)*\.?', s):
            raise BladKonfiguracji(f'[serwer] dozwolone_hosty: {s!r} nie jest nazwą '
                                   'hosta (np. jarvis.example.org albo .ts.net)')
        wynik.append(s.rstrip('.'))
    return tuple(wynik)


def _czytaj_plik(sciezka: Path) -> dict:
    """Plik ustawień → {sekcja: {klucz: tekst}}; nieznana sekcja/klucz = błąd
    (literówka nie może po cichu zostawić wartości domyślnej)."""
    cp = configparser.ConfigParser(inline_comment_prefixes=(';', '#'),
                                   interpolation=None)
    try:
        with open(sciezka, encoding='utf-8') as f:
            cp.read_file(f)
    except (OSError, configparser.Error) as e:
        raise BladKonfiguracji(f'nie da się odczytać {sciezka}: {e}') from None
    wynik = {}
    for sek in cp.sections():
        if sek not in KLUCZE:
            raise BladKonfiguracji(f'{sciezka}: nieznana sekcja [{sek}] '
                                   f'(dozwolone: {", ".join(KLUCZE)})')
        for k, v in cp.items(sek):
            if k not in KLUCZE[sek]:
                raise BladKonfiguracji(f'{sciezka}: nieznany klucz [{sek}] {k}')
            wynik.setdefault(sek, {})[k] = v.strip()
    return wynik


def wczytaj(sciezka=None, env=None) -> Konfiguracja:
    """Ustawienia instancji. sciezka=None → z ANBERFILES_CONF; brak zmiennej →
    domyślne Anbernica. env=None → os.environ (testy podają własny słownik)."""
    env = os.environ if env is None else env
    if sciezka is None:
        sciezka = env.get('ANBERFILES_CONF', '').strip() or None
    plik = {}
    zrodlo = 'domyślne (Anbernic)'
    if sciezka is not None:
        sciezka = Path(sciezka)
        if not sciezka.is_file():
            raise BladKonfiguracji(
                f'ANBERFILES_CONF wskazuje {sciezka}, a pliku nie ma')
        plik = _czytaj_plik(sciezka)
        zrodlo = str(sciezka)
    s = plik.get('serwer', {})
    k = plik.get('katalogi', {})
    m = plik.get('moduly', {})

    # [serwer] — zmienna środowiska > plik > domyślne
    host = env.get('SERVER_HOST') or s.get('host') or '0.0.0.0'
    if env.get('SERVER_PORT'):
        port = _port('SERVER_PORT', env['SERVER_PORT'])
    else:
        port = _port('[serwer] port', s.get('port', '8765'))
    uzytkownik = env.get('SERVER_USER') or s.get('uzytkownik_www') or 'anbernic'

    def sciezka_k(klucz, domyslna):
        v = k.get(klucz, '')
        return Path(v) if v else Path(domyslna)

    glowny = sciezka_k('katalog_glowny', DOMYSLNY_KATALOG_GLOWNY)
    dane = sciezka_k('katalog_danych', DOMYSLNY_KATALOG_DANYCH)
    lektor = k.get('katalog_lektora', '')
    logowanie = s.get('logowanie', 'basic').strip().lower()
    if logowanie not in LOGOWANIE:
        raise BladKonfiguracji(f'[serwer] logowanie = {logowanie!r}: dozwolone '
                               f'{" albo ".join(LOGOWANIE)}')
    return Konfiguracja(
        nazwa_instancji=s.get('nazwa_instancji') or 'Anbernic',
        host=host,
        port=port,
        uzytkownik_www=uzytkownik,
        haslo=env.get('SERVER_PASS', ''),
        haslo_wymagane=_bool('serwer', 'haslo_wymagane', s.get('haslo_wymagane', 'nie')),
        tylko_odczyt=_bool('serwer', 'tylko_odczyt', s.get('tylko_odczyt', 'nie')),
        katalog_glowny=glowny,
        katalog_danych=dane,
        rejestr_zdarzen=sciezka_k('rejestr_zdarzen', dane / 'anberfiles-events.log'),
        kolejka_lektora=sciezka_k('kolejka_lektora', dane / 'lektor_queue.json'),
        bledy_lektora=sciezka_k('bledy_lektora', dane / 'lektor_errors.log'),
        bledy_druku=sciezka_k('bledy_druku', dane / 'print_errors.log'),
        katalog_lektora=Path(lektor) if lektor else None,
        pamiec_podr_docx=sciezka_k('pamiec_podr_docx', dane / '.cache' / 'docx-preview'),
        katalog_zip_tmp=sciezka_k('katalog_zip_tmp', glowny / '.zip_tmp'),
        katalog_eksportu=sciezka_k('katalog_eksportu', glowny / 'EXPORT'),
        favikona=sciezka_k('favikona', dane / 'dev-skills' / 'favicons' / 'favicon.ico'),
        kosz=sciezka_k('kosz', glowny / '.kosz'),
        moduly={n: _bool('moduly', n, m.get(n, 'tak')) for n in MODULY},
        zrodlo=zrodlo,
        logowanie=logowanie,
        limit_wgrywania_mb=_dodatnia('limit_wgrywania_mb',
                                     s.get('limit_wgrywania_mb', '512')),
        prog_pamieci_mb=_dodatnia('prog_pamieci_mb', s.get('prog_pamieci_mb', '400')),
        limit_zip_mb=_dodatnia('limit_zip_mb', s.get('limit_zip_mb', '2048')),
        ustaw_haslo_bez_tokenu=_sieci('ustaw_haslo_bez_tokenu',
                                      s.get('ustaw_haslo_bez_tokenu', '')),
        dozwolone_hosty=_hosty(s.get('dozwolone_hosty', '')),
    )


def domyslna() -> Konfiguracja:
    """Ustawienia Anbernica bez czytania środowiska (stan przy imporcie)."""
    return wczytaj(sciezka=None, env={})


def _w_katalogu(p: Path, rodzic: Path) -> bool:
    p, rodzic = p.resolve(), rodzic.resolve()
    return p == rodzic or rodzic in p.parents


def sprawdz_przy_starcie(k: Konfiguracja) -> list:
    """Warunki startu. Zwraca listę błędów (pusta = można startować).
    Próba zapisu do katalogu danych: zapis + odczyt + usunięcie pliku próbnego."""
    bledy = []
    if k.logowanie == 'basic' and k.haslo_wymagane and not k.haslo:
        bledy.append(
            f'Brak hasła: ustaw SERVER_PASS w {PLIK_HASLA_PODPOWIEDZ} '
            f'(instancja {k.nazwa_instancji} ma haslo_wymagane = tak). '
            'Serwer NIE wystartuje bez hasła.')
    if not k.katalog_glowny.is_dir():
        bledy.append(f'katalog_glowny {k.katalog_glowny} nie istnieje '
                     'albo nie jest katalogiem.')
    proba = k.katalog_danych / f'.anberfiles-proba-{os.getpid()}'
    try:
        proba.write_text('proba', encoding='utf-8')
        if proba.read_text(encoding='utf-8') != 'proba':
            raise OSError('odczyt zwrócił inną treść niż zapis')
    except OSError as e:
        bledy.append(f'katalog_danych {k.katalog_danych}: próba zapisu '
                     f'nieudana ({e}). Rejestr zdarzeń, kolejka i błędy '
                     'lektora nie miałyby gdzie się zapisać.')
    finally:
        try:
            proba.unlink()
        except OSError:
            pass
    for nazwa in ('rejestr_zdarzen', 'kolejka_lektora', 'bledy_lektora', 'bledy_druku'):
        rodzic = getattr(k, nazwa).parent
        if not rodzic.is_dir():
            bledy.append(f'{nazwa}: katalog {rodzic} nie istnieje.')
    if k.katalog_lektora is not None and k.katalog_glowny.is_dir() \
            and not _w_katalogu(k.katalog_lektora, k.katalog_glowny):
        bledy.append(f'katalog_lektora {k.katalog_lektora} leży poza '
                     f'katalog_glowny {k.katalog_glowny} — nagrań nie dałoby '
                     'się odtworzyć ani pobrać przez przeglądarkę.')
    return bledy


def opis(k: Konfiguracja) -> str:
    """Jedna linia do dziennika startu."""
    wyl = [n for n in MODULY if not k.modul(n)]
    return (f'instancja={k.nazwa_instancji} ustawienia={k.zrodlo} '
            f'katalog={k.katalog_glowny} logowanie={k.logowanie} '
            f'tylko_odczyt={"tak" if k.tylko_odczyt else "nie"} '
            f'moduły_wyłączone={",".join(wyl) or "brak"}')
