"""Automatyczne odświeżanie nagrań lektora po zmianie dokumentu.

Karol 02.10: „Przy okazji automatycznie powinien odtworzyć nowy plik audio po
jego modyfikacjach.”

Każde nagranie `<nazwa>_lektor.<audio>` dostaje metrykę `<nazwa>_lektor.zrodlo.json`:
ścieżkę dokumentu (względem katalogu głównego), odcisk SHA-256 jego treści
i silnik mowy. Przegląd (przy starcie usługi i cyklicznie — wychwytuje każde
odświeżenie klonu vaulta) porównuje odcisk z bieżącą treścią WYŁĄCZNIE dla
dokumentów, które mają nagranie. Inny odcisk = nagranie nieaktualne → serwer
dopisuje zadanie ponownego generowania do kolejki lektora.

Odcisk treści: SHA-256 CZĘŚCI CZYTANEJ (do znacznika `<!-- lektor: koniec -->`
— zmiana tylko w załączniku nie wymusza nowego nagrania), bez nagłówka
Obsidiana (zmiana samego statusu nie wymusza nagrania), końce linii
ujednolicone — ta sama funkcja co lista „Do przesłuchania” (przesluchania.odcisk).
Sama zmiana czasu pliku (git pull, przywracanie czasów z historii) nie wywołuje
generowania. Dokument .docx z bliźniaczym .md (ten sam
rdzeń nazwy, ten katalog albo ../processed/) — odcisk .md, bo to on jest
czytany; .docx bez bliźniaka — odcisk bajtów pliku.

Nagranie sprzed metryk (powstałe przed wdrożeniem): dokument zmieniony PO
nagraniu (czas zmiany treści z gita, tools/czas_z_gita.py) = nieaktualne;
inaczej metryka powstaje z bieżącym odciskiem (stan wyjściowy).

Tylko biblioteka standardowa (testy, Anbernic).
"""
import hashlib
import json
import os
import re
from pathlib import Path

import nagrania as _nagr
import przesluchania as _prz

METRYKA = '.zrodlo.json'
# Znacznik końca części czytanej: od tej linii do końca pliku lektor nie czyta
# (załącznik ze szczegółami i źródłami). Ta sama reguła w tools/czytaj_tts.py
# (plik kopiowany osobno do katalogu eksportu, więc nie importuje modułów app/);
# zgodność obu pilnuje test_lektor_znacznik_koniec.py.
ZNACZNIK_KONCA = re.compile(r'(?im)^[ \t]*<!--[ \t]*lektor:[ \t]*koniec[ \t]*-->[ \t]*$')


def czesc_czytana(tekst: str) -> str:
    """Tekst do linii `<!-- lektor: koniec -->` (bez niej); brak znacznika = całość."""
    m = ZNACZNIK_KONCA.search(tekst)
    return tekst if m is None else tekst[:m.start()]


def podziel_na_zalacznik(tekst: str) -> tuple:
    """(część czytana, załącznik bez linii znacznika albo None)."""
    m = ZNACZNIK_KONCA.search(tekst)
    if m is None:
        return tekst, None
    return tekst[:m.start()], tekst[m.end():].lstrip('\r\n')


ZRODLA = ('.md', '.docx', '.txt')
WERSJA_METRYKI = 1


def odcisk_tekstu(tekst: str) -> str:
    """Odcisk listy „Do przesłuchania” (przesluchania.odcisk: treść bez nagłówka
    Obsidiana, końce linii ujednolicone) — ta sama funkcja, nie kopia."""
    return _prz.odcisk(tekst)


def blizniak_md(docx: Path):
    """Bliźniaczy .md dokumentu .docx (ten sam rdzeń nazwy: ten katalog albo
    ../processed/) — to on jest czytany przez lektora; None = brak."""
    docx = Path(docx)
    for cand in (docx.parent / (docx.stem + '.md'),
                 docx.parent.parent / 'processed' / (docx.stem + '.md')):
        if cand.exists():
            return cand
    return None


def odcisk_dokumentu(plik: Path) -> str:
    """Odcisk treści, którą lektor czyta z dokumentu (OSError przy braku pliku)."""
    plik = Path(plik)
    if plik.suffix.lower() == '.docx':
        md = blizniak_md(plik)
        if md is None:
            return hashlib.sha256(plik.read_bytes()).hexdigest()
        plik = md
    return odcisk_tekstu(czesc_czytana(plik.read_text(encoding='utf-8', errors='replace')))


def plik_metryki(audio: Path) -> Path:
    audio = Path(audio)
    return audio.with_name(audio.stem + METRYKA)


def czytaj_metryke(audio: Path):
    """Metryka nagrania albo None (brak, nieczytelna, bez odcisku)."""
    try:
        d = json.loads(plik_metryki(audio).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get('sha256'), str):
        return None
    return d


def zapisz_metryke(audio: Path, plik_rel: str, sha256: str, silnik: str = '',
                   czas: str = '') -> Path:
    """Zapis atomowy (plik tymczasowy + zamiana) — przerwany zapis nie zostawia
    połowy pliku, którą przegląd uznałby za brak metryki."""
    cel = plik_metryki(audio)
    tmp = cel.with_name('.' + cel.name + '.tmp')
    tmp.write_text(json.dumps({'wersja': WERSJA_METRYKI, 'zrodlo': plik_rel,
                               'sha256': sha256, 'silnik': silnik, 'czas': czas},
                              ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, cel)
    return cel


def nagrania(katalog_lektora: Path) -> list:
    """Nagrania w katalogu lektora (rekurencyjnie, bez katalogów ukrytych);
    przy kilku formatach jednego dokumentu — pierwszy wg kolejności AUDIO_EXT
    (ten sam, który odtwarza serwer)."""
    k = Path(katalog_lektora)
    if not k.is_dir():
        return []
    wynik = []
    for korzen, katalogi, pliki in os.walk(k):
        katalogi[:] = sorted(d for d in katalogi if not d.startswith('.'))
        zbior = set(pliki)
        rdzenie = sorted({Path(n).stem for n in pliki if _nagr.jest_nagraniem_lektora(Path(n))})
        for rdzen in rdzenie:
            for ext in _nagr.AUDIO_EXT:
                if rdzen + ext in zbior:
                    wynik.append(Path(korzen) / (rdzen + ext))
                    break
    return wynik


def dokument_nagrania(audio: Path, katalog_lektora: Path, root: Path, metryka=None):
    """Dokument źródłowy nagrania: ścieżka z metryki, inaczej z układu katalogu
    lektora (katalog_lektora/<ścieżka względna>/<rdzeń>_lektor.<audio> →
    root/<ścieżka względna>/<rdzeń>.md|.docx|.txt). None = dokumentu nie ma
    (przeniesiony, usunięty) — wtedy nie ma czego porównywać."""
    audio, root = Path(audio), Path(root)
    if metryka and metryka.get('zrodlo'):
        p = root / metryka['zrodlo']
        if p.is_file():
            return p
    try:
        rel = audio.parent.relative_to(Path(katalog_lektora))
    except ValueError:
        return None
    rdzen = audio.stem[:-len(_nagr.PRZYROSTEK)]
    for ext in ZRODLA:
        p = root / rel / (rdzen + ext)
        if p.is_file():
            return p
    return None


def przeglad(katalog_lektora: Path, root: Path, pamiec: dict) -> list:
    """Nagrania nieaktualne: [{audio, plik, stary, nowy, silnik, powod}].

    pamiec: {str(dokument): {klucz, audio, sha, nieaktualne}} — między
    przeglądami; dokument o niezmienionym (czas, rozmiar) i nagranie o
    niezmienionym czasie nie są czytane ponownie. Nagranie bez metryki dostaje
    ją tu (stan wyjściowy), o ile dokument nie zmienił się po nagraniu.
    Wynik zawiera każde nagranie nieaktualne (także już zgłoszone) — o
    dublowaniu kolejki decyduje wywołujący."""
    wynik = []
    root = Path(root)
    for audio in nagrania(katalog_lektora):
        met = czytaj_metryke(audio)
        doc = dokument_nagrania(audio, katalog_lektora, root, met)
        if doc is None:
            continue
        try:
            st, sa = doc.stat(), audio.stat()
        except OSError:
            continue
        klucz = (st.st_mtime_ns, st.st_size)
        pam = pamiec.get(str(doc))
        if not (pam and pam['klucz'] == klucz and pam['audio'] == sa.st_mtime_ns
                and pam.get('met') == (met or {}).get('sha256')):
            try:
                sha = odcisk_dokumentu(doc)
            except OSError:
                continue
            powod = ''
            if met is None:
                if st.st_mtime > sa.st_mtime:
                    powod = 'dokument zmieniony po nagraniu (nagranie sprzed odcisków)'
                else:
                    try:
                        zapisz_metryke(audio, doc.relative_to(root).as_posix(), sha)
                        met = czytaj_metryke(audio)
                    except (OSError, ValueError):
                        pass
            elif met['sha256'] != sha:
                powod = 'zmiana treści'
            pam = {'klucz': klucz, 'audio': sa.st_mtime_ns, 'sha': sha,
                   'met': (met or {}).get('sha256'), 'nieaktualne': bool(powod),
                   'powod': powod, 'stary': (met or {}).get('sha256'),
                   'silnik': (met or {}).get('silnik', '')}
            pamiec[str(doc)] = pam
        if pam['nieaktualne']:
            wynik.append({'audio': audio, 'plik': doc, 'stary': pam['stary'],
                          'nowy': pam['sha'], 'silnik': pam['silnik'],
                          'powod': pam['powod']})
    return wynik


def puls_zajety(status: dict) -> bool:
    """Czy Puls (claude-cron) ma trwający bieg — z odpowiedzi /api/status."""
    if not isinstance(status, dict):
        return False
    return bool(status.get('current_run')) or bool(status.get('current_runs'))


def puls_status(adres: str, limit_s: float = 3.0) -> dict:
    """GET <adres>/api/status z limitem czasu (wyjątek przy braku odpowiedzi)."""
    import urllib.request
    with urllib.request.urlopen(adres.rstrip('/') + '/api/status', timeout=limit_s) as r:
        return json.loads(r.read(1 << 20).decode('utf-8', 'replace'))
