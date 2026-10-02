"""Lista „Do przesłuchania”: które notatki czekają na decyzję Karola, stan
decyzji i odsłuchu, eksport decyzji do przeniesienia do vaulta.

Źródła statusu notatki (od najważniejszego):
  1. ostatnia decyzja zapisana w AnberFiles, dopóki treść notatki się nie
     zmieniła (odcisk SHA-256 treści bez nagłówka) — decyzja jest ważniejsza od
     nagłówka, bo vault na serwerze jest tylko do odczytu i dowiaduje się
     o niej dopiero po przeniesieniu przez agenta laptopa;
  2. `status:` w nagłówku notatki (frontmatter Obsidiana);
  3. plik propozycji klasyfikacji (tabela Markdown: | `ścieżka` | rodzaj | status |).

Stan (decyzje, odsłuch, ustawienia widoku) w JEDNYM pliku JSON w katalogu
danych instancji, zapis atomowy: plik tymczasowy → fsync → os.replace. Vault
nigdy nie jest zapisywany.

Model decyzji ma od razu pola na przyszłe porcje: `zakres` (dziś tylko
„notatka”; później „akapit”, „fragment”), `kotwica` (cytat, nagłówek, odcisk
akapitu), `zrodlo_uwagi` (dziś „tekst”; później „glos”).
"""
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

WERSJA_STANU = 1

STATUSY = ('do-akceptacji', 'zaakceptowane-niewdrozone', 'wdrozone',
           'do-poprawy', 'odrzucone')
# decyzja Karola → status notatki
DECYZJE = {
    'akceptuje': 'zaakceptowane-niewdrozone',
    'do-poprawy': 'do-poprawy',
    'odrzuca': 'odrzucone',
}
NAZWY_DECYZJI = {'akceptuje': 'Akceptuję', 'do-poprawy': 'Do poprawy',
                 'odrzuca': 'Odrzucam'}
NAZWY_STATUSOW = {
    'do-akceptacji': 'do akceptacji',
    'zaakceptowane-niewdrozone': 'zaakceptowane, niewdrożone',
    'wdrozone': 'wdrożone',
    'do-poprawy': 'do poprawy',
    'odrzucone': 'odrzucone',
}
ZAKRESY = ('notatka',)            # później: 'akapit', 'fragment'
ZRODLA_UWAGI = ('tekst',)         # później: 'glos'
LIMIT_UWAGI = 4000                # znaków
LIMIT_NAGLOWKA_B = 8192           # tyle bajtów początku pliku czyta skan
LIMIT_NOTATKI_B = 2 * 1024 * 1024  # większych plików skan nie bierze
# zakładki listy: nazwa → statusy (kolejność = kolejność przycisków).
# „wdrozenia” i „informacyjne” zbiera stan wdrożenia z pliku klasyfikacji
# (decyzja Karola 02.10) — tylko dla pozycji jeszcze nierozstrzygniętych.
ZAKLADKI = {
    'decyzja': ('do-akceptacji',),
    'wdrozenia': (),
    'informacyjne': (),
    'zaakceptowane': ('zaakceptowane-niewdrozone',),
    'rozstrzygniete': ('do-poprawy', 'odrzucone', 'wdrozone'),
}
NAZWY_ZAKLADEK = {'decyzja': 'Do decyzji',
                  'wdrozenia': 'Zlecone i wdrożone',
                  'informacyjne': 'Informacyjne',
                  'zaakceptowane': 'Zaakceptowane, niewdrożone',
                  'rozstrzygniete': 'Rozstrzygnięte'}
# stan wdrożenia z kolumny „Stan wdrożenia” pliku klasyfikacji (wielkie litery,
# porównanie po początku; NIEZLECONE przed ZLECONE)
STANY_WDROZENIA = (('NIE DOTYCZY', 'nie-dotyczy'), ('NIEZLECONE', 'niezlecone'),
                   ('CZĘŚCIOWO', 'czesciowo'), ('ZLECONE', 'zlecone'),
                   ('WDROŻONE', 'wdrozone'))
NAZWY_STANOW = {'niezlecone': 'niezlecone', 'czesciowo': 'częściowo',
                'zlecone': 'zlecone', 'wdrozone': 'wdrożone',
                'nie-dotyczy': 'nie dotyczy', 'nieznany': 'stan nieznany'}
ZAKLADKA_STANU = {'niezlecone': 'decyzja', 'czesciowo': 'decyzja', 'nieznany': 'decyzja',
                  'zlecone': 'wdrozenia', 'wdrozone': 'wdrozenia',
                  'nie-dotyczy': 'informacyjne'}


class BladStanu(Exception):
    """Zapis stanu nieudany — żądanie kończy się błędem, nie ciszą."""


# ── Notatka: nagłówek, treść, odcisk, tytuł ─────────────────────────────────

_NAGLOWEK = re.compile(r'\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', re.S)


def naglowek(tekst: str) -> dict:
    """Frontmatter Obsidiana → {klucz: wartość} (proste pary „klucz: wartość”;
    listy i zagnieżdżenia pomijane). Brak nagłówka → {}."""
    m = _NAGLOWEK.match(tekst)
    if not m:
        return {}
    wynik = {}
    for linia in m.group(1).splitlines():
        if ':' not in linia or linia[:1] in (' ', '\t', '-', '#'):
            continue
        k, v = linia.split(':', 1)
        wynik[k.strip().lower()] = v.strip().strip('"\'')
    return wynik


def tresc_bez_naglowka(tekst: str) -> str:
    m = _NAGLOWEK.match(tekst)
    return tekst[m.end():] if m else tekst


def odcisk(tekst: str) -> str:
    """SHA-256 treści BEZ nagłówka (zmiana samego statusu w nagłówku nie
    unieważnia decyzji), końce linii ujednolicone."""
    t = tresc_bez_naglowka(tekst).replace('\r\n', '\n').strip()
    return hashlib.sha256(t.encode('utf-8')).hexdigest()


def tytul(tekst: str, nazwa: str) -> str:
    """Pierwszy nagłówek „# …” treści albo nazwa pliku bez rozszerzenia."""
    for linia in tresc_bez_naglowka(tekst).splitlines():
        if linia.startswith('# '):
            t = linia[2:].strip()
            if t:
                return t
    return Path(nazwa).stem


# ── Plik propozycji klasyfikacji ────────────────────────────────────────────

_WIERSZ_KANDYDATA = re.compile(r'^\|\s*`([^`]+?\.md)`\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|')


class Kandydaci(dict):
    """{ścieżka: wpis}; ma_stan = któraś tabela pliku ma kolumnę „Stan wdrożenia”."""
    ma_stan = False


def _komorki(linia: str) -> list:
    """Komórki wiersza tabeli Markdown (bez skrajnych „|”; „\\|” nie dzieli)."""
    czesci = re.split(r'(?<!\\)\|', linia.strip())
    return [c.strip() for c in czesci[1:-1]]


def stan_wdrozenia(komorka: str) -> str:
    s = komorka.replace('*', '').replace('`', '').strip().upper()
    for wzor, stan in STANY_WDROZENIA:
        if s.startswith(wzor):
            return stan
    return 'nieznany'


def _tekst_komorki(k: str, limit: int = 300) -> str:
    k = k.replace('\\|', '|').replace('`', '').replace('**', '').strip()
    if k in ('—', '-', '–'):
        return ''
    return k if len(k) <= limit else k[:limit - 1].rstrip() + '…'


def wczytaj_kandydatow(plik) -> dict:
    """Tabele Markdown z wierszami | `ścieżka względna` | rodzaj | status | …
    → {ścieżka: {'rodzaj', 'status', 'stan', 'do_wdrozenia', 'dowod', 'kolejnosc'}}.
    Status spoza STATUSY → „do-akceptacji” (propozycja domyślna). Kolumny od
    czwartej czytane PO NAZWIE nagłówka tabeli: „Stan wdrożenia”, „Do wdrożenia”,
    „… dowód” (wersja 2 pliku, 02.10); tabela bez nich → stan None, kolejność
    None (zachowanie jak w wersji 1). Brak pliku → {}."""
    wynik = Kandydaci()
    if not plik:
        return wynik
    try:
        tekst = Path(plik).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return wynik
    kolumny = {}
    for linia in tekst.splitlines():
        lin = linia.strip()
        m = _WIERSZ_KANDYDATA.match(lin)
        if not m:
            if lin.startswith('|') and 'ścieżka' in lin.lower():
                naz = [k.lower() for k in _komorki(lin)]
                kolumny = {}
                for i, k in enumerate(naz):
                    if k.startswith('stan'):
                        kolumny.setdefault('stan', i)
                    elif k.startswith('do wdrożenia'):
                        kolumny.setdefault('do_wdrozenia', i)
                    elif 'dowód' in k or 'pilność' in k:
                        kolumny.setdefault('dowod', i)
                if 'stan' in kolumny:
                    wynik.ma_stan = True
            continue
        sciezka = m.group(1).replace('\\', '/').strip('/')
        if '..' in Path(sciezka).parts:
            continue
        status = m.group(3).strip().lower().replace(' ', '-')
        wpis = {'rodzaj': m.group(2).strip(),
                'status': status if status in STATUSY else 'do-akceptacji',
                'stan': None, 'do_wdrozenia': '', 'dowod': '', 'kolejnosc': None}
        if 'stan' in kolumny:
            k = _komorki(lin)
            def kom(n):
                i = kolumny.get(n)
                return k[i] if i is not None and i < len(k) else ''
            wpis.update(stan=stan_wdrozenia(kom('stan')),
                        do_wdrozenia=_tekst_komorki(kom('do_wdrozenia')),
                        dowod=_tekst_komorki(kom('dowod')),
                        kolejnosc=len(wynik))
        wynik[sciezka] = wpis
    return wynik


# ── Skan zakresu (vault) ────────────────────────────────────────────────────

def _poczatek(p: Path) -> str:
    with open(p, 'rb') as f:
        return f.read(LIMIT_NAGLOWKA_B).decode('utf-8', errors='replace')


def skanuj(zakres: Path, kandydaci: dict) -> list:
    """Notatki .md w zakresie z `status:` w nagłówku albo z pliku propozycji.
    Zwraca listę słowników: sciezka (względna wobec zakresu, „/”), plik, tytul,
    projekt, rodzaj, status_zrodlowy, zrodlo_statusu, odcisk, mtime.
    Katalogi ukryte (.git, .obsidian, .trash) pomijane."""
    zakres = Path(zakres)
    if not zakres.is_dir():
        return []
    wynik = []
    for korzen, katalogi, pliki in os.walk(zakres):
        katalogi[:] = sorted(d for d in katalogi if not d.startswith('.'))
        for nazwa in sorted(pliki):
            if not nazwa.lower().endswith('.md') or nazwa.startswith('.'):
                continue
            p = Path(korzen) / nazwa
            rel = p.relative_to(zakres).as_posix()
            try:
                st = p.stat()
                if st.st_size > LIMIT_NOTATKI_B:
                    continue
                nag = naglowek(_poczatek(p))
            except OSError:
                continue
            status = nag.get('status', '').lower()
            kand = kandydaci.get(rel)
            if status in STATUSY:
                zrodlo = 'naglowek'
            elif kand is not None:
                status, zrodlo = kand['status'], 'propozycja'
            else:
                continue
            try:
                tekst = p.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            wynik.append({
                'sciezka': rel,
                'plik': p,
                'tytul': tytul(tekst, nazwa),
                'projekt': projekt_notatki(rel),
                'rodzaj': nag.get('rodzaj') or (kand or {}).get('rodzaj', ''),
                'status_zrodlowy': status,
                'zrodlo_statusu': zrodlo,
                'odcisk': odcisk(tekst),
                'mtime': st.st_mtime,
                # stan wdrożenia (wersja 2 pliku klasyfikacji); notatka spoza
                # pliku, gdy plik ma stany → „stan nieznany”
                'stan': ((kand or {}).get('stan')
                         or ('nieznany' if getattr(kandydaci, 'ma_stan', False) else None)),
                'do_wdrozenia': (kand or {}).get('do_wdrozenia', ''),
                'dowod': (kand or {}).get('dowod', ''),
                'kolejnosc': (kand or {}).get('kolejnosc'),
            })
    return wynik


def projekt_notatki(rel: str) -> str:
    """Projekt = katalog pierwszego poziomu pod `Zasoby/` (tak układa vault
    Karola), inaczej katalog nadrzędny pliku; pliki luzem → „”."""
    czesci = rel.split('/')
    if len(czesci) > 2 and czesci[0].lower() == 'zasoby':
        return czesci[1]
    return czesci[-2] if len(czesci) > 1 else ''


def status_efektywny(notatka: dict, ostatnia) -> tuple:
    """(status, źródło): decyzja z AnberFiles, gdy dotyczy TEJ treści
    (odcisk zgodny), inaczej status z nagłówka / propozycji."""
    if ostatnia is not None and ostatnia.get('odcisk_notatki') == notatka['odcisk']:
        return DECYZJE[ostatnia['decyzja']], 'decyzja'
    return notatka['status_zrodlowy'], notatka['zrodlo_statusu']


def zakladka_statusu(status: str) -> str:
    for z, statusy in ZAKLADKI.items():
        if status in statusy:
            return z
    return ''


def zakladka_pozycji(status: str, stan) -> str:
    """Pozycja nierozstrzygnięta (do-akceptacji) ze stanem wdrożenia → zakładka
    wg stanu; decyzje Karola i statusy z nagłówka → zakładka wg statusu."""
    if status == 'do-akceptacji' and stan in ZAKLADKA_STANU:
        return ZAKLADKA_STANU[stan]
    return zakladka_statusu(status)


# ── Stan: decyzje, odsłuch, ustawienia widoku ───────────────────────────────

def _teraz_iso(teraz=None) -> str:
    t = time.time() if teraz is None else teraz
    return datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S')


def zapisz_atomowo(plik: Path, tekst: str):
    """Plik tymczasowy w tym samym katalogu → fsync → os.replace. Przerwanie
    w dowolnej chwili zostawia albo stary, albo nowy plik — nigdy połówkę."""
    plik = Path(plik)
    fd, tmp = tempfile.mkstemp(prefix=f'.{plik.name}.', suffix='.tmp', dir=plik.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(tekst)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, plik)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Magazyn:
    """Stan listy w pliku JSON (katalog danych). Jeden proces serwera —
    blokada chroni przed przeplotem wątków (executor)."""

    def __init__(self, plik: Path, eksport_md: Path = None):
        self.plik = Path(plik)
        self.eksport_md = Path(eksport_md) if eksport_md else None
        self._blokada = threading.Lock()
        self.uszkodzony = None          # opis odłożonego, nieczytelnego pliku stanu
        self._stan = self._wczytaj()

    @staticmethod
    def _pusty() -> dict:
        return {'wersja': WERSJA_STANU, 'nastepne_id': 1, 'potwierdzono_do': 0,
                'decyzje': [], 'odsluch': {}, 'ui': {}}

    def _wczytaj(self) -> dict:
        if not self.plik.exists():
            return self._pusty()
        try:
            stan = json.loads(self.plik.read_text(encoding='utf-8'))
            if not isinstance(stan, dict) or 'decyzje' not in stan:
                raise ValueError('brak pola decyzje')
        except (OSError, ValueError) as e:
            # nieczytelny plik NIE jest nadpisywany pustym stanem — odkładamy
            # go obok (dowód) i zaczynamy od zera; serwer melduje to w rejestrze
            kopia = self.plik.with_name(
                f'{self.plik.name}.uszkodzony-{datetime.now():%Y%m%d-%H%M%S}')
            try:
                os.replace(self.plik, kopia)
            except OSError:
                pass
            self.uszkodzony = f'{self.plik.name}: {e} → odłożony jako {kopia.name}'
            return self._pusty()
        pusty = self._pusty()
        for k, v in pusty.items():
            stan.setdefault(k, v)
        return stan

    def _zapisz(self):
        try:
            zapisz_atomowo(self.plik, json.dumps(self._stan, ensure_ascii=False, indent=1))
            if self.eksport_md is not None:
                zapisz_atomowo(self.eksport_md, eksport_markdown(self._stan))
        except OSError as e:
            raise BladStanu(f'zapis {self.plik} nieudany: {e}') from None

    # odczyt
    def decyzje(self) -> list:
        with self._blokada:
            return [dict(d) for d in self._stan['decyzje']]

    def ostatnie_decyzje(self) -> dict:
        """{ścieżka: ostatnia decyzja} — obowiązuje ostatni wpis."""
        wynik = {}
        for d in self.decyzje():
            wynik[d['sciezka']] = d
        return wynik

    def nieprzeniesione(self) -> list:
        with self._blokada:
            granica = self._stan['potwierdzono_do']
            return [dict(d) for d in self._stan['decyzje'] if d['id'] > granica]

    def potwierdzono_do(self) -> int:
        return self._stan['potwierdzono_do']

    def odsluch(self, sciezka: str) -> dict:
        with self._blokada:
            return dict(self._stan['odsluch'].get(sciezka, {}))

    def ui(self, klucz: str, domyslna=None):
        with self._blokada:
            return self._stan['ui'].get(klucz, domyslna)

    # zapis
    def dodaj_decyzje(self, sciezka: str, decyzja: str, uwaga: str, odcisk_notatki: str,
                      tytul_notatki: str = '', zakres: str = 'notatka', kotwica=None,
                      zrodlo_uwagi: str = 'tekst', teraz=None) -> dict:
        if decyzja not in DECYZJE:
            raise ValueError(f'nieznana decyzja: {decyzja!r}')
        if zakres not in ZAKRESY:
            raise ValueError(f'nieobsługiwany zakres: {zakres!r}')
        if zrodlo_uwagi not in ZRODLA_UWAGI:
            raise ValueError(f'nieobsługiwane źródło uwagi: {zrodlo_uwagi!r}')
        uwaga = (uwaga or '').strip()
        if len(uwaga) > LIMIT_UWAGI:
            raise ValueError(f'uwaga dłuższa niż {LIMIT_UWAGI} znaków')
        if decyzja == 'do-poprawy' and not uwaga:
            raise ValueError('„Do poprawy” wymaga uwagi — co poprawić?')
        with self._blokada:
            wpis = {
                'id': self._stan['nastepne_id'],
                'sciezka': sciezka,
                'tytul': tytul_notatki,
                'decyzja': decyzja,
                'status': DECYZJE[decyzja],
                'uwaga': uwaga,
                'zrodlo_uwagi': zrodlo_uwagi,
                'zakres': zakres,
                'kotwica': kotwica or {},
                'odcisk_notatki': odcisk_notatki,
                'utworzono': _teraz_iso(teraz),
            }
            self._stan['decyzje'].append(wpis)
            self._stan['nastepne_id'] += 1
            try:
                self._zapisz()
            except BladStanu:
                self._stan['decyzje'].pop()
                self._stan['nastepne_id'] -= 1
                raise
            return dict(wpis)

    def ustaw_odsluch(self, sciezka: str, pozycja_s: float = None,
                      odsluchane: bool = False, teraz=None) -> dict:
        with self._blokada:
            o = self._stan['odsluch'].setdefault(sciezka, {})
            if pozycja_s is not None:
                o['pozycja_s'] = round(max(0.0, float(pozycja_s)), 1)
            if odsluchane and not o.get('odsluchane'):
                o['odsluchane'] = _teraz_iso(teraz)
            self._zapisz()
            return dict(o)

    def ustaw_ui(self, klucz: str, wartosc):
        with self._blokada:
            self._stan['ui'][klucz] = wartosc
            self._zapisz()

    def potwierdz(self, do_id: int) -> int:
        """Agent laptopa przeniósł decyzje do vaulta aż do do_id włącznie.
        Powtórzenie albo mniejsze do_id nic nie zmienia (idempotentne)."""
        with self._blokada:
            maks = self._stan['nastepne_id'] - 1
            if not 0 <= int(do_id) <= maks:
                raise ValueError(f'do={do_id} poza zakresem 0–{maks}')
            if int(do_id) > self._stan['potwierdzono_do']:
                self._stan['potwierdzono_do'] = int(do_id)
                self._zapisz()
            return self._stan['potwierdzono_do']


# ── Eksport dla agenta laptopa ──────────────────────────────────────────────

def linia_todo(d: dict) -> str:
    """Pozycja kolejki zadań dla decyzji „do poprawy”."""
    nazwa = d.get('tytul') or Path(d['sciezka']).stem
    return f'- [ ] do poprawy: {nazwa} (`{d["sciezka"]}`) — {d["uwaga"]}'


def _komorka(s: str) -> str:
    return str(s).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def eksport_markdown(stan: dict) -> str:
    """Plik dla agenta laptopa (i człowieka): decyzje jeszcze nieprzeniesione
    do vaulta, docelowy `status:` każdej notatki i gotowe linie do to_do.md."""
    granica = stan.get('potwierdzono_do', 0)
    nowe = [d for d in stan.get('decyzje', []) if d['id'] > granica]
    ostatnie = {}
    for d in nowe:
        ostatnie[d['sciezka']] = d
    w = ['# AnberFiles — decyzje do przeniesienia do vaulta', '',
         f'*Wygenerowano: {_teraz_iso()} · przeniesione do id = {granica} · '
         f'nowych decyzji: {len(nowe)}*', '',
         'Procedura agenta laptopa: (1) dla każdej notatki z tabeli „Status” '
         'ustaw w nagłówku `status:` na wartość z kolumny „Nowy status”; '
         '(2) dopisz linie z sekcji „Do kolejki zadań” do `Zadania/to_do.md`; '
         '(3) zatwierdź i wypchnij vault; (4) potwierdź: POST '
         f'`/?przesluchania=potwierdz` z treścią `{{"do": {nowe[-1]["id"] if nowe else granica}}}` '
         '(nagłówek `X-AnberFiles: 1`). Powtórzenie procedury niczego nie dubluje.', '',
         '## Status', '',
         '| Notatka | Nowy status | Decyzja | Kiedy | id |', '|---|---|---|---|---|']
    for s, d in ostatnie.items():
        w.append(f'| `{_komorka(s)}` | {d["status"]} | {NAZWY_DECYZJI[d["decyzja"]]} | '
                 f'{d["utworzono"]} | {d["id"]} |')
    w += ['', '## Do kolejki zadań', '']
    poprawki = [d for d in ostatnie.values() if d['decyzja'] == 'do-poprawy']
    w += [linia_todo(d) for d in poprawki] or ['(brak)']
    w += ['', '## Wszystkie nowe decyzje (z uwagami)', '']
    for d in nowe:
        w.append(f'- id {d["id"]} · {d["utworzono"]} · `{d["sciezka"]}` · '
                 f'{NAZWY_DECYZJI[d["decyzja"]]}' + (f' — {d["uwaga"]}' if d['uwaga'] else ''))
    if not nowe:
        w.append('(brak)')
    return '\n'.join(w) + '\n'


def eksport_json(magazyn: Magazyn) -> dict:
    nowe = magazyn.nieprzeniesione()
    ostatnie = {}
    for d in nowe:
        ostatnie[d['sciezka']] = d
    return {
        'potwierdzono_do': magazyn.potwierdzono_do(),
        'ostatnie_id': nowe[-1]['id'] if nowe else magazyn.potwierdzono_do(),
        'decyzje': nowe,
        'statusy': {s: d['status'] for s, d in ostatnie.items()},
        'do_kolejki': [linia_todo(d) for d in ostatnie.values()
                       if d['decyzja'] == 'do-poprawy'],
    }
