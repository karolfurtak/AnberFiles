#!/usr/bin/env python3
"""Pobiera z AnberFiles decyzje Karola z listy „Do przesłuchania” i wypisuje,
co nanieść w vaulcie (plan 98, 10.10.2026). Uruchamia agent laptopa — vault na
Jarvisie jest tylko do odczytu, więc decyzje czekają w AnberFiles, aż ktoś je
przeniesie. Skrypt NICZEGO w vaulcie nie zmienia.

    python narzedzia/pobierz_decyzje.py [--adres URL] [--json] [--potwierdz] [--limit-s N]

Hasło: zmienna ANBERFILES_HASLO albo pytanie na terminalu (nigdy argument,
nigdy zapis, nigdy wydruk). Adres: --adres, zmienna ANBERFILES_ADRES albo
http://100.67.174.47:8790. Każde żądanie ma limit czasu --limit-s (20 s).

--potwierdz: PO wypisaniu zmian i naniesieniu ich w vaulcie — POST
/?przesluchania=potwierdz {"do": ostatnie_id}; pozycje przestają być „do
przeniesienia”. Bez tej flagi skrypt tylko czyta (można powtarzać).

Kody wyjścia: 0 dane pobrane (także „brak nowych decyzji”), 2 zły argument,
3 odmowa logowania / brak hasła / za dużo prób, 4 błąd sieci albo limit czasu,
5 odpowiedź niezgodna ze schematem, 6 potwierdzenie odrzucone.
Standardowa biblioteka, Python 3.10+.
"""
import argparse
import getpass
import http.cookiejar
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

ADRES_DOMYSLNY = 'http://100.67.174.47:8790'
SCIEZKA_LOGOWANIA = '/__anberfiles/zaloguj'


class Blad(Exception):
    def __init__(self, kod: int, tekst: str):
        super().__init__(tekst)
        self.kod = kod


class _BezPrzekierowan(urllib.request.HTTPRedirectHandler):
    """Udane logowanie odpowiada 303 z ciasteczkiem — nie idziemy dalej."""
    def redirect_request(self, *a, **k):
        return None


def _otwieracz():
    sloik = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(sloik),
                                       _BezPrzekierowan())


def _zadanie(otw, url: str, limit_s: float, dane: bytes = None, naglowki=None):
    """(kod HTTP, treść). Błąd sieci / limit czasu → Blad(4)."""
    req = urllib.request.Request(url, data=dane, headers=dict(naglowki or {}))
    try:
        with otw.open(req, timeout=limit_s) as odp:
            return odp.status, odp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        powod = getattr(e, 'reason', e)
        raise Blad(4, f'BŁĄD sieci: {url.split("?")[0]} — {powod} (limit {limit_s:g} s)')


def zaloguj(otw, adres: str, haslo: str, limit_s: float):
    dane = urllib.parse.urlencode({'haslo': haslo, 'dalej': '/'}).encode()
    kod, _ = _zadanie(otw, adres + SCIEZKA_LOGOWANIA, limit_s, dane,
                      {'Content-Type': 'application/x-www-form-urlencoded'})
    if kod == 429:
        raise Blad(3, 'BŁĄD logowania: za dużo prób (limit na minutę) — odczekaj minutę.')
    if kod in (401, 403):
        raise Blad(3, f'BŁĄD logowania: odmowa (HTTP {kod}) — złe hasło?')
    if kod not in (200, 302, 303):
        raise Blad(3, f'BŁĄD logowania: nieoczekiwana odpowiedź HTTP {kod}.')


def pobierz(otw, adres: str, limit_s: float) -> dict:
    kod, tresc = _zadanie(otw, adres + '/?przesluchania=eksport', limit_s,
                          naglowki={'Accept': 'application/json'})
    if kod == 401:
        raise Blad(3, 'BŁĄD: eksport wymaga zalogowania (HTTP 401).')
    if kod != 200:
        raise Blad(5, f'BŁĄD: eksport odpowiedział HTTP {kod}.')
    try:
        d = json.loads(tresc.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise Blad(5, 'BŁĄD: eksport nie jest poprawnym JSON-em.')
    if not (isinstance(d, dict) and isinstance(d.get('ostatnie_id'), int)
            and isinstance(d.get('decyzje'), list) and isinstance(d.get('wybory'), list)
            and all(isinstance(w, dict) and {'id', 'zrodlo', 'wybor', 'kotwica'} <= set(w)
                    for w in d['wybory'])):
        raise Blad(5, 'BŁĄD: eksport niezgodny ze schematem (ostatnie_id, decyzje, wybory).')
    return d


def zmiana(w: dict) -> str:
    """Minimalna zmiana w pliku vaulta dla jednego wyboru (plan 98, 3.9)."""
    k, wyb, czas = w.get('kotwica') or {}, w['wybor'], w.get('czas', '')
    z = w['zrodlo']
    if z == 'skill-scout':
        status = 'zatwierdzone' if wyb == 'zatwierdz' else 'odrzucone'
        dop = (' (utworzenie skilla to osobna sprawa)' if wyb == 'zatwierdz'
               else ' (element zostaje, żeby skill-scout nie zaproponował go ponownie)')
        return (f'w elemencie "slug": "{k.get("slug")}" dopisz "status": "{status}" '
                f'i "decyzja_czas": "{czas}"' + dop)
    if z == 'reflect':
        if wyb == 'zatwierdz':
            return (f'w sekcji „{k.get("naglowek")}” zmień „- [ ] ✅ Zatwierdź tę zmianę” '
                    'na „- [x] ✅ Zatwierdź tę zmianę” (potem /reflect apply)')
        return (f'usuń sekcję „{k.get("naglowek")}” albo przenieś ją pod '
                '„### Odrzucone w tym przebiegu (świadomie)”')
    if z == 'now':
        opis = {'zatwierdz': 'zatwierdzone', 'odrzuc': 'odrzucone'}.get(wyb, f'wariant {wyb}')
        return (f'linię „{k.get("linia")}” zmień na „- [x] …” z dopiskiem '
                f'„— decyzja: {opis} ({czas[:10]})”')
    if z == 'now-pending':
        return f'usuń wpis „{k.get("naglowek")}” (do następnego ###) — przyjęty do wiadomości'
    return f'źródło {z}: wybór {wyb} (kotwica {json.dumps(k, ensure_ascii=False)})'


def wydruk(d: dict) -> str:
    w = []
    if not d['decyzje'] and not d['wybory']:
        return (f'Brak nowych decyzji do naniesienia (przeniesione do id {d.get("potwierdzono_do", 0)}).')
    for s, status in (d.get('statusy') or {}).items():
        w.append(f'NOTATKA {s}: w nagłówku ustaw status: {status}')
    for linia in d.get('do_kolejki') or ():
        w.append(f'DO KOLEJKI ZADAŃ: {linia}')
    for x in d['wybory']:
        w += ['', f'[id {x["id"]}] {x["zrodlo"]} · {x.get("tytul", "")} · wybór: {x["wybor"]} '
                  f'({x.get("czas", "")})',
              f'  plik:   {(x.get("kotwica") or {}).get("plik", "?")}',
              f'  zmiana: {zmiana(x)}']
        if x.get('uwaga'):
            w.append(f'  uwaga:  {x["uwaga"]}')
    w += ['', f'Po naniesieniu i wypchnięciu vaulta: --potwierdz (do id {d["ostatnie_id"]}).']
    return '\n'.join(w)


def potwierdz(otw, adres: str, do: int, limit_s: float) -> int:
    kod, tresc = _zadanie(otw, adres + '/?przesluchania=potwierdz', limit_s,
                          json.dumps({'do': do}).encode(),
                          {'Content-Type': 'application/json', 'X-AnberFiles': '1'})
    if kod != 200:
        raise Blad(6, f'BŁĄD: potwierdzenie odrzucone (HTTP {kod}): '
                      f'{tresc.decode("utf-8", "replace")[:200]}')
    return json.loads(tresc.decode('utf-8')).get('potwierdzono_do', do)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--adres', default=os.environ.get('ANBERFILES_ADRES') or ADRES_DOMYSLNY)
    ap.add_argument('--json', action='store_true', help='wynik jako JSON (z polem „zmiana”)')
    ap.add_argument('--potwierdz', action='store_true',
                    help='po wypisaniu potwierdź przeniesienie do ostatniego id')
    ap.add_argument('--limit-s', type=float, default=20.0, help='limit czasu żądania (s)')
    a = ap.parse_args(argv)
    if a.limit_s <= 0:
        ap.error('--limit-s musi być dodatnie')
    adres = a.adres.rstrip('/')
    haslo = os.environ.get('ANBERFILES_HASLO')
    if haslo is None and sys.stdin.isatty():
        haslo = getpass.getpass('Hasło AnberFiles: ')
    if not haslo:
        print('BŁĄD: brak hasła (zmienna ANBERFILES_HASLO albo terminal).', file=sys.stderr)
        return 3
    otw = _otwieracz()
    try:
        zaloguj(otw, adres, haslo, a.limit_s)
        d = pobierz(otw, adres, a.limit_s)
        if a.json:
            print(json.dumps(dict(d, wybory=[dict(x, zmiana=zmiana(x)) for x in d['wybory']]),
                  ensure_ascii=False, indent=1))
        else:
            print(wydruk(d))
        if a.potwierdz:
            if not d['decyzje'] and not d['wybory']:
                print('Nic do potwierdzenia.')
            else:
                print(f'Potwierdzono przeniesienie do id {potwierdz(otw, adres, d["ostatnie_id"], a.limit_s)}.')
    except Blad as e:
        print(str(e), file=sys.stderr)
        return e.kod
    return 0


if __name__ == '__main__':
    sys.exit(main())
