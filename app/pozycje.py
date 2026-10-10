"""Pozycje do decyzji z plików vaulta i raporty automatów dla listy
„Do przesłuchania” (plan 98, 10.10.2026).

Polecenie Karola: „Najlepsze byłoby zatwierdzanie każdej propozycji skilla
oddzielnie” — „(wszystko co wymaga decyzji zwrotnej usera)”. Źródła (ścieżki
względem `przesluchania_zakres`, czyli klonu vaulta):

  skill-scout  Zasoby/raporty/skill-scout/_proposed.json — element `proposed[]`
  reflect      Zasoby/Jarwis-vault-i-skille/_reflect-pending.md — sekcja `## `
  now          .claude/rules/NOW.md — linia `- [ ]` sekcji `## Otwarte decyzje`
  now-pending  NOW-pending.md — wpis `### ` sekcji `## Decyzje do briefingu`
  raporty      najnowszy przebieg /skill-scout (data/*.json), pamięć
               (/daily, /memory-update: nagłówek NOW.md), /reflect (wstęp)

Czyste funkcje: tylko odczyt, nic nie zapisują (vault jest tylko do odczytu —
pilnuje tego test AST), nie importują serwera. Każde źródło zwraca
(pozycje, stan); stan ma trzy rozróżnialne wartości: „ok” (także 0 pozycji),
„brak” (pliku nie ma), „nieczytelne” (zły format, za duży, brak sekcji).
Tekst z vaulta to dane — escapuje widok.
"""
import hashlib
import json
import re
from pathlib import Path

LIMIT_ZRODLA_B = 2 * 1024 * 1024
LIMIT_TYTULU = 140
LIMIT_TEKSTU = 600

SCIEZKI = {
    'skill-scout': 'Zasoby/raporty/skill-scout/_proposed.json',
    'skill-scout-dane': 'Zasoby/raporty/skill-scout/data',
    'skille': '.claude/skills',
    'reflect': 'Zasoby/Jarwis-vault-i-skille/_reflect-pending.md',
    'now': '.claude/rules/NOW.md',
    'now-pending': 'NOW-pending.md',
}
ZRODLA = ('skill-scout', 'reflect', 'now', 'now-pending')
NAZWY_ZRODEL = {
    'skill-scout': 'Propozycje skilli (/skill-scout)',
    'reflect': 'Zmiany reguł (/reflect)',
    'now': 'Otwarte decyzje (NOW.md)',
    'now-pending': 'Do wiadomości (NOW-pending.md)',
    'raport-skill-scout': 'Raport /skill-scout',
    'raport-pamiec': 'Raport pamięci (/daily, /memory-update)',
    'raport-reflect': 'Raport /reflect',
}
WARIANTY_DOMYSLNE = ({'kod': 'zatwierdz', 'napis': 'Zatwierdź'},
                     {'kod': 'odrzuc', 'napis': 'Odrzuć'})
WARIANTY_PRZYJETE = ({'kod': 'przyjete', 'napis': 'Przyjąłem do wiadomości'},)
ROZSTRZYGNIETE = ('zatwierdzone', 'odrzucone')
CHECKBOX_REFLECT = re.compile(r'^\s*- \[( |x|X)\] ✅')


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def obetnij(s: str, limit: int) -> str:
    """Najwyżej `limit` znaków; dłuższe ucięte na granicy słowa z „…”."""
    s = s.strip()
    if len(s) <= limit:
        return s
    cut = s[:limit - 1]
    if ' ' in cut:
        cut = cut[:cut.rindex(' ')]
    return cut.rstrip(' ,;:—-') + '…'


def plaski(tekst: str) -> str:
    """Markdown → jeden akapit do czytania i odsłuchu: bez płotów ```,
    znaczników cytatu i diffu, pogrubień i odwrotnych apostrofów."""
    wiersze = []
    for linia in tekst.splitlines():
        s = linia.strip()
        if s.startswith('```'):
            continue
        s = re.sub(r'^>\s?', '', s)
        s = re.sub(r'^\+\s', '', s)
        wiersze.append(s)
    s = ' '.join(wiersze).replace('**', '').replace('`', '')
    return re.sub(r'\s+', ' ', s).strip()


def _stan(zrodlo: str, stan: str, plik: str, liczba: int = 0, powod: str = '',
          **inne) -> dict:
    nazwa = NAZWY_ZRODEL.get(zrodlo, zrodlo)
    if stan == 'ok' and zrodlo.startswith('raport-'):
        kom = f'{nazwa}: z pliku {plik}'
    elif stan == 'ok':
        kom = f'{nazwa}: źródło poprawne, {liczba} poz. do decyzji'
    elif stan == 'brak':
        kom = f'{nazwa}: brak pliku {plik}'
    else:
        kom = f'{nazwa}: nieczytelne — {powod}'
    if inne.get('juz_skillem'):
        n = inne['juz_skillem']
        kom += (f' · {n} propozycja jest już skillem' if n == 1
                else f' · {n} propozycji jest już skillami')
    dopisek = inne.pop('dopisek', '')
    if dopisek:
        kom += f' — {dopisek}'
    d = {'zrodlo': zrodlo, 'nazwa': nazwa, 'stan': stan, 'plik': plik, 'liczba': liczba,
         'powod': powod, 'komunikat': kom}
    d.update(inne)
    return d


def _czytaj(zakres: Path, rel: str):
    """(tekst, None) albo (None, (stan, powód))."""
    p = Path(zakres) / rel
    try:
        if not p.is_file():
            return None, ('brak', '')
        rozmiar = p.stat().st_size
        if rozmiar > LIMIT_ZRODLA_B:
            return None, ('nieczytelne', f'za duży ({rozmiar} B, limit {LIMIT_ZRODLA_B} B)')
        with open(p, 'rb') as f:
            return f.read(LIMIT_ZRODLA_B + 1).decode('utf-8', errors='replace'), None
    except OSError as e:
        return None, ('nieczytelne', f'błąd odczytu: {e.strerror or e}')


def _pozycja(rodzaj, id_, tytul, tekst, plik, kotwica, warianty, odcisk, kolejnosc,
             znaczniki=None, ostrzezenie='') -> dict:
    tytul = obetnij(tytul, LIMIT_TYTULU)
    return {'rodzaj': rodzaj, 'id': id_, 'tytul': tytul,
            'tekst': obetnij(tekst, LIMIT_TEKSTU) or tytul,
            'zrodlo_plik': plik, 'kotwica': kotwica, 'warianty': [dict(w) for w in warianty],
            'odcisk': odcisk, 'kolejnosc': kolejnosc, 'znaczniki': znaczniki or {},
            'ostrzezenie': ostrzezenie}


def _sekcje(tekst: str, poziom: str, naglowek=None, koniec=('## ',)):
    """[(linia nagłówka, [linie treści])] dla nagłówków `poziom` (np. '## ');
    sekcja kończy się na następnym nagłówku tego poziomu albo na nagłówku
    z `koniec`. `naglowek` → tylko sekcje o tej linii nagłówka."""
    wynik, biezaca = [], None
    for linia in tekst.splitlines():
        if linia.startswith(poziom):
            biezaca = None
            if naglowek is None or linia.strip() == naglowek:
                biezaca = (linia.rstrip(), [])
                wynik.append(biezaca)
            continue
        if any(linia.startswith(k) for k in koniec):
            biezaca = None
            continue
        if biezaca is not None:
            biezaca[1].append(linia)
    return wynik


# ── skill-scout ─────────────────────────────────────────────────────────────

def skill_scout(zakres) -> tuple:
    rel = SCIEZKI['skill-scout']
    tekst, blad = _czytaj(zakres, rel)
    if blad:
        return [], _stan('skill-scout', blad[0], rel, powod=blad[1])
    try:
        dane = json.loads(tekst)
    except ValueError as e:
        return [], _stan('skill-scout', 'nieczytelne', rel, powod=f'zły JSON: {e}')
    if not isinstance(dane, dict) or not isinstance(dane.get('proposed'), list):
        return [], _stan('skill-scout', 'nieczytelne', rel,
                         powod='brak listy „proposed” w obiekcie głównym')
    skille = Path(zakres) / SCIEZKI['skille']
    wynik, pominiete, rozstrz, juz = [], 0, 0, 0
    for i, e in enumerate(dane['proposed']):
        if not isinstance(e, dict) or not isinstance(e.get('slug'), str) or not e['slug'].strip():
            pominiete += 1
            continue
        slug = e['slug'].strip()
        if str(e.get('status', '')).lower() in ROZSTRZYGNIETE or \
                str(e.get('decyzja', '')).lower() in ROZSTRZYGNIETE:
            rozstrz += 1
            continue
        typ = str(e.get('type') or '')
        if typ == 'new' and '/' not in slug and (skille / slug / 'SKILL.md').is_file():
            juz += 1
            continue
        tytul = str(e.get('title') or slug)
        co = str(e.get('what') or '')
        cel = e.get('update_target')
        try:
            prio = float(e.get('priority') or 0)
        except (TypeError, ValueError):
            prio = 0.0
        odc = _sha(json.dumps([e.get('title'), e.get('type'), cel, e.get('what')],
                              ensure_ascii=False, sort_keys=True))
        zn = {'typ': typ, 'priorytet': int(prio) if prio == int(prio) else prio}
        if cel:
            zn['cel'] = str(cel)
        wynik.append(_pozycja('skill-scout', f'skill-scout:{slug}', tytul, plaski(co),
                              rel, {'plik': rel, 'slug': slug}, WARIANTY_DOMYSLNE, odc,
                              i, zn))
    # od najwyższego priorytetu; remis — kolejność w pliku (sortowanie stabilne)
    wynik.sort(key=lambda p: -p['znaczniki']['priorytet'])
    for n, p in enumerate(wynik):
        p['kolejnosc'] = n
    return wynik, _stan('skill-scout', 'ok', rel, len(wynik), pominiete=pominiete,
                        rozstrzygniete=rozstrz, juz_skillem=juz)


# ── reflect ─────────────────────────────────────────────────────────────────

def reflect(zakres) -> tuple:
    rel = SCIEZKI['reflect']
    tekst, blad = _czytaj(zakres, rel)
    if blad:
        return [], _stan('reflect', blad[0], rel, powod=blad[1],
                         dopisek='kolejka pusta albo plik przeniesiony — nie da się ustalić'
                         if blad[0] == 'brak' else '')
    wynik, uzyte, rozstrz = [], {}, 0
    for nag, linie in _sekcje(tekst, '## ', koniec=('# ', '### ')):
        if nag.startswith('###'):
            continue
        boxy = [ln for ln in linie if CHECKBOX_REFLECT.match(ln)]
        if any('[ ]' not in ln for ln in boxy):
            rozstrz += 1
            continue
        tresc = '\n'.join(ln for ln in linie if not CHECKBOX_REFLECT.match(ln)).strip()
        tytul = nag[3:].strip()
        h = _sha(nag)[:12]
        uzyte[h] = uzyte.get(h, 0) + 1
        id_ = f'reflect:{h}' + (f'-{uzyte[h]}' if uzyte[h] > 1 else '')
        wynik.append(_pozycja('reflect', id_, tytul, plaski(tresc), rel,
                              {'plik': rel, 'naglowek': nag}, WARIANTY_DOMYSLNE,
                              _sha(tresc), len(wynik),
                              ostrzezenie='' if boxy else
                              'brak checkboxa „- [ ] ✅ Zatwierdź tę zmianę” w sekcji'))
    return wynik, _stan('reflect', 'ok', rel, len(wynik), rozstrzygniete=rozstrz)


# ── NOW.md: Otwarte decyzje ─────────────────────────────────────────────────

_AB = re.compile(r'(?:^|\s)A\s+(.+?)\s+·\s+B\s+(.+?)(?:\s+·\s+C\s+(.+?))?(?=\s+—\s|\s*$)')
_ZNAK = re.compile(r'[^\W_]', re.U)        # litera albo cyfra
META_NOW = ('Kolejki propozycji',)


def warianty_linii(tekst: str) -> list:
    """Przyciski z treści decyzji: „A … · B …” → A/B (koniec wariantu na „ · ”,
    „ — ” albo końcu linii); „X czy Y” → słowo przed i po „czy” (lewe musi
    mieć literę albo cyfrę — przegląd 98b, ustalenie 3); inaczej Zatwierdź/Odrzuć."""
    t = tekst.replace('**', '').replace('`', '')
    m = _AB.search(t)
    if m:
        return [{'kod': k, 'napis': f'{k}: {v.strip()}'}
                for k, v in zip('ABC', m.groups()) if v]
    if ' czy ' in t:
        lewa, prawa = t.split(' czy ', 1)
        lw = lewa.split()[-1] if lewa.split() else ''
        pw = prawa.split()[0].rstrip(',.;:') if prawa.split() else ''
        if _ZNAK.search(lw) and _ZNAK.search(pw):
            return [{'kod': 'A', 'napis': lw}, {'kod': 'B', 'napis': pw}]
    return [dict(w) for w in WARIANTY_DOMYSLNE]


def now(zakres) -> tuple:
    rel = SCIEZKI['now']
    tekst, blad = _czytaj(zakres, rel)
    if blad:
        return [], _stan('now', blad[0], rel, powod=blad[1])
    sekcje = _sekcje(tekst, '## ', '## Otwarte decyzje')
    if not sekcje:
        return [], _stan('now', 'nieczytelne', rel, powod='brak sekcji „## Otwarte decyzje”')
    wynik, rozstrz, meta = [], 0, 0
    for linia in sekcje[0][1]:
        m = re.match(r'^\s*- \[( |x|X)\]\s+(.*\S)', linia)
        if not m:
            continue
        if m.group(1) != ' ':
            rozstrz += 1
            continue
        reszta = m.group(2)
        b = re.match(r'\*\*(.+?)\*\*', reszta)
        tytul = (b.group(1) if b else reszta[:60]).replace('`', '').strip().rstrip(':').strip()
        if tytul.startswith(META_NOW):
            meta += 1
            continue
        wynik.append(_pozycja('now', f'now:{_sha(tytul)[:12]}', tytul, plaski(reszta), rel,
                              {'plik': rel, 'linia': linia.strip()}, warianty_linii(reszta),
                              _sha(linia.strip()), len(wynik)))
    return wynik, _stan('now', 'ok', rel, len(wynik), rozstrzygniete=rozstrz, pominiete=meta)


# ── NOW-pending.md: Decyzje do briefingu ────────────────────────────────────

def now_pending(zakres) -> tuple:
    rel = SCIEZKI['now-pending']
    tekst, blad = _czytaj(zakres, rel)
    if blad:
        return [], _stan('now-pending', blad[0], rel, powod=blad[1])
    sekcje = _sekcje(tekst, '## ', '## Decyzje do briefingu')
    if not sekcje:
        return [], _stan('now-pending', 'nieczytelne', rel,
                         powod='brak sekcji „## Decyzje do briefingu”')
    wynik, po_id, wyk = [], {}, 0
    for _, linie in sekcje:
        for nag, tresc in _sekcje('\n'.join(linie), '### '):
            tytul = nag[4:].strip()
            if tytul.startswith('Wykonawca:'):
                wyk += 1
                continue
            id_ = f'now-pending:{_sha(nag)[:12]}'
            if id_ in po_id:
                po_id[id_]['znaczniki']['powtorzenia'] += 1
                continue
            t = '\n'.join(tresc).strip()
            p = _pozycja('now-pending', id_, plaski(tytul), plaski(t), rel,
                         {'plik': rel, 'naglowek': nag}, WARIANTY_PRZYJETE,
                         _sha(nag + '\n' + t), len(wynik), {'powtorzenia': 1})
            po_id[id_] = p
            wynik.append(p)
    return wynik, _stan('now-pending', 'ok', rel, len(wynik), pominiete_wykonawca=wyk)


# ── Raporty automatów ───────────────────────────────────────────────────────

_PLIK_DANYCH = re.compile(r'^(\d{4}-\d{2}-\d{2})(.*)\.json$')
_DATA = re.compile(r'\d{4}-\d{2}-\d{2}')


def _cytaty(tekst: str, ile: int) -> list:
    """Pierwsze `ile` akapitów cytatu (`>`) przed pierwszą sekcją `## `."""
    akapity, biez = [], []
    for linia in tekst.splitlines():
        if linia.startswith('## '):
            break
        s = linia.strip()
        if s.startswith('>') and s.lstrip('>').strip():
            biez.append(s.lstrip('>').strip())
            continue
        if biez:
            akapity.append(' '.join(biez))
            biez = []
            if len(akapity) >= ile:
                break
    if biez and len(akapity) < ile:
        akapity.append(' '.join(biez))
    return akapity[:ile]


def _raport_skill_scout(zakres) -> tuple:
    kat_rel = SCIEZKI['skill-scout-dane']
    kat = Path(zakres) / kat_rel
    if not kat.is_dir():
        return None, _stan('raport-skill-scout', 'brak', kat_rel)
    try:
        nazwy = sorted(n for n in (p.name for p in kat.iterdir()) if _PLIK_DANYCH.match(n))
    except OSError as e:
        return None, _stan('raport-skill-scout', 'nieczytelne', kat_rel, powod=str(e))
    if not nazwy:
        return None, _stan('raport-skill-scout', 'brak', kat_rel + '/<data>.json')
    # najnowsza data z nazwy; remis → plik bez przyrostka (laptop, jedyny
    # z generatorem raportu — przegląd 98b, ustalenie 9)
    nazwa = max(nazwy, key=lambda n: (_PLIK_DANYCH.match(n).group(1),
                                      _PLIK_DANYCH.match(n).group(2) == '', n))
    rel = f'{kat_rel}/{nazwa}'
    tekst, blad = _czytaj(zakres, rel)
    if blad:
        return None, _stan('raport-skill-scout', blad[0], rel, powod=blad[1])
    try:
        d = json.loads(tekst)
        if not isinstance(d, dict):
            raise ValueError('obiekt główny nie jest słownikiem')
    except ValueError as e:
        return None, _stan('raport-skill-scout', 'nieczytelne', rel, powod=f'zły JSON: {e}')
    stem = nazwa[:-len('.json')]
    kand = [c for c in d.get('candidates') or [] if isinstance(c, dict)]
    st = d.get('stats') if isinstance(d.get('stats'), dict) else {}
    tytuly = '; '.join(str(c.get('title') or c.get('slug') or '?') for c in kand)
    tekst_r = (f'Przebieg z {d.get("date", stem)}, okno {d.get("window_days", "?")} dni, '
               f'{st.get("intents", "?")} wypowiedzi z {st.get("sessions", "?")} sesji. '
               f'Nowych kandydatów: {len(kand)}' + (f': {tytuly}.' if kand else '.'))
    poz = _pozycja('raport', f'raport:skill-scout:{stem}',
                   f'Raport /skill-scout z {d.get("date", stem)}', tekst_r, rel,
                   {'plik': rel}, (), _sha(tekst_r), 0, {'automat': '/skill-scout'})
    return poz, _stan('raport-skill-scout', 'ok', rel, 1)


def _raport_pamiec(zakres) -> tuple:
    rel = SCIEZKI['now']
    tekst, blad = _czytaj(zakres, rel)
    if blad:
        return None, _stan('raport-pamiec', blad[0], rel, powod=blad[1])
    m = re.search(r'^\*?Ostatni update:\s*(\d{4}-\d{2}-\d{2}(?: \d{1,2}:\d{2})?)\s*(.*?)\*?\s*$',
                  tekst, re.M)
    if not m:
        return None, _stan('raport-pamiec', 'nieczytelne', rel,
                           powod='brak linii „Ostatni update: <data>”')
    klucz, opis = m.group(1), m.group(2).strip()
    akapit = plaski(' '.join(_cytaty(tekst, 1)))
    poz = _pozycja('raport', f'raport:pamiec:{klucz}',
                   f'Pamięć (/daily, /memory-update): ostatni update {klucz} {opis}'.strip(),
                   akapit, rel, {'plik': rel}, (), _sha(klucz + akapit), 1,
                   {'automat': '/daily, /memory-update'})
    return poz, _stan('raport-pamiec', 'ok', rel, 1)


def _raport_reflect(zakres) -> tuple:
    rel = SCIEZKI['reflect']
    tekst, blad = _czytaj(zakres, rel)
    if blad:
        return None, _stan('raport-reflect', blad[0], rel, powod=blad[1],
                           dopisek='kolejka pusta albo plik przeniesiony — nie da się ustalić'
                           if blad[0] == 'brak' else '')
    m = re.search(r'Wygenerowane:([^|\n*]*)', tekst)
    daty = _DATA.findall(m.group(1)) if m else []
    if not daty:
        return None, _stan('raport-reflect', 'nieczytelne', rel,
                           powod='brak daty w linii „Wygenerowane: …”')
    klucz = daty[-1]                       # ostatnia: „uzupełnione <data>”
    akapit = plaski(' '.join(_cytaty(tekst, 2)))
    poz = _pozycja('raport', f'raport:reflect:{klucz}', f'Raport /reflect z {klucz}',
                   akapit, rel, {'plik': rel}, (), _sha(klucz + akapit), 2,
                   {'automat': '/reflect'})
    return poz, _stan('raport-reflect', 'ok', rel, 1)


def raporty(zakres) -> tuple:
    """Najnowszy raport każdego automatu do przeczytania/odsłuchu (bez decyzji)."""
    pozycje, stany = [], []
    for f in (_raport_skill_scout, _raport_pamiec, _raport_reflect):
        p, s = f(zakres)
        if p is not None:
            pozycje.append(p)
        stany.append(s)
    return pozycje, stany


# ── Całość ──────────────────────────────────────────────────────────────────

def skanuj(zakres) -> tuple:
    """(pozycje ze wszystkich źródeł i raporty, lista stanów źródeł)."""
    zakres = Path(zakres)
    pozycje, stany = [], []
    for f in (skill_scout, reflect, now, now_pending):
        p, s = f(zakres)
        pozycje += p
        stany.append(s)
    p, s = raporty(zakres)
    return pozycje + p, stany + s


def zakladka_wyboru(obecna: bool, rekord, odcisk_teraz) -> tuple:
    """(zakładka, znacznik) pozycji wg tabeli 3.4 planu 98:
    w źródle bez wyboru → Do decyzji; wybór przy tej samej treści →
    Rozstrzygnięte („zgodny”); treść zmieniona po wyborze → Do decyzji
    („zmieniona”); pozycji już nie ma w źródle → Rozstrzygnięte z migawki
    („usuniete”); ani pozycji, ani wyboru → nic do pokazania (None)."""
    if rekord is None:
        return ('decyzja', '') if obecna else (None, '')
    if not obecna:
        return 'rozstrzygniete', 'usuniete'
    if rekord.get('odcisk') == odcisk_teraz:
        return 'rozstrzygniete', 'zgodny'
    return 'decyzja', 'zmieniona'
