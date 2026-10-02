#!/usr/bin/env python3
"""AnberFiles — serwer plików HTTP (aiohttp), HTTP Basic Auth albo logowanie
formularzem z zapamiętaniem urządzenia (app/logowanie.py, [serwer] logowanie).

Ścieżki, port i przełączniki modułów: app/konfiguracja.py (plik ustawień
instancji wskazany zmienną ANBERFILES_CONF; brak zmiennej = ustawienia konsoli
Anbernic). Zmienne środowiska nadal działają i wygrywają z plikiem:
    SERVER_PORT, SERVER_USER, SERVER_HOST; hasło wyłącznie SERVER_PASS.

Listing katalogu = sortowalna tabela (Nazwa, Rozmiar, Modyfikacja, Utworzono).
Kliknięcie nagłówka kolumny sortuje (rozmiar i daty sortowane numerycznie).
"""
import os
import re
import html as _html
import base64
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
import asyncio
import collections
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import logging
import shutil
import sys
from aiohttp import web

import konfiguracja as _konf

try:
    import markdown as _markdown      # renderowany podgląd .md (opcjonalny)
except Exception:
    _markdown = None

try:
    import nh3 as _nh3                # sanityzacja HTML z Markdown (audyt A3)
except Exception:
    _nh3 = None


# ── Bezpieczeństwo treści (audyt: A3, B1, B2, C5) ───────────────────────────
# Jedna definicja każdej funkcji ochronnej; używane we wszystkich widokach.

def _esc(s) -> str:
    """Tekst do HTML (treść ORAZ wartość atrybutu w cudzysłowie): & < > " '."""
    return _html.escape(str(s), quote=True)


def _link_startowy() -> str:
    """Link do strony startowej serwera („🏠 <nazwa instancji>”) do pasków widoków;
    pusty, gdy ustawienie strona_startowa jest puste (Anbernic). Adres z ustawień
    (walidowany w konfiguracji do http/https), w HTML escapowany."""
    adres = getattr(KONF, 'strona_startowa', '') if KONF is not None else ''
    if not adres:
        return ''
    return (f'<a href="{_esc(adres)}" title="Strona startowa">'
            f'🏠 {_esc(KONF.nazwa_instancji)}</a>')


def _json_do_script(obj) -> str:
    """JSON bezpieczny wewnątrz <script>…</script>: „<" jako \\u003c (nie da się
    zamknąć znacznika ani otworzyć komentarza <!--), U+2028/U+2029 jako ucieczki
    (dawne silniki JS traktowały je jak koniec wiersza). Wynik to nadal poprawny
    JSON — JSON.parse/literał JS daje dokładnie te same dane."""
    import json as _json
    return (_json.dumps(obj, ensure_ascii=False)
            .replace('<', '\\u003c')
            .replace(' ', '\\u2028')
            .replace(' ', '\\u2029'))


# Escapowanie po stronie przeglądarki (to samo co _esc) — jedna definicja JS,
# wklejana do skryptów, które budują HTML z danych (CSV, XLSX, kolejka lektora).
JS_ESC = ("function esc(s){return String(s).replace(/[&<>\"']/g,c=>({'&':'&amp;',"
          "'<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));}")

# B9: żądania zmieniające stan (POST/PUT/PATCH/DELETE) muszą nieść nagłówek
# X-AnberFiles: 1 (straz_zrodla). Formularz z obcej strony go nie doda, a fetch
# z obcego originu z własnym nagłówkiem wymaga zgody CORS, której serwer nie
# daje. JEDNA definicja JS: afFetch(u,o) = fetch z nagłówkiem, afXhr(x) =
# nagłówek dla XMLHttpRequest (po open). Wklejana do każdego skryptu, który
# zapisuje; test pilnuje, że w kodzie stron nie ma gołego fetch z POST/DELETE.
NAGLOWEK_ZAPISU = 'X-AnberFiles'
JS_ZAPIS = ("function afFetch(u,o){o=Object.assign({},o||{});"
            "o.headers=Object.assign({'X-AnberFiles':'1'},o.headers||{});"
            "return fetch(u,o);}"
            "function afXhr(x){x.setRequestHeader('X-AnberFiles','1');return x;}")

# Obce źródła skryptów dopuszczone w CSP: MathJax (wzory w podglądzie .md)
# i Three.js (podgląd modeli 3D). Dane (connect-src, img-src) — wyłącznie własny
# origin, więc wstrzyknięty skrypt nie wyśle treści vaulta na zewnątrz fetch-em
# ani obrazkiem-sygnałem.
CDN_SKRYPTY = ('https://cdn.jsdelivr.net', 'https://unpkg.com')
CDN_CZCIONKI = ('https://cdn.jsdelivr.net',)    # czcionki MathJax CHTML

# Pliki, które przeglądarka wykonałaby jako dokument ze skryptami (HTML, SVG,
# XML/XHTML) — przy zwykłym serwowaniu dostają CSP „sandbox" bez allow-scripts:
# treść da się obejrzeć, ale skrypt nie ruszy, a origin jest nieprzezroczysty.
EXT_AKTYWNE = {'.html', '.htm', '.xhtml', '.svg', '.svgz', '.xml', '.xsl', '.xslt'}


def _csp(pdf: bool = False) -> str:
    skr = ' '.join(CDN_SKRYPTY)
    czc = ' '.join(CDN_CZCIONKI)
    dyr = [
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline' {skr}",
        "style-src 'self' 'unsafe-inline'",
        "connect-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        f"font-src 'self' data: {czc}",
        "worker-src 'self' blob:",
        "frame-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ]
    if not pdf:
        # wbudowana przeglądarka PDF (Chrome) bywa blokowana przez object-src
        # na odpowiedzi z samym PDF-em — tam dyrektywę pomijamy
        dyr.insert(8, "object-src 'none'")
    return '; '.join(dyr)


_CSP = _csp()
_CSP_PDF = _csp(pdf=True)


async def _naglowki_bezpieczenstwa(request, response):
    """on_response_prepare: KAŻDA odpowiedź (strony, pliki, JSON, błędy 4xx/5xx,
    ekrany logowania) dostaje nagłówki bezpieczeństwa. CSP ustawiona wcześniej
    przez widok (np. „sandbox" dla plików aktywnych) jest dołączana, nie gubiona."""
    h = response.headers
    pdf = h.get('Content-Type', '').startswith('application/pdf')
    wlasna = h.get('Content-Security-Policy')
    baza = _CSP_PDF if pdf else _CSP
    h['Content-Security-Policy'] = baza + (f'; {wlasna}' if wlasna else '')
    h['X-Content-Type-Options'] = 'nosniff'
    h['Referrer-Policy'] = 'no-referrer'
    h['X-Frame-Options'] = 'SAMEORIGIN'      # drzewo (?explorer) osadza własne strony
    h['Server'] = 'AnberFiles'               # bez wersji Pythona i aiohttp (C5)


def _battery_html() -> str:
    """Poziom baterii konsoli (PMIC axp2202) do linii informacyjnej —
    aktualizuje się razem z auto-odświeżaniem listingu."""
    if not KONF.modul('bateria'):
        return ''
    try:
        base = _konf.SCIEZKA_BATERII
        cap = int((base / 'capacity').read_text().strip())
        st = (base / 'status').read_text().strip()
    except Exception:
        return ''
    ikona = '⚡' if st in ('Charging', 'Full') else '🔋'
    col = '#1d7a36' if cap > 40 else ('#9a7b00' if cap > 15 else '#c00')
    return (f' · <span style="color:{col};font-weight:600">'
            f'{ikona} {cap}%</span>')


def _natkey(s: str):
    """Klucz sortowania naturalnego: 'plik_10' PO 'plik_9' (liczby jako liczby)."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', s)]

# Ustawienia instancji — wartości przypisuje zastosuj_konfiguracje() (przy
# imporcie: domyślne Anbernica; main() i testy podają właściwe).
KONF = None
ROOT = PORT = HOST = USER = PASSW = None
EVENT_LOG = DOCX_CACHE = EXPORT_DIR = FAVICON_ICO_PATH = None
CZYTAJ_TTS = LEKTOR_CONF = LEKTOR_QFILE = None
# logowanie formularzem: moduł app/logowanie.py i limit prób (utworz_aplikacje)
_LOGOWANIE = _BRAMKA = None


def zastosuj_konfiguracje(k):
    """Przypisuje stałe modułu z ustawień instancji (jedno źródło prawdy)."""
    global KONF, ROOT, PORT, HOST, USER, PASSW, EVENT_LOG, DOCX_CACHE
    global EXPORT_DIR, FAVICON_ICO_PATH, CZYTAJ_TTS, LEKTOR_CONF, LEKTOR_QFILE
    KONF = k
    ROOT = Path(k.katalog_glowny).resolve()
    PORT, HOST, USER, PASSW = k.port, k.host, k.uzytkownik_www, k.haslo
    EVENT_LOG = k.rejestr_zdarzen
    DOCX_CACHE = k.pamiec_podr_docx
    EXPORT_DIR = k.katalog_eksportu
    FAVICON_ICO_PATH = k.favikona
    CZYTAJ_TTS = str(k.czytaj_tts)
    LEKTOR_CONF = str(k.lektor_conf)
    LEKTOR_QFILE = k.kolejka_lektora


zastosuj_konfiguracje(_konf.domyslna())


def _odmowa_zapisu():
    """403 dla operacji zapisu w trybie tylko do odczytu (None = wolno)."""
    if KONF.tylko_odczyt:
        return web.Response(
            status=403, text=f'Instancja {KONF.nazwa_instancji} działa w trybie '
                             'tylko do odczytu — zapis wyłączony w ustawieniach.')
    return None


def _modul_wylaczony(nazwa: str):
    """403 dla funkcji wyłączonej przełącznikiem [moduly] (None = włączona)."""
    if not KONF.modul(nazwa):
        return web.Response(
            status=403, text=f'Moduł „{nazwa}" jest wyłączony w ustawieniach '
                             f'instancji {KONF.nazwa_instancji}.')
    return None


# Ciało żądania wczytywane do pamięci w całości (request.read/post/json):
# kadrowanie (JSON), formularze. Wgrywanie plików czyta strumieniowo i ma
# osobny limit (limit_wgrywania_mb), logowanie — 4 KiB (logowanie.py).
LIMIT_CIALA_ZADANIA = 64 * 1024


# ── Rejestr zdarzeń i błędów (podgląd: /?events=1) ───────────────────────────
_EVLOG_CAP = 2 * 1024 * 1024          # 2 MB — przytnij gdy urośnie


def _evlog(kind: str, msg: str, level: str = 'info'):
    """Dopisz zdarzenie/błąd do rejestru: TS \\t LEVEL \\t KIND \\t MSG.
    Nigdy nie rzuca (logowanie nie może wywrócić żądania)."""
    try:
        line = (f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\t'
                f'{level}\t{kind}\t{msg}\n')
        with open(EVENT_LOG, 'a', encoding='utf-8') as f:
            f.write(line)
        if EVENT_LOG.stat().st_size > _EVLOG_CAP:        # prosta rotacja
            data = EVENT_LOG.read_text(encoding='utf-8', errors='replace')
            EVENT_LOG.write_text(data[-_EVLOG_CAP // 2:], encoding='utf-8')
    except Exception:
        pass

# ── Dziennik dostępu (D2) ───────────────────────────────────────────────────
# Jedna linia na żądanie: czas \t adres \t metoda \t ścieżka?zapytanie \t kod
# \t bajty treści \t czas obsługi. Bez nagłówków i ciasteczek; wartości
# parametrów z sekretem (token=, haslo=) zastąpione ***. Plik
# katalog_danych/access.log (klucz dziennik_dostepu), rotacja 5 × 5 MB.
DOSTEP_MAX_B = 5 * 1024 * 1024
DOSTEP_KOPIE = 5
_PARAM_SEKRETNY = re.compile(r'(?i)(^|&)((?:token|haslo|password)=)[^&]*')
_DZIENNIK_DOSTEPU = None


def _maskuj_zapytanie(zapytanie: str) -> str:
    return _PARAM_SEKRETNY.sub(r'\1\2***', zapytanie)


class DziennikDostepu(web.AbstractAccessLogger):
    """Rejestrator dostępu aiohttp (access_log_class) — format jak wyżej."""

    def log(self, request, response, czas):
        try:
            zap = request.rel_url.raw_query_string
            sciezka = request.rel_url.raw_path + ('?' + _maskuj_zapytanie(zap) if zap else '')
            # bajty treści (Content-Length); strumień bez długości (ZIP) —
            # bajty wysłane łącznie z nagłówkami
            bajty = response.content_length
            if bajty is None:
                bajty = response.body_length
            self.logger.info('%s\t%s\t%s\t%s\t%d\t%d\t%.0fms',
                             datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                             request.remote or '-', request.method, sciezka,
                             response.status, bajty, czas * 1000)
        except Exception:
            pass                             # dziennik nie może wywrócić odpowiedzi


def zamknij_dziennik_dostepu(app=None):
    """Zamyka plik dziennika dostępu (on_cleanup; kolejny start otwiera nowy)."""
    lg = logging.getLogger('anberfiles.dostep')
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


async def _dziennik_stop(app):
    zamknij_dziennik_dostepu()


def opcje_dziennika_dostepu() -> dict:
    """Argumenty dla web.run_app / AppRunner: rejestrator i jego logger."""
    import logging.handlers
    zamknij_dziennik_dostepu()
    lg = logging.getLogger('anberfiles.dostep')
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        h = logging.handlers.RotatingFileHandler(
            KONF.dziennik_dostepu, maxBytes=DOSTEP_MAX_B, backupCount=DOSTEP_KOPIE,
            encoding='utf-8')
    except OSError as e:
        _evlog('start', f'dziennik dostępu {KONF.dziennik_dostepu} niedostępny: {e}',
               level='warn')
        return {'access_log': None}
    h.setFormatter(logging.Formatter('%(message)s'))
    lg.addHandler(h)
    return {'access_log_class': DziennikDostepu, 'access_log': lg}


ICONS = {'pdf': '📕', 'html': '🌐', 'md': '📝', 'jpg': '🖼', 'jpeg': '🖼',
         'png': '🖼', 'xlsx': '📊', 'xls': '📊', 'csv': '📊', 'txt': '📄',
         'docx': '📘', 'doc': '📘', 'svg': '🖼', 'json': '🔧',
         'stl': '🧊', 'obj': '🧊', 'ply': '🧊', 'glb': '🧊',
         'gltf': '🧊', '3mf': '🧊',
         'mp4': '🎬', 'webm': '🎬', 'mov': '🎬', 'mkv': '🎬',
         'm4v': '🎬', 'avi': '🎬', 'ogv': '🎬'}

IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}
AUDIO_EXT = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.opus'}
VIDEO_EXT = {'.mp4', '.webm', '.mov', '.m4v', '.ogv', '.mkv', '.avi'}
MODEL_EXT = {'.stl', '.obj', '.ply', '.glb', '.gltf', '.3mf'}   # podgląd 3D (Three.js)
CSV_EXT = {'.csv', '.tsv'}                       # podgląd tabelaryczny + kod (parser w JS)
XLSX_EXT = {'.xlsx', '.xlsm'}                    # podgląd przez openpyxl (.xls = legacy → pobranie)
AUDIO_MIME = {'.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.wav': 'audio/wav',
              '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.opus': 'audio/opus'}

# Lektor zapisuje <stem>_lektor.<ext>: obok dokumentu (Anbernic) albo w
# katalog_lektora/<ścieżka względna katalogu źródła>/ (gdy ustawiony).
# Preferuj FLAC > MP3 > ...
LEKTOR_AUDIO_EXT = ('.flac', '.mp3', '.wav', '.ogg', '.m4a', '.opus')
LEKTOR_ZRODLA = ('.md', '.docx', '.txt')        # dokumenty, które lektor czyta
LEKTOR_SILNIKI = ('edge', 'piper')              # silniki mowy czytaj_tts.py
LEKTOR_UWAGA_EDGE = 'Silnik edge: tekst opuszcza urządzenie (usługa Microsoft).'


def _lektor_dir_for(target: Path):
    """Katalog wyników lektora dla dokumentu w katalogu lektora instancji
    (None = katalog lektora nie ustawiony albo dokument poza katalogiem głównym)."""
    if KONF.katalog_lektora is None:
        return None
    try:
        rel = target.parent.resolve().relative_to(ROOT)
    except ValueError:
        return None
    return Path(KONF.katalog_lektora).resolve() / rel


def _lektor_dirs(target: Path, exports: bool):
    """Katalogi, w których może leżeć audio lektora dla dokumentu — w kolejności."""
    dirs = [target.parent]
    if exports:
        dirs.append(target.parent.parent / 'exports')
    kl = _lektor_dir_for(target)
    if kl is not None:
        dirs.append(kl)
    return dirs


def _lektor_audio_for(target: Path, exports: bool = False):
    """Ścieżka pliku lektora dla podglądanego dokumentu, jeśli istnieje
    (obok dokumentu, opcjonalnie w ../exports/, w katalogu lektora)."""
    base = target.stem + '_lektor'
    for d in _lektor_dirs(target, exports):
        for ext in LEKTOR_AUDIO_EXT:
            p = d / (base + ext)
            if p.exists():
                return p
    return None


def _lektor_audio_for_doc(target: Path):
    """Jak wyżej, ale dla DOKUMENTU ŹRÓDŁOWEGO: także siostrzany ../exports/
    (układ projektu: processed/<X>.md → exports/<X>_lektor.*)."""
    return _lektor_audio_for(target, exports=True)


def _url_abs(p: Path) -> str:
    """Bezwzględny adres URL pliku w katalogu głównym (audio z innego katalogu)."""
    return '/' + quote(p.resolve().relative_to(ROOT).as_posix())


def _audio_chapters(aud, md_text: str = ''):
    """Rozdziały dla audio: DOKŁADNE z `<stem>.chapters.json` (czytaj_tts, czasy z
    bajtów), inaczej SZACOWANE z nagłówków md (frac wg słów — JS mnoży przez
    audio.duration). Zwraca [{title,t}] (dokładne) lub [{title,frac}] (szacowane)."""
    import json as _json
    import re as _re
    if aud is not None:
        cp = aud.with_name(aud.stem + '.chapters.json')
        if cp.exists():
            try:
                ch = _json.loads(cp.read_text(encoding='utf-8'))
                if ch:
                    return ch
            except Exception:
                pass
    if md_text:
        parts = _re.split(r'(?m)^(#{1,6}\s+.+)$', md_text)
        segs = [(None, parts[0])]
        for i in range(1, len(parts), 2):
            title = _re.sub(r'^#{1,6}\s+', '', parts[i]).strip()
            body = parts[i + 1] if i + 1 < len(parts) else ''
            segs.append((title, parts[i] + ' ' + body))
        wc = lambda s: len(_re.findall(r'\w+', s))
        total = sum(wc(t) for _, t in segs) or 1
        cum, out = 0, []
        for title, text in segs:
            if title is not None:
                out.append({'title': title, 'frac': round(cum / total, 4)})
            cum += wc(text)
        return out
    return []


def _lektor_cues_for(target: Path, aud: Path = None):
    """Sidecar timingów zdaniowych (<stem>_lektor.cues.json), jeśli istnieje —
    najpierw obok znalezionego audio, potem w katalogach lektora dokumentu."""
    name = target.stem + '_lektor.cues.json'
    dirs = ([aud.parent] if aud is not None else []) + _lektor_dirs(target, True)
    for d in dirs:
        p = d / name
        if p.exists():
            return p
    return None


def render_read_page(target: Path, audio: Path, cues_path: Path) -> str:
    """Read-along: zdania z sidecara timingów, podświetlane wg pozycji audio
    (timeupdate). Klik w zdanie = przewinięcie audio. Tekst = wersja CZYTANA
    (po normalizacji), nie surowy DOCX — zgodny 1:1 z tym, co słychać."""
    import html as _html
    import json as _json
    try:
        cues = _json.loads(cues_path.read_text(encoding='utf-8'))
    except Exception:
        cues = []
    times = [float(c.get('t', 0)) for c in cues]
    spans = []
    for i, c in enumerate(cues):
        txt = _html.escape(c.get('text', ''))
        spans.append(f'<span class=s data-i="{i}" onclick="seek({i})">{txt}</span>')
    aq = _url_abs(audio)
    dq = quote(target.name)
    body = ' '.join(spans) or '<em>brak zdań w sidecarze</em>'
    times_js = '[' + ','.join(f'{t:.2f}' for t in times) + ']'
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>read-along: {_html.escape(target.name)}</title>'
        '<style>'
        'body{margin:0;background:#14161c;color:#cfd6df;'
        'font-family:system-ui,sans-serif}'
        '.hd{position:sticky;top:0;z-index:5;background:#1d2027;'
        'box-shadow:0 2px 8px #0007}'
        '.bar{display:flex;gap:.6em;align-items:center;padding:.5em 1em;'
        'flex-wrap:wrap;font-size:.9em}'
        '.bar a{color:#7ab7ff;text-decoration:none;border:1px solid #343a45;'
        'border-radius:5px;padding:.2em .6em}'
        'audio{width:100%;display:block;background:#1d2027}'
        '#txt{max-width:760px;margin:0 auto;padding:1.2em 1.1em 60vh;'
        'line-height:2.05;font-size:1.18em}'
        '.s{cursor:pointer;padding:.04em .12em;border-radius:4px}'
        '.s:hover{background:#262d39}'
        '.s.cur{background:#1a5fb4;color:#fff}'
        '</style>'
        '<div class="hd"><div class="bar">'
        f'<a href="./">📁 folder</a>{_link_startowy()}'
        f'<a href="{dq}?view=1">📘 podgląd</a>'
        f'<span style="word-break:break-all">{_html.escape(target.name)}</span>'
        f'<span style="color:#6fce8f;margin-left:auto">📖 {len(cues)} zdań</span>'
        '</div>'
        f'<audio id="au" controls preload="metadata" src="{aq}"></audio>'
        '</div>'
        f'<div id="txt">{body}</div>'
        '<script>(function(){'
        f'const T={times_js};'
        'const sp=[...document.querySelectorAll(".s")];'
        'const au=document.getElementById("au");let cur=-1;'
        'function hi(i){if(i===cur)return;'
        'if(cur>=0&&sp[cur])sp[cur].classList.remove("cur");cur=i;'
        'if(i>=0&&sp[i]){sp[i].classList.add("cur");'
        'sp[i].scrollIntoView({block:"center",behavior:"smooth"});}}'
        'au.addEventListener("timeupdate",function(){'
        'const t=au.currentTime;let lo=0,h=T.length-1,k=-1;'
        'while(lo<=h){const m=(lo+h)>>1;if(T[m]<=t){k=m;lo=m+1;}else h=m-1;}'
        'hi(k);});'
        'window.seek=function(i){au.currentTime=T[i]+0.02;au.play();};'
        '})();</script>')

AUDIO_STYLE = (
    'body{margin:0;background:#14161c;color:#cfd6df;font-family:system-ui,sans-serif;'
    'display:flex;flex-direction:column;min-height:100vh}'
    '.bar{display:flex;gap:.6em;align-items:center;padding:.55em 1em;'
    'background:#1d2027;flex-wrap:wrap}'
    '.bar a,.bar button{color:#7ab7ff;background:none;border:1px solid #343a45;'
    'border-radius:5px;padding:.25em .7em;text-decoration:none;cursor:pointer;'
    'font:inherit;font-size:.95em}'
    '.bar a:hover,.bar button:hover{background:#272b34}'
    '.bar a.dis{opacity:.35;pointer-events:none}'
    '.bar button.on{background:#1a5fb4;border-color:#1a5fb4;color:#fff}'
    '.wrap{flex:1;display:flex;flex-direction:column;align-items:center;'
    'justify-content:center;gap:1.2em;padding:1em}'
    '.tname{font-size:1.15em;color:#eee;word-break:break-all;text-align:center;'
    'max-width:90%}'
    'audio{width:min(680px,92vw)}'
    '.spd{display:flex;gap:.4em;align-items:center;color:#8a93a0;font-size:.9em}'
    '.chaps{width:min(680px,92vw);background:#1d2027;border:1px solid #2a2f38;'
    'border-radius:8px;padding:.3em .5em;max-height:42vh;overflow:auto}'
    '.chaps summary{cursor:pointer;color:#8a93a0;font-size:.9em;padding:.25em}'
    '.chp{display:block;width:100%;text-align:left;background:none;border:0;'
    'color:#cfd6df;padding:.35em .5em;border-radius:5px;cursor:pointer;'
    'font:inherit;font-size:.95em}'
    '.chp:hover{background:#272b34}'
    '.chp.cur{background:#1a5fb4;color:#fff}'
    '.chp .ct{color:#6fce8f;font-variant-numeric:tabular-nums;margin-right:.6em}'
    '.chp.cur .ct{color:#d6e9ff}'
)


def render_3d_page(target: Path) -> str:
    """Podgląd 3D (?view=1) — Three.js z CDN dla formatów MESH: STL/OBJ/PLY/glTF/GLB/3MF.
    Model wczytywany w PRZEGLĄDARCE z surowego pliku. STEP/IGES (B-rep CAD) NIE — wymagają
    konwersji serwerowej do STL (FreeCAD/gmsh — niezainstalowane)."""
    ext = target.suffix.lower().lstrip('.')
    q = quote(target.name)
    _lm = {'stl': ('STLLoader', 'STLLoader.js'), 'ply': ('PLYLoader', 'PLYLoader.js'),
           'obj': ('OBJLoader', 'OBJLoader.js'), 'glb': ('GLTFLoader', 'GLTFLoader.js'),
           'gltf': ('GLTFLoader', 'GLTFLoader.js'), '3mf': ('ThreeMFLoader', '3MFLoader.js')}
    loader, lfile = _lm.get(ext, ('STLLoader', 'STLLoader.js'))
    tpl = (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>__NAME__</title>'
        '<style>html,body{margin:0;height:100%;overflow:hidden;'
        'font-family:system-ui,sans-serif;background:#c4cdd8}'
        '.bar{position:fixed;top:0;left:0;right:0;z-index:5;display:flex;gap:.4em;'
        'align-items:center;padding:.4em .7em;background:#161a22ee;color:#ddd;'
        'font-size:.85em;flex-wrap:wrap}'
        '.bar a,.bar button{color:#cfe3ff;background:#2a3140;text-decoration:none;'
        'border:1px solid #3a4250;border-radius:5px;padding:.25em .6em;'
        'font-size:1em;line-height:1;cursor:pointer}'
        '.bar a:hover,.bar button:hover{background:#364052}'
        '#ld{position:fixed;inset:0;display:flex;align-items:center;'
        'justify-content:center;color:#2a2f38;text-align:center;padding:1em}'
        '#hint{position:fixed;bottom:.35em;left:0;right:0;text-align:center;'
        'color:#566270;font-size:.78em;pointer-events:none}'
        'canvas{display:block;touch-action:none}</style>'
        f'<div class="bar"><a href="./">📁</a>{_link_startowy()}'
        '<button id="bfit">⤢ Dopasuj</button>'
        '<button id="b11">1:1</button>'
        '<button id="bzi">＋</button>'
        '<button id="bzo">－</button>'
        '<span style="word-break:break-all;color:#aeb6c2">🧊 __NAME__</span>'
        '<a href="__URL__?dl=1" style="margin-left:auto">⬇</a></div>'
        '<div id="ld">⏳ Ładowanie modelu 3D…</div>'
        '<div id="hint">obrót: przeciągnij · zoom: kółko / pinch / ＋－ · przesuń: prawy / dwa palce</div>'
        '<script type="importmap">{"imports":{'
        '"three":"https://unpkg.com/three@0.160.0/build/three.module.js",'
        '"three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>'
        '<script type="module">'
        'import * as THREE from "three";'
        'import {OrbitControls} from "three/addons/controls/OrbitControls.js";'
        'import {__LOADER__} from "three/addons/loaders/__LFILE__";'
        'const sc=new THREE.Scene();'
        'function mkbg(){const c=document.createElement("canvas");c.width=2;c.height=256;'
        'const g=c.getContext("2d"),lg=g.createLinearGradient(0,0,0,256);'
        'lg.addColorStop(0,"#eef2f7");lg.addColorStop(1,"#aab6c5");'
        'g.fillStyle=lg;g.fillRect(0,0,2,256);'
        'const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;return t;}'
        'sc.background=mkbg();'
        'const cam=new THREE.PerspectiveCamera(50,innerWidth/innerHeight,0.01,1e7);'
        'const rd=new THREE.WebGLRenderer({antialias:true});'
        'rd.setSize(innerWidth,innerHeight);rd.setPixelRatio(devicePixelRatio);'
        'document.body.appendChild(rd.domElement);'
        'sc.add(new THREE.HemisphereLight(0xffffff,0xc6ccd6,0.5));'
        'sc.add(new THREE.AmbientLight(0xffffff,0.45));'
        'const d1=new THREE.DirectionalLight(0xffffff,0.5);d1.position.set(1,1.4,0.8);sc.add(d1);'
        'const d2=new THREE.DirectionalLight(0xffffff,0.28);d2.position.set(-0.8,-0.5,-1);sc.add(d2);'
        'const ctr=new OrbitControls(cam,rd.domElement);'
        'ctr.enableZoom=true;ctr.zoomSpeed=0.2;ctr.rotateSpeed=1.0;'
        'rd.domElement.addEventListener("wheel",function(e){e.preventDefault();'
        'e.stopPropagation();dolly(e.deltaY>0?1.1:0.909);},{passive:false,capture:true});'
        'const mat=new THREE.MeshStandardMaterial({color:0x9aa4b2,metalness:0.0,'
        'roughness:0.95,side:THREE.DoubleSide,polygonOffset:true,'
        'polygonOffsetFactor:1,polygonOffsetUnits:1});'
        'let grid=null,SZ=1;'
        'function fit(o){const b=new THREE.Box3().setFromObject(o);'
        'const s=b.getSize(new THREE.Vector3()),c=b.getCenter(new THREE.Vector3());'
        'o.position.sub(c);SZ=Math.max(s.x,s.y,s.z)||1;'
        'cam.near=Math.max(SZ/2000,1e-4);cam.far=SZ*500;'
        'ctr.minDistance=SZ*0.15;ctr.maxDistance=SZ*40;'
        'cam.position.set(SZ*1.6,SZ*1.1,SZ*1.8);cam.updateProjectionMatrix();'
        'ctr.target.set(0,0,0);ctr.update();'
        'if(grid)sc.remove(grid);'
        'grid=new THREE.GridHelper(SZ*4,20,0x8a96a6,0xc2ccd6);'
        'grid.position.y=-s.y/2;sc.add(grid);}'
        'function dolly(k){const v=new THREE.Vector3().subVectors(cam.position,ctr.target);'
        'let len=v.length()*k;'
        'len=Math.max(ctr.minDistance,Math.min(ctr.maxDistance,len));'
        'v.setLength(len);cam.position.copy(ctr.target).add(v);ctr.update();}'
        'function one1(){const f=THREE.MathUtils.degToRad(cam.fov);'
        'const pxmm=96/25.4,d=innerHeight/(2*Math.tan(f/2)*pxmm);'
        'const dir=new THREE.Vector3().subVectors(cam.position,ctr.target).normalize();'
        'cam.position.copy(ctr.target).addScaledVector(dir,d);'
        'cam.updateProjectionMatrix();ctr.update();}'
        'document.getElementById("bfit").onclick=function(){if(window.__obj)fit(window.__obj);};'
        'document.getElementById("b11").onclick=one1;'
        'document.getElementById("bzi").onclick=function(){dolly(0.98);};'
        'document.getElementById("bzo").onclick=function(){dolly(1.02);};'
        'function addEdges(m){try{if(!m.geometry)return;'
        'const eg=new THREE.EdgesGeometry(m.geometry,30);'
        'const ls=new THREE.LineSegments(eg,new THREE.LineBasicMaterial('
        '{color:0x10151d,transparent:true,opacity:0.7}));m.add(ls);}catch(e){}}'
        'const L=new __LOADER__();'
        'L.load("__URL__",function(res){let o;'
        'if(res&&res.isBufferGeometry){res.computeVertexNormals();o=new THREE.Mesh(res,mat);addEdges(o);}'
        'else if(res&&res.scene){o=res.scene;o.traverse(function(n){if(n.isMesh)addEdges(n);});}'
        'else{o=res;o.traverse(function(n){if(n.isMesh){if(!n.material)n.material=mat;addEdges(n);}});}'
        'window.__obj=o;sc.add(o);fit(o);document.getElementById("ld").style.display="none";},'
        'undefined,function(e){document.getElementById("ld").textContent='
        '"\\u26a0 B\\u0142\\u0105d \\u0142adowania: "+(e&&e.message?e.message:e);});'
        'addEventListener("resize",function(){cam.aspect=innerWidth/innerHeight;'
        'cam.updateProjectionMatrix();rd.setSize(innerWidth,innerHeight);});'
        '(function a(){requestAnimationFrame(a);ctr.update();rd.render(sc,cam);})();'
        '</script>')
    return (tpl.replace('__NAME__', _html.escape(target.name))
               .replace('__URL__', q)
               .replace('__LOADER__', loader)
               .replace('__LFILE__', lfile))


def render_audio_page(target: Path) -> str:
    """Odtwarzacz audio (?view=1): natywny <audio>, poprzedni/następny
    w katalogu, regulacja tempa (lektor!), autoodtwarzanie."""
    sibs = sorted((x.name for x in target.parent.iterdir()
                   if x.is_file() and x.suffix.lower() in AUDIO_EXT
                   and not x.name.startswith('.')), key=_natkey)
    idx = sibs.index(target.name) if target.name in sibs else 0
    prv = quote(sibs[idx - 1]) + '?view=1' if idx > 0 else None
    nxt = quote(sibs[idx + 1]) + '?view=1' if idx < len(sibs) - 1 else None
    a_prev = (f'<a href="{prv}" id="prev">← poprzedni</a>' if prv
              else '<a id="prev" class="dis">← poprzedni</a>')
    a_next = (f'<a href="{nxt}" id="next">następny →</a>' if nxt
              else '<a id="next" class="dis">następny →</a>')
    q = quote(target.name)
    cur_js = _json_do_script(target.name)
    spd_btns = ''.join(
        f'<button class="sp" data-s="{s}">{s}×</button>'
        for s in ('0.75', '1', '1.25', '1.5', '2'))
    # ROZDZIAŁY: <stem>.chapters.json obok audio (z czytaj_tts) — klikalny seek
    chap_p = target.with_name(target.stem + '.chapters.json')
    chapters = []
    if chap_p.exists():
        try:
            chapters = __import__('json').loads(chap_p.read_text(encoding='utf-8'))
        except Exception:
            chapters = []
    chaps_html = ''
    if chapters:
        def _ms(t):
            t = int(float(t))
            return f'{t // 60}:{t % 60:02d}'
        rows = ''.join(
            f'<button class="chp" data-t="{c.get("t", 0)}">'
            f'<span class="ct">{_ms(c.get("t", 0))}</span>'
            f'{_html.escape(str(c.get("title", "")))}</button>'
            for c in chapters)
        chaps_html = (f'<details class="chaps" open>'
                      f'<summary>📖 Rozdziały ({len(chapters)})</summary>{rows}</details>')
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>♪ {_esc(target.name)}</title><style>{AUDIO_STYLE}</style>'
        f'<div class="bar"><a href="./">📁 folder</a>{_link_startowy()}{a_prev}{a_next}'
        f'<span id="cnt" style="color:#8a93a0">{idx + 1} / {len(sibs)}</span>'
        f'<a href="{q}?dl=1">⬇ pobierz</a></div>'
        f'<div class="wrap"><div class="tname">🎵 {_esc(target.name)}</div>'
        f'<audio id="au" controls autoplay src="{q}"></audio>'
        f'<div class="spd">tempo: {spd_btns}</div>{chaps_html}</div>'
        '<script>(function(){'
        'const au=document.getElementById("au");'
        # rozdziały: klik = seek; podświetlenie bieżącego wg czasu
        'const chp=[...document.querySelectorAll(".chp")];'
        'const CT=chp.map(b=>parseFloat(b.dataset.t));'
        'chp.forEach(b=>b.onclick=()=>{au.currentTime=parseFloat(b.dataset.t)+0.05;'
        'au.play();});'
        'au.addEventListener("timeupdate",function(){let k=-1;'
        'for(let j=0;j<CT.length;j++){if(CT[j]<=au.currentTime+0.3)k=j;else break;}'
        'chp.forEach((b,j)=>b.classList.toggle("cur",j===k));});'
        'const sp=[...document.querySelectorAll(".sp")];'
        'function setr(r){au.playbackRate=parseFloat(r);'
        'sp.forEach(b=>b.classList.toggle("on",b.dataset.s===r));'
        'try{localStorage.setItem("audioRate",r)}catch(e){}}'
        'sp.forEach(b=>b.onclick=()=>setr(b.dataset.s));'
        'setr(localStorage.getItem("audioRate")||"1");'
        # koniec utworu → automatycznie następny (playlista po katalogu)
        'au.onended=()=>{const n=document.getElementById("next");if(n)location=n.href};'
        'document.addEventListener("keydown",e=>{'
        'if(e.key==="ArrowLeft"){const a=document.getElementById("prev");if(a)location=a.href}'
        'if(e.key==="ArrowRight"){const a=document.getElementById("next");if(a)location=a.href}'
        'if(e.key===" "){e.preventDefault();au.paused?au.play():au.pause()}});'
        # LIVE odświeżanie prev/next: poll listy rodzeństwa co 4 s -> gdy dojdzie
        # nowy mp3 (np. lektor dogenerował), „następny" się odszarza (i licznik)
        f'const CUR={cur_js};'
        'function setNav(id,h){const a=document.getElementById(id);'
        'if(h){a.href=h;a.classList.remove("dis");}'
        'else{a.removeAttribute("href");a.classList.add("dis");}}'
        'async function refreshNav(){try{'
        'const s=await(await fetch("?siblings=1",{cache:"no-store"})).json();'
        'const i=s.indexOf(CUR);if(i<0)return;'
        'setNav("prev",i>0?encodeURIComponent(s[i-1])+"?view=1":null);'
        'setNav("next",i<s.length-1?encodeURIComponent(s[i+1])+"?view=1":null);'
        'const c=document.getElementById("cnt");'
        'if(c)c.textContent=(i+1)+" / "+s.length;}catch(e){}}'
        'setInterval(refreshNav,4000);'
        '})();</script>')

# ── Podgląd danych tabelarycznych: CSV/TSV (parser w JS) + XLSX (openpyxl) ──────
TABLE_STYLE = (
    '*{box-sizing:border-box}'
    'body{margin:0;height:100vh;display:flex;flex-direction:column;'
    'background:#1e2127;color:#dde;font-family:system-ui,sans-serif}'
    '.bar{display:flex;gap:.5em;align-items:center;flex-wrap:wrap;'
    'padding:.4em .9em;background:#26292f;font-size:.9em}'
    '.bar a{color:#7ab7ff;text-decoration:none;border:1px solid #3a3f47;'
    'border-radius:5px;padding:.2em .6em}'
    '.bar a.dis{color:#555;border-color:#2c2f35;pointer-events:none}'
    '.bar .name{font-weight:600;color:#cfd6df;word-break:break-all}'
    '.bar .cnt{color:#8a93a0}.bar .sp{margin-left:auto}'
    '.ctl{display:flex;gap:.7em;align-items:center;flex-wrap:wrap;'
    'padding:.4em .9em;background:#21242a;font-size:.85em;color:#aeb6c0;'
    'border-top:1px solid #2c2f35}'
    '.ctl select,.ctl button{background:#2b2f36;color:#dde;'
    'border:1px solid #3a3f47;border-radius:5px;padding:.25em .55em;font:inherit;'
    'cursor:pointer}'
    '.ctl button.on{background:#2f5d8a;border-color:#3d77ad;color:#fff}'
    '.ctl label{display:flex;gap:.35em;align-items:center}'
    '.wrap{flex:1;overflow:auto;position:relative}'
    'table.grid{border-collapse:collapse;font-size:.85em;'
    'font-variant-numeric:tabular-nums;white-space:pre}'
    'table.grid th,table.grid td{border:1px solid #353a42;padding:.25em .55em;'
    'text-align:left;vertical-align:top;max-width:46ch;overflow:hidden;'
    'text-overflow:ellipsis}'
    'table.grid thead th{position:sticky;top:0;background:#2c3742;color:#e8eef6;'
    'z-index:2}'
    'table.grid tbody tr:nth-child(even){background:#23262c}'
    'table.grid tbody tr:hover{background:#2a3340}'
    'table.grid td.rn,table.grid th.rn{position:sticky;left:0;background:#262b31;'
    'color:#7b8694;text-align:right;z-index:1;user-select:none}'
    'table.grid thead th.rn{z-index:3}'
    'pre.code{margin:0;padding:.8em 1em;white-space:pre;'
    'font:13px/1.45 ui-monospace,Menlo,Consolas,monospace;color:#cdd3db}'
    '.msg{padding:1em;color:#cfa86b}'
    '.tabs{display:flex;gap:.3em;padding:.35em .9em;background:#21242a;'
    'overflow:auto;border-top:1px solid #2c2f35}'
    '.tabs button{background:#2b2f36;color:#bcc4ce;border:1px solid #3a3f47;'
    'border-radius:6px 6px 0 0;padding:.3em .75em;white-space:nowrap;cursor:pointer}'
    '.tabs button.on{background:#2f5d8a;border-color:#3d77ad;color:#fff}')

# Parser CSV w przeglądarce: live re-parse przy zmianie separatora, przełącznik
# Tabela/Kod, stan UI (separator/nagłówek/widok) trwały w localStorage (F5-safe).
CSV_TABLE_JS = r'''(function(){
const SEP={comma:',',semicolon:';',tab:'\t',pipe:'|',space:' '};
const MAXROWS=4000,$=id=>document.getElementById(id);
''' + JS_ESC + r'''
function detect(t){const ln=(t.split(/\r?\n/).find(l=>l.trim())||'');
 let best=',',bn=-1;for(const d of[',',';','\t','|']){const c=ln.split(d).length-1;if(c>bn){bn=c;best=d;}}
 return bn>0?best:',';}
function parse(t,d){const rows=[];let row=[],cur='',q=false,i=0;const n=t.length;
 while(i<n){const c=t[i];
  if(q){if(c==='"'){if(t[i+1]==='"'){cur+='"';i+=2;continue;}q=false;i++;continue;}cur+=c;i++;continue;}
  if(c==='"'){q=true;i++;continue;}
  if(c===d){row.push(cur);cur='';i++;continue;}
  if(c==='\r'){i++;continue;}
  if(c==='\n'){row.push(cur);rows.push(row);row=[];cur='';i++;continue;}
  cur+=c;i++;}
 if(cur!==''||row.length){row.push(cur);rows.push(row);}
 return rows;}
function delim(){const v=$('sep').value;return v==='auto'?detect(RAW):(SEP[v]||',');}
function render(){
 const d=delim(),header=$('hdr').checked;let rows=parse(RAW,d);
 const total=rows.length;let cut=false;
 if(rows.length>MAXROWS){rows=rows.slice(0,MAXROWS);cut=true;}
 const nc=rows.reduce((m,r)=>Math.max(m,r.length),0);
 const head=header&&rows.length?rows[0]:null;
 let h='<table class="grid"><thead><tr><th class="rn">#</th>';
 for(let c=0;c<nc;c++)h+='<th>'+(head?esc(head[c]||''):'C'+(c+1))+'</th>';
 h+='</tr></thead><tbody>';
 for(let r=header?1:0;r<rows.length;r++){h+='<tr><td class="rn">'+(header?r:r+1)+'</td>';
  for(let c=0;c<nc;c++){const v=rows[r][c]||'';h+='<td title="'+esc(v)+'">'+esc(v)+'</td>';}
  h+='</tr>';}
 h+='</tbody></table>';$('tbl').innerHTML=h;$('code').textContent=RAW;
 $('info').textContent=total+' w. × '+nc+' kol.'+(cut?' (pokazano '+MAXROWS+')':'');}
function setView(tab){$('tbl').hidden=!tab;$('code').hidden=tab;
 $('vtab').classList.toggle('on',tab);$('vcode').classList.toggle('on',!tab);
 try{localStorage.setItem('csvView',tab?'t':'c')}catch(e){}}
try{const s=localStorage.getItem('csvSep');if(s)$('sep').value=s;
 const hd=localStorage.getItem('csvHdr');if(hd!=null)$('hdr').checked=hd==='1';}catch(e){}
$('sep').onchange=()=>{try{localStorage.setItem('csvSep',$('sep').value)}catch(e){}render();};
$('hdr').onchange=()=>{try{localStorage.setItem('csvHdr',$('hdr').checked?'1':'0')}catch(e){}render();};
$('vtab').onclick=()=>setView(true);$('vcode').onclick=()=>setView(false);
render();try{setView((localStorage.getItem('csvView')||'t')!=='c');}catch(e){setView(true);}
document.addEventListener('keydown',e=>{
 if(/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName))return;
 if(e.key==='ArrowLeft'){const a=$('prev');if(a&&a.href)location=a.href;}
 if(e.key==='ArrowRight'){const a=$('next');if(a&&a.href)location=a.href;}});
})();'''

# Render arkuszy XLSX (dane sparsowane serwerowo do SHEETS=[{name,rows,truncated}]).
XLSX_TABLE_JS = r'''(function(){
if(typeof SHEETS==='undefined'||!SHEETS||!SHEETS.length){return;}
const $=id=>document.getElementById(id);
''' + JS_ESC + r'''
let active=0;try{const v=parseInt(localStorage.getItem('xlsxSheet'));
 if(v>=0&&v<SHEETS.length)active=v;}catch(e){}
function tabs(){const t=$('tabs');if(SHEETS.length<2){t.style.display='none';return;}
 t.innerHTML=SHEETS.map((s,i)=>'<button data-i="'+i+'"'+(i===active?' class="on"':'')+'>'+esc(s.name)+'</button>').join('');
 [...t.querySelectorAll('button')].forEach(b=>b.onclick=()=>{active=+b.dataset.i;
  try{localStorage.setItem('xlsxSheet',active)}catch(e){}
  [...t.querySelectorAll('button')].forEach(x=>x.classList.toggle('on',+x.dataset.i===active));draw();});}
function draw(){const s=SHEETS[active],rows=s.rows||[];
 if(!rows.length){$('tbl').innerHTML='<div class="msg">— arkusz pusty —</div>';
  $('info').textContent=s.name+': 0 wierszy';return;}
 const nc=rows.reduce((m,r)=>Math.max(m,r.length),0);
 let h='<table class="grid"><thead><tr><th class="rn">#</th>';
 for(let c=0;c<nc;c++)h+='<th>'+esc(rows[0][c]!=null?rows[0][c]:'')+'</th>';
 h+='</tr></thead><tbody>';
 for(let r=1;r<rows.length;r++){h+='<tr><td class="rn">'+r+'</td>';
  for(let c=0;c<nc;c++){const v=rows[r][c]!=null?rows[r][c]:'';h+='<td title="'+esc(v)+'">'+esc(v)+'</td>';}
  h+='</tr>';}
 h+='</tbody></table>';$('tbl').innerHTML=h;
 $('info').textContent=s.name+': '+rows.length+' w. × '+nc+' kol.'+(s.truncated?' (przycięto)':'');}
tabs();draw();
document.addEventListener('keydown',e=>{
 if(/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName))return;
 if(e.key==='ArrowLeft'){const a=$('prev');if(a&&a.href)location=a.href;}
 if(e.key==='ArrowRight'){const a=$('next');if(a&&a.href)location=a.href;}});
})();'''


def _read_text_best(target: Path) -> str:
    """Dekoduj CSV elastycznie: utf-8(+BOM) → cp1250 (polski Excel ;) → latin-1."""
    raw = target.read_bytes()
    for enc in ('utf-8-sig', 'utf-8', 'cp1250', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def _sniff_delim(text: str) -> str:
    """Domyślny separator z pierwszego niepustego wiersza (JS i tak ma 'auto')."""
    for line in text.splitlines():
        if line.strip():
            best, bn = ',', -1
            for d in (',', ';', '\t', '|'):
                c = line.count(d)
                if c > bn:
                    bn, best = c, d
            return best if bn > 0 else ','
    return ','


def _cell_str(v) -> str:
    import datetime as _dt
    if v is None:
        return ''
    if isinstance(v, _dt.datetime):
        s = v.strftime('%Y-%m-%d %H:%M:%S')
        return s[:-9] if s.endswith(' 00:00:00') else s
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def _xlsx_to_sheets(target: Path, max_rows: int = 4000, max_cols: int = 80):
    """openpyxl read_only/data_only → [{name, rows[[str]], truncated}].
    data_only=True ⇒ wartości policzone (nie formuły). Przycina puste brzegi."""
    import openpyxl
    wb = openpyxl.load_workbook(target, read_only=True, data_only=True)
    sheets = []
    try:
        for ws in wb.worksheets:
            rows, truncated = [], False
            for ri, row in enumerate(ws.iter_rows(values_only=True)):
                if ri >= max_rows:
                    truncated = True
                    break
                rows.append([_cell_str(v) for v in row[:max_cols]])
            while rows and not any(c.strip() for c in rows[-1]):
                rows.pop()                       # puste wiersze na końcu
            if rows:                             # puste kolumny na końcu
                last = 0
                for r in rows:
                    for c in range(len(r) - 1, -1, -1):
                        if r[c].strip():
                            last = max(last, c)
                            break
                rows = [r[:last + 1] for r in rows]
            sheets.append({'name': ws.title, 'rows': rows, 'truncated': truncated})
    finally:
        wb.close()
    return sheets


def render_csv_page(target: Path) -> str:
    """Podgląd CSV/TSV (?view=1): tabela z wyborem separatora (auto/,/;/Tab/|/spacja)
    + przełącznik Tabela/Kod. Parsowanie w przeglądarce (live), stan UI w localStorage."""
    sibs = sorted((x.name for x in target.parent.iterdir()
                   if x.is_file() and x.suffix.lower() in CSV_EXT
                   and not x.name.startswith('.')), key=_natkey)
    idx = sibs.index(target.name) if target.name in sibs else 0
    prv = quote(sibs[idx - 1]) + '?view=1' if idx > 0 else None
    nxt = quote(sibs[idx + 1]) + '?view=1' if idx < len(sibs) - 1 else None
    a_prev = (f'<a id="prev" href="{prv}">← poprzedni</a>' if prv
              else '<a id="prev" class="dis">← poprzedni</a>')
    a_next = (f'<a id="next" href="{nxt}">następny →</a>' if nxt
              else '<a id="next" class="dis">następny →</a>')
    q = quote(target.name)
    text = _read_text_best(target)
    raw_js = _json_do_script(text)
    # ustaw domyślny wybór separatora w <select> wg sniffu serwerowego
    snif = _sniff_delim(text)
    sel = {',': 'comma', ';': 'semicolon', '\t': 'tab', '|': 'pipe'}.get(snif, 'auto')
    opts = [('auto', 'auto'), ('comma', ',  przecinek'), ('semicolon', ';  średnik'),
            ('tab', '⇥ tab'), ('pipe', '|  pionowa'), ('space', '␠ spacja')]
    opt_html = ''.join(
        f'<option value="{v}"{" selected" if v == sel else ""}>{lab}</option>'
        for v, lab in opts)
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>▦ {_esc(target.name)}</title><style>{TABLE_STYLE}</style>'
        f'<div class="bar"><a href="./">📁 folder</a>{_link_startowy()}{a_prev}{a_next}'
        f'<span class="name">{_html.escape(target.name)}</span>'
        f'<span class="cnt">{idx + 1} / {len(sibs)}</span>'
        f'<a class="sp" href="{q}?dl=1">⬇ pobierz</a></div>'
        '<div class="ctl">'
        f'<label>Separator:<select id="sep">{opt_html}</select></label>'
        '<label><input type="checkbox" id="hdr" checked> nagłówek (1. wiersz)</label>'
        '<span style="margin-left:auto"></span>'
        '<button id="vtab" class="on">▦ Tabela</button>'
        '<button id="vcode">⟨⟩ Kod</button>'
        '<span id="info" style="color:#7b8694"></span></div>'
        '<div class="wrap"><div id="tbl"></div>'
        '<pre class="code" id="code" hidden></pre></div>'
        f'<script>const RAW={raw_js};</script>'
        f'<script>{CSV_TABLE_JS}</script>')


def render_xlsx_page(target: Path) -> str:
    """Podgląd XLSX/XLSM (?view=1): arkusze jako tabele (zakładki), openpyxl."""
    sibs = sorted((x.name for x in target.parent.iterdir()
                   if x.is_file() and x.suffix.lower() in XLSX_EXT
                   and not x.name.startswith('.')), key=_natkey)
    idx = sibs.index(target.name) if target.name in sibs else 0
    prv = quote(sibs[idx - 1]) + '?view=1' if idx > 0 else None
    nxt = quote(sibs[idx + 1]) + '?view=1' if idx < len(sibs) - 1 else None
    a_prev = (f'<a id="prev" href="{prv}">← poprzedni</a>' if prv
              else '<a id="prev" class="dis">← poprzedni</a>')
    a_next = (f'<a id="next" href="{nxt}">następny →</a>' if nxt
              else '<a id="next" class="dis">następny →</a>')
    q = quote(target.name)
    try:
        sheets = _xlsx_to_sheets(target)
        data_js = _json_do_script(sheets)
        body = ('<div class="tabs" id="tabs"></div>'
                '<div class="wrap"><div id="tbl"></div></div>')
    except Exception as e:
        data_js = '[]'
        body = (f'<div class="msg">⚠ Nie udało się odczytać arkusza: '
                f'{_html.escape(str(e))}<br><a href="{q}?dl=1">⬇ pobierz plik</a></div>')
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>▦ {_esc(target.name)}</title><style>{TABLE_STYLE}</style>'
        f'<div class="bar"><a href="./">📁 folder</a>{_link_startowy()}{a_prev}{a_next}'
        f'<span class="name">{_html.escape(target.name)}</span>'
        f'<span class="cnt">{idx + 1} / {len(sibs)}</span>'
        f'<span id="info" style="color:#7b8694"></span>'
        f'<a class="sp" href="{q}?dl=1">⬇ pobierz</a></div>'
        + body
        + f'<script>const SHEETS={data_js};</script>'
        + f'<script>{XLSX_TABLE_JS}</script>')


_LO_LOCK = asyncio.Lock()   # jedna konwersja naraz (A53)


async def docx_to_pdf(target: Path) -> Path | None:
    """Podglad .docx: konwersja LibreOffice -> PDF, cache per (sciezka, mtime).
    Pierwsze otwarcie ~8-12 s, kolejne natychmiast."""
    DOCX_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(str(target).encode()).hexdigest()[:16]
    cached = DOCX_CACHE / f'{key}_{int(target.stat().st_mtime)}.pdf'
    if cached.exists():
        return cached
    # sprzatnij stare wersje tego pliku
    for old_f in DOCX_CACHE.glob(f'{key}_*.pdf'):
        try:
            old_f.unlink()
        except Exception:
            pass
    cmd = polecenie_soffice('--headless', profil_lo('lo-profil-podglad'),
                            '--convert-to', 'pdf', '--outdir', str(DOCX_CACHE), str(target))
    if cmd is None:                      # soffice_bez_sieci = tak, piaskownicy brak
        return None
    async with _LO_LOCK:
        if cached.exists():
            return cached
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(proc.wait(), timeout=90)
        except asyncio.TimeoutError:
            proc.kill()
            return None
    produced = DOCX_CACHE / (target.stem + '.pdf')
    if produced.exists():
        produced.rename(cached)
        return cached
    return None


_DOCX_EXPORT_LOCK = asyncio.Lock()   # python-docx + obrazy — jeden eksport naraz (A53)


def _read_template_exporter(proj: Path):
    """Czyta `<projekt>/szablon/eksporter.conf` → nazwa skryptu eksportu (lub None).
    Format: linia `eksporter = <plik.py>` (komentarze `#`). To „konfiguracja w
    szablonie": który skrypt buduje DOCX dla projektu."""
    conf = proj / 'szablon' / 'eksporter.conf'
    try:
        for line in conf.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = re.match(r'(?:eksporter|exporter|export)\s*[:=]\s*(.+)$', line, re.I)
            if m:
                return m.group(1).strip()
    except OSError:
        pass
    return None


_NAZWA_EKSPORTERA = re.compile(r'[A-Za-z0-9_-]+(?:\.py)?')


def _eksportery() -> list:
    """Biała lista: skrypty export_*.py leżące wprost w katalogu eksportu
    (zwykłe pliki, nie dowiązania) — katalog instancji, nie treść projektu."""
    try:
        return sorted(p.name for p in EXPORT_DIR.glob('export_*.py')
                      if p.is_file() and not p.is_symlink())
    except OSError:
        return []


def _skrypt_eksportu(nazwa: str):
    """Nazwa z dyrektywy/eksporter.conf → skrypt z białej listy albo None.
    „X" → export_X.py, „export_X" / „export_X.py" → wprost; bez /, \\ i ..."""
    if not _NAZWA_EKSPORTERA.fullmatch(nazwa or ''):
        return None
    stem = nazwa[:-3] if nazwa.endswith('.py') else nazwa
    dozwolone = _eksportery()
    for plik in (f'{stem}.py', f'export_{stem}.py'):
        if plik in dozwolone:
            return EXPORT_DIR / plik
    return None


async def _export_md_docx(target: Path):
    """Eksport .md → .docx silnikiem EXPORT/export_to_docx.py (obrazy + równania
    Word) — ten sam skrypt i argumenty co bot. Wynik ląduje w exports/ i jest
    serwowany do pobrania."""
    script = EXPORT_DIR / 'export_to_docx.py'
    if not script.exists():
        return web.Response(status=500, text='Brak EXPORT/export_to_docx.py')
    proj = target.parent.parent          # processed/<md> → katalog projektu
    cwd = EXPORT_DIR
    # Wybór SKRYPTU eksportu — NAZWA, nigdy ścieżka (B5: skrypt z katalogu
    # projektu = wykonanie kodu z treści vaulta/sprawozdań):
    #   1) override per-dokument: <!-- eksporter: X --> w nagłówku .md,
    #   2) config szablonu projektu: <projekt>/szablon/eksporter.conf,
    #   3) kanoniczny EXPORT/export_to_docx.py.
    # Nazwa wskazuje wyłącznie skrypt export_*.py leżący w katalogu eksportu
    # (_skrypt_eksportu); inna nazwa albo ścieżka = 400, nic nie jest uruchamiane.
    _name = None
    try:
        _m = re.search(r'<!--\s*(?:eksporter|exporter|export)\s*:\s*(\S+?)\s*-->',
                       target.read_text(encoding='utf-8', errors='replace'), re.I)
        if _m:
            _name = _m.group(1).strip()
    except OSError:
        pass
    if not _name:
        _name = _read_template_exporter(proj)
    if _name:
        script = _skrypt_eksportu(_name)
        if script is None:
            _evlog('eksport', f'odrzucono eksporter {_name!r} dla '
                   f'{target.relative_to(ROOT)} (dozwolone: export_*.py w katalogu '
                   'eksportu)', level='warn')
            return web.Response(
                status=400, text=f'Eksport DOCX: eksporter „{_name}" niedozwolony. '
                'Dyrektywa wybiera NAZWĘ skryptu export_<nazwa>.py z katalogu eksportu '
                f'({", ".join(_eksportery()) or "brak"}); skrypty z katalogu projektu '
                'nie są uruchamiane.')
    base_dir = target.parent             # do ścieżek obrazów; input/raw mają pierwszeństwo
    for cand in (proj / 'input', proj / 'raw'):
        try:
            if cand.is_dir() and any(cand.iterdir()):
                base_dir = cand
                break
        except OSError:
            pass
    out_dir = proj / 'exports'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_docx = out_dir / (target.stem + '.docx')
    async with _DOCX_EXPORT_LOCK:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script), str(target), '-o', str(out_docx),
            '--base-dir', str(base_dir),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd))
        try:
            so, se = await asyncio.wait_for(proc.communicate(), timeout=240)
        except asyncio.TimeoutError:
            proc.kill()
            return web.Response(status=504, text='Eksport DOCX: przekroczono 240 s')
    if proc.returncode != 0 or not out_docx.exists():
        err = (se or so).decode('utf-8', 'replace').strip()[-400:]
        return web.Response(status=500, text='Eksport DOCX nie powiódł się:\n' + err)
    return web.FileResponse(out_docx, headers={
        'Content-Type': 'application/vnd.openxmlformats-officedocument.'
                        'wordprocessingml.document',
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(out_docx.name),
        'Cache-Control': 'no-cache'})


MD_STYLE = (
    'body{margin:0;background:#f7f7f9;color:#222;font-family:system-ui,sans-serif}'
    '.stickyhead{position:sticky;top:0;z-index:10;background:#26292f;'
    'box-shadow:0 2px 6px rgba(0,0,0,.25)}'
    '.bar{display:flex;gap:.6em;align-items:center;'
    'padding:.5em 1em;background:#26292f;color:#ddd;flex-wrap:wrap}'
    '.bar a,.bar button{color:#7ab7ff;background:none;border:1px solid #3a3f47;'
    'border-radius:5px;padding:.25em .7em;text-decoration:none;cursor:pointer;'
    'font:inherit;font-size:.95em}'
    '.bar a:hover,.bar button:hover{background:#32363d}'
    '.bar .name{color:#eee;font-weight:600;word-break:break-all}'
    '.bar button.on{background:#1a5fb4;border-color:#1a5fb4;color:#fff}'
    '.lekplay{display:flex;align-items:center;gap:.6em;padding:.5em 1em;'
    'background:#1d2027;border-bottom:1px solid #313640}'
    '.lekplay audio{flex:1;height:36px;min-width:0}'
    '.lekplay .lk{color:#6fce8f;font-size:.9em;white-space:nowrap}'
    '.lekplay a{color:#7ab7ff;text-decoration:none;border:1px solid #3a3f47;'
    'border-radius:5px;padding:.1em .55em;font-size:1.1em;line-height:1.4}'
    '.chaps{background:#1a1d24;border-bottom:1px solid #313640;'
    'padding:.2em .8em;max-height:34vh;overflow:auto}'
    '.chaps summary{cursor:pointer;color:#8a93a0;font-size:.88em;padding:.25em}'
    '.chp{display:block;width:100%;text-align:left;background:none;border:0;'
    'color:#cfd6df;padding:.3em .5em;border-radius:5px;cursor:pointer;'
    'font:inherit;font-size:.92em}'
    '.chp:hover{background:#262b34}.chp.cur{background:#1a5fb4;color:#fff}'
    '.chp .ct{color:#6fce8f;font-variant-numeric:tabular-nums;margin-right:.6em}'
    '.chp.cur .ct{color:#d6e9ff}'
    '.docxprog{display:none;align-items:center;gap:.7em;padding:.45em 1em;'
    'background:#1d2027;border-bottom:1px solid #313640}'
    '.docxprog .dpt{color:#cfd6df;font-size:.88em;white-space:nowrap}'
    '.docxprog .dptrack{flex:1;height:10px;border-radius:5px;'
    'background:#2a2f38;overflow:hidden}'
    '.docxprog .dpbar{height:100%;width:2%;background:#3aa657;transition:width .35s}'
    '.docxprog .dpbar.err{background:#c0392b}'
    '#rendered{--s:1;max-width:calc(900px * var(--s));margin:0 auto;'
    'padding:1.5em 2em;background:#fff;font-size:calc(1em * var(--s));'
    'min-height:92vh;box-shadow:0 0 12px rgba(0,0,0,.07)}'
    '#rendered img{max-width:100%;cursor:zoom-in}'
    '#rendered table{border-collapse:collapse;margin:.8em 0}'
    '#rendered th,#rendered td{border:1px solid #999;padding:.35em .6em}'
    '#rendered code{background:#eef1f6;padding:0 .25em;border-radius:3px}'
    '#rendered pre{background:#1b1d21;color:#e8e8e8;padding:1em;border-radius:6px;'
    'overflow-x:auto}'
    '#rendered pre code{background:none}'
    '#rendered blockquote{border-left:4px solid #bcd;margin-left:0;'
    'padding-left:1em;color:#555}'
    '#raw{display:none;max-width:1100px;margin:0 auto;padding:1em}'
    '#raw pre{background:#1b1d21;color:#d8e0ea;padding:1.2em;border-radius:8px;'
    'overflow-x:auto;font-size:.9em;line-height:1.45;white-space:pre-wrap;'
    "font-family:'Cascadia Mono',Consolas,monospace}"
)


def _fix_md_imgs(body: str, md_dir: Path) -> str:
    """Obrazki w .md bywaja zapisane gola nazwa, a fizycznie leza w raw/
    obok (md w processed/). Znajdz plik i przepisz src na sciezke wzgledna
    dzialajaca z URL-a strony podgladu."""
    def repl(m):
        from urllib.parse import unquote
        src = m.group(2).strip()
        if src.startswith(('http://', 'https://', 'data:', '/')):
            return m.group(0)
        # markdown: ![](<ścieżka ze spacjami>) — zdejmij nawiasy ostre; zdekoduj %20
        if src.startswith('<') and src.endswith('>'):
            src = src[1:-1].strip()
        src = unquote(src)
        name = Path(src).name
        # PRESERWUJ podfoldery (input/Dane mat/…, input/<mat>/Diagrams/…) — stąd
        # priorytet kandydatów ze ścieżką 'src', nie tylko gołą nazwą.
        cands = [md_dir / src, md_dir / name,
                 md_dir.parent / 'input' / src, md_dir.parent / 'input' / name,
                 md_dir.parent / 'raw' / src, md_dir.parent / 'raw' / name,
                 md_dir / 'raw' / name,
                 md_dir.parent / 'exports' / name,
                 md_dir.parent / 'processed' / name]
        for c in cands:
            try:
                cr = c.resolve()
            except Exception:
                continue
            if cr.exists() and (ROOT in cr.parents):
                rel = os.path.relpath(cr, md_dir).replace(os.sep, '/')
                return m.group(1) + quote(rel) + m.group(3)
        return m.group(0)
    return re.sub(r'(<img[^>]*?src=")([^"]+)(")', repl, body)


def _md_render(src: str) -> str:
    """Markdown CHRONIĄC wzory $...$/$$...$$ przed manglowaniem przez markdown
    (znaki _ * \\ {} w LaTeX → kursywa/escape; nl2br łamał bloki $$). Wzory
    wyciągamy do placeholderów, renderujemy markdown, przywracamy CZYSTY LaTeX
    → MathJax dostaje nietknięte wzory. ŹRÓDŁO .md SIĘ NIE ZMIENIA (eksport
    DOCX czyta to samo .md osobnym torem — bez wpływu)."""
    store = []

    def _stash(m):
        store.append(m.group(0))
        return f'\x00M{len(store) - 1}\x00'

    # display $$...$$ (też wieloliniowy) PRZED inline $...$
    s = re.sub(r'\$\$.+?\$\$', _stash, src, flags=re.S)
    s = re.sub(r'\$[^$\n]+?\$', _stash, s)
    html = _markdown.markdown(
        s, extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists'])
    # wzór wraca jako TEKST (escapowany): MathJax czyta treść tekstową, więc
    # „a < b" działa, a „$<script>…$" nie staje się znacznikiem
    for i, tex in enumerate(store):
        html = html.replace(f'\x00M{i}\x00', _html.escape(tex, quote=False))
    return _sanityzuj_html(html)


def _sanityzuj_html(html: str) -> str:
    """HTML z treści użytkownika (Markdown) → tylko znaczniki i atrybuty
    bezpieczne (lista dozwolonych nh3): bez <script>, on*=, javascript:, <iframe>.
    Brak nh3 = cały wynik escapowany do tekstu — NIGDY render bez sanityzacji."""
    if _nh3 is None:
        return ('<p><i>(brak biblioteki nh3 — HTML pokazany jako tekst)</i></p>'
                f'<pre>{_html.escape(html)}</pre>')
    attrs = {t: set(a) for t, a in _nh3.ALLOWED_ATTRIBUTES.items()}
    for t in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
        attrs.setdefault(t, set()).add('id')
    attrs.setdefault('code', set()).add('class')            # language-xxx
    for t in ('th', 'td'):
        attrs.setdefault(t, set()).update({'style', 'align'})
    attrs.setdefault('img', set()).update({'alt', 'title', 'src', 'width', 'height'})
    return _nh3.clean(html, attributes=attrs,
                      filter_style_properties={'text-align'})


VIDEO_STYLE = (
    'body{margin:0;background:#0d0f12;color:#ddd;font-family:system-ui,sans-serif;'
    'display:flex;flex-direction:column;min-height:100vh}'
    '.bar{display:flex;align-items:center;gap:1em;padding:.55em 1em;background:#1b1d21;'
    'font-size:.95em;flex-wrap:wrap;position:sticky;top:0;z-index:5}'
    '.bar a{color:#7ab7ff;text-decoration:none;padding:.25em .7em;border:1px solid #2c313a;'
    'border-radius:5px;white-space:nowrap}'
    '.bar a:hover{background:#262b34}.bar a.dis{opacity:.3;pointer-events:none}'
    '.bar .name{color:#eee;font-weight:600;word-break:break-all}'
    '.bar .cnt{color:#888}'
    '.stage{flex:1;display:flex;align-items:center;justify-content:center;'
    'padding:.6em;min-height:0}'
    '.stage video{max-width:100%;max-height:calc(100vh - 4em);background:#000;'
    'box-shadow:0 2px 18px rgba(0,0,0,.6);outline:none}'
)


def render_video_page(target: Path) -> str:
    """Odtwarzacz wideo (?view=1): natywny <video controls> z przewijaniem
    (FileResponse obsługuje Range), poprzedni/następny w katalogu, strzałki = seek
    (natywne), N/P = poprzedni/następny plik."""
    sibs = sorted((x.name for x in target.parent.iterdir()
                   if x.is_file() and x.suffix.lower() in VIDEO_EXT
                   and not x.name.startswith('.')), key=_natkey)
    idx = sibs.index(target.name) if target.name in sibs else 0
    prv = quote(sibs[idx - 1]) + '?view=1' if idx > 0 else None
    nxt = quote(sibs[idx + 1]) + '?view=1' if idx < len(sibs) - 1 else None
    a_prev = (f'<a href="{prv}" id="prev">← poprzedni</a>' if prv
              else '<a id="prev" class="dis">← poprzedni</a>')
    a_next = (f'<a href="{nxt}" id="next">następny →</a>' if nxt
              else '<a id="next" class="dis">następny →</a>')
    q = quote(target.name)
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>🎬 {_esc(target.name)}</title><style>{VIDEO_STYLE}</style>'
        f'<div class="bar"><a href="./">📁 folder</a>{_link_startowy()}{a_prev}{a_next}'
        f'<span class="name">{_esc(target.name)}</span>'
        f'<span class="cnt">{idx + 1} / {len(sibs)}</span>'
        f'<span class="cnt" id="fps" title="Klatki na sekundę (z odtwarzania)">FPS: –</span>'
        f'<a href="{q}?dl=1">⬇ pobierz</a></div>'
        f'<div class="stage"><video id="vid" controls preload="metadata" '
        f'playsinline src="{q}"></video></div>'
        '<script>(function(){'
        'document.addEventListener("keydown",e=>{'
        'const a=document.activeElement;if(a&&/INPUT|TEXTAREA/.test(a.tagName))return;'
        # N/P = następny/poprzedni plik (strzałki zostawiamy natywnemu seekowi wideo)
        'if(e.key==="n"||e.key==="N"){const x=document.getElementById("next");'
        'if(x&&x.href)location=x.href;}'
        'if(e.key==="p"||e.key==="P"){const x=document.getElementById("prev");'
        'if(x&&x.href)location=x.href;}'
        '});'
        # FPS: różnice mediaTime kolejnych klatek (requestVideoFrameCallback) →
        # mediana → 1/Δ = FPS źródłowy; snap do typowych wartości jeśli blisko.
        'const v=document.getElementById("vid"),fe=document.getElementById("fps");'
        'if(v&&"requestVideoFrameCallback" in HTMLVideoElement.prototype){'
        'let last=null,sm=[];'
        'const COM=[23.976,24,25,29.97,30,48,50,59.94,60,90,120,144,240];'
        'function onF(now,meta){'
        'if(last!=null){const d=meta.mediaTime-last;'
        'if(d>0.0008&&d<0.2){sm.push(d);if(sm.length>40)sm.shift();'
        'const s=[...sm].sort((a,b)=>a-b),med=s[s.length>>1],fps=1/med;'
        'let best=fps,bd=1e9;for(const c of COM){const dd=Math.abs(c-fps);'
        'if(dd<bd){bd=dd;best=c;}}'
        'fe.textContent="FPS: "+(bd<1.0?Math.round(best):fps.toFixed(1));}}'
        'last=meta.mediaTime;v.requestVideoFrameCallback(onF);}'
        'v.requestVideoFrameCallback(onF);'
        '}else{fe.textContent="";}'  # brak rVFC (stara przeglądarka) → ukryj
        '})();</script>')


def render_md_page(target: Path) -> str:
    """Podgląd .md: zakładki Render (markdown+MathJax) / Kod (surowe źródło).
    Względne ścieżki obrazków działają — strona żyje w katalogu pliku."""
    src = target.read_text(encoding='utf-8', errors='replace')
    if _markdown is not None:
        body = _fix_md_imgs(_md_render(src), target.parent)
    else:
        body = '<p><i>(brak biblioteki python-markdown — dostępny tylko Kod)</i></p>'
    raw = _html.escape(src)
    q = quote(target.name)
    # odtwarzacz lektora na górze podglądu, jeśli istnieje audio dla tego .md
    # (w tym folderze LUB w siostrzanym ../exports/)
    aud = _lektor_audio_for_doc(target)
    audio_html = ''
    if aud is not None:
        aurl = _url_abs(aud)
        chaps = _audio_chapters(aud, src)
        chaps_js = _json_do_script(chaps)
        chrows = ''
        if chaps:
            def _ms(t):
                t = int(float(t))
                return f'{t // 60}:{t % 60:02d}'
            chrows = ''.join(
                f'<button class="chp" data-i="{i}">'
                + (f'<span class="ct">{_ms(c["t"])}</span>' if 't' in c
                   else '<span class="ct">…</span>')
                + f'{_html.escape(str(c.get("title", "")))}</button>'
                for i, c in enumerate(chaps))
        est = ' · szac.' if (chaps and 't' not in chaps[0]) else ''
        chaps_panel = (f'<details class="chaps"><summary>📖 Rozdziały '
                       f'({len(chaps)}){est}</summary>{chrows}</details>'
                       if chaps else '')
        audio_html = (
            '<div class="lekwrap"><div class="lekplay">'
            '<span class="lk">🔊 lektor</span>'
            f'<audio id="lek" controls preload="metadata" src="{aurl}"></audio>'
            f'<a href="{aurl}?view=1" title="Pełny odtwarzacz (tempo)">⛶</a>'
            + (f'<a href="{q}?read=1" title="Czytanie z podświetlaniem zdań">📖</a>'
               if _lektor_cues_for(target, aud) else '')
            + '</div>'
            + chaps_panel +
            '<script>(function(){'
            f'const CH={chaps_js};const au=document.getElementById("lek");'
            'const bs=[...document.querySelectorAll(".lekwrap .chp")];'
            'function tof(c){return c.t!=null?c.t:(c.frac||0)*(au.duration||0);}'
            'bs.forEach(b=>b.onclick=()=>{const c=CH[+b.dataset.i];'
            'au.currentTime=tof(c)+0.05;au.play();});'
            'au.addEventListener("timeupdate",function(){let k=-1;'
            'for(let j=0;j<CH.length;j++){if(tof(CH[j])<=au.currentTime+0.3)k=j;else break;}'
            'bs.forEach((b,j)=>b.classList.toggle("cur",j===k));});'
            'au.addEventListener("loadedmetadata",function(){'
            'bs.forEach((b,j)=>{const c=CH[j];if(c.t==null){'
            'const t=Math.round(tof(c));const e=b.querySelector(".ct");'
            'if(e)e.textContent=(t/60|0)+":"+("0"+(t%60)).slice(-2);}});});'
            '})();</script>')
    # nawigacja po plikach .md w tym katalogu (jak w viewerze zdjec)
    sibs = sorted((x.name for x in target.parent.iterdir()
                   if x.is_file() and x.suffix.lower() == '.md'
                   and not x.name.startswith('.')), key=_natkey)
    idx = sibs.index(target.name) if target.name in sibs else 0
    prv = quote(sibs[idx - 1]) + '?view=1' if idx > 0 else None
    nxt = quote(sibs[idx + 1]) + '?view=1' if idx < len(sibs) - 1 else None
    a_prev = (f'<a href="{prv}" id="prev">← poprzedni</a>' if prv
              else '<a style="opacity:.3;pointer-events:none">← poprzedni</a>')
    a_next = (f'<a href="{nxt}" id="next">następny →</a>' if nxt
              else '<a style="opacity:.3;pointer-events:none">następny →</a>')
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{_esc(target.name)}</title><style>{MD_STYLE}</style>'
        '<script>window.MathJax={tex:{inlineMath:[["$","$"]],'
        'displayMath:[["$$","$$"]]}};</script>'
        '<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/'
        'tex-mml-chtml.js"></script>'
        '<div class="stickyhead">'
        f'<div class="bar"><a href="./">📁 folder</a>{_link_startowy()}{a_prev}{a_next}'
        f'<button id="bren" class="on">Render</button>'
        f'<button id="braw">Kod</button>'
        f'<button id="zout" title="Mniejszy tekst (Ctrl - / Ctrl+scroll)">A−</button>'
        f'<span id="zlbl" title="Kliknij = 100%" style="color:#bbb;min-width:3.4em;'
        f'text-align:center;cursor:pointer;align-self:center;font-size:.9em">100%</span>'
        f'<button id="zin" title="Większy tekst (Ctrl + / Ctrl+scroll)">A+</button>'
        f'<span class="name">{_esc(target.name)}</span>'
        f'<span style="color:#888">{idx + 1} / {len(sibs)}</span>'
        f'<a href="{q}?dl=1">⬇ pobierz</a>'
        + (f'<a href="{q}?docx=1" id="docxbtn" title="Eksport do Word (.docx) — '
           'obrazy + równania">⬇ DOCX</a>'
           if KONF.modul('eksport_docx') and not KONF.tylko_odczyt else '')
        + (f'<a href="#" id="prn" data-n="{q}">🖨 drukuj</a>'
           if KONF.modul('druk') else '')
        + _lektor_przycisk(target, aud)
        + '</div>'
        '<div id="docxprog" class="docxprog"><span class="dpt"></span>'
        '<div class="dptrack"><div class="dpbar"></div></div></div>'
        + audio_html +
        '</div>'  # /stickyhead
        + f'<div id="rendered">{body}</div>'
        f'<div id="raw"><pre>{raw}</pre></div>'
        # Skalowanie tekstu renderu: zmienna --s skaluje font ORAZ max-width →
        # kolumna rośnie z tekstem i zostaje wyśrodkowana (margin:auto), dopóki nie
        # wypełni szerokości ekranu. Stan w localStorage (przeżywa F5).
        '<script>(function(){'
        'const r=document.getElementById("rendered"),lbl=document.getElementById("zlbl");'
        'let s=1;try{const v=parseFloat(localStorage.getItem("mdScale"));'
        'if(v>=0.6&&v<=3)s=v;}catch(e){}'
        'function ap(){r.style.setProperty("--s",s);'
        'if(lbl)lbl.textContent=Math.round(s*100)+"%";'
        'try{localStorage.setItem("mdScale",s)}catch(e){}}'
        'function step(d){s=Math.min(3,Math.max(0.6,Math.round((s+d)*100)/100));ap();}'
        'document.getElementById("zin").onclick=()=>step(0.1);'
        'document.getElementById("zout").onclick=()=>step(-0.1);'
        'if(lbl)lbl.onclick=()=>{s=1;ap();};'
        'document.addEventListener("keydown",e=>{if(!e.ctrlKey&&!e.metaKey)return;'
        'if(e.key==="="||e.key==="+"){e.preventDefault();step(0.1);}'
        'else if(e.key==="-"){e.preventDefault();step(-0.1);}'
        'else if(e.key==="0"){e.preventDefault();s=1;ap();}});'
        'r.addEventListener("wheel",e=>{if(!(e.ctrlKey||e.metaKey))return;'
        'e.preventDefault();step(e.deltaY<0?0.1:-0.1);},{passive:false});'
        'ap();})();</script>'
        '<script>' + JS_ZAPIS + '(function(){'
        'const r=document.getElementById("rendered"),'
        'w=document.getElementById("raw"),'
        'br=document.getElementById("bren"),bw=document.getElementById("braw");'
        'function show(ren){r.style.display=ren?"block":"none";'
        'w.style.display=ren?"none":"block";'
        'br.classList.toggle("on",ren);bw.classList.toggle("on",!ren);}'
        'br.onclick=()=>show(true);bw.onclick=()=>show(false);'
        # klik w obrazek -> viewer obrazów (?view=1, z kadrowaniem). DELEGACJA na
        # #rendered: przeżywa auto-podmianę innerHTML co 3 s (per-img listener
        # ginąłby po każdym odświeżeniu pliku → „klik nie działa").
        'r.title="Kliknij obrazek, aby otworzyć w podglądzie (np. przyciąć)";'
        'r.addEventListener("click",e=>{'
        'const im=e.target.closest&&e.target.closest("img");'
        'if(im&&r.contains(im))location.href=im.src.split("?")[0]+"?view=1";});'
        # druk na Canon G3070 (md→DOCX→PDF→lp na konsoli, ~1-2 min)
        'const _prn=document.getElementById("prn");'
        'if(_prn)_prn.onclick=async e=>{'
        'e.preventDefault();const a=e.target;'
        'if(!confirm("Wydrukować na Canon G3070?\\n'
        '(md→DOCX→PDF jak przy sprawozdaniach; ok. 1–2 min; '
        'drukarka musi być w sieci domowej)"))return;'
        'a.textContent="⏳...";'
        'try{const r=await afFetch(a.dataset.n+"?print=1",{method:"POST"});'
        'if(r.status===202)a.textContent="🖨 wysłano";'
        'else{a.textContent="🖨 błąd";alert("Druk: "+await r.text());}}'
        'catch(err){a.textContent="🖨 błąd";}'
        'setTimeout(()=>a.textContent="🖨 drukuj",4000);};'
        # eksport DOCX z paskiem postępu (jak lektor): fetch blob + poll ?docxprog
        'const dxb=document.getElementById("docxbtn");'
        f'const DXN={_json_do_script(target.stem)};'
        'if(dxb){dxb.onclick=async function(e){e.preventDefault();'
        'if(dxb.dataset.busy)return;dxb.dataset.busy="1";'
        'const dp=document.getElementById("docxprog");'
        'const bar=dp.querySelector(".dpbar"),txt=dp.querySelector(".dpt");'
        'dp.style.display="flex";bar.classList.remove("err");'
        'bar.style.width="2%";txt.textContent="⚙ DOCX 0%";let stop=false;'
        'const pp=dxb.getAttribute("href").replace("?docx=1","?docxprog=1");'
        '(async function(){while(!stop){try{'
        'const p=await(await fetch(pp,{cache:"no-store"})).json();'
        'const pct=Math.max(2,Math.min(99,p.pct||0));'
        'bar.style.width=pct+"%";txt.textContent="⚙ DOCX "+pct+"%";'
        '}catch(_){}await new Promise(r=>setTimeout(r,400));}})();'
        'try{const r=await afFetch(dxb.getAttribute("href"),{method:"POST"});stop=true;'
        'if(!r.ok)throw new Error("HTTP "+r.status);'
        'const blob=await r.blob();bar.style.width="100%";'
        'txt.textContent="✓ DOCX gotowy — pobieranie";'
        'const u=URL.createObjectURL(blob),a=document.createElement("a");'
        'a.href=u;a.download=DXN+".docx";document.body.appendChild(a);a.click();'
        'a.remove();URL.revokeObjectURL(u);'
        'setTimeout(()=>{dp.style.display="none";dxb.removeAttribute("data-busy");},2500);'
        '}catch(err){stop=true;bar.classList.add("err");bar.style.width="100%";'
        'txt.textContent="❌ "+err.message;'
        'setTimeout(()=>{dp.style.display="none";dxb.removeAttribute("data-busy");},6000);}'
        '};}'
        # AUTO-ODSWIEZANIE: poll mtime co 3 s; przy zmianie pobierz strone,
        # podmien tresc obu widokow i przelicz wzory MathJax. Zakladka
        # i pozycja przewiniecia zostaja.
        'let _mt=0;'
        'async function chk(){try{'
        'const j=await(await fetch(location.pathname+"?mt=1",'
        '{cache:"no-store"})).json();'
        'if(_mt&&j.mt!==_mt){'
        'const doc=new DOMParser().parseFromString('
        'await(await fetch(location.pathname+"?view=1",'
        '{cache:"no-store"})).text(),"text/html");'
        'const nr=doc.getElementById("rendered"),nw=doc.getElementById("raw");'
        'if(nr)r.innerHTML=nr.innerHTML;'
        'if(nw)w.innerHTML=nw.innerHTML;'
        'if(window.MathJax&&MathJax.typesetPromise)MathJax.typesetPromise([r]);}'
        '_mt=j.mt;}catch(e){}}'
        'setInterval(chk,3000);chk();'
        'document.addEventListener("keydown",e=>{'
        'if(e.key==="ArrowLeft"){const a=document.getElementById("prev");'
        'if(a)location=a.href}'
        'if(e.key==="ArrowRight"){const a=document.getElementById("next");'
        'if(a)location=a.href}});'
        '})();</script>')

STYLE = (
    'body{font-family:system-ui,sans-serif;max-width:1000px;margin:1.2em auto;'
    'padding:0 1em;background:#f7f7f9;color:#222}'
    'h2{color:#333;font-size:1.1em;word-break:break-all}'
    'table{width:100%;border-collapse:collapse;background:#fff;border-radius:6px;'
    'overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}'
    'th,td{text-align:left;padding:.5em .7em;border-bottom:1px solid #eee;'
    'font-size:.92em;white-space:nowrap}'
    'th{background:#eef2f7;cursor:pointer;user-select:none}'
    'th:hover{background:#e0e8f0}th::after{content:" \\2195";color:#aaa;font-size:.8em}'
    'td:first-child,th:first-child{white-space:normal;word-break:break-all}'
    'tr:hover td{background:#f3f7fc}'
    'a{text-decoration:none;color:#0066cc}a:hover{text-decoration:underline}'
    '.dir{font-weight:600}'
    'td:nth-child(2),th:nth-child(2){text-align:right;color:#555}'
    '.muted{color:#999;font-size:.85em;margin:.6em 0}'
    'h2 a{color:#0066cc}h2 .sep{color:#bbb;font-weight:400}'
    '.dl{margin-right:.45em;text-decoration:none;opacity:.55}'
    '.dl:hover{opacity:1;text-decoration:none}'
    '.del:hover{filter:drop-shadow(0 0 2px #c00)}'
    '.crumb{position:relative;display:inline-block}'
    '.crumb>.dd{display:none;position:absolute;top:100%;left:0;background:#fff;'
    'border:1px solid #c5cdd8;border-radius:6px;box-shadow:0 4px 14px rgba(0,0,0,.18);'
    'z-index:60;max-height:320px;overflow-y:auto;min-width:200px;padding:.25em 0}'
    '.crumb:hover>.dd{display:block}'
    '.crumb>.dd a{display:block;padding:.3em .9em;white-space:nowrap;font-size:.85em}'
    '.crumb>.dd a:hover{background:#eef2f7}'
    '.crumb>.dd a.cur{font-weight:700;color:#1a5fb4}'
    # TRYB MOBILNY (telefon): prostszy interfejs — bez usuwania/zmiany
    # nazwy/kopiowania nazwy; większe pola dotyku; mniej kolumn
    '@media (max-width:700px){'
    'body{margin:.6em auto;padding:0 .5em}'
    'a.del,a.ren,a.cpy{display:none}'
    'th:nth-child(4),td:nth-child(4){display:none}'   # kolumna Utworzono
    'th,td{padding:.85em .55em;font-size:1.02em}'
    '.dl{font-size:1.35em;padding:.15em .25em;margin-right:.4em}'
    'h2{font-size:1.05em}'
    '.crumb>.dd a{padding:.7em 1em;font-size:1em}'
    '}'
    '#dropov{position:fixed;inset:0;display:flex;align-items:center;'
    'justify-content:center;background:rgba(15,50,110,.88);color:#fff;'
    'font-size:1.5em;z-index:50;visibility:hidden;pointer-events:none;'
    'text-align:center;padding:1em}'
    '#dropov.on{visibility:visible}'
)

# Wskaźniki lektora w LIŚCIE FOLDERU (poll ?lektorqj co 2 s, dopasowanie po
# data-name): plik wyjściowy mp3 + plik ŹRÓDŁOWY .md generowanego = pasek 🔊%;
# pliki źródłowe CZEKAJĄCE w kolejce = „⏳ #N w kolejce" (miejsce w kolejce).
LEKTOR_BAR_JS = (
    '<style>'
    # block-level (własna linia POD nazwą) — ta sama pozycja dla paska i dla
    # „w kolejce", niezależnie od długości nazwy pliku
    '.lekbar{display:flex;align-items:center;gap:.4em;margin-top:.25em}'
    '.lekbar .track{display:inline-block;width:90px;height:8px;border-radius:4px;'
    'background:#2a2d33;overflow:hidden}'
    '.lekbar .fill{display:block;height:100%;background:#3aa657;transition:width .4s}'
    '.lekbar .pct{font-size:.8em;color:#6fce8f;white-space:nowrap}'
    '.lekbar.q .pct{color:#e0a33a}'   # oczekujący w kolejce = bursztynowy
    '</style>'
    '<script>(function(){'
    'function mk(){var b=document.createElement("span");b.className="lekbar";'
    'b.innerHTML=\'<span class="track"><span class="fill"></span></span>'
    '<span class="pct"></span>\';return b;}'
    'async function tick(){try{'
    'const d=await(await fetch("?lektorqj",{cache:"no-store"})).json();'
    # st: basename -> {run:1,pct} (generowany) | {run:0,pos:N} (w kolejce)
    'const st={};let qn=0;'
    '(d.jobs||[]).forEach(function(j){'
    'const sn=(j.src||"").split("/").pop();'
    'if(j.state==="running"){'
    'if(sn)st[sn]={run:1,pct:j.pct||0};'
    'if(j.out)st[j.out]={run:1,pct:j.pct||0};'
    '}else if(j.state==="queued"){qn++;if(sn)st[sn]={run:0,pos:qn};}'
    '});'
    'if(d.ext_out)st[d.ext_out]={run:1,pct:d.ext_pct||0};'
    'if(d.ext_src)st[d.ext_src]={run:1,pct:d.ext_pct||0};'
    'document.querySelectorAll("tr[data-name]").forEach(function(tr){'
    'const s=st[tr.dataset.name];var el=tr.querySelector(".lekbar");'
    'if(!s){if(el)el.remove();return;}'
    'if(!el){el=mk();tr.querySelector("td").appendChild(el);}'
    'const tk=el.querySelector(".track"),pc=el.querySelector(".pct");'
    'if(s.run){el.classList.remove("q");tk.style.display="";'
    'el.querySelector(".fill").style.width=(s.pct||0)+"%";'
    'pc.textContent="🔊 "+(s.pct||0)+"%";}'
    'else{el.classList.add("q");tk.style.display="none";'
    'pc.textContent="⏳ #"+s.pos+" w kolejce";}'
    '});'
    '}catch(e){}}'
    'setInterval(tick,2000);tick();'
    '})();</script>'
)

# Okno wyboru lektora (format, opisy ilustracji, silnik mowy) — wspólne dla
# 🔊 w liście folderu i przycisku w podglądzie dokumentu. Wymaga JS_ESC.
# Domyślny silnik podaje strona: window._LEK_SILNIK (z lektor-ustawienia.conf).
LEKTOR_WYBOR_JS = (
    # modal wyboru formatu — RADIOBUTTONY per format + Generuj/Anuluj
    'function pickFmt(name){return new Promise(res=>{'
    'const ov=document.createElement("div");'
    'ov.style.cssText="position:fixed;inset:0;background:rgba(0,0,0,.5);'
    'display:flex;align-items:center;justify-content:center;z-index:99";'
    'const opts=[["","⚙ wg ustawień lektora (#e-lektor-ustawienia)"],'
    '["mp3","MP3 — 96 kbps (najmniejszy plik)"],'
    '["flac","FLAC — bezstratny zapis źródła 96 kbps"],'
    '["wav","WAV — nieskompresowany PCM"]];'
    'const dopts=[["","⚙ wg ustawień lektora"],'
    '["tak","TAK — model wizyjny opisze każdą ilustrację (dłużej)"],'
    '["nie","NIE — czytaj tylko podpisy rysunków"]];'
    'function mkradios(arr,nm){let h="";'
    'for(const [v,l] of arr){h+=\'<label style="display:flex;'
    'align-items:center;gap:.55em;padding:.45em .3em;cursor:pointer;'
    'border-radius:6px" onmouseover="this.style.background=\\\'#eef2f7\\\'" '
    'onmouseout="this.style.background=\\\'\\\'">'
    '<input type="radio" name="\'+nm+\'" value="\'+v+\'"\'+(v===""?" checked":"")'
    '+\' style="accent-color:#1a5fb4;width:1.05em;height:1.05em">\'+l'
    '+\'</label>\';}return h;}'
    'const sopts=[["","⚙ wg ustawień lektora ("+(window._LEK_SILNIK'
    '||"Piper, gdy usługa działa; inaczej Edge")+")"],'
    '["piper","Piper — lokalnie, bez internetu"],'
    '["edge","Edge — głosy online Microsoftu (internet)"]];'
    'const radios=mkradios(opts,"lfmt"),dradios=mkradios(dopts,"lopis"),'
    'sradios=mkradios(sopts,"lsil");'
    'ov.innerHTML=\'<div style="background:#fff;border-radius:10px;'
    'padding:1.1em 1.4em;max-width:92vw;min-width:300px;'
    'box-shadow:0 8px 30px rgba(0,0,0,.35)">'
    '<div style="font-weight:600;margin-bottom:.35em;word-break:break-all">'
    '🔊 Lektor: \'+esc(name)+\'</div>'
    # silnik edge wysyła tekst do Microsoftu — napis z ustawień lektora (B8);
    # treść ustawia lsw() wg wybranego silnika (domyślny: window._LEK_UWAGA)
    '<div class="lek-uwaga" id="lekwarn" style="display:none;color:#9a5b00;'
    'font-size:.88em;margin-bottom:.6em;max-width:30em"></div>'
    '<div style="color:#556;font-size:.9em;margin-bottom:.7em">'
    'Format nagrania (generacja dłuższych dokumentów może potrwać '
    'kilkanaście minut):</div>\'+radios+(window._LEK_OPISY===false?"":'
    '\'<div style="color:#556;font-size:.9em;margin:.8em 0 .3em;'
    'border-top:1px solid #e4e8ee;padding-top:.7em">'
    '🖼 Opisy ilustracji (model wizyjny):</div>\'+dradios)+'
    '\'<div style="color:#556;font-size:.9em;margin:.8em 0 .3em;'
    'border-top:1px solid #e4e8ee;padding-top:.7em">'
    '🗣 Głos (silnik mowy):</div>\'+sradios+'
    '\'<div style="display:flex;gap:.6em;margin-top:1em;'
    'justify-content:flex-end">'
    '<button data-a="x">Anuluj</button>'
    '<button data-a="ok" style="background:#1a5fb4;color:#fff;'
    'border-color:#1a5fb4">Generuj</button></div></div>\';'
    'ov.querySelectorAll("button").forEach(b=>{'
    'b.style.cssText+=";padding:.5em 1.2em;border:1px solid #b8c0cc;'
    'border-radius:6px;cursor:pointer;font-size:1em"'
    '+(b.dataset.a==="x"?";background:#f2f5f9":"");'
    'b.onclick=()=>{const v=ov.querySelector("input[name=lfmt]:checked");'
    'const d=ov.querySelector("input[name=lopis]:checked");'
    'const sl=ov.querySelector("input[name=lsil]:checked");'
    'ov.remove();res(b.dataset.a==="ok"'
    '?{fmt:(v?v.value:""),opisy:(d?d.value:""),silnik:(sl?sl.value:"")}:null);};});'
    # napis „tekst opuszcza urządzenie": wybrany edge → zawsze; Piper → brak;
    # wg ustawień → window._LEK_UWAGA (z lektor-ustawienia.conf, B8)
    'function lsw(){const sl=ov.querySelector("input[name=lsil]:checked");'
    'const v=(sl&&sl.value)||"",w=ov.querySelector("#lekwarn");'
    'const t=v==="piper"?"":(v==="edge"?' + _json_do_script(LEKTOR_UWAGA_EDGE)
    + ':(window._LEK_UWAGA||""));'
    'w.textContent=t?"⚠ "+t:"";w.style.display=t?"block":"none";}'
    'ov.querySelectorAll("input[name=lsil]").forEach(i=>i.onchange=lsw);lsw();'
    'ov.onclick=e=>{if(e.target===ov){ov.remove();res(null);}};'
    'document.body.appendChild(ov);});}'
)


# Akcje plikowe: 🗑 usuń (→.kosz), ✎ zmień nazwę (prompt → POST ?rename=),
# ⧉ kopiuj nazwę do schowka, 🔊 lektor (okno wyboru: LEKTOR_WYBOR_JS).
DEL_JS = (
    '<script>' + JS_ESC + JS_ZAPIS + LEKTOR_WYBOR_JS +
    'document.addEventListener("click",async e=>{'
    'const a=e.target.closest("a.del,a.ren,a.cpy,a.lek");if(!a)return;'
    'e.preventDefault();'
    'const n=decodeURIComponent(a.dataset.n);'
    'if(a.classList.contains("lek")){'
    'const fm=await pickFmt(n);'
    'if(fm===null)return;'
    'a.textContent="⏳";'
    'try{'
    'let u=a.dataset.n+"?lektor=1"+(fm.fmt?"&fmt="+fm.fmt:"")'
    '+(fm.opisy?"&opisy="+fm.opisy:"")+(fm.silnik?"&silnik="+fm.silnik:"");'
    'let r=await afFetch(u,{method:"POST"});'
    'let j=await r.json().catch(()=>({}));'
    'if(j.status==="busy"){'
    'a.textContent="🔊";'
    'if(!confirm("Lektor jest teraz zajęty — czyta inny dokument'
    '(np. na polecenie z Discorda)."+(j.pending?"\\nW kolejce czeka: "'
    '+j.pending+" plik(ów).":"")+"\\n\\nDopisać ten plik do kolejki? '
    'Audio wygeneruje się automatycznie, gdy przyjdzie jego kolej."))return;'
    'a.textContent="⏳";'
    'r=await afFetch(u+"&queue=1",{method:"POST"});'
    'j=await r.json().catch(()=>({}));}'
    'if(r.status===202){'
    'a.title=(j.status==="queued"?"W kolejce (poz. "+j.position+"): "'
    ':"Generuję: ")+(j.out||"");'
    'setTimeout(()=>a.textContent="🔊",2500);}'
    'else{alert("Lektor: "+(j.blad||j.status||r.status));a.textContent="🔊";}'
    '}catch(err){alert("Błąd lektora");a.textContent="🔊";}return;}'
    'if(a.classList.contains("cpy")){'
    'let ok=false;'
    'try{if(navigator.clipboard&&navigator.clipboard.writeText){'
    'await navigator.clipboard.writeText(n);ok=true;}}catch(e){}'
    # fallback dla HTTP (niezabezpieczony kontekst — clipboard API zablokowane):
    # tymczasowy textarea + execCommand("copy")
    'if(!ok){try{const ta=document.createElement("textarea");ta.value=n;'
    'ta.style.position="fixed";ta.style.top="0";ta.style.opacity="0";'
    'document.body.appendChild(ta);ta.focus();ta.select();'
    'ok=document.execCommand("copy");document.body.removeChild(ta);}'
    'catch(e){ok=false;}}'
    'if(ok){a.textContent="✓";setTimeout(()=>a.textContent="⧉",900);}'
    'else{prompt("Skopiuj nazwę (Ctrl+C):",n);}return;}'
    'if(a.classList.contains("ren")){'
    'const nn=prompt("Nowa nazwa:",n);'
    'if(!nn||nn===n)return;'
    'try{const r=await afFetch(a.dataset.n+"?rename="+encodeURIComponent(nn),'
    '{method:"POST"});'
    'if(r.ok){location.reload();}'
    'else{alert("Błąd zmiany nazwy: "+await r.text());}'
    '}catch(err){alert("Błąd zmiany nazwy");}return;}'
    'if(!confirm("Usunąć \\""+n+"\\"?\\n(plik trafi do kosza .kosz)"))return;'
    'try{const r=await afFetch(a.dataset.n,{method:"DELETE"});'
    'if(r.ok){a.closest("tr").remove();}'
    'else{alert("Błąd usuwania: HTTP "+r.status);}'
    '}catch(err){alert("Błąd usuwania");}'
    '});</script>'
)

# Przycisk lektora w PODGLĄDZIE dokumentu (.md, .docx): okno wyboru → POST
# ?lektor=1 → stan zadania z ?lektorqj co 2 s → po zakończeniu przeładowanie
# strony (pojawia się odtwarzacz). Działa także w trybie tylko do odczytu —
# nagranie trafia wtedy do katalogu lektora instancji.
LEKTOR_PODGLAD_JS = (
    '<script>' + JS_ESC + JS_ZAPIS + LEKTOR_WYBOR_JS +
    '(function(){'
    'const b=document.getElementById("lekgen");if(!b)return;'
    'const st=document.getElementById("lekst"),lab=b.textContent;'
    'function koniec(t){st.textContent=t;b.textContent=lab;delete b.dataset.busy;}'
    'async function sledz(id,out){for(;;){'
    'await new Promise(r=>setTimeout(r,2000));let d;'
    'try{d=await(await fetch("?lektorqj=1",{cache:"no-store"})).json();}'
    'catch(e){continue;}'
    'const j=(d.jobs||[]).find(x=>id!=null?x.id===id:x.out===out);'
    'if(!j){const bl=(d.bledy||[]).find(x=>id!=null?x.id===id:x.out===out);'
    'if(bl){koniec("❌ "+bl.blad);return;}'
    'st.textContent="✓ gotowe";location.reload();return;}'
    'st.textContent=j.state==="running"?"🔊 "+(j.pct||0)+"%":"⏳ w kolejce";}}'
    'b.onclick=async e=>{e.preventDefault();if(b.dataset.busy)return;'
    'const fm=await pickFmt(decodeURIComponent(b.dataset.n));if(fm===null)return;'
    'b.dataset.busy="1";b.textContent="⏳";'
    'try{const u=b.dataset.n+"?lektor=1"+(fm.fmt?"&fmt="+fm.fmt:"")'
    '+(fm.opisy?"&opisy="+fm.opisy:"")+(fm.silnik?"&silnik="+fm.silnik:"");'
    'let r=await afFetch(u,{method:"POST"});let j=await r.json().catch(()=>({}));'
    'if(j.status==="busy"){'
    'if(!confirm("Lektor jest teraz zajęty — czyta inny dokument."'
    '+(j.pending?"\\nW kolejce czeka: "+j.pending+" plik(ów).":"")'
    '+"\\n\\nDopisać ten dokument do kolejki?")){koniec("");return;}'
    'r=await afFetch(u+"&queue=1",{method:"POST"});j=await r.json().catch(()=>({}));}'
    'if(r.status===202||j.status==="duplikat"){'
    'st.textContent=j.status==="queued"?"⏳ w kolejce":"🔊 0%";'
    'sledz(j.id!=null?j.id:null,j.out);}'
    'else{koniec("❌ "+(j.blad||j.status||("HTTP "+r.status)));}'
    '}catch(err){koniec("❌ błąd lektora");}};'
    '})();</script>'
)


def _lektor_przycisk(target: Path, aud) -> str:
    """Przycisk 🔊 lektora do paska podglądu dokumentu + skrypt; pusty, gdy
    moduł lektora wyłączony. Domyślny silnik i napis o edge z
    lektor-ustawienia.conf (_lektor_silnik, _lektor_uwaga_js)."""
    if not KONF.modul('lektor') or target.suffix.lower() not in LEKTOR_ZRODLA:
        return ''
    q = quote(target.name)
    tytul = ('Nagraj ponownie lektorem (wybór formatu i głosu)' if aud is not None
             else 'Przeczytaj lektorem — wygeneruj nagranie (wybór formatu i głosu)')
    return (f'<a href="#" id="lekgen" data-n="{q}" title="{tytul}">'
            + ('🔊 lektor ↻' if aud is not None else '🔊 lektor') + '</a>'
            '<span id="lekst" style="color:#6fce8f;font-size:.9em"></span>'
            f'<script>window._LEK_SILNIK={_json_do_script(_lektor_silnik())};'
            + ('' if KONF.modul('lektor_opisy_ai') else 'window._LEK_OPISY=false;')
            + '</script>' + _lektor_uwaga_js() + LEKTOR_PODGLAD_JS)


# Drag & drop upload — upuszczenie plików na listing wgrywa je do bieżącego
# katalogu (POST multipart); tabela odświeży się sama (auto-refresh).
DROP_JS = (
    '<script>' + JS_ZAPIS + '(function(){'
    'const ov=document.createElement("div");ov.id="dropov";'
    'ov.textContent="⬆ Upuść pliki, aby wgrać do tego katalogu";'
    'document.body.appendChild(ov);let d=0;'
    'window.addEventListener("dragenter",e=>{e.preventDefault();d++;ov.classList.add("on");});'
    'window.addEventListener("dragleave",e=>{e.preventDefault();d--;'
    'if(d<=0){d=0;ov.classList.remove("on");}});'
    'window.addEventListener("dragover",e=>e.preventDefault());'
    'window.addEventListener("drop",async e=>{e.preventDefault();d=0;'
    'let files=[];'                       # [ [File, relpath], ... ]
    'async function walk(en,path){'
    'if(en.isFile){const f=await new Promise((res,rej)=>en.file(res,rej));'
    'files.push([f,path+f.name]);}'
    'else if(en.isDirectory){const rd=en.createReader();let ents;'
    'do{ents=await new Promise((res,rej)=>rd.readEntries(res,rej));'
    'for(const c of ents){await walk(c,path+en.name+"/");}}while(ents.length);}}'
    'const items=e.dataTransfer.items;'
    'if(items&&items.length&&items[0].webkitGetAsEntry){'
    'const ents=[...items].map(it=>it.webkitGetAsEntry()).filter(Boolean);'
    'ov.textContent="⬆ Czytam zawartość…";'
    'for(const en of ents){await walk(en,"");}}'
    'else{files=[...e.dataTransfer.files].map(f=>[f,f.name]);}'
    'if(!files.length){ov.classList.remove("on");return;}'
    'const tot=files.length;'
    'ov.innerHTML="<div id=ptxt>⬆ Wgrywam 0/"+tot+"…</div>"'
    '+"<div style=\\"margin-top:.7em;width:60%;max-width:420px;height:12px;"'
    '+"background:#0004;border-radius:6px;overflow:hidden\\">"'
    '+"<div id=pbar style=\\"height:100%;width:0%;background:#6fce8f;"'
    '+"transition:width .15s\\"></div></div>";'
    'const ptxt=ov.querySelector("#ptxt"),pbar=ov.querySelector("#pbar");'
    'let nw=0,sk=0,failed=[];let proc=0;'
    # PLIK-PO-PLIKU: zerwanie gubi 1 plik (retry x3), nie cala paczke 93+video
    'function send1(f,rel){return new Promise(res=>{'
    'const fd=new FormData();fd.append("file",f,rel);'
    'const xhr=new XMLHttpRequest();xhr.open("POST",location.pathname);afXhr(xhr);'
    'xhr.timeout=600000;'
    'xhr.upload.onprogress=ev=>{if(ev.lengthComputable){'
    'pbar.style.width=Math.round((proc+ev.loaded/ev.total)/tot*100)+"%";}};'
    'xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300){'
    'let s=0;try{s=(JSON.parse(xhr.responseText).skipped)||0;}catch(e){}'
    'res({ok:true,skip:s>0});}else res({ok:false});};'
    'xhr.onerror=()=>res({ok:false});xhr.ontimeout=()=>res({ok:false});'
    'xhr.send(fd);});}'
    'for(const [f,rel] of files){'
    'let r={ok:false};for(let a=0;a<3&&!r.ok;a++){r=await send1(f,rel);}'
    'if(r.ok){if(r.skip)sk++;else nw++;}else failed.push(rel);proc++;'
    'ptxt.textContent="⬆ Wgrywam "+proc+"/"+tot'
    '+(failed.length?(" — błędy: "+failed.length):"")+"…";'
    'pbar.style.width=Math.round(proc/tot*100)+"%";}'
    'const okm="✓ Gotowe — nowe: "+nw+", już były: "+sk;'
    'const failm="⚠ Nowe: "+nw+", już były: "+sk+", NIEUDANE: "+failed.length'
    '+" — przeciągnij ten folder PONOWNIE, aby uzupełnić brakujące "'
    '+"(pliki o rozmiarze ze źródła nie zduplikują się)";'
    'ov.textContent=failed.length?failm:okm;'
    'setTimeout(()=>{ov.classList.remove("on");'
    'ov.textContent="⬆ Upuść pliki, aby wgrać do tego katalogu";},'
    'failed.length?9000:2500);'
    '});})();</script>'
)

VIEWER_STYLE = (
    'body{margin:0;background:#1b1d21;color:#ddd;font-family:system-ui,sans-serif;'
    'display:flex;flex-direction:column;min-height:100vh}'
    '.bar{display:flex;align-items:center;gap:1em;padding:.55em 1em;background:#26292f;'
    'position:sticky;top:0;z-index:25;font-size:.95em;flex-wrap:wrap}'
    # --- kadrowanie (✂) ---
    '.cropov{position:fixed;inset:0;z-index:20;display:none;cursor:crosshair;touch-action:none}'
    '.cropov.on{display:block}'
    '.cropbox{position:fixed;z-index:21;border:1.5px dashed #ffd34d;box-sizing:border-box;'
    'box-shadow:0 0 0 9999px rgba(0,0,0,.5);cursor:move;display:none;touch-action:none}'
    '.cropbox.on{display:block}'
    '.cropbox .h{position:absolute;width:14px;height:14px;background:#ffd34d;'
    'border:1px solid #6b5300;border-radius:2px;touch-action:none}'
    '.cropbox .h.nw{left:-7px;top:-7px;cursor:nwse-resize}'
    '.cropbox .h.ne{right:-7px;top:-7px;cursor:nesw-resize}'
    '.cropbox .h.sw{left:-7px;bottom:-7px;cursor:nesw-resize}'
    '.cropbox .h.se{right:-7px;bottom:-7px;cursor:nwse-resize}'
    '.cropbox .h.n{left:50%;top:-7px;margin-left:-7px;cursor:ns-resize}'
    '.cropbox .h.s{left:50%;bottom:-7px;margin-left:-7px;cursor:ns-resize}'
    '.cropbox .h.w{left:-7px;top:50%;margin-top:-7px;cursor:ew-resize}'
    '.cropbox .h.e{right:-7px;top:50%;margin-top:-7px;cursor:ew-resize}'
    '.cropbtns .asp{color:#cfe3ff;font-size:.9em;display:flex;align-items:center;gap:.3em}'
    '.cropbtns select{background:#2a3140;color:#fff;border:1px solid #3a4250;'
    'border-radius:5px;padding:.3em}'
    '.cropbtns{position:fixed;left:50%;bottom:1.2em;transform:translateX(-50%);z-index:21;'
    'display:none;gap:.5em;background:#26292fee;padding:.5em .7em;border-radius:8px;'
    'box-shadow:0 2px 12px rgba(0,0,0,.5)}'
    '.cropbtns.on{display:flex}'
    '.cropbtns button{color:#fff;background:#2a3140;border:1px solid #3a4250;border-radius:6px;'
    'padding:.45em .85em;cursor:pointer;font-size:.95em}'
    '.cropbtns button.prim{background:#1a5fb4;border-color:#1a5fb4}'
    '.crophint{position:fixed;top:3.4em;left:50%;transform:translateX(-50%);z-index:21;'
    'background:#26292fdd;color:#cfe3ff;padding:.3em .8em;border-radius:6px;'
    'font-size:.85em;display:none;pointer-events:none}'
    '.crophint.on{display:block}'
    '.bar a{color:#7ab7ff;text-decoration:none;padding:.25em .7em;border:1px solid #3a3f47;'
    'border-radius:5px;white-space:nowrap}'
    '.bar a:hover{background:#32363d}'
    '.bar .name{color:#eee;font-weight:600;word-break:break-all}'
    '.bar .cnt{color:#888}'
    '.stage{flex:1;display:flex;align-items:center;justify-content:center;'
    'padding:.8em;overflow:hidden;cursor:grab}'
    '.stage.drag{cursor:grabbing}'
    '.stage img{max-width:100%;max-height:calc(100vh - 5em);'
    'box-shadow:0 2px 14px rgba(0,0,0,.5);transform-origin:center center;'
    'user-select:none;-webkit-user-drag:none}'
    '.nav-off{opacity:.3;pointer-events:none}'
)

# Nowy folder: prompt o nazwę → POST ?mkdir=nazwa do bieżącego katalogu → reload.
MKDIR_JS = (
    '<script>' + JS_ZAPIS +
    'var _mkd=document.getElementById("mkd");'
    'if(_mkd)_mkd.addEventListener("click",async function(e){'
    'e.preventDefault();'
    'var n=(prompt("Nazwa nowego folderu:")||"").trim();'
    'if(!n)return;'
    'try{'
    'var r=await afFetch(location.pathname+"?mkdir="+encodeURIComponent(n),{method:"POST"});'
    'if(r.ok){location.reload();}'
    'else{alert("Nie udało się: "+(await r.text()));}'
    '}catch(err){alert("Błąd: "+err);}'
    '});'
    '</script>'
)

# „⧉ kopiuj nazwy plików": zbiera nazwy WSZYSTKICH plików (bez folderów) z bieżącego
# listingu w aktualnej kolejności sortowania, po jednej w wierszu → schowek
# (writeText + fallback execCommand/prompt jak per-plik ⧉).
COPYCOL_JS = (
    '<script>(function(){'
    'const b=document.getElementById("cpcol");if(!b)return;'
    'async function copy(t){let ok=false;'
    'try{if(navigator.clipboard&&navigator.clipboard.writeText){'
    'await navigator.clipboard.writeText(t);ok=true;}}catch(e){}'
    'if(!ok){try{const ta=document.createElement("textarea");ta.value=t;'
    'ta.style.position="fixed";ta.style.top="0";ta.style.opacity="0";'
    'document.body.appendChild(ta);ta.focus();ta.select();'
    'ok=document.execCommand("copy");document.body.removeChild(ta);}catch(e){ok=false;}}'
    'return ok;}'
    'b.addEventListener("click",async e=>{e.preventDefault();'
    'const names=[...document.querySelectorAll("tbody tr")].filter('
    'tr=>tr.dataset.name&&!tr.querySelector("a.dir")&&tr.offsetParent!==null)'
    '.map(tr=>tr.dataset.name);'
    'if(!names.length){alert("Brak plików w tym katalogu.");return;}'
    'const t=names.join("\\n"),old=b.textContent;'
    'if(await copy(t)){b.textContent="✓ skopiowano "+names.length;'
    'setTimeout(()=>b.textContent=old,1600);}'
    'else{prompt("Skopiuj nazwy (Ctrl+C):",t);}'
    '});'
    '})();</script>'
)

# Zoom: kółko / CTRL+kółko wokół kursora, drag = pan, dwuklik = fit/100%,
# klawisze +/-/0; % powiększenia widoczny na pasku (#zl).
VIEWER_JS = (
    '<script>(function(){'
    'const st=document.querySelector(".stage"),im=st.querySelector("img"),'
    'zl=document.getElementById("zl");'
    'let s=1,tx=0,ty=0;'
    'function ap(){im.style.transform=`translate(${tx}px,${ty}px) scale(${s})`;'
    'zl.textContent=Math.round(s*100)+"%";}'
    'function clamp(){s=Math.min(Math.max(s,0.2),20);}'
    'st.addEventListener("wheel",e=>{e.preventDefault();'
    'const r=im.getBoundingClientRect(),'
    'cx=e.clientX-(r.left+r.width/2),cy=e.clientY-(r.top+r.height/2),'
    'k=e.deltaY<0?1.2:1/1.2,o=s;s*=k;clamp();const f=s/o;'
    'tx=tx*f - cx*(f-1);ty=ty*f - cy*(f-1);ap();},{passive:false});'
    'let dr=null;'
    'st.addEventListener("mousedown",e=>{dr={x:e.clientX-tx,y:e.clientY-ty};'
    'st.classList.add("drag");e.preventDefault();});'
    'window.addEventListener("mousemove",e=>{if(!dr)return;'
    'tx=e.clientX-dr.x;ty=e.clientY-dr.y;ap();});'
    'window.addEventListener("mouseup",()=>{dr=null;st.classList.remove("drag");});'
    'function fit(){s=1;tx=ty=0;ap();}'
    'function nat(){s=im.naturalWidth&&im.clientWidth?im.naturalWidth/im.clientWidth:1;'
    'clamp();tx=ty=0;ap();}'
    # dwuklik: powiększone/przesunięte -> dopasuj do okna; dopasowane -> 1:1
    'st.addEventListener("dblclick",()=>{if(s!==1||tx||ty){fit();}else{nat();}});'
    'const fb=document.getElementById("fitb");if(fb)fb.onclick=e=>{e.preventDefault();fit();};'
    'const nb=document.getElementById("natb");if(nb)nb.onclick=e=>{e.preventDefault();nat();};'
    'document.addEventListener("keydown",e=>{'
    'if(e.key==="+"||e.key==="="){s*=1.2;clamp();ap();}'
    'if(e.key==="-"){s/=1.2;clamp();ap();}'
    'if(e.key==="0"){fit();}'
    'if(e.key==="1"){nat();}});'
    'ap();})();</script>'
)

# Kadrowanie zdjęcia: ✂ → 3 sposoby definiowania kadru; ramka NIGDY nie wychodzi
# poza zdjęcie (model w pikselach NATURALNYCH + clamp do granic obrazu):
#  • LPM przeciągnij = nowe pole,  • 8 uchwytów = przesuń krawędzie/rogi,
#  • scroll = skaluj ramkę,  • selektor „Proporcje" = narzucony aspekt (+scroll).
# _cropEsc(): ESC w trybie kadru anuluje (true); poza nim główny keydown -> folder.
# EXIF orientacja telefonów obsłużona serwerowo (ImageOps.exif_transpose).
CROP_JS = (
    '<script>' + JS_ZAPIS + '(function(){'
    'const im=document.querySelector(".stage img");'
    'const ov=document.getElementById("cropov"),box=document.getElementById("cropbox");'
    'const btns=document.getElementById("cropbtns"),hint=document.getElementById("crophint");'
    'const cb=document.getElementById("cropb"),asel=document.getElementById("craspect");'
    'if(!cb||!im)return;'
    'let on=false,B=null,aspect=null;const MIN=10;'
    'function rect(){return im.getBoundingClientRect();}'
    'function s2n(cx,cy){const r=rect();return {x:(cx-r.left)/(r.width/im.naturalWidth),'
    'y:(cy-r.top)/(r.height/im.naturalHeight)};}'
    'function clamp(){if(!B)return;const NW=im.naturalWidth,NH=im.naturalHeight;'
    'B.w=Math.max(MIN,Math.min(B.w,NW));B.h=Math.max(MIN,Math.min(B.h,NH));'
    'B.x=Math.max(0,Math.min(B.x,NW-B.w));B.y=Math.max(0,Math.min(B.y,NH-B.h));}'
    'function render(){if(!B){box.classList.remove("on");return;}'
    'const r=rect(),k=r.width/im.naturalWidth;'
    'box.style.left=(r.left+B.x*k)+"px";box.style.top=(r.top+B.y*k)+"px";'
    'box.style.width=(B.w*k)+"px";box.style.height=(B.h*k)+"px";box.classList.add("on");}'
    'function aspFit(){if(!aspect||!B)return;const cx=B.x+B.w/2,cy=B.y+B.h/2;'
    'B.h=B.w/aspect;B.x=cx-B.w/2;B.y=cy-B.h/2;clamp();}'
    'function enter(){const fb=document.getElementById("fitb");if(fb)fb.click();'
    'on=true;ov.classList.add("on");btns.classList.add("on");hint.classList.add("on");B=null;render();}'
    'function exit(){on=false;ov.classList.remove("on");btns.classList.remove("on");'
    'hint.classList.remove("on");box.classList.remove("on");B=null;}'
    'cb.onclick=e=>{e.preventDefault();on?exit():enter();};'
    'document.getElementById("crcancel").onclick=exit;'
    'window._cropEsc=function(){if(on){exit();return true;}return false;};'
    'asel.onchange=()=>{const v=asel.value;'
    'aspect=v?(parseFloat(v.split(":")[0])/parseFloat(v.split(":")[1])):null;'
    'if(aspect){const NW=im.naturalWidth,NH=im.naturalHeight;'
    'if(!B){let w=NW*0.6,h=w/aspect;if(h>NH*0.9){h=NH*0.9;w=h*aspect;}'
    'B={x:(NW-w)/2,y:(NH-h)/2,w:w,h:h};}else{aspFit();}clamp();render();}};'
    'let dr=null;'
    'ov.addEventListener("pointerdown",e=>{e.preventDefault();ov.setPointerCapture(e.pointerId);'
    'const p=s2n(e.clientX,e.clientY);'
    'p.x=Math.max(0,Math.min(p.x,im.naturalWidth));p.y=Math.max(0,Math.min(p.y,im.naturalHeight));'
    'dr={ox:p.x,oy:p.y};B={x:p.x,y:p.y,w:MIN,h:MIN};render();});'
    'ov.addEventListener("pointermove",e=>{if(!dr)return;const p=s2n(e.clientX,e.clientY);'
    'let x0=Math.min(dr.ox,p.x),y0=Math.min(dr.oy,p.y),w=Math.abs(p.x-dr.ox),h=Math.abs(p.y-dr.oy);'
    'if(aspect){h=w/aspect;if(p.y<dr.oy)y0=dr.oy-h;}'
    'B={x:x0,y:y0,w:Math.max(MIN,w),h:Math.max(MIN,h)};clamp();render();});'
    'ov.addEventListener("pointerup",()=>{dr=null;});'
    'function scale(f){if(!B)return;const cx=B.x+B.w/2,cy=B.y+B.h/2;'
    'B.w*=f;B.h=aspect?B.w/aspect:B.h*f;B.x=cx-B.w/2;B.y=cy-B.h/2;clamp();render();}'
    'ov.addEventListener("wheel",e=>{e.preventDefault();e.stopPropagation();'
    'scale(e.deltaY<0?1.06:1/1.06);},{passive:false});'
    'let mv=null;'
    'box.addEventListener("pointerdown",e=>{if(e.target!==box)return;'
    'e.preventDefault();e.stopPropagation();box.setPointerCapture(e.pointerId);'
    'const p=s2n(e.clientX,e.clientY);mv={dx:p.x-B.x,dy:p.y-B.y};});'
    'box.addEventListener("pointermove",e=>{if(!mv)return;const p=s2n(e.clientX,e.clientY);'
    'B.x=p.x-mv.dx;B.y=p.y-mv.dy;clamp();render();});'
    'box.addEventListener("pointerup",()=>{mv=null;});'
    'box.addEventListener("wheel",e=>{e.preventDefault();e.stopPropagation();'
    'scale(e.deltaY<0?1.06:1/1.06);},{passive:false});'
    'let rz=null;'
    'box.querySelectorAll(".h").forEach(h=>{'
    'h.addEventListener("pointerdown",e=>{e.preventDefault();e.stopPropagation();'
    'h.setPointerCapture(e.pointerId);rz={c:h.className,x0:B.x,y0:B.y,w0:B.w,h0:B.h};});'
    'h.addEventListener("pointermove",e=>{if(!rz)return;const p=s2n(e.clientX,e.clientY);'
    'let L=rz.x0,T=rz.y0,R=rz.x0+rz.w0,Bt=rz.y0+rz.h0,c=rz.c;'
    'if(c.indexOf("w")>=0)L=p.x;if(c.indexOf("e")>=0)R=p.x;'
    'if(c.indexOf("n")>=0)T=p.y;if(c.indexOf("s")>=0)Bt=p.y;'
    'let nx=Math.min(L,R),ny=Math.min(T,Bt),nw=Math.abs(R-L),nh=Math.abs(Bt-T);'
    'if(aspect){nh=nw/aspect;if(c.indexOf("n")>=0)ny=(rz.y0+rz.h0)-nh;}'
    'B={x:nx,y:ny,w:Math.max(MIN,nw),h:Math.max(MIN,nh)};clamp();render();});'
    'h.addEventListener("pointerup",()=>{rz=null;});});'
    'async function doCrop(mode){if(!B){alert("Najpierw zaznacz obszar");return;}clamp();'
    'const r={x:Math.round(B.x),y:Math.round(B.y),w:Math.round(B.w),h:Math.round(B.h),mode:mode};'
    'try{const res=await afFetch(_Q+"?crop",{method:"POST",'
    'headers:{"Content-Type":"application/json"},body:JSON.stringify(r)});'
    'const j=await res.json().catch(()=>({}));'
    'if(res.ok){exit();if(mode==="overwrite"){im.src=_Q+"?v="+Date.now();}'
    'else{location.href=encodeURIComponent(j.file)+"?view=1";}}'
    'else{alert("Nie udało się: "+(j.error||await res.text()));}'
    '}catch(err){alert("Błąd: "+err);}}'
    'document.getElementById("crsave").onclick=()=>doCrop("copy");'
    'document.getElementById("crover").onclick=()=>doCrop("overwrite");'
    'window.addEventListener("resize",()=>{if(on)render();});'
    '})();</script>'
)

# Sortowanie zapamiętywane w localStorage (przeżywa odświeżenie i nawigację);
# wiersz ".." (class="up") zawsze przypięty na górze.
SORT_JS = (
    '<script>(function(){'
    'const ths=[...document.querySelectorAll("th")];'
    'const tb=document.querySelector("table").tBodies[0];'
    # sortowanie NATURALNE tekstów: liczby w nazwach porównywane numerycznie
    'function nat(s){return s.split(/(\\d+)/).map('
    'p=>/^\\d+$/.test(p)?p.padStart(14,"0"):p.toLowerCase()).join("\\u0001");}'
    'function srt(i,asc){'
    'const up=tb.querySelector("tr.up");'
    '[...tb.rows].filter(r=>!r.classList.contains("up")).sort((a,b)=>{'
    'const dx=a.cells[i].dataset.sort,dy=b.cells[i].dataset.sort;'
    'let x=dx!==undefined?parseFloat(dx):nat(a.cells[i].textContent.trim());'
    'let y=dy!==undefined?parseFloat(dy):nat(b.cells[i].textContent.trim());'
    'return (x>y?1:x<y?-1:0)*(asc?1:-1);'
    '}).forEach(r=>tb.appendChild(r));'
    'if(up)tb.prepend(up);}'
    'ths.forEach((th,i)=>{th.onclick=()=>{'
    'const asc=th._a=!th._a;srt(i,asc);'
    'try{localStorage.setItem("dirSort",JSON.stringify({i:i,asc:asc}))}catch(e){}'
    '};});'
    'window._applySaved=function(){try{const s=JSON.parse(localStorage.getItem("dirSort"));'
    'if(s&&ths[s.i]!==undefined){ths[s.i]._a=s.asc;srt(s.i,s.asc);}}catch(e){}};'
    'window._applySaved();'
    # AUTO-ODŚWIEŻANIE: co 4 s pobierz w tle tę samą stronę, porównaj <tbody>
    # serwerowe z poprzednim pobraniem; przy zmianie podmień tabelę i licznik,
    # po czym przywróć zapamiętane sortowanie. Nowe pliki (np. świeży DOCX)
    # pojawiają się bez ręcznego odświeżania.
    '(function(){let sig="";'
    'async function tick(){try{'
    'const r=await fetch(location.pathname+location.search,{cache:"no-store"});'
    'if(!r.ok)return;'
    'const doc=new DOMParser().parseFromString(await r.text(),"text/html");'
    'const nb=doc.querySelector("tbody");if(!nb)return;'
    'const ns=nb.innerHTML;'
    'if(sig&&ns!==sig){'
    'document.querySelector("tbody").innerHTML=ns;'
    'const m=doc.querySelector("p.muted"),lm=document.querySelector("p.muted");'
    'if(m&&lm)lm.innerHTML=m.innerHTML;'
    'window._applySaved();}'
    'sig=ns;}catch(e){}}'
    'setInterval(tick,4000);})();'
    '})();</script>'
)


@web.middleware
async def auth(request, handler):
    if KONF.logowanie == 'formularz':
        return await _LOGOWANIE.obsluz(request, handler, KONF.katalog_auth, _BRAMKA,
                                       KONF.nazwa_instancji)
    # Pusty SERVER_PASS = brak autoryzacji (open access — tylko dla prywatnych sieci!)
    if not PASSW:
        return await handler(request)
    h = request.headers.get('Authorization', '')
    if not h.startswith('Basic '):
        return web.Response(status=401, headers={'WWW-Authenticate': 'Basic realm="Anbernic"'})
    try:
        u, p = base64.b64decode(h[6:]).decode().split(':', 1)
    except Exception:
        return web.Response(status=401, headers={'WWW-Authenticate': 'Basic realm="Anbernic"'})
    if not (hmac.compare_digest(u.encode(), USER.encode())
            and hmac.compare_digest(p.encode(), PASSW.encode())):
        return web.Response(status=401, text='Niepoprawne dane',
                            headers={'WWW-Authenticate': 'Basic realm="Anbernic"'})
    return await handler(request)


@web.middleware
async def errlog(request, handler):
    """Loguje NIEOBSŁUŻONE wyjątki do rejestru (świadome 4xx/redirecty pomija)."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        import traceback
        tb = traceback.format_exc().strip().splitlines()
        _evlog('error', f'{request.method} {request.path_qs} -> '
               f'{type(e).__name__}: {e} | {tb[-1] if tb else ""}',
               level='error')
        return web.Response(
            status=500, text='500 — błąd serwera (zapisany w rejestrze /?events=1)')


def sciezka_ukryta(sciezka: str) -> bool:
    """Czy którykolwiek segment ścieżki adresu zaczyna się od kropki
    (.kosz, .git, .opisy_cache, .part, .zip_tmp — i „..")."""
    return any(seg.startswith('.') for seg in re.split(r'[/\\]', sciezka) if seg)


@web.middleware
async def straz_ukrytych(request, handler):
    """C1: pliki i katalogi z kropką są ukryte w listingu — i niedostępne po
    bezpośrednim adresie: 403 dla KAŻDEJ metody i każdego handlera plików
    (jedno miejsce). Serwer sam takich adresów nie generuje."""
    if sciezka_ukryta(request.path):
        return web.Response(status=403, text='403 — pliki i katalogi zaczynające się '
                                             'od kropki są niedostępne.')
    return await handler(request)


# ── Strażnik źródła żądania (B9: DNS rebinding, CSRF) ──────────────────────
METODY_ZAPISU = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})


def _nazwa_hosta(host: str) -> str:
    """Wartość nagłówka Host → nazwa bez portu, małymi literami, bez kropki
    końcowej; literał IPv6 bez nawiasów."""
    h = host.strip().lower()
    if h.startswith('['):
        return h[1:h.find(']')] if ']' in h else h[1:]
    if h.count(':') == 1:
        h = h.split(':', 1)[0]
    return h.rstrip('.')


def host_dozwolony(host: str, dozwolone=()) -> bool:
    """Literał IP (v4/v6), localhost, nazwy *.local i wpisy dozwolone_hosty
    (wpis z kropką na początku = sufiks, np. .ts.net). DNS rebinding wymaga
    nazwy domenowej napastnika — taka nazwa nie przejdzie."""
    import ipaddress
    h = _nazwa_hosta(host)
    if not h:
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    if h == 'localhost' or h.endswith('.local'):
        return True
    for d in dozwolone:
        if (d.startswith('.') and h.endswith(d)) or h == d:
            return True
    return False


def origin_zgodny(origin, host: str) -> bool:
    """Brak Origin (stara przeglądarka, curl) = zgodny; obecny musi wskazywać
    ten sam host:port co nagłówek Host („null" i obcy host = niezgodny)."""
    if origin is None:
        return True
    from urllib.parse import urlsplit
    try:
        netloc = urlsplit(origin.strip()).netloc.lower()
    except ValueError:
        return False
    return bool(netloc) and netloc == host.strip().lower()


@web.middleware
async def straz_zrodla(request, handler):
    """B9: (a) Host spoza listy → 421; (b) POST/PUT/PATCH/DELETE bez
    X-AnberFiles: 1 → 403. Wyjątek (b): formularze logowania i „Ustaw hasło"
    (zwykły <form method=post> przed zalogowaniem, działa bez JS i z menedżerem
    haseł) — zamiast nagłówka zgodność Origin z Host."""
    host = request.headers.get('Host')
    if host is not None and not host_dozwolony(host, KONF.dozwolone_hosty):
        return web.Response(status=421, text='421 — nieznana nazwa serwera w adresie. '
                            'Użyj adresu IP, nazwy .local albo dopisz nazwę do '
                            'dozwolone_hosty w ustawieniach instancji.')
    if request.method in METODY_ZAPISU:
        if (KONF.logowanie == 'formularz' and request.path in
                (_LOGOWANIE.SCIEZKA_LOGOWANIA, _LOGOWANIE.SCIEZKA_USTAWIENIA)):
            if not origin_zgodny(request.headers.get('Origin'), host or ''):
                _evlog('ochrona', f'formularz {request.path} z obcego originu '
                       f'{request.headers.get("Origin")!r} od {request.remote}',
                       level='warn')
                return web.Response(status=403, text='403 — formularz wysłany z innej '
                                                     'strony.')
        elif request.headers.get(NAGLOWEK_ZAPISU) != '1':
            return web.Response(status=403, text='403 — brak nagłówka X-AnberFiles: '
                                'żądanie zmieniające stan przyjmowane tylko ze stron '
                                'AnberFiles.')
    return await handler(request)


# ── Obserwacja pamięci (serwer + procesy potomne: soffice, lektor) ──────────
# Jądro urządzeń nie ma kontrolera pamięci cgroup (MemoryMax w systemd nie
# działa) — pamięć się obserwuje: pomiar co 60 s z /proc, szczyt i przekroczenie
# progu trafiają do rejestru zdarzeń, stan widać na /?events=1.
PAMIEC_CO_S = 60
PAMIEC_OSTRZEZENIE_CO_S = 600       # ostrzeżenie o progu najwyżej raz na 10 min
PAMIEC_SZCZYT_KROK = 1.10           # zapis nowego szczytu przy wzroście ≥ 10 %


def _status_proc(plik: Path):
    """(PPid, VmRSS w kB) z /proc/<pid>/status; None = proces zniknął."""
    ppid = rss = None
    try:
        for linia in plik.read_text(encoding='utf-8', errors='replace').splitlines():
            if linia.startswith('PPid:'):
                ppid = int(linia.split()[1])
            elif linia.startswith('VmRSS:'):
                rss = int(linia.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return ppid, rss or 0                # wątki jądra nie mają VmRSS


def zmierz_pamiec(pid: int = None, proc: Path = Path('/proc')):
    """RSS procesu serwera i sumy jego potomków (rekurencyjnie: soffice →
    soffice.bin) w MB; None, gdy /proc niedostępny (np. Windows w testach)."""
    pid = os.getpid() if pid is None else pid
    if not (proc / str(pid) / 'status').is_file():
        return None
    try:
        wpisy = list(proc.iterdir())
    except OSError:
        return None
    dzieci, rss = {}, {}
    for d in wpisy:
        if not d.name.isdigit():
            continue
        st = _status_proc(d / 'status')
        if st is None:
            continue
        n = int(d.name)
        dzieci.setdefault(st[0], []).append(n)
        rss[n] = st[1]
    potomne, stos, widziane = 0, list(dzieci.get(pid, ())), {pid}
    while stos:
        n = stos.pop()
        if n in widziane:
            continue
        widziane.add(n)
        potomne += rss.get(n, 0)
        stos.extend(dzieci.get(n, ()))
    serwer = rss.get(pid, 0)
    return {'serwer_mb': round(serwer / 1024, 1), 'potomne_mb': round(potomne / 1024, 1),
            'razem_mb': round((serwer + potomne) / 1024, 1)}


class ObserwatorPamieci:
    """Stan pomiarów pamięci; krok() = jeden pomiar i ewentualne wpisy w rejestrze.
    pomiar, rejestr i zegar wstrzykiwane (testy)."""

    def __init__(self, prog_mb: int, pomiar=None, rejestr=None, zegar=None):
        import time
        self.prog_mb = prog_mb
        self.pomiar = pomiar or zmierz_pamiec
        self.rejestr = rejestr or (lambda *a, **kw: _evlog(*a, **kw))
        self.zegar = zegar or time.monotonic
        self.teraz = None                # ostatni pomiar (słownik) albo None
        self.szczyt_mb = 0.0
        self._zapisany_szczyt = 0.0
        self._ostatnie_ostrzezenie = None

    @property
    def teraz_mb(self):
        return None if self.teraz is None else self.teraz['razem_mb']

    def krok(self):
        m = self.pomiar()
        self.teraz = m
        if m is None:
            return None
        razem = m['razem_mb']
        self.szczyt_mb = max(self.szczyt_mb, razem)
        if self.szczyt_mb > 0 and (
                self._zapisany_szczyt == 0
                or self.szczyt_mb >= self._zapisany_szczyt * PAMIEC_SZCZYT_KROK):
            self._zapisany_szczyt = self.szczyt_mb
            self.rejestr('pamiec', f'nowy szczyt pamięci: {self.szczyt_mb:.0f} MB '
                         f'(serwer {m["serwer_mb"]:.0f} MB, procesy potomne '
                         f'{m["potomne_mb"]:.0f} MB; próg {self.prog_mb} MB)')
        if razem > self.prog_mb:
            t = self.zegar()
            if (self._ostatnie_ostrzezenie is None
                    or t - self._ostatnie_ostrzezenie >= PAMIEC_OSTRZEZENIE_CO_S):
                self._ostatnie_ostrzezenie = t
                self.rejestr('pamiec', f'pamięć ponad próg: {razem:.0f} MB > '
                             f'{self.prog_mb} MB (serwer {m["serwer_mb"]:.0f} MB, '
                             f'procesy potomne {m["potomne_mb"]:.0f} MB)', level='warn')
        return m

    def stan(self) -> dict:
        teraz = self.teraz_mb
        return {'teraz_mb': teraz, 'szczyt_mb': self.szczyt_mb, 'prog_mb': self.prog_mb,
                'przekroczony': teraz is not None and teraz > self.prog_mb}


_PAMIEC = None


async def _pamiec_petla():
    while True:
        try:
            _PAMIEC.krok()
        except Exception as e:           # pomiar nie może zatrzymać serwera
            _evlog('pamiec', f'pomiar nieudany: {type(e).__name__}: {e}', level='error')
        await asyncio.sleep(PAMIEC_CO_S)


async def _pamiec_start(app):
    app['pamiec_zadanie'] = asyncio.ensure_future(_pamiec_petla())


async def _pamiec_stop(app):
    z = app.get('pamiec_zadanie')
    if z is not None:
        z.cancel()


def _pamiec_html() -> str:
    """Stan pamięci w nagłówku rejestru zdarzeń (wyróżniony ponad progiem)."""
    if _PAMIEC is None:
        return ''
    s = _PAMIEC.stan()
    if s['teraz_mb'] is None:
        return (f'<span class="pamiec">Pamięć: brak pomiaru (/proc niedostępny), '
                f'próg {s["prog_mb"]} MB</span>')
    tekst = (f'Pamięć: teraz {s["teraz_mb"]:.0f} MB, szczyt {s["szczyt_mb"]:.0f} MB, '
             f'próg {s["prog_mb"]} MB')
    if s['przekroczony']:
        return ('<span class="pamiec pamiec-przekroczona" style="color:#ff6b6b;'
                f'font-weight:600">⚠ {tekst} — ponad próg</span>')
    return f'<span class="pamiec">{tekst}</span>'


def _fmt_size(s):
    if s >= 1 << 30:
        return f'{s/(1<<30):.1f} GB'
    if s >= 1 << 20:
        return f'{s>>20} MB'
    if s >= 1024:
        return f'{s>>10} KB'
    return f'{s} B'


def _fmt_time(ts):
    try:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return '—'


def render_events_page():
    """Widok rejestru zdarzeń/błędów (/?events=1) — najnowsze u góry."""
    try:
        lines = EVENT_LOG.read_text(encoding='utf-8', errors='replace').splitlines()
    except FileNotFoundError:
        lines = []
    rows = []
    for ln in lines[-300:][::-1]:
        p = ln.split('\t')
        if len(p) < 4:
            continue
        ts, level, kind, msg = p[0], p[1], p[2], '\t'.join(p[3:])
        col = {'error': '#ff6b6b', 'warn': '#e2b340'}.get(level, '#9fd29f')
        rows.append(f'<tr><td class=t>{_html.escape(ts)}</td>'
                    f'<td style="color:{col}">{_html.escape(level)}</td>'
                    f'<td>{_html.escape(kind)}</td>'
                    f'<td>{_html.escape(msg)}</td></tr>')
    body = ''.join(rows) or '<tr><td colspan=4>brak zdarzeń</td></tr>'
    return web.Response(content_type='text/html', text=(
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Rejestr zdarzeń</title>'
        '<style>body{margin:0;background:#14161c;color:#cfd6df;'
        'font-family:ui-monospace,monospace;font-size:13px}'
        '.bar{padding:.6em 1em;background:#1d2027;display:flex;gap:1em;'
        'align-items:center}.bar a{color:#7ab7ff;text-decoration:none}'
        'table{width:100%;border-collapse:collapse}'
        'td{padding:.25em .7em;border-bottom:1px solid #23262d;'
        'vertical-align:top}td.t{white-space:nowrap;color:#8a93a0}'
        'th{position:sticky;top:0;background:#1d2027;text-align:left;'
        'padding:.4em .7em}</style>'
        f'<div class="bar"><a href="./">📁 folder</a>{_link_startowy()}'
        '<b>Rejestr zdarzeń i błędów</b>'
        + _pamiec_html() +
        '<span style="color:#8a93a0;margin-left:auto">'
        f'{len(lines)} wpisów · auto-odświeżanie 10 s</span></div>'
        '<table><tr><th>czas</th><th>poziom</th><th>typ</th><th>opis</th></tr>'
        + body + '</table>'
        '<script>setTimeout(()=>location.reload(),10000)</script>'))


def _dir_children(p):
    try:
        return sorted((d for d in p.iterdir()
                       if d.is_dir() and not d.name.startswith('.')),
                      key=lambda d: _natkey(d.name))
    except Exception:
        return []


def _tree_html(p, depth=0, maxdepth=10):
    kids = _dir_children(p)
    if not kids or depth >= maxdepth:
        return ''
    out = ['<ul>']
    for d in kids:
        try:
            rel = d.resolve().relative_to(ROOT.resolve()).as_posix()
        except Exception:
            continue
        url = '/' + quote(rel, safe='/') + '/'
        # [katalogi] bez_drzewa: wpis katalogu pierwszego poziomu zostaje, dzieci nie
        if depth == 0 and d.name in KONF.bez_drzewa:
            sub = ''
        else:
            sub = _tree_html(d, depth + 1, maxdepth)
        tog = ('<span class=tg onclick="tg(this)">▸</span>' if sub
               else '<span class="tg e"></span>')
        out.append(f'<li data-p="{url}">{tog}'
                   f'<a href="#" onclick="op(\'{url}\');return false">'
                   f'📁 {_html.escape(d.name)}</a>{sub}</li>')
    out.append('</ul>')
    return ''.join(out)


def render_explorer_page(start_url='/'):
    """Explorer: zwijane drzewo folderów (chowane) + 1 lub 2 panele-iframe
    (lewy/prawy) z istniejącym listingiem — przełączane on/off."""
    tree = _tree_html(ROOT)
    su = _html.escape(start_url or '/')
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Drzewo — AnberFiles</title><style>'
        'html,body{margin:0;height:100%;font-family:system-ui,sans-serif}'
        'body{display:flex;flex-direction:column}'
        '.tb{display:flex;gap:.5em;align-items:center;padding:.4em .8em;'
        'background:#26292f;color:#ddd;font-size:.9em;flex-wrap:wrap}'
        '.tb a,.tb button{color:#7ab7ff;background:none;border:1px solid #3a3f47;'
        'border-radius:5px;padding:.2em .6em;cursor:pointer;font:inherit;'
        'text-decoration:none}.tb button.on{background:#1a5fb4;'
        'border-color:#1a5fb4;color:#fff}'
        '.main{flex:1;display:flex;min-height:0}'
        '#tree{width:260px;overflow:auto;background:#1d2027;color:#cfd6df;'
        'padding:.5em;font-size:.9em;white-space:nowrap}#tree.hidden{display:none}'
        '#tree ul{list-style:none;margin:0;padding-left:1.1em}'
        '#tree>ul{padding-left:.2em}#tree li ul{display:none}'
        '#tree li.open>ul{display:block}'
        '#tree a{color:#cfd6df;text-decoration:none;cursor:pointer}'
        '#tree a:hover{color:#7ab7ff}'
        '#tree li.cur>a{color:#fff;background:#1a5fb4;border-radius:4px;'
        'padding:0 .25em}.tg{display:inline-block;width:1em;'
        'cursor:pointer;color:#8a93a0}.tg.e{cursor:default}'
        '.panes{flex:1;display:flex;min-width:0}.pane{flex:1;display:flex;'
        'flex-direction:column;min-width:0;border-left:2px solid #26292f}'
        '.pane.act{border-left-color:#1a5fb4}.pane .ph{font-size:.8em;'
        'padding:.2em .5em;background:#2b2f37;color:#9fb;cursor:pointer}'
        '.pane iframe{flex:1;width:100%;border:0}#paneB{display:none}</style>'
        '<div class="tb"><a href="' + su + '">← powrót</a>'
        '<button id="bt" class="on" onclick="ht()">☰ drzewo</button>'
        '<button id="bb" onclick="tpb()">⧉ druga karta</button>'
        '<span style="margin-left:.5em">ładuj do:</span>'
        '<button id="ta" class="on" onclick="setT(\'A\')">◧ lewa</button>'
        '<button id="tb2" onclick="setT(\'B\')">▣ prawa</button></div>'
        '<div class="main"><div id="tree">' + tree + '</div>'
        '<div class="panes">'
        '<div class="pane act" id="paneA"><div class="ph" onclick="setT(\'A\')">'
        'lewa</div><iframe id="ifA" src="' + su + '"></iframe></div>'
        '<div class="pane" id="paneB"><div class="ph" onclick="setT(\'B\')">'
        'prawa</div><iframe id="ifB" src=""></iframe></div></div></div>'
        '<script>'
        # SYSTEMOWO: CAŁY stan UI w localStorage -> przeżywa F5/odświeżenie
        # (rozwinięcie drzewa, ukrycie drzewa, drugi panel, aktywny panel, URL
        # obu paneli). Snapshot s0 czytany RAZ na starcie -> brak wyścigu z
        # load-eventem iframe'a, który by go nadpisał przed odtworzeniem.
        'const SK="explorerState";'
        'function L(){try{return JSON.parse(localStorage.getItem(SK)||"{}");}'
        'catch(e){return {};}}'
        'function Sv(p){const s=L();Object.assign(s,p);'
        'try{localStorage.setItem(SK,JSON.stringify(s));}catch(e){}}'
        'const ifA=document.getElementById("ifA");'
        'const ifB=document.getElementById("ifB");'
        'let act="A";'
        'function op(u){(act==="A"?ifA:ifB).src=u;}'
        # rozwiń drzewo do folderu AKTYWNEGO panelu + podświetl (.cur)
        'function dec(u){try{return decodeURIComponent(u);}catch(e){return u;}}'
        'function paneLoc(){try{const f=(act==="A"?ifA:ifB);'
        'let p=f.contentWindow.location.pathname;'
        'return p.endsWith("/")?p:p+"/";}catch(e){return null;}}'
        'function expandToActive(){const u=paneLoc();'
        'document.querySelectorAll("#tree li.cur").forEach(x=>'
        'x.classList.remove("cur"));if(!u)return;const du=dec(u);'
        'const li=[...document.querySelectorAll("#tree li[data-p]")].find('
        'x=>dec(x.dataset.p)===du);if(!li)return;let c=li;'
        'while(c&&c.id!=="tree"){if(c.tagName==="LI"){c.classList.add("open");'
        'const t=c.querySelector(":scope>.tg");'
        'if(t&&!t.classList.contains("e"))t.textContent="▾";}c=c.parentElement;}'
        'li.classList.add("cur");saveOpen();'
        'const a=li.querySelector(":scope>a");'
        'if(a)a.scrollIntoView({block:"center"});}'
        'function saveOpen(){Sv({treeOpen:[...document.querySelectorAll'
        '("#tree li.open")].map(li=>li.dataset.p).filter(Boolean)});}'
        'function tg(el){const li=el.parentNode;li.classList.toggle("open");'
        'el.textContent=li.classList.contains("open")?"▾":"▸";saveOpen();}'
        'function ht(){const t=document.getElementById("tree");'
        't.classList.toggle("hidden");'
        'document.getElementById("bt").classList.toggle("on");'
        'Sv({treeHidden:t.classList.contains("hidden")});'
        'if(!t.classList.contains("hidden"))expandToActive();}'
        'function showB(on){const b=document.getElementById("paneB");'
        'b.style.display=on?"flex":"none";'
        'document.getElementById("bb").classList.toggle("on",on);'
        'if(on&&!ifB.getAttribute("src"))ifB.src="/";}'
        'function tpb(){const on=getComputedStyle('
        'document.getElementById("paneB")).display==="none";'
        'showB(on);Sv({paneB:on});}'
        'function setT(p){act=p;'
        'document.getElementById("paneA").classList.toggle("act",p==="A");'
        'document.getElementById("paneB").classList.toggle("act",p==="B");'
        'document.getElementById("ta").classList.toggle("on",p==="A");'
        'document.getElementById("tb2").classList.toggle("on",p==="B");'
        'Sv({act:p});expandToActive();}'
        # zapis URL paneli przy nawigacji WEWNĄTRZ iframe (same-origin)
        'ifA.addEventListener("load",function(){try{Sv({srcA:'
        'ifA.contentWindow.location.pathname'
        '+ifA.contentWindow.location.search});if(act==="A")expandToActive();'
        '}catch(e){}});'
        'ifB.addEventListener("load",function(){try{'
        'if(ifB.getAttribute("src")){Sv({srcB:'
        'ifB.contentWindow.location.pathname'
        '+ifB.contentWindow.location.search});if(act==="B")expandToActive();}'
        '}catch(e){}});'
        # ODTWORZENIE ze snapshotu s0 (raz, na starcie)
        '(function(){const s0=L();'
        'const o=new Set(s0.treeOpen||[]);'
        'document.querySelectorAll("#tree li[data-p]").forEach(li=>{'
        'if(o.has(li.dataset.p)){li.classList.add("open");'
        'const t=li.querySelector(":scope>.tg");if(t)t.textContent="▾";}});'
        'if(s0.treeHidden){document.getElementById("tree").classList.add("hidden");'
        'document.getElementById("bt").classList.remove("on");}'
        'if(s0.srcA)ifA.src=s0.srcA;'
        'if(s0.paneB){showB(true);if(s0.srcB)ifB.src=s0.srcB;}'
        'if(s0.act&&s0.act!=="A")setT(s0.act);})();'
        '</script>')


# Favikona serwera plików: D-pad (gaming/retro). SVG = ostry na każdym DPI;
# ICO (raster 16/32/48/64) = uniwersalny auto-fetch /favicon.ico na każdej stronie.
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#1b0f33"/>'
    '<path d="M13 6h6v7h7v6h-7v7h-6v-7H6v-6h7z" fill="#9d6bff"/>'
    '<circle cx="16" cy="16" r="2.1" fill="#1b0f33"/>'
    '</svg>')
FAVICON_LINK = ('<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
                '<link rel="alternate icon" href="/favicon.ico">')


_ZIP_LOCK = asyncio.Lock()   # jeden zip naraz (A53: I/O + RAM)


class _ZipZaDuzy(Exception):
    """Łączny rozmiar plików folderu przekracza limit_zip_mb."""


def _pliki_do_zip(target: Path, limit_b: int) -> list:
    """[(plik, nazwa w archiwum)] — bez dowiązań symbolicznych, tylko pliki,
    których rzeczywista ścieżka leży w pakowanym katalogu (a więc w ROOT);
    pomija .kosz, dotfiles, .part i sidecary. Suma rozmiarów > limit_b →
    _ZipZaDuzy, ZANIM cokolwiek zostanie zapisane."""
    target = target.resolve()
    base = target.name
    wynik, razem = [], 0
    for f in sorted(target.rglob('*')):
        try:
            if f.is_symlink() or not f.is_file():
                continue
            relparts = f.relative_to(target).parts
            if '.kosz' in f.parts or any(p.startswith('.') for p in relparts):
                continue
            if f.name.endswith(('.part', '.meta.json', '.resume.json')):
                continue
            rzecz = f.resolve()
            if target not in rzecz.parents or ROOT not in rzecz.parents:
                continue                   # dowiązanie wyżej w ścieżce → poza katalog
            razem += f.stat().st_size
        except (OSError, ValueError):
            continue
        if razem > limit_b:
            raise _ZipZaDuzy(razem)
        wynik.append((f, base + '/' + '/'.join(relparts)))
    return wynik


def _build_zip(target: Path, out_path: str, limit_b: int):
    """Pakuje rekurencyjnie zawartość katalogu do out_path (synchronicznie — wołane
    w executorze, by nie blokować pętli). ZIP_STORED: bez rekompresji (zdjęcia i tak
    skompresowane; szybciej na A53). Lista plików i limit: _pliki_do_zip."""
    import zipfile
    pliki = _pliki_do_zip(target, limit_b)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
        for f, nazwa in pliki:
            try:
                zf.write(f, nazwa)
            except (OSError, ValueError):
                continue


async def _zip_dir(request, target: Path):
    """GET <katalog>?zip=1 → spakowanie folderu do .zip i strumieniowe pobranie.
    Zip budowany do pliku tymczasowego w katalog_zip_tmp (NIE /tmp = tmpfs/RAM), usuwany
    po wysłaniu. Budowa w executorze + lock (jeden naraz)."""
    import os
    import tempfile
    import time
    tmpdir = KONF.katalog_zip_tmp
    tmpdir.mkdir(parents=True, exist_ok=True)
    _now = time.time()                       # sprzątnij osierocone zipy (>1 h, np. po restarcie)
    for _old in tmpdir.glob('*.zip'):
        try:
            if _now - _old.stat().st_mtime > 3600:
                _old.unlink()
        except OSError:
            pass
    fd, tmppath = tempfile.mkstemp(suffix='.zip', dir=str(tmpdir))
    os.close(fd)
    try:
        try:
            async with _ZIP_LOCK:
                await asyncio.get_event_loop().run_in_executor(
                    None, _build_zip, target, tmppath, KONF.limit_zip_mb * 1024 ** 2)
        except _ZipZaDuzy as e:
            _evlog('zip', f'odrzucono {target.relative_to(ROOT)}: ponad '
                   f'{KONF.limit_zip_mb} MB (co najmniej {e.args[0] >> 20} MB)', level='warn')
            return web.Response(status=413, text=f'413 — folder większy niż '
                                f'{KONF.limit_zip_mb} MB (limit_zip_mb w ustawieniach); '
                                'pobierz mniejszy podkatalog.')
        size = os.path.getsize(tmppath)
        resp = web.StreamResponse(headers={
            'Content-Type': 'application/zip',
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(target.name + '.zip'),
            'Content-Length': str(size),
            'Cache-Control': 'no-store',
        })
        await resp.prepare(request)
        with open(tmppath, 'rb') as fh:
            while True:
                chunk = fh.read(256 * 1024)
                if not chunk:
                    break
                await resp.write(chunk)
        await resp.write_eof()
        _evlog('zip', f'{target.relative_to(ROOT)} → {target.name}.zip ({size // 1024} KB)')
        return resp
    finally:
        try:
            os.unlink(tmppath)
        except OSError:
            pass


# ── Odczyt katalogu z limitem czasu (zdalne systemy plików) ──────────────────
# Osobna pula wątków: zawieszone wywołanie systemowe zajmuje wątek aż do
# odmontowania zasobu, więc nie może wyczerpać puli domyślnej pętli.
_EXEC_FS = ThreadPoolExecutor(max_workers=8, thread_name_prefix='anberfiles-fs')


class _OdczytKatalogu(Exception):
    def __init__(self, odpowiedz):
        super().__init__()
        self.odpowiedz = odpowiedz


def _proba_katalogu(p):
    """Próba odczytu: scandir + stat() każdego wpisu (to, co zrobi listing).
    503 tylko gdy nie da się otworzyć/iterować SAMEGO katalogu."""
    with os.scandir(p) as it:
        for e in it:
            try:
                e.stat()
            except OSError:
                # błąd pojedynczego wpisu (zerwany symlink, EPERM na sshfs)
                # nie unieważnia katalogu — listing pomija go sam; zawieszony
                # stat() nadal liczy się do limitu czasu (504)
                continue


def _zbadaj_sciezke(raw):
    """(cel, istnieje, jest_katalogiem); dla katalogu także próba odczytu.
    Blokujące — wołać tylko przez _zbadaj_z_limitem."""
    target = (ROOT / raw).resolve()
    if ROOT not in target.parents and target != ROOT:
        return target, False, False
    if not target.exists():
        return target, False, False
    jest_kat = target.is_dir()
    if jest_kat:
        _proba_katalogu(target)
    return target, True, jest_kat


def _strona_katalogu_niedostepnego(raw, status, tytul, przyczyna=''):
    rodzic = '/'.join(raw.split('/')[:-1])
    rodzic_url = '/' + quote(rodzic, safe='/') + ('/' if rodzic else '')
    szczegol = (f'<p>Przyczyna: {_html.escape(przyczyna)}</p>' if przyczyna else '')
    html = ('<!doctype html><meta charset=utf-8>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Katalog niedostępny — AnberFiles</title>'
            '<body style="font-family:system-ui,sans-serif;margin:1.5em">'
            f'<h2>{_html.escape(tytul)}</h2>{szczegol}'
            f'<p><a href="{_html.escape(rodzic_url)}">↑ katalog nadrzędny</a></p>')
    return web.Response(status=status, text=html, content_type='text/html',
                        headers={'Cache-Control': 'no-store'})


async def _zbadaj_z_limitem(raw):
    """Wynik _zbadaj_sciezke albo _OdczytKatalogu z odpowiedzią 504 (limit
    czasu) / 503 (błąd systemu plików). Inne wyjątki (np. zły znak w ścieżce)
    przechodzą dalej."""
    limit = KONF.limit_odczytu_katalogu_s
    loop = asyncio.get_running_loop()
    opis_sciezki = '/' + raw
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_EXEC_FS, _zbadaj_sciezke, raw), limit)
    except asyncio.TimeoutError:
        _evlog('katalog', f'{opis_sciezki}: brak odpowiedzi w {limit:g} s '
                          '(504)', 'warn')
        raise _OdczytKatalogu(_strona_katalogu_niedostepnego(
            raw, 504, f'Katalog nie odpowiada w {limit:g} s — urządzenie zdalne '
                      'jest wyłączone albo poza siecią. Spróbuj ponownie za '
                      'chwilę.')) from None
    except OSError as e:
        przyczyna = e.strerror or type(e).__name__
        _evlog('katalog', f'{opis_sciezki}: błąd odczytu ({przyczyna}) (503)', 'warn')
        raise _OdczytKatalogu(_strona_katalogu_niedostepnego(
            raw, 503, 'Katalog nie jest dostępny — urządzenie zdalne jest '
                      'wyłączone albo poza siecią. Spróbuj ponownie za chwilę.',
            przyczyna)) from None


async def serve(request):
    # widok/stan kolejki lektora (dostępny z dowolnej ścieżki)
    if 'lektorq' in request.query or 'lektorqj' in request.query:
        odm = _modul_wylaczony('lektor')
        if odm is not None:
            return odm
    if 'lektorq' in request.query:
        return web.Response(text=LEKTORQ_PAGE, content_type='text/html')
    if 'lektorqj' in request.query:
        return web.json_response(_lektor_queue_json())
    if 'docxprog' in request.query:
        odm = _modul_wylaczony('eksport_docx')
        if odm is not None:
            return odm
        try:
            import json as _j
            return web.json_response(
                _j.loads(Path('/tmp/docx_progress.json').read_text()),
                headers={'Cache-Control': 'no-store'})
        except Exception:
            return web.json_response({'pct': 0, 'done': 0, 'total': 0})
    if 'events' in request.query:
        return render_events_page()

    raw = request.match_info.get('path', '').strip('/')

    # favikona (D-pad) — działa na każdej stronie serwera
    if raw in ('favicon.ico', 'favicon.svg'):
        if raw == 'favicon.ico' and FAVICON_ICO_PATH.exists():
            return web.FileResponse(FAVICON_ICO_PATH, headers={
                'Content-Type': 'image/x-icon',
                'Cache-Control': 'public, max-age=86400'})
        return web.Response(text=FAVICON_SVG, content_type='image/svg+xml',
                            headers={'Cache-Control': 'public, max-age=86400'})

    # ustalenie ścieżki i próba odczytu katalogu — w wątku, z limitem czasu
    # (zdalny system plików potrafi zawisnąć; pętla zdarzeń nie może czekać)
    try:
        target, istnieje, jest_katalogiem = await _zbadaj_z_limitem(raw)
    except _OdczytKatalogu as e:
        return e.odpowiedz
    except Exception:
        return web.Response(status=400)
    # path traversal guard
    if ROOT not in target.parents and target != ROOT:
        return web.Response(status=403)
    if not istnieje:
        return web.Response(status=404, text=f'Not found: {raw}')

    if 'explorer' in request.query and jest_katalogiem:
        su = '/' + quote(raw, safe='/') + ('/' if raw else '')
        return web.Response(text=render_explorer_page(su),
                            content_type='text/html',
                            headers={'Cache-Control': 'no-cache'})

    # lista rodzeństwa audio (do odświeżania prev/next w odtwarzaczu na żywo)
    if 'siblings' in request.query and target.is_file():
        sibs = sorted((x.name for x in target.parent.iterdir()
                       if x.is_file() and x.suffix.lower() in AUDIO_EXT
                       and not x.name.startswith('.')), key=_natkey)
        return web.json_response(sibs, headers={'Cache-Control': 'no-cache'})

    if 'zip' in request.query and jest_katalogiem:
        return await _zip_dir(request, target)

    if jest_katalogiem:
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), _natkey(p.name)))
        rel = target.relative_to(ROOT)
        rel_url = f'/{rel}/' if str(rel) != '.' else '/'

        # breadcrumb — każdy segment klikalny; najechanie rozwija listę
        # folderów-RODZEŃSTWA z danego poziomu (skok w inną gałąź drzewa)
        crumbs = ['<a href="/" class="dir">📁 root</a>']
        acc = ''
        if str(rel) != '.':
            parent = ROOT
            for seg in str(rel).replace('\\', '/').split('/'):
                parent_url = quote(acc)          # ścieżka rodzica ('' dla 1. poziomu)
                acc += '/' + seg
                try:
                    sibs = sorted((d.name for d in parent.iterdir()
                                   if d.is_dir() and not d.name.startswith('.')),
                                  key=_natkey)
                except Exception:
                    sibs = []
                dd_items = []
                for s in sibs:
                    cls = ' class="cur"' if s == seg else ''
                    dd_items.append(f'<a href="{parent_url}/{quote(s)}/"{cls}>📁 {_esc(s)}</a>')
                dd = (f'<div class="dd">{"".join(dd_items)}</div>') if dd_items else ''
                crumbs.append(f'<span class="crumb">'
                              f'<a href="{quote(acc)}/">{_esc(seg)}</a>{dd}</span>')
                parent = parent / seg
        breadcrumb = ' <span class="sep">/</span> '.join(crumbs) + ' <span class="sep">/</span>'

        rows = []
        n = 0
        zapis = not KONF.tylko_odczyt            # 🗑 ✎ 📁+ i upuszczanie plików
        lektor_on = KONF.modul('lektor')
        docx_view = KONF.modul('podglad_docx')
        if target != ROOT:
            rows.append('<tr class="up"><td><a href="../" class="dir">📁 ..</a></td>'
                        '<td data-sort="-2">—</td><td data-sort="0"></td>'
                        '<td data-sort="0"></td></tr>')
        for item in items:
            if item.name.endswith(('.meta.json', '.resume.json')) or item.name.startswith('.'):
                continue
            try:
                st = item.stat()
                bt = getattr(st, 'st_birthtime', st.st_ctime)   # crtime jeśli dostępny, inaczej ctime
                mt_s, bt_s = _fmt_time(st.st_mtime), _fmt_time(bt)
                if item.is_dir():
                    dq = quote(item.name)
                    rows.append(
                        f'<tr data-name="{_html.escape(item.name, quote=True)}">'
                        '<td>'
                        + (f'<a href="#" class="dl del" data-n="{dq}" title="Usuń (do .kosz; tylko pusty katalog)">🗑</a>'
                           f'<a href="#" class="dl ren" data-n="{dq}" title="Zmień nazwę">✎</a>'
                           if zapis else '')
                        + f'<a href="#" class="dl cpy" data-n="{dq}" title="Kopiuj nazwę">⧉</a>'
                        f'<a href="{dq}/?zip=1" class="dl" title="Pobierz folder jako .zip">🗜</a>'
                        f'<a href="{dq}/" class="dir">📁 {_esc(item.name)}/</a></td>'
                        f'<td data-sort="-1">—</td>'
                        f'<td data-sort="{st.st_mtime:.0f}">{mt_s}</td>'
                        f'<td data-sort="{bt:.0f}">{bt_s}</td></tr>')
                else:
                    s = st.st_size
                    icon = ICONS.get(item.suffix.lower().lstrip('.'), '📄')
                    q = quote(item.name)
                    # zdjęcia → przeglądarka z nawigacją; reszta → plik wprost
                    ext = item.suffix.lower()
                    href = (f'{q}?view=1'
                            if (ext in IMG_EXT or ext in AUDIO_EXT
                                or ext in VIDEO_EXT or ext in MODEL_EXT
                                or ext in CSV_EXT or ext in XLSX_EXT
                                or ext == '.md'
                                or (ext in ('.docx', '.doc') and docx_view))
                            else q)
                    lek = ('<a href="#" class="dl lek" data-n="' + q
                           + '" title="Lektor → audio">🔊</a>'
                           if lektor_on and ext in ('.md', '.docx', '.txt') else '')
                    # audio lektora w osobnym katalogu — skrót przy dokumencie
                    if ext in ('.md', '.docx', '.txt') and KONF.katalog_lektora:
                        _aud = _lektor_audio_for(item, exports=True)
                        if _aud is not None and _aud.parent != item.parent:
                            lek += (f'<a href="{_url_abs(_aud)}?view=1" class="dl" '
                                    f'title="Odtwórz nagranie lektora">🎧</a>')
                    rows.append(
                        f'<tr data-name="{_html.escape(item.name, quote=True)}">'
                        f'<td><a href="{q}?dl=1" class="dl" title="Pobierz">⬇</a>'
                        + (f'<a href="#" class="dl del" data-n="{q}" title="Usuń (do .kosz)">🗑</a>'
                           f'<a href="#" class="dl ren" data-n="{q}" title="Zmień nazwę">✎</a>'
                           if zapis else '')
                        + f'<a href="#" class="dl cpy" data-n="{q}" title="Kopiuj nazwę">⧉</a>'
                        f'{lek}'
                        f'<a href="{href}">{icon} {_esc(item.name)}</a></td>'
                        f'<td data-sort="{s}">{_fmt_size(s)}</td>'
                        f'<td data-sort="{st.st_mtime:.0f}">{mt_s}</td>'
                        f'<td data-sort="{bt:.0f}">{bt_s}</td></tr>')
                n += 1
            except Exception:
                pass

        html = [
            '<!doctype html><meta charset=utf-8>',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            FAVICON_LINK,
            f'<title>{_esc(rel_url)}</title>',
            f'<style>{STYLE}</style>',
            f'<h2>{breadcrumb}</h2>',
            f'<p class="muted">{n} pozycji · kliknij nagłówek aby sortować · '
            f'⟳ auto-odświeżanie'
            + (' · <a href="/?lektorq=1">🔊 kolejka lektora</a>' if lektor_on else '')
            + ' · <a href="?explorer=1">🌳 drzewo</a>'
            + (f' · {_link_startowy()}' if _link_startowy() else '')
            + (' · <a href="#" id="mkd">📁+ nowy folder</a>' if zapis else '')
            + (f' · <span class="muted">🔒 tylko odczyt ({_html.escape(KONF.nazwa_instancji)})</span>'
               if not zapis else '')
            +
            ' · <a href="?zip=1" title="Pobierz CAŁY ten folder jako .zip">🗜 ZIP folderu</a>'
            ' · <a href="#" id="cpcol" title="Skopiuj nazwy wszystkich plików (po jednej w wierszu)">⧉ kopiuj nazwy plików</a>'
            + (f' · <a href="{_LOGOWANIE.SCIEZKA_WYLOGOWANIA}" title="Usuń zapamiętanie tego '
               'urządzenia">⎋ wyloguj</a>' if KONF.logowanie == 'formularz' else '')
            + f'{_battery_html()}</p>',
            '<table><thead><tr>'
            '<th>Nazwa</th><th>Rozmiar</th><th>Modyfikacja</th><th>Utworzono</th>'
            '</tr></thead><tbody>',
            *rows,
            '</tbody></table>',
            SORT_JS,
            DROP_JS if zapis else '',
            '' if KONF.modul('lektor_opisy_ai') else '<script>window._LEK_OPISY=false;</script>',
            _lektor_uwaga_js() if lektor_on else '',
            DEL_JS,
            LEKTOR_BAR_JS if lektor_on else '',
            MKDIR_JS if zapis else '',
            COPYCOL_JS,
        ]
        return web.Response(text='\n'.join(html), content_type='text/html')

    # plik — ?dl=1 wymusza pobieranie, ?view=1 dla zdjęć otwiera przeglądarkę
    return await _serve_file(request, target)


async def delete_item(request):
    """DELETE na pliku = przeniesienie do kosza (klucz kosz; domyślnie ROOT/.kosz/,
    nic nie znika trwale). Katalogi: tylko puste (rmdir). .kosz ukryty w listingu."""
    odm = _odmowa_zapisu()
    if odm is not None:
        return odm
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents:          # ROOT samego nie ruszamy
        return web.Response(status=403)
    if not target.exists():
        return web.Response(status=404)
    if target.is_dir():
        if any(target.iterdir()):
            return web.Response(status=400, text='Katalog niepusty')
        target.rmdir()
        return web.json_response({'deleted': raw})
    trash = KONF.kosz
    trash.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = trash / f'{ts}_{target.name}'
    shutil.move(str(target), str(dest))
    return web.json_response({'deleted': raw, 'kosz': dest.name})


_LEKTOR_LOCK = None        # asyncio.Lock tworzony leniwie (jeden lektor naraz)
_LEKTOR_SEQ = 0
_LEKTOR_QUEUE: list = []   # rejestr zadań: id/out/src/fmt/state/cancelled/proc
_LEKTOR_PAUSED = False     # pauza całej kolejki (⏸ w widoku kolejki)
_LEKTOR_SHUTDOWN = False   # „wyłącz konsolę po ukończeniu kolejki" (⏻)
_LEKTOR_BLEDY: list = []   # ostatnie nieudane zadania (widok kolejki) — nigdy cisza
_LEKTOR_BLEDY_MAX = 20




def _lektor_save_queue():
    """Trwałość kolejki: przeżywa restart serwera i REBOOT konsoli
    (na starcie zadania wracają; czytaj_tts wznawia z checkpointu chunków)."""
    import json
    try:
        LEKTOR_QFILE.write_text(json.dumps({
            'paused': _LEKTOR_PAUSED,
            'shutdown': _LEKTOR_SHUTDOWN,
            'jobs': [{'src': j['src'], 'out': j['out'], 'fmt': j['fmt'],
                      'opisy': j.get('opisy', ''), 'silnik': j.get('silnik', '')}
                     for j in _LEKTOR_QUEUE if not j['cancelled']]},
            ensure_ascii=False))
    except Exception:
        pass


async def _lektor_restore(app):
    """on_startup: odtwórz kolejkę z dysku. Zadanie, które właśnie generuje
    osierocony proces (KillMode=process), pomijamy — dokończy się samo."""
    import json
    global _LEKTOR_PAUSED, _LEKTOR_SHUTDOWN
    try:
        data = json.loads(LEKTOR_QFILE.read_text())
    except Exception:
        return
    _LEKTOR_PAUSED = bool(data.get('paused'))
    # flaga ⏻ z pliku kolejki działa tylko tam, gdzie moduł wyłączania włączony
    _LEKTOR_SHUTDOWN = bool(data.get('shutdown')) and KONF.modul('wylaczanie')
    if KONF.modul('wylaczanie'):
        asyncio.ensure_future(_lektor_shutdown_watch())
    prog = _lektor_progress()
    busy_stem = Path(prog['out']).stem if prog else None
    for it in data.get('jobs', []):
        if not Path(it['src']).exists():
            continue
        if busy_stem and Path(it['out']).stem == busy_stem \
                and _ext_lektor_running():
            continue   # już generowane przez osierocony/zewnętrzny proces
        _lektor_new_job(it['src'], Path(it['out']), it['fmt'],
                        it.get('opisy', ''), it.get('silnik', ''))


def _lektor_new_job(src, out, fmt, opisy='', silnik='') -> dict:
    """Rejestracja zadania + start workera (wspólne dla 🔊 i „przejdź
    do następnego" przy pauzie)."""
    global _LEKTOR_SEQ, _LEKTOR_LOCK
    if _LEKTOR_LOCK is None:
        _LEKTOR_LOCK = asyncio.Lock()
    _LEKTOR_SEQ += 1
    import time as _t
    job = {'id': _LEKTOR_SEQ, 'out': str(out), 'src': str(src), 'fmt': fmt,
           'opisy': opisy, 'silnik': silnik, 'state': 'queued', 'cancelled': False,
           'proc': None, 'started': _t.time()}
    _LEKTOR_QUEUE.append(job)
    _lektor_save_queue()
    asyncio.ensure_future(_lektor_run(job))
    return job


def _lektor_blad(job: dict, powod: str):
    """Nieudane zadanie lektora: stan failed, wpis w bledy_lektora, w rejestrze
    zdarzeń i na liście błędów widoku kolejki. Nigdy nie rzuca."""
    import time as _t
    job['state'] = 'failed'
    job['blad'] = powod
    _LEKTOR_BLEDY.append({'id': job['id'], 'out': Path(job['out']).name,
                          'src': _lektor_src_rel(job['src']), 'state': 'failed',
                          'blad': powod, 'czas': _t.strftime('%Y-%m-%d %H:%M:%S')})
    del _LEKTOR_BLEDY[:-_LEKTOR_BLEDY_MAX]
    try:
        with open(KONF.bledy_lektora, 'ab') as f:
            f.write(f'\n=== {Path(job["out"]).name} === BŁĄD: {powod}\n'.encode())
    except OSError:
        pass
    _evlog('lektor', f'{Path(job["out"]).name}: {powod}', level='error')


def _lektor_sprawdz_zapis(out: Path):
    """Czy katalog wyniku przyjmie plik: utworzenie katalogu + plik próbny.
    Zwraca opis błędu albo None."""
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        proba = out.parent / f'.{out.name}.proba'
        proba.write_bytes(b'')
        proba.unlink()
    except OSError as e:
        return f'katalog wyniku {out.parent} nie przyjmuje zapisu: {e}'
    return None


def _lektor_ogon_logu(n: int = 300) -> str:
    """Ostatnie znaki logu błędów lektora (powód padu do widoku kolejki)."""
    try:
        with open(KONF.bledy_lektora, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode('utf-8', 'replace').strip().splitlines()[-1]
    except (OSError, IndexError):
        return ''


async def _lektor_run(job: dict):
    out = Path(job['out'])
    shutdown = False
    try:
        async with _LEKTOR_LOCK:
            if job['cancelled']:
                return
            # czekaj: pauza kolejki ORAZ lektor spoza serwera (agent/CLI)
            while _LEKTOR_PAUSED or _ext_lektor_running():
                await asyncio.sleep(5)
                if job['cancelled']:
                    return
            job['state'] = 'running'
            import time as _t
            job['started'] = _t.time()
            blad = _lektor_sprawdz_zapis(out)
            if blad:
                _lektor_blad(job, blad)
                return
            # stderr do logu — bez tego pad lektora był niemy (incydent:
            # zadanie znikało z kolejki bez śladu i bez pliku)
            errlog = open(KONF.bledy_lektora, 'ab')
            errlog.write(f'\n=== {Path(job["out"]).name} ===\n'.encode())
            cmd = [sys.executable, CZYTAJ_TTS, job['src'], '-o', job['out'],
                   '--format', job['fmt']]
            if job.get('opisy') in ('tak', 'nie'):
                cmd += ['--opisy', job['opisy']]
            if job.get('silnik') in LEKTOR_SILNIKI:      # brak = wg ustawień lektora
                cmd += ['--silnik', job['silnik']]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=errlog)
            job['proc'] = proc
            await proc.wait()
            errlog.close()
            if proc.returncode not in (0, None) and not job['cancelled']:
                _lektor_blad(job, f'czytaj_tts.py zakończył się kodem '
                                  f'{proc.returncode}: {_lektor_ogon_logu()}')
            if job['cancelled']:
                # przerwane — sprzątnij TYLKO pliki powstałe w trakcie
                # TEGO zadania (mtime > start)
                for suf in ('.mp3', '.wav', '.flac'):
                    p = out.with_suffix(suf)
                    try:
                        if p.stat().st_mtime >= job['started']:
                            p.unlink()
                    except FileNotFoundError:
                        pass
    except asyncio.CancelledError:
        # shutdown/restart serwera — zadanie ma PRZETRWAĆ w pliku kolejki
        # (restore odtworzy je na starcie); nie nadpisujemy rejestru
        shutdown = True
        raise
    finally:
        try:
            _LEKTOR_QUEUE.remove(job)
        except ValueError:
            pass
        if not shutdown:
            _lektor_save_queue()
            _lektor_maybe_shutdown()


# C10: lektor uruchomiony POZA serwerem (agent, CLI) zapisuje swój PID do
# pliku obok rygla /tmp/lektor.lock (czytaj_tts.py: PID_FILE, po zdobyciu
# rygla — w pliku jest zawsze lektor, który akurat czyta). Serwer sygnałuje
# WYŁĄCZNIE ten PID i tylko, gdy /proc/<pid>/cmdline nadal zawiera
# czytaj_tts.py (PID mógł zostać ponownie użyty przez inny program). Dawniej
# pgrep -f trafiał w każdy proces z tą nazwą w linii poleceń.
LEKTOR_PID_PLIK = Path('/tmp/lektor.pid')
KATALOG_PROC = Path('/proc')
# numery sygnałów Linuksa (moduł signal na Windows ich nie ma — testy)
SYGNAL_KILL, SYGNAL_STOP, SYGNAL_CONT = 9, 19, 18


def _wyslij_sygnal(pid: int, sig: int) -> None:
    """Jedyne miejsce wysyłania sygnału do lektora zewnętrznego (testy podstawiają)."""
    os.kill(pid, sig)


def _ext_lektor_pids() -> set:
    """PID lektora zewnętrznego z pliku PID — zbiór pusty albo jednoelementowy.
    Warunki: liczba > 1, nie ten serwer, /proc/<pid>/cmdline zawiera
    czytaj_tts.py, comm = interpreter Pythona, nie nasze dziecko z kolejki."""
    try:
        pid = int(LEKTOR_PID_PLIK.read_text(encoding='ascii').strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return set()
    if pid <= 1 or pid == os.getpid():
        return set()
    try:
        cmd = (KATALOG_PROC / str(pid) / 'cmdline').read_bytes()
        comm = (KATALOG_PROC / str(pid) / 'comm').read_text().strip()
    except OSError:
        return set()
    if b'czytaj_tts.py' not in cmd or not comm.startswith('python'):
        return set()
    ours = {j['proc'].pid for j in _LEKTOR_QUEUE
            if j.get('proc') is not None and j['proc'].returncode is None}
    return {pid} - ours


def _ext_lektor_running() -> bool:
    return bool(_ext_lektor_pids())


def _ext_lektor_src() -> str:
    """Plik ŹRÓDŁOWY (.md/.docx/...) zewnętrznego lektora — odczytany z cmdline
    procesu (progress.json zna tylko `out`/mp3). Basename, by JS dokleił pasek
    także na wierszu pliku źródłowego. '' = brak."""
    for pid in _ext_lektor_pids():
        try:
            parts = (KATALOG_PROC / str(pid) / 'cmdline').read_bytes().split(b'\x00')
        except OSError:
            continue
        args = [p.decode('utf-8', 'replace') for p in parts if p]
        for i, a in enumerate(args):
            if a.endswith('czytaj_tts.py') and i + 1 < len(args):
                return Path(args[i + 1]).name
    return ''


def _lektor_busy() -> bool:
    global _LEKTOR_LOCK
    return bool(_LEKTOR_QUEUE) or _ext_lektor_running() \
        or (_LEKTOR_LOCK is not None and _LEKTOR_LOCK.locked())


def _lektor_progress():
    """Postęp bieżącej generacji (pisze czytaj_tts.py; lektor jest jeden,
    więc plik globalny wystarcza). None = brak danych."""
    import json
    import time as _t
    try:
        p = Path('/tmp/lektor_progress.json')
        if _t.time() - p.stat().st_mtime > 300:   # stęchły = po crashu
            return None
        return json.loads(p.read_text())
    except Exception:
        return None


def _lektor_src_rel(src) -> str:
    try:
        return Path(src).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return Path(src).name


def _lektor_queue_json() -> dict:
    prog = _lektor_progress()
    jobs = []
    for j in _LEKTOR_QUEUE:
        if j['cancelled'] or j['state'] == 'failed':
            continue
        e = {'id': j['id'], 'out': Path(j['out']).name,
             'src': _lektor_src_rel(j['src']),
             'fmt': j['fmt'], 'state': j['state']}
        if j['state'] == 'running' and prog:
            e['pct'] = prog.get('pct', 0)
            e['chunk'] = f"{prog.get('chunk', 0)}/{prog.get('chunks', 0)}"
        jobs.append(e)
    ext = _ext_lektor_running()
    out = {'jobs': jobs, 'external': ext, 'paused': _LEKTOR_PAUSED,
           'shutdown': _LEKTOR_SHUTDOWN, 'bat': _battery_html(),
           'wylaczanie': KONF.modul('wylaczanie'),
           'bledy': list(_LEKTOR_BLEDY)}
    if ext and prog and not any(j['state'] == 'running' for j in jobs):
        out['ext_pct'] = prog.get('pct', 0)
        out['ext_chunk'] = f"{prog.get('chunk', 0)}/{prog.get('chunks', 0)}"
        out['ext_out'] = prog.get('out', '')
        out['ext_src'] = _ext_lektor_src()
    return out


LEKTORQ_PAGE = (
    '<!doctype html><meta charset=utf-8>'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Kolejka lektora</title><style>'
    # styl 1:1 z listingiem serwera plików (jasny motyw)
    'body{font-family:system-ui,sans-serif;max-width:1000px;margin:1.2em auto;'
    'padding:0 1em;background:#f7f7f9;color:#222}'
    'h2{color:#333;font-size:1.1em;word-break:break-all}'
    'h2 a{text-decoration:none}h2 a:hover{text-decoration:underline}'
    '.sep{color:#999}'
    '.muted{color:#888;font-size:.85em}'
    'table{width:100%;border-collapse:collapse;background:#fff;'
    'border-radius:6px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}'
    'th,td{text-align:left;padding:.5em .7em;border-bottom:1px solid #eee;'
    'font-size:.92em}'
    'th{background:#eef2f7;user-select:none}'
    'tr:hover td{background:#f4f7fb}'
    '.run{color:#1d7a36;font-weight:600}.que{color:#9a7b00}'
    '.ext{color:#888;font-style:italic}'
    '.x{color:#c00;cursor:pointer;text-decoration:none;font-weight:700;'
    'opacity:.55}.x:hover{opacity:1}'
    '.empty{text-align:center;color:#888;padding:2em}'
    '.pb{width:130px;height:13px;background:#e4e8ee;border-radius:7px;'
    'overflow:hidden;display:inline-block;vertical-align:middle;'
    'margin-right:.5em}'
    '.pb>i{display:block;height:100%;background:linear-gradient(90deg,'
    '#3584e4,#33a02c);transition:width .8s}'
    '.pct{font-size:.85em;color:#888}'
    # mobile: bez kolumny źródła, duże pola dotyku dla ⏸/✕
    '@media (max-width:700px){'
    'body{margin:.6em auto;padding:0 .5em}'
    'th:nth-child(3),td:nth-child(3){display:none}'
    'th,td{padding:.8em .5em}'
    '.x,.p{font-size:1.3em;padding:.2em .3em}'
    '.pb{width:90px}'
    '}'
    '</style>'
    '<h2><a href="/" class="dir">📁 root</a> <span class="sep">/</span> '
    '🔊 Kolejka lektora</h2>'
    '<p class="muted">podgląd na żywo (co 3 s) · ✕ usuwa pozycję '
    '(trwająca generacja zostaje przerwana) · '
    '<a id="shd" href="#" style="text-decoration:none"></a>'
    ' · <span id="st"></span></p>'
    '<table><thead><tr><th>#</th><th>plik wynikowy</th><th>źródło</th>'
    '<th>format</th><th>stan</th><th></th></tr></thead>'
    '<tbody id="tb"></tbody></table>'
    '<div id="ebw" style="display:none"><h2 style="color:#c00">✖ Nieudane zadania '
    'lektora</h2><table><tbody id="eb"></tbody></table></div>'
    '<script>' + JS_ESC + JS_ZAPIS +
    'async function load(){'
    'try{const j=await(await fetch("/?lektorqj=1",{cache:"no-store"})).json();'
    'const tb=document.getElementById("tb");let h="";let i=0;'
    'function bar(p,c){return p==null?"GENERUJE":'
    '"<span class=pb><i style=\'width:"+p+"%\'></i></span>'
    '<span class=pct>"+p+"% ("+(c||"")+")</span>";}'
    'function acts(id,run){return "<td style=\'white-space:nowrap\'>"'
    '+(run?"<a class=p data-i="+id+" href=# title=\'Pauza\' '
    'style=\'margin-right:.6em;text-decoration:none\'>⏸</a>":"")'
    '+"<a class=x data-i="+id+" href=#>✕</a></td>";}'
    'if(j.paused){h+="<tr><td colspan=6 style=\'background:#fff7e0;'
    'color:#7a5b00;font-weight:600;text-align:center;padding:.7em\'>'
    '⏸ KOLEJKA WSTRZYMANA &nbsp; '
    '<a id=res href=# style=\'color:#1a5fb4\'>▶ wznów</a></td></tr>";}'
    'if(j.external){h+="<tr><td>—</td><td colspan=3>"'
    '+(j.ext_out?esc(j.ext_out)+" <span class=ext>(proces niezależny — '
    'zlecenie agenta albo kontynuacja po restarcie serwera)</span>"'
    ':"<span class=ext>lektor uruchomiony poza serwerem '
    '(agent Discord / CLI)</span>")'
    '+"</td><td class="+(j.paused?"que":"run")+">"'
    '+(j.paused?"⏸ wstrzymane":bar(j.ext_pct,j.ext_chunk))+"</td>"'
    '+acts("ext",!j.paused)+"</tr>";}'
    'for(const x of j.jobs){i++;'
    'const run=(x.state==="running");'
    'h+="<tr><td>"+i+"</td><td>"+esc(x.out)+"</td><td style=\'color:#888\'>"'
    '+esc(x.src)+"</td><td>"+esc(x.fmt)+"</td><td class="'
    '+(run?"run":"que")+">"'
    '+(x.state==="paused"?"⏸ wstrzymane":(run?bar(x.pct,x.chunk):"czeka"))'
    '+"</td>"+acts(x.id,run)+"</tr>";}'
    'if(!h)h="<tr><td colspan=6 class=empty>Kolejka pusta — lektor wolny</td></tr>";'
    'tb.innerHTML=h;'
    'document.getElementById("st").innerHTML='
    '"odświeżono "+new Date().toLocaleTimeString()+(j.bat||"");'
    'const sh=document.getElementById("shd");'
    'sh.style.display=(j.wylaczanie===false)?"none":"";'
    'const eb=document.getElementById("eb");let eh="";'
    'for(const b of (j.bledy||[]).slice().reverse()){'
    'eh+="<tr><td>✖</td><td>"+esc(b.out)+"</td><td style=\'color:#888\'>"+esc(b.src)'
    '+"</td><td colspan=3 style=\'color:#c00\'>"+esc(b.czas)+" — "+esc(b.blad)+"</td></tr>";}'
    'eb.innerHTML=eh;document.getElementById("ebw").style.display=eh?"":"none";'
    'sh.dataset.on=j.shutdown?"1":"0";'
    'sh.innerHTML=j.shutdown'
    '?"⏻ wyłącz konsolę po ukończeniu: <b style=\'color:#1d7a36\'>WŁĄCZONE</b>"'
    ':"⏻ wyłącz konsolę po ukończeniu: <b style=\'color:#888\'>wyłączone</b>";'
    '}catch(e){}}'
    # pauza: pytanie co dalej — następny plik czy wstrzymanie całej kolejki
    'function askPause(ext){return new Promise(res=>{'
    'const ov=document.createElement("div");'
    'ov.style.cssText="position:fixed;inset:0;background:rgba(0,0,0,.5);'
    'display:flex;align-items:center;justify-content:center;z-index:99";'
    'ov.innerHTML=\'<div style="background:#fff;border-radius:10px;'
    'padding:1.1em 1.4em;max-width:92vw;box-shadow:0 8px 30px rgba(0,0,0,.35)">'
    '<div style="font-weight:600;margin-bottom:.5em">⏸ Pauza generowania</div>'
    '<div style="color:#556;font-size:.9em;margin-bottom:1em">'
    'Co zrobić z bieżącym plikiem?</div>'
    '<div style="display:flex;flex-direction:column;gap:.5em">\'+'
    '(ext?"":\'<button data-m="skip">⏭ Przejdź do następnego pliku '
    '(ten wróci na koniec kolejki)</button>\')+'
    '\'<button data-m="hold">⏸ Wstrzymaj CAŁĄ kolejkę '
    '(wznowisz przyciskiem ▶)</button>'
    '<button data-m="x">Anuluj</button></div></div>\';'
    'ov.querySelectorAll("button").forEach(b=>{'
    'b.style.cssText="padding:.55em 1em;border:1px solid #b8c0cc;'
    'border-radius:6px;background:#f2f5f9;cursor:pointer;font-size:.95em;'
    'text-align:left";'
    'b.onclick=()=>{ov.remove();res(b.dataset.m==="x"?null:b.dataset.m);};});'
    'ov.onclick=e=>{if(e.target===ov){ov.remove();res(null);}};'
    'document.body.appendChild(ov);});}'
    'document.addEventListener("click",async e=>{'
    'const s=e.target.closest("a#shd");'
    'if(s){e.preventDefault();'
    'const to=s.dataset.on==="1"?"0":"1";'
    'if(to==="1"&&!confirm("Konsola WYŁĄCZY SIĘ automatycznie po '
    'ukończeniu wszystkich pozycji kolejki (1 min na anulowanie). '
    'Włączyć?"))return;'
    'let r=await afFetch("/?lektorqshutdown="+to,{method:"POST"});'
    'let j=await r.json().catch(()=>({}));'
    'if(j.status==="empty-queue"){'
    'if(confirm("UWAGA: kolejka jest PUSTA — konsola wyłączy się '
    'JUŻ ZA MINUTĘ, nie po przyszłych zadaniach.\\n\\n'
    'Na pewno wyłączyć konsolę teraz?"))'
    'await afFetch("/?lektorqshutdown=1&force=1",{method:"POST"});}'
    'load();return;}'
    'const r=e.target.closest("a#res");'
    'if(r){e.preventDefault();'
    'await afFetch("/?lektorqresume=1",{method:"POST"});load();return;}'
    'const p=e.target.closest("a.p");'
    'if(p){e.preventDefault();'
    'const m=await askPause(p.dataset.i==="ext");'
    'if(!m)return;'
    'await afFetch("/?lektorqpause="+p.dataset.i+"&mode="+m,{method:"POST"});'
    'load();return;}'
    'const a=e.target.closest("a.x");if(!a)return;e.preventDefault();'
    'if(!confirm("Usunąć tę pozycję z kolejki lektora?"+'
    '"\\n(trwająca generacja zostanie przerwana)"))return;'
    'await afFetch("/?lektorqdel="+a.dataset.i,{method:"POST"});load();});'
    'load();setInterval(load,3000);'
    '</script>')


def _lektor_conf(klucz: str) -> str:
    """Wartość klucza z lektor-ustawienia.conf ('' = brak linii albo pliku)."""
    try:
        for line in Path(LEKTOR_CONF).read_text(encoding='utf-8',
                                                errors='replace').splitlines():
            line = line.split('#', 1)[0]
            if '=' in line:
                k, v = line.split('=', 1)
                if k.strip().lower() == klucz:
                    return v.strip()
    except Exception:
        pass
    return ''


def _lektor_fmt() -> str:
    """Format z lektor-ustawienia.conf (mp3|wav|flac), domyślnie mp3."""
    v = _lektor_conf('format')
    return v if v in ('mp3', 'wav', 'flac') else 'mp3'


def _lektor_uwaga_silnika() -> str:
    """Napis w oknie lektora: czy tekst opuszcza urządzenie. Wybór silnika
    robi czytaj_tts.py (wybierz_silnik); serwer zna tylko ustawienie."""
    s = _lektor_conf('silnik').lower()
    if s == 'piper':
        return ''
    if s == 'edge' or s:
        return LEKTOR_UWAGA_EDGE
    return ('Silnik: lokalny Piper, gdy usługa działa; inaczej edge — wtedy tekst '
            'opuszcza urządzenie (usługa Microsoft).')


def _lektor_uwaga_js() -> str:
    import json
    u = _lektor_uwaga_silnika()
    return f'<script>window._LEK_UWAGA={json.dumps(u)};</script>' if u else ''


def _lektor_silnik() -> str:
    """Silnik z lektor-ustawienia.conf (edge|piper); '' = brak linii, wtedy
    czytaj_tts.py wybiera sam (Piper, gdy usługa odpowiada, inaczej edge)."""
    v = _lektor_conf('silnik').lower()
    return v if v in LEKTOR_SILNIKI else ''


# ── Pliki robocze (C8) ──────────────────────────────────────────────────────
# Nazwy w /tmp NIEPRZEWIDYWALNE (tempfile: losowa część, prawa 0600/0700) —
# inny użytkownik maszyny nie podłoży dowiązania pod znaną nazwę, a dwa
# równoległe zadania dla tego samego pliku nie nadpisują sobie wyników.
# Profile LibreOffice (stan między uruchomieniami) — w katalogu danych instancji.
# /tmp/lektor.lock, /tmp/lektor.pid i /tmp/lektor_progress.json zostają: to
# rygiel i stan dzielone z lektorem uruchamianym poza serwerem.

def tmp_plik(stem: str, sufiks: str) -> Path:
    """Nowy pusty plik roboczy <stem>_<losowe><sufiks> w katalogu tymczasowym."""
    import tempfile
    fd, sciezka = tempfile.mkstemp(prefix=f'{stem}_', suffix=sufiks)
    os.close(fd)
    return Path(sciezka)


def tmp_katalog(prefiks: str):
    """Katalog roboczy usuwany po wyjściu z bloku with (TemporaryDirectory)."""
    import tempfile
    return tempfile.TemporaryDirectory(prefix=f'anberfiles-{prefiks}-')


def profil_lo(nazwa: str) -> str:
    """Argument -env:UserInstallation dla soffice: profil w katalogu danych."""
    return '-env:UserInstallation=' + (KONF.katalog_danych / nazwa).resolve().as_uri()


# ── LibreOffice bez sieci (C9) ──────────────────────────────────────────────
# soffice konwertuje dokumenty z treści katalogu; pole INCLUDEPICTURE
# http://… w DOCX kazałoby mu pobrać adres z sieci serwera (SSRF). Piaskownica
# odcina sieć: bwrap --unshare-net, a gdy go brak — unshare -n (bez roota
# zwykle zablokowane; usługa konsoli działa jako root). Wykrycie RAZ na start
# (on_startup): próbne uruchomienie „true" w piaskownicy — RestrictNamespaces=yes
# w systemd blokuje przestrzenie nazw, wtedy próba się nie udaje i soffice
# rusza zwyczajnie (podgląd DOCX działa), a rejestr dostaje jedno ostrzeżenie.
PIASKOWNICE = (
    ('bwrap', ['bwrap', '--unshare-net', '--dev-bind', '/', '/']),
    ('unshare', ['unshare', '-n']),
)
_PIASKOWNICA = None          # None = nie wykryto; [] = brak; lista = przedrostek


def _proba_polecenia(cmd) -> bool:
    import subprocess
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _wykryj_piaskownice(which=None, proba=None) -> list:
    """Pierwsza działająca piaskownica sieci (przedrostek polecenia) albo []."""
    global _PIASKOWNICA
    which = which or shutil.which
    proba = proba or _proba_polecenia
    _PIASKOWNICA = []
    if KONF.soffice_bez_sieci == 'nie':
        return _PIASKOWNICA
    nieudane = []
    for nazwa, przedrostek in PIASKOWNICE:
        if not which(nazwa):
            continue
        if proba(przedrostek + ['true']):
            _PIASKOWNICA = przedrostek
            _evlog('soffice', f'konwersje LibreOffice bez sieci: {" ".join(przedrostek)}')
            return _PIASKOWNICA
        nieudane.append(nazwa)
    powod = (f'{" i ".join(nieudane)} nie uruchamia się (przestrzenie nazw zablokowane, '
             'np. RestrictNamespaces=yes albo brak uprawnień)' if nieudane
             else 'brak bwrap i unshare')
    skutek = ('podgląd DOCX i druk ODMAWIAJĄ konwersji (soffice_bez_sieci = tak)'
              if KONF.soffice_bez_sieci == 'tak' else
              'soffice konwertuje Z DOSTĘPEM do sieci — dokument z polem '
              'INCLUDEPICTURE http://… może kazać serwerowi pobrać adres '
              '(zainstaluj bubblewrap: apt install bubblewrap)')
    _evlog('soffice', f'piaskownica sieci niedostępna: {powod}; {skutek}', level='warn')
    return _PIASKOWNICA


def polecenie_soffice(*args):
    """Lista argumentów uruchomienia soffice (z piaskownicą, jeśli działa);
    None = soffice_bez_sieci = tak, a piaskownicy brak (konwersja zabroniona)."""
    baza = ['soffice', *args]
    if KONF.soffice_bez_sieci == 'nie':
        return baza
    if _PIASKOWNICA is None:
        _wykryj_piaskownice()
    if _PIASKOWNICA:
        return [*_PIASKOWNICA, *baza]
    if KONF.soffice_bez_sieci == 'tak':
        return None
    return baza


async def _soffice_start(app):
    """on_startup: wykrycie piaskownicy raz na start (próby w wątku)."""
    global _PIASKOWNICA
    _PIASKOWNICA = None
    if KONF.modul('podglad_docx') or KONF.modul('druk'):
        await asyncio.get_running_loop().run_in_executor(None, _wykryj_piaskownice)


def _docx_to_txt(p: Path) -> Path:
    """Awaryjne źródło dla lektora: tekst wprost z DOCX (python-docx)."""
    import docx
    d = docx.Document(str(p))
    parts = [par.text for par in d.paragraphs]
    for tbl in d.tables:
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(' . '.join(cells))
    tmp = tmp_plik(p.stem, '_lektor_src.txt')
    tmp.write_text('\n'.join(parts), encoding='utf-8')
    return tmp


async def print_item(request):
    """POST ?print=1 na .md/.docx/.pdf — druk na Canon G3070 (CUPS).
    .md → export_to_docx → soffice→PDF (paginacja jak w Wordzie) → lp.
    Async; błędy → plik bledy_druku z ustawień instancji."""
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents or not target.is_file():
        return web.Response(status=403)
    ext = target.suffix.lower()
    if ext not in ('.md', '.docx', '.pdf'):
        return web.Response(status=400, text='Druk: .md/.docx/.pdf')
    if ext == '.md' and not KONF.skrypt_eksportu_docx.is_file():
        return web.Response(status=400, text='Druk .md niemożliwy: brak '
                            f'export_to_docx.py w katalogu eksportu ({EXPORT_DIR}). '
                            'Druk .docx i .pdf działa.')
    # D5: limit zadań druku na godzinę (okno przesuwne) — 429 z Retry-After
    import time as _t
    teraz = _t.monotonic()
    while _DRUK_CZASY and teraz - _DRUK_CZASY[0] >= 3600:
        _DRUK_CZASY.popleft()
    if len(_DRUK_CZASY) >= KONF.limit_druku_na_godzine:
        za_ile = int(3600 - (teraz - _DRUK_CZASY[0])) + 1
        _evlog('druk', f'limit {KONF.limit_druku_na_godzine} zadań na godzinę — '
               f'odmowa druku {target.name}', level='warn')
        return web.Response(status=429, headers={'Retry-After': str(za_ile)},
                            text=f'Limit druku: {KONF.limit_druku_na_godzine} zadań '
                                 f'na godzinę. Następne za {za_ile // 60 + 1} min.')
    _DRUK_CZASY.append(teraz)

    asyncio.ensure_future(_drukuj(target))
    return web.json_response({'status': 'wysłano do druku'}, status=202)


_DRUK_CZASY = collections.deque()        # chwile przyjęcia zadań druku (D5)


async def _polecenie(*cmd, log=None) -> int:
    """Uruchomienie programu zewnętrznego (druk); wyjście do dziennika błędów.
    Jedno miejsce — testy podstawiają atrapę."""
    p = await asyncio.create_subprocess_exec(*cmd, stdout=log, stderr=log)
    await p.wait()
    return p.returncode


async def _drukuj(target: Path):
    """.md → export_to_docx → soffice → PDF → lp; .docx → soffice → PDF → lp;
    .pdf → lp. Pliki pośrednie w katalogu roboczym zadania (C8), usuwanym
    po przekazaniu PDF-u do CUPS (lp kopiuje plik do kolejki)."""
    import time as _t
    ext = target.suffix.lower()
    with open(KONF.bledy_druku, 'ab') as log, tmp_katalog('druk') as kat:
        log.write(f'\n=== {_t.strftime("%F %T")} {target.name} ===\n'.encode())
        log.flush()
        kat = Path(kat)
        pdf = target
        zrodlo_pdf = None
        if ext == '.md':
            zrodlo_pdf = kat / (target.stem + '_print.docx')
            if await _polecenie(sys.executable, str(KONF.skrypt_eksportu_docx),
                                str(target), '-o', str(zrodlo_pdf), log=log):
                return
        elif ext == '.docx':
            zrodlo_pdf = target
        if zrodlo_pdf is not None:
            pdf = kat / (zrodlo_pdf.stem + '.pdf')
            cmd = polecenie_soffice('--headless', profil_lo('lo-profil-druk'),
                                    '--convert-to', 'pdf', '--outdir', str(kat),
                                    str(zrodlo_pdf))
            if cmd is None:
                log.write('soffice_bez_sieci = tak, a piaskownicy sieci brak — '
                          'konwersja do PDF zabroniona\n'.encode())
                return
            if await _polecenie(*cmd, log=log):
                return
        await _polecenie('lp', '-d', 'Canon_G3070', str(pdf), log=log)


async def lektor_item(request):
    """POST ?lektor=1 na .md/.docx/.txt — generacja audio w TLE
    (czytaj_tts.py). Wynik: <nazwa>_lektor.<fmt> obok pliku (fmt z conf) albo
    w katalog_lektora/<ścieżka względna katalogu źródła>/, gdy ustawiony;
    auto-odświeżanie listingu pokaże go po zakończeniu. Dozwolone także
    w trybie tylko do odczytu (zapis wyłącznie do katalogu lektora)."""
    odm = _modul_wylaczony('lektor')
    if odm is not None:
        return odm
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents or not target.is_file():
        return web.Response(status=403)
    ext = target.suffix.lower()
    if ext not in ('.md', '.docx', '.txt'):
        return web.Response(status=400, text='Lektor czyta .md/.docx/.txt')

    # źródło tekstu: .md wprost; .docx → bliźniaczy .md (ten sam stem,
    # ten katalog lub ../processed/) albo ekstrakcja tekstu z DOCX
    src = target
    if ext == '.docx':
        for cand in (target.parent / (target.stem + '.md'),
                     target.parent.parent / 'processed' / (target.stem + '.md')):
            if cand.exists():
                src = cand
                break
        else:
            try:
                src = _docx_to_txt(target)
            except Exception as e:
                return web.Response(status=500, text=f'Ekstrakcja DOCX: {e}')

    fmt = request.query.get('fmt', '').lower() or _lektor_fmt()
    if fmt not in ('mp3', 'wav', 'flac'):
        fmt = 'mp3'
    # katalog lektora instancji (np. Jarvis: źródła tylko do odczytu) albo
    # konwencja sprawozdań: źródło w processed/ → audio do exports/ obok
    # (ta sama lokalizacja co lektor agenta z Discorda); inaczej obok pliku
    out_dir = _lektor_dir_for(target)
    if out_dir is None:
        if KONF.tylko_odczyt:
            return web.Response(status=403, text='Tryb tylko do odczytu, a katalog '
                                'lektora nie jest ustawiony — nie ma gdzie zapisać nagrania.')
        out_dir = target.parent
        if out_dir.name == 'processed' and (out_dir.parent / 'exports').is_dir():
            out_dir = out_dir.parent / 'exports'
    out = out_dir / f'{target.stem}_lektor.{fmt}'
    if any(j['out'] == str(out) and not j['cancelled'] for j in _LEKTOR_QUEUE):
        return web.json_response({'status': 'duplikat', 'out': out.name})
    blad = _lektor_sprawdz_zapis(out)
    if blad:
        global _LEKTOR_SEQ
        _LEKTOR_SEQ += 1
        job = {'id': _LEKTOR_SEQ, 'out': str(out), 'src': str(src), 'fmt': fmt}
        _lektor_blad(job, blad)
        return web.json_response({'status': 'failed', 'out': out.name, 'blad': blad},
                                 status=500)

    # lektor zajęty (przeglądarka ALBO agent z Discorda) → bez flagi queue
    # zwracamy 'busy'; UI pyta usera o dopisanie do kolejki
    if _lektor_busy() and 'queue' not in request.query:
        return web.json_response(
            {'status': 'busy', 'pending': len(_LEKTOR_QUEUE)})

    queued = bool(_LEKTOR_QUEUE) or _ext_lektor_running() or _LEKTOR_PAUSED
    opisy = request.query.get('opisy', '')
    if opisy not in ('tak', 'nie'):
        opisy = ''
    if not KONF.modul('lektor_opisy_ai'):
        opisy = 'nie'                     # model wizyjny wyłączony w ustawieniach
    # silnik mowy: edge (online, tekst wychodzi z urządzenia) | piper (lokalnie);
    # brak albo nieznana wartość = wg lektor-ustawienia.conf
    silnik = request.query.get('silnik', '')
    if silnik not in LEKTOR_SILNIKI:
        silnik = ''
    job = _lektor_new_job(src, out, fmt, opisy, silnik)
    return web.json_response(
        {'status': 'queued' if queued else 'start', 'id': job['id'],
         'out': out.name, 'position': len(_LEKTOR_QUEUE)}, status=202)


async def eksport_docx_item(request):
    """POST ?docx=1 na pliku .md = eksport do .docx (zapis w exports/)."""
    odm = _odmowa_zapisu()
    if odm is None:
        odm = _modul_wylaczony('eksport_docx')
    if odm is not None:
        return odm
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents:
        return web.Response(status=403)
    if not target.is_file() or target.suffix.lower() != '.md':
        return web.Response(status=400, text='Eksport DOCX: tylko pliki .md')
    return await _export_md_docx(target)


async def mkdir_item(request):
    """POST ?mkdir=<nazwa> na KATALOGU = utworzenie podkatalogu (bez nadpisywania).
    Nazwa sanityzowana przez .name (odcina ścieżki/.. ); 409 gdy już istnieje."""
    odm = _odmowa_zapisu()
    if odm is not None:
        return odm
    raw = request.match_info.get('path', '').strip('/')
    try:
        parent = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in parent.parents and parent != ROOT:
        return web.Response(status=403)
    if not parent.is_dir():
        return web.Response(status=400, text='Cel nie jest katalogiem')
    name = Path(request.query.get('mkdir', '')).name.strip()
    if not name or name.startswith('.'):
        return web.Response(status=400, text='Nieprawidłowa nazwa')
    newdir = parent / name
    if newdir.exists():
        return web.Response(status=409, text='Folder już istnieje')
    try:
        newdir.mkdir()
        _evlog('mkdir', f'utworzono katalog: {newdir.relative_to(ROOT)}')
        return web.Response(text='ok')
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def rename_item(request):
    """POST ?rename=<nowa-nazwa> na pliku/katalogu = zmiana nazwy (ten sam
    katalog, bez nadpisywania — 409 gdy cel istnieje)."""
    odm = _odmowa_zapisu()
    if odm is not None:
        return odm
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents:
        return web.Response(status=403)
    if not target.exists():
        return web.Response(status=404)
    new_name = Path(request.query.get('rename', '')).name.strip()
    if not new_name or new_name.startswith('.'):
        return web.Response(status=400, text='Nieprawidłowa nazwa')
    dest = target.parent / new_name
    if dest.exists():
        return web.Response(status=409, text='Plik o tej nazwie już istnieje')
    target.rename(dest)
    return web.json_response({'renamed': target.name, 'to': new_name})


async def lektor_pause(request):
    """POST ?lektorqpause=<id|ext>&mode=hold|skip
    hold = SIGSTOP bieżącej generacji + wstrzymanie CAŁEJ kolejki;
    skip = przerwij bieżącą, jej zadanie wraca NA KONIEC kolejki,
           startuje następny plik."""
    global _LEKTOR_PAUSED
    import os
    import signal as _sig
    rid = request.query.get('lektorqpause', '')
    mode = request.query.get('mode', 'hold')
    if rid == 'ext':
        # zewnętrzny lektor (agent/CLI): tylko hold/zamrożenie
        for pid in _ext_lektor_pids():
            try:
                _wyslij_sygnal(pid, SYGNAL_STOP)
            except ProcessLookupError:
                pass
        _LEKTOR_PAUSED = True
        _lektor_save_queue()
        return web.json_response({'paused': 'ext'})
    try:
        jid = int(rid)
    except ValueError:
        return web.Response(status=400)
    job = next((j for j in _LEKTOR_QUEUE if j['id'] == jid), None)
    if job is None:
        return web.Response(status=404)
    if mode == 'skip':
        # bieżący na koniec kolejki, następny rusza
        job['cancelled'] = True
        if job.get('proc') is not None and job['state'] == 'running':
            try:
                job['proc'].kill()
            except ProcessLookupError:
                pass
        nj = _lektor_new_job(job['src'], Path(job['out']), job['fmt'],
                             job.get('opisy', ''), job.get('silnik', ''))
        return web.json_response({'skipped': jid, 'requeued_as': nj['id']})
    # hold
    if job.get('proc') is not None and job['state'] == 'running':
        try:
            os.kill(job['proc'].pid, _sig.SIGSTOP)
            job['state'] = 'paused'
        except ProcessLookupError:
            pass
    _LEKTOR_PAUSED = True
    _lektor_save_queue()
    return web.json_response({'paused': jid})


async def lektor_resume(request):
    """POST ?lektorqresume=1 — wznowienie kolejki (SIGCONT zamrożonych)."""
    global _LEKTOR_PAUSED
    import os
    import signal as _sig
    _LEKTOR_PAUSED = False
    _lektor_save_queue()
    for j in _LEKTOR_QUEUE:
        if j['state'] == 'paused' and j.get('proc') is not None:
            try:
                os.kill(j['proc'].pid, _sig.SIGCONT)
                j['state'] = 'running'
            except ProcessLookupError:
                pass
    for pid in _ext_lektor_pids():
        try:
            _wyslij_sygnal(pid, SYGNAL_CONT)
        except ProcessLookupError:
            pass
    return web.json_response({'resumed': True})


async def lektor_cancel(request):
    """POST ?lektorqdel=<id|ext> — usunięcie pozycji z kolejki lektora;
    trwająca generacja zostaje ubita (i sprzątnięte częściowe pliki).
    'ext' = przerwij lektora uruchomionego poza serwerem (agent/CLI)."""
    raw_id = request.query.get('lektorqdel', '')
    if raw_id == 'ext':
        killed = []
        for pid in _ext_lektor_pids():
            try:
                _wyslij_sygnal(pid, SYGNAL_KILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
        return web.json_response({'cancelled': 'ext', 'pids': killed})
    try:
        jid = int(raw_id)
    except ValueError:
        return web.Response(status=400)
    for j in _LEKTOR_QUEUE:
        if j['id'] == jid:
            j['cancelled'] = True
            if j['state'] == 'running' and j.get('proc') is not None:
                try:
                    j['proc'].kill()
                except ProcessLookupError:
                    pass
            else:
                try:
                    _LEKTOR_QUEUE.remove(j)
                except ValueError:
                    pass
            return web.json_response({'cancelled': jid})
    return web.Response(status=404)


async def lektor_shutdown_toggle(request):
    """POST ?lektorqshutdown=1|0 — wyłączenie konsoli po ukończeniu kolejki.
    0 anuluje też zaplanowane już `shutdown` (gdy kolejka właśnie się
    skończyła i odliczanie trwa)."""
    global _LEKTOR_SHUTDOWN
    import subprocess
    val = request.query.get('lektorqshutdown', '0') == '1'
    # PUSTA kolejka + włączenie = natychmiastowe wyłączenie konsoli —
    # wymagaj jawnego force (incydent 2026-06-07 11:15: konsola zgasła
    # w trakcie odtwarzania muzyki)
    if val and not _LEKTOR_QUEUE and not _ext_lektor_running() \
            and 'force' not in request.query:
        return web.json_response({'status': 'empty-queue'})
    _LEKTOR_SHUTDOWN = val
    _lektor_save_queue()
    if not val:
        subprocess.run(['shutdown', '-c'], capture_output=True)
    else:
        _lektor_maybe_shutdown()
    return web.json_response({'shutdown': _LEKTOR_SHUTDOWN})


def _lektor_maybe_shutdown():
    """Kolejka pusta + nic nie generuje + flaga ⏻ → shutdown za 1 min
    (okno na anulowanie togglem); flaga konsumowana."""
    global _LEKTOR_SHUTDOWN
    if not KONF.modul('wylaczanie'):
        _LEKTOR_SHUTDOWN = False          # urządzenie, którego nie wolno wyłączać
        return
    if not _LEKTOR_SHUTDOWN:
        return
    if _LEKTOR_QUEUE or _ext_lektor_running():
        return
    import subprocess
    subprocess.run(['shutdown', '-h', '+1',
                    'Lektor ukończył kolejkę — wyłączanie'],
                   capture_output=True)
    _LEKTOR_SHUTDOWN = False
    _lektor_save_queue()


async def _lektor_shutdown_watch():
    """Strażnik flagi ⏻ dla zadań ZEWNĘTRZNYCH (agent) — _lektor_run ich
    nie widzi, więc sprawdzamy co 60 s."""
    while True:
        await asyncio.sleep(60)
        if _LEKTOR_SHUTDOWN:
            _lektor_maybe_shutdown()


async def crop_item(request):
    """POST <obraz>?crop z JSON {x,y,w,h,mode} → kadruje obraz (PIL).
    mode='copy' → nowy plik <stem>_crop<ext>; mode='overwrite' → nadpisuje
    oryginał (backup do .kosz). Współrzędne w pikselach NATURALNYCH obrazu
    (orientacja EXIF uwzględniona — jak widzi go przeglądarka)."""
    odm = _odmowa_zapisu()
    if odm is None:
        odm = _modul_wylaczony('kadrowanie')
    if odm is not None:
        return odm
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents and target != ROOT:
        return web.Response(status=403)
    if not target.is_file() or target.suffix.lower() not in IMG_EXT:
        return web.json_response({'error': 'To nie jest obraz'}, status=400)
    if target.suffix.lower() == '.svg':
        return web.json_response({'error': 'SVG nie obsługuje kadrowania'}, status=400)
    try:
        d = await request.json()
        x = int(round(float(d['x']))); y = int(round(float(d['y'])))
        w = int(round(float(d['w']))); h = int(round(float(d['h'])))
        mode = d.get('mode', 'copy')
    except web.HTTPException:            # 413: ciało ponad LIMIT_CIALA_ZADANIA
        raise
    except Exception:
        return web.json_response({'error': 'Złe dane kadru'}, status=400)
    if w < 2 or h < 2:
        return web.json_response({'error': 'Zaznaczenie za małe'}, status=400)
    try:
        from PIL import Image, ImageOps
    except Exception:
        return web.json_response({'error': 'Brak Pillow na serwerze'}, status=500)
    try:
        im = ImageOps.exif_transpose(Image.open(target))
        W, H = im.size
        x = max(0, min(x, W - 1)); y = max(0, min(y, H - 1))
        w = min(w, W - x); h = min(h, H - y)
        if w < 2 or h < 2:
            return web.json_response({'error': 'Kadr poza obrazem'}, status=400)
        crop = im.crop((x, y, x + w, y + h))
        suf = target.suffix.lower()

        def _save(img, path):
            if suf in ('.jpg', '.jpeg'):
                img.convert('RGB').save(path, quality=90, optimize=True)
            elif suf == '.webp':
                img.save(path, quality=92)
            else:
                img.save(path)

        if mode == 'overwrite':
            import time
            import shutil
            kosz = target.parent / '.kosz'
            kosz.mkdir(exist_ok=True)
            shutil.copy2(target, kosz / (target.name + '.orig-'
                                        + time.strftime('%Y%m%d_%H%M%S')))
            _save(crop, target)
            out = target
        else:
            stem = target.stem
            out = target.with_name(f'{stem}_crop{target.suffix}')
            i = 2
            while out.exists():
                out = target.with_name(f'{stem}_crop{i}{target.suffix}'); i += 1
            _save(crop, out)
        _evlog('crop', f'{target.relative_to(ROOT)} -> {out.name} ({w}x{h}, {mode})')
    except Exception as e:
        return web.json_response({'error': f'Błąd kadrowania: {e}'}, status=500)
    return web.json_response({'ok': True, 'file': out.name, 'mode': mode})


class _WgrywanieZaDuze(Exception):
    """Łączny rozmiar plików w żądaniu przekroczył limit_wgrywania_mb."""


def _wgrywanie_za_duze(target: Path, rozmiar: int, saved=()) -> web.Response:
    _evlog('upload', f'odrzucono wgrywanie do {target.relative_to(ROOT)}: '
           f'ponad {KONF.limit_wgrywania_mb} MB (co najmniej {rozmiar >> 20} MB)'
           + (f'; zapisane wcześniej w tym żądaniu: {len(saved)}' if saved else ''),
           level='warn')
    return web.json_response(
        {'error': f'Wgrywanie większe niż {KONF.limit_wgrywania_mb} MB '
                  '(limit_wgrywania_mb w ustawieniach).', 'saved': list(saved)},
        status=413)


async def upload(request):
    if 'print' in request.query:
        odm = _modul_wylaczony('druk')
        return odm if odm is not None else await print_item(request)
    if 'crop' in request.query:
        return await crop_item(request)
    if 'docx' in request.query:
        return await eksport_docx_item(request)
    if any(k in request.query for k in ('lektorqshutdown', 'lektorqpause',
                                        'lektorqresume', 'lektorqdel')):
        odm = _modul_wylaczony('lektor')
        if odm is not None:
            return odm
    if 'lektorqshutdown' in request.query:
        odm = _modul_wylaczony('wylaczanie')
        return odm if odm is not None else await lektor_shutdown_toggle(request)
    if 'lektorqpause' in request.query:
        return await lektor_pause(request)
    if 'lektorqresume' in request.query:
        return await lektor_resume(request)
    if 'lektorqdel' in request.query:
        return await lektor_cancel(request)
    if 'rename' in request.query:
        return await rename_item(request)
    if 'mkdir' in request.query:
        return await mkdir_item(request)
    if 'lektor' in request.query:
        return await lektor_item(request)
    # POST multipart na katalog = wgranie plików (drag&drop z przeglądarki).
    # Duplikaty nazw dostają sufiks z timestampem (jak w bocie) — nic nie nadpisujemy.
    odm = _odmowa_zapisu()
    if odm is not None:
        return odm
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents and target != ROOT:
        return web.Response(status=403)
    if not target.is_dir():
        return web.Response(status=400, text='Cel nie jest katalogiem')
    limit = KONF.limit_wgrywania_mb * 1024 ** 2
    if request.content_length is not None and request.content_length > limit:
        return _wgrywanie_za_duze(target, request.content_length)
    saved = []
    skipped = 0
    wczytano = 0                     # łącznie bajtów plików w tym żądaniu
    reader = await request.multipart()
    async for part in reader:
        if part.name != 'file' or not part.filename:
            continue
        # part.filename może być ŚCIEŻKĄ WZGLĘDNĄ (upload folderu) — zachowaj
        # podkatalogi, sanityzując każdy segment (bez '..', bez absolutnej).
        rel = (part.filename or '').replace('\\', '/').lstrip('/')
        segs = [s for s in rel.split('/') if s not in ('', '.', '..')]
        if not segs:
            continue
        *subdirs, name = segs
        if name.startswith('.'):
            continue
        destdir = target.joinpath(*subdirs) if subdirs else target
        troot = target.resolve()
        if destdir.resolve() != troot and troot not in destdir.resolve().parents:
            _evlog('upload', f'odrzucono ścieżkę poza katalogiem: {rel}',
                   level='warn')
            continue
        destdir.mkdir(parents=True, exist_ok=True)
        dest = destdir / name
        # zapis do .part, potem decyzja — idempotentny re-upload (resume):
        #  istnieje TEN SAM rozmiar -> pomijamy (bez duplikatu),
        #  inny rozmiar (np. ucięty partial) -> sufiks (nic nie nadpisujemy).
        tmp = destdir / (name + '.part')
        size = 0
        try:
            with open(tmp, 'wb') as f:
                while True:
                    chunk = await part.read_chunk(64 * 1024)
                    if not chunk:
                        break
                    wczytano += len(chunk)
                    if wczytano > limit:
                        raise _WgrywanieZaDuze()
                    f.write(chunk)
                    size += len(chunk)
        except _WgrywanieZaDuze:
            tmp.unlink(missing_ok=True)
            return _wgrywanie_za_duze(target, wczytano, saved)
        except Exception:
            tmp.unlink(missing_ok=True)     # niedokończony -> SAM SIĘ KASUJE
            _evlog('upload', f'zerwany w pół, .part usunięty: {rel}',
                   level='warn')
            raise
        relname = '/'.join(subdirs + [name]) if subdirs else name
        if dest.exists():
            old = dest.stat().st_size
            if old == size:                 # identyczny -> pomijamy (resume)
                tmp.unlink()
                skipped += 1
                continue
            if old < size:                  # istniejący był UCIĘTY -> zastąp AUTO
                dest.unlink()
                _evlog('upload', f'auto-zastąpiono ucięty plik '
                       f'({old}->{size} B): {relname}', level='warn')
            else:                           # istniejący WIĘKSZY -> sufiks (zachowaj)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                dest = destdir / f'{dest.stem}_{ts}{dest.suffix}'
                relname = ('/'.join(subdirs + [dest.name]) if subdirs
                           else dest.name)
        tmp.rename(dest)
        saved.append(relname)
    _evlog('upload', f'{target.relative_to(ROOT)} <- {len(saved)} nowych'
           + (f', {skipped} pominiętych (już są)' if skipped else ''))
    return web.json_response({'saved': saved, 'skipped': skipped})


async def _serve_file(request, target):
    if 'dl' in request.query:
        return web.FileResponse(target, headers={
            'Cache-Control': 'no-cache',
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(target.name)}"})

    if 'view' in request.query and target.suffix.lower() in VIDEO_EXT:
        return web.Response(text=render_video_page(target),
                            content_type='text/html')

    if 'view' in request.query and target.suffix.lower() in AUDIO_EXT:
        return web.Response(text=render_audio_page(target),
                            content_type='text/html')

    if 'view' in request.query and target.suffix.lower() in MODEL_EXT:
        return web.Response(text=render_3d_page(target),
                            content_type='text/html',
                            headers={'Cache-Control': 'no-store, max-age=0'})

    if 'view' in request.query and target.suffix.lower() in CSV_EXT:
        return web.Response(text=render_csv_page(target), content_type='text/html')

    if 'view' in request.query and target.suffix.lower() in XLSX_EXT:
        return web.Response(text=render_xlsx_page(target), content_type='text/html')

    # audio bez ?view: poprawny MIME (mimetypes nie zna .flac → pobieranie
    # zamiast odtwarzania w <audio>)
    if target.suffix.lower() in AUDIO_EXT and 'dl' not in request.query:
        return web.FileResponse(target, headers={
            'Content-Type': AUDIO_MIME[target.suffix.lower()]})

    if target.suffix.lower() in ('.docx', '.doc') and (
            'pdf' in request.query or 'view' in request.query):
        odm = _modul_wylaczony('podglad_docx')
        if odm is not None:
            return odm
    # surowy PDF z konwersji (źródło dla <embed> w stronie podglądu)
    if 'pdf' in request.query and target.suffix.lower() in ('.docx', '.doc'):
        pdf = await docx_to_pdf(target)
        if pdf is None:
            return web.Response(status=500, text='Konwersja DOCX nie powiodla sie')
        return web.FileResponse(pdf, headers={
            'Content-Type': 'application/pdf',
            'Content-Disposition': f"inline; filename*=UTF-8''{quote(target.stem)}.pdf",
            'Cache-Control': 'no-cache'})

    if 'read' in request.query:
        aud = _lektor_audio_for(target, exports=True)
        cue = _lektor_cues_for(target, aud)
        if aud is not None and cue is not None:
            return web.Response(text=render_read_page(target, aud, cue),
                                content_type='text/html')
        return web.Response(status=404,
                            text='Brak read-along (potrzebny audio + cues)')

    if 'view' in request.query and target.suffix.lower() in ('.docx', '.doc'):
        # strona-opakowanie: PDF w <embed> + poll mtime DOCX co 3 s —
        # po zmianie pliku (np. re-eksport przez agenta) podgląd sam się
        # przeładowuje (świeża konwersja, cache-bust parametrem v=)
        q = quote(target.name)
        mt = target.stat().st_mtime
        # odtwarzacz lektora, jeśli istnieje <stem>_lektor.flac/mp3/... obok
        aud = _lektor_audio_for(target, exports=True)
        cue = _lektor_cues_for(target, aud)
        if aud is not None:
            aq = _url_abs(aud)
            audio_html = (
                '<audio id="lek" controls preload="metadata" '
                f'src="{aq}" title="{_esc(aud.name)}"></audio>')
        else:
            audio_html = ''
        page = (
            '<!doctype html><meta charset=utf-8>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_esc(target.name)}</title>'
            '<style>body{margin:0;height:100vh;display:flex;'
            'flex-direction:column}'
            '.bar{display:flex;gap:.6em;align-items:center;padding:.4em .9em;'
            'background:#26292f;color:#ddd;font-family:system-ui,sans-serif;'
            'font-size:.9em}'
            '.bar a{color:#7ab7ff;text-decoration:none;border:1px solid '
            '#3a3f47;border-radius:5px;padding:.2em .6em}'
            '#lek{width:100%;background:#26292f;display:block}'
            '.pvw{flex:1;position:relative;background:#2a2d33}'
            '#pv{position:absolute;inset:0;width:100%;height:100%;border:0}'
            '#cvt{position:absolute;inset:0;display:flex;align-items:center;'
            'justify-content:center;color:#cfd6df;font-family:system-ui,'
            'sans-serif;text-align:center;padding:1em;font-size:1.05em}'
            '#cvt.hide{display:none}</style>'
            f'<div class="bar"><a href="./">📁 folder</a>{_link_startowy()}'
            f'<span style="word-break:break-all">{_esc(target.name)}</span>'
            f'<span id="st" style="color:#8a93a0"></span>'
            + (f'<a href="{q}?read=1" style="color:#6fce8f;'
               'border-color:#2f6b46">📖 śledź tekst</a>' if cue
               else ('<span style="color:#6fce8f">🔊 lektor</span>'
                     if aud else ''))
            + _lektor_przycisk(target, aud)
            + f'<a href="{q}?dl=1" style="margin-left:auto">⬇ DOCX</a></div>'
            + audio_html
            + '<div class="pvw">'
            + f'<iframe id="pv" src="{q}?pdf=1&v={mt}"></iframe>'
            + '<div id="cvt">⏳ Konwertuję dokument do PDF…<br>'
            '<span style="opacity:.7;font-size:.85em">pierwsze otwarcie '
            '~10–30 s na tym sprzęcie (potem natychmiast)</span></div></div>'
            '<script>(function(){'
            f'let mt={mt};'
            'const pv=document.getElementById("pv");'
            'const cvt=document.getElementById("cvt");'
            # iframe.onload = PDF gotowy -> chowamy overlay (niezawodne, w
            # przeciwienstwie do <embed> ktory nie ma load-eventu)
            'pv.addEventListener("load",function(){cvt.classList.add("hide");});'
            'setTimeout(function(){if(!cvt.classList.contains("hide"))'
            'cvt.innerHTML="\\u26A0 Konwersja trwa nietypowo długo lub się nie '
            'powiodła — odśwież stronę (F5).";},55000);'
            'async function chk(){try{'
            f'const j=await(await fetch("{q}?mt=1",'
            '{cache:"no-store"})).json();'
            'if(j.mt!==mt){mt=j.mt;cvt.textContent="⏳ Odświeżam podgląd…";'
            'cvt.classList.remove("hide");'
            f'pv.src="{q}?pdf=1&v="+mt;}}'
            '}catch(e){}}'
            'setInterval(chk,3000);})();</script>')
        return web.Response(text=page, content_type='text/html')

    if 'mt' in request.query:
        return web.json_response({'mt': target.stat().st_mtime})

    if 'docx' in request.query and target.suffix.lower() == '.md':
        # eksport zapisuje plik w exports/ — tylko POST z nagłówkiem (B9)
        return web.Response(status=405, headers={'Allow': 'POST'},
                            text='Eksport DOCX: użyj przycisku w podglądzie (POST).')

    if 'view' in request.query and target.suffix.lower() == '.md':
        return web.Response(text=render_md_page(target), content_type='text/html')

    if 'view' in request.query and target.suffix.lower() in IMG_EXT:
        siblings = sorted(
            (p for p in target.parent.iterdir()
             if p.is_file() and p.suffix.lower() in IMG_EXT
             and not p.name.startswith('.')),
            key=lambda p: p.name.lower())
        names = [p.name for p in siblings]
        idx = names.index(target.name) if target.name in names else 0
        prv = quote(names[idx-1]) + '?view=1' if idx > 0 else None
        nxt = quote(names[idx+1]) + '?view=1' if idx < len(names)-1 else None
        a_prev = (f'<a href="{prv}" id="prev">← poprzednie</a>' if prv
                  else '<a class="nav-off">← poprzednie</a>')
        a_next = (f'<a href="{nxt}" id="next">następne →</a>' if nxt
                  else '<a class="nav-off">następne →</a>')
        crop_btn = ('' if (target.suffix.lower() == '.svg' or KONF.tylko_odczyt
                           or not KONF.modul('kadrowanie'))
                    else '<a href="#" id="cropb" title="Przytnij obszar (np. do kartki)">'
                         '✂ przytnij</a>')
        html = (
            '<!doctype html><meta charset=utf-8>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_esc(target.name)}</title><style>{VIEWER_STYLE}</style>'
            f'<div class="bar"><a href="./">📁 folder</a>{_link_startowy()}{a_prev}{a_next}'
            f'<span class="name">{_esc(target.name)}</span>'
            f'<span class="cnt">{idx+1} / {len(names)}</span>'
            f'<span class="cnt" id="zl">100%</span>'
            f'<a href="#" id="fitb" title="Dopasuj do okna (dwuklik / klawisz 0)">⊡ dopasuj</a>'
            f'<a href="#" id="natb" title="Rozmiar naturalny (klawisz 1)">1:1</a>'
            f'{crop_btn}'
            f'<a href="{quote(target.name)}?dl=1">⬇ pobierz</a></div>'
            f'<div class="stage"><img src="{quote(target.name)}?v={target.stat().st_mtime:.0f}" alt="{_esc(target.name)}"></div>'
            '<div class="cropov" id="cropov"></div>'
            '<div class="cropbox" id="cropbox">'
            '<i class="h nw"></i><i class="h ne"></i><i class="h sw"></i><i class="h se"></i>'
            '<i class="h n"></i><i class="h s"></i><i class="h w"></i><i class="h e"></i></div>'
            '<div class="crophint" id="crophint">LPM = zaznacz pole · uchwyty = '
            'przesuń krawędzie · scroll = skaluj · proporcje poniżej</div>'
            '<div class="cropbtns" id="cropbtns">'
            '<span class="asp">Proporcje:'
            '<select id="craspect">'
            '<option value="">dowolne</option>'
            '<option value="1:1">1:1</option>'
            '<option value="4:3">4:3</option>'
            '<option value="3:2">3:2</option>'
            '<option value="16:9">16:9</option>'
            '<option value="210:297">A4 pion</option>'
            '<option value="297:210">A4 poziom</option>'
            '</select></span>'
            '<button class="prim" id="crsave">📄 Kopia</button>'
            '<button id="crover">✏️ Nadpisz</button>'
            '<button id="crcancel">✖ Anuluj</button></div>'
            '<script>'
            'const _Q=' + repr(quote(target.name)) + ';'
            'let _mt=' + f'{target.stat().st_mtime:.0f}' + ';'
            'document.addEventListener("keydown",e=>{'
            'if(e.key==="ArrowLeft"){const a=document.getElementById("prev");if(a)location=a.href}'
            'if(e.key==="ArrowRight"){const a=document.getElementById("next");if(a)location=a.href}'
            # ESC: w trybie kadrowania -> anuluj narzędzie (bez zapisu, _cropEsc()
            # zwraca true); w zwykłym podglądzie -> jak „📁 folder" (powrót).
            'if(e.key==="Escape"){if(!(window._cropEsc&&window._cropEsc()))location.href="./";}'
            '});'
            # auto-odświeżanie: poll mtime co 3 s -> cache-bust src (zoom/pan zostają)
            '(function(){'
            'const im=document.querySelector(".stage img");'
            'async function chk(){try{'
            'const j=await(await fetch(_Q+"?mt=1",{cache:"no-store"})).json();'
            'const nm=Math.round(j.mt);'
            'if(nm!==_mt){_mt=nm;im.src=_Q+"?v="+nm;}'
            '}catch(e){}}'
            'setInterval(chk,3000);})();'
            '</script>' + VIEWER_JS + CROP_JS)
        return web.Response(text=html, content_type='text/html')

    # Generyczny plik. Dla TEKSTU wymuś charset=utf-8 — inaczej przeglądarka
    # czyta UTF-8 jako Windows-1252 → mojibake polskich znaków (się→siÄĹ).
    import mimetypes
    headers = {'Cache-Control': 'no-cache'}
    TEXT_EXT = {'.md', '.markdown', '.txt', '.text', '.json', '.csv', '.tsv',
                '.log', '.py', '.sh', '.conf', '.cfg', '.ini', '.yaml', '.yml',
                '.xml', '.srt', '.vtt', '.css', '.js'}
    ctype, _ = mimetypes.guess_type(target.name)
    if (ctype and ctype.startswith('text/')) or target.suffix.lower() in TEXT_EXT:
        headers['Content-Type'] = f'{ctype or "text/plain"}; charset=utf-8'
    if target.suffix.lower() in EXT_AKTYWNE:
        # HTML/SVG/XML z vaulta: podgląd tak, skrypty nie (A3); <img src=x.svg>
        # w przeglądarce zdjęć działa dalej — obraz i tak nie wykonuje skryptów
        headers['Content-Security-Policy'] = 'sandbox'
    return web.FileResponse(target, headers=headers)


def _katalogi_sprzatania() -> list:
    """Katalogi, w których serwer sam zapisuje (a więc może zostawić odpadki):
    zapis dozwolony → katalog główny (z katalogiem lektora w środku);
    tylko odczyt → katalog lektora i katalog danych. Bez zagnieżdżonych dubli."""
    if not KONF.tylko_odczyt:
        kand = [ROOT]
    else:
        kand = ([Path(KONF.katalog_lektora)] if KONF.katalog_lektora else []) \
            + [Path(KONF.katalog_danych)]
    kand = [d.resolve() for d in kand if d.is_dir()]
    return [d for d in kand
            if not any(o != d and o in d.parents for o in kand)]


def _pliki_sprzatania():
    """Pliki katalogów sprzątania; błąd odczytu katalogu nie wywraca startu."""
    for d in _katalogi_sprzatania():
        try:
            yield from list(d.rglob('*'))
        except OSError:
            continue


async def _cleanup_parts(app):
    """Self-clean przy starcie (po _lektor_restore — kolejka już wczytana):
      • orphany .part (przerwane uploady),
      • transientne .wav lektora (nieudana konwersja mp3->flac),
      • PORZUCONE partiale lektora (_lektor.mp3 + sidecary) — ALE TYLKO gdy nie
        mają aktywnego zadania w kolejce (te z zadaniem ZOSTAJĄ do wznowienia).
    Przeszukuje tylko katalogi z zapisem (_katalogi_sprzatania)."""
    import time
    import subprocess
    # czy JAKIŚ lektor aktualnie generuje (agent/CLI poza serwerem)? w razie
    # wątpliwości NIE ruszaj partiali lektora.
    try:
        gen = subprocess.run(['pgrep', '-f', 'czytaj_tts.py'],
                             capture_output=True).returncode == 0
    except Exception:
        gen = True
    active = set()
    for it in list(_LEKTOR_QUEUE):
        try:
            active.add(Path(it['out']).stem)      # <stem>_lektor
        except Exception:
            pass
    now = time.time()
    removed = 0
    for p in _pliki_sprzatania():
        try:
            if '.kosz' in p.parts:            # NIE ruszaj kosza
                continue
            n = p.name
            if n.endswith('.part') or n.endswith('_lektor.wav'):
                p.unlink(); removed += 1
            elif n.endswith('_lektor.mp3.resume.json') and not gen:
                # NIEUKOŃCZONY lektor (ma checkpoint). UKOŃCZONY mp3 NIE ma
                # .resume.json -> nie trafia tu. Kasuj komplet tylko gdy brak
                # aktywnego zadania i plik nie jest świeżo zapisywany.
                stem = n[:-len('.mp3.resume.json')]     # <docstem>_lektor
                if stem in active:
                    continue
                mp3 = p.with_name(stem + '.mp3')
                if mp3.exists() and now - mp3.stat().st_mtime < 120:
                    continue
                for suf in ('.mp3', '.mp3.resume.json', '.mp3.cues.json'):
                    q = p.with_name(stem + suf)
                    if q.exists():
                        q.unlink(); removed += 1
        except Exception:
            pass
    if removed:
        _evlog('start', f'self-clean: usunięto {removed} niedokończonych plików')


def _oglos_token_startowy(token: str) -> None:
    """Jednorazowy token ekranu „Ustaw hasło" → dziennik usługi (stderr →
    journal), z gotowym adresem. Nigdy do rejestru zdarzeń (widać go w WWW)."""
    import socket
    host = HOST if HOST not in ('', '0.0.0.0', '::') else socket.gethostname()
    print(f'AnberFiles [{KONF.nazwa_instancji}]: hasło NIEUSTAWIONE — ustaw je '
          'w przeglądarce pod adresem z jednorazowym tokenem startowym (ważny do '
          'ustawienia hasła albo restartu usługi; zamiast nazwy hosta można użyć '
          f'adresu Tailscale):\n  http://{host}:{PORT}'
          f'{_LOGOWANIE.SCIEZKA_USTAWIENIA}?token={token}', file=sys.stderr, flush=True)


def utworz_aplikacje(k):
    """Aplikacja aiohttp dla ustawień instancji k (main() i testy)."""
    global _LEKTOR_LOCK, _LEKTOR_PAUSED, _LEKTOR_SHUTDOWN, _LOGOWANIE, _BRAMKA
    zastosuj_konfiguracje(k)
    if k.logowanie == 'formularz':
        # import tylko tu: konsola (basic) nie potrzebuje pliku logowanie.py
        import logowanie as _LOGOWANIE
        _LOGOWANIE.zapewnij_sekret(k.katalog_auth)   # 32 bajty, 0600, przy 1. starcie
        _BRAMKA = _LOGOWANIE.Bramka(rejestr=_evlog, bez_tokenu=k.ustaw_haslo_bez_tokenu,
                                    oglos=_oglos_token_startowy)
        if not _LOGOWANIE.haslo_ustawione(k.katalog_auth):
            _BRAMKA.zapewnij_token()                 # adres z tokenem do dziennika usługi
    # stan kolejki lektora od zera (pętla zdarzeń nowa przy każdym starcie)
    _LEKTOR_QUEUE.clear()
    _LEKTOR_BLEDY.clear()
    _DRUK_CZASY.clear()
    _LEKTOR_LOCK = None
    _LEKTOR_PAUSED = False
    _LEKTOR_SHUTDOWN = False
    # client_max_size: ciało żądania wczytywane do pamięci (request.read/post/json)
    # — małe; wgrywanie czyta strumieniowo z własnym licznikiem (upload)
    app = web.Application(middlewares=[errlog, straz_zrodla, auth, straz_ukrytych],
                          client_max_size=LIMIT_CIALA_ZADANIA)
    app.on_response_prepare.append(_naglowki_bezpieczenstwa)   # CSP itd. (A3, C5)
    global _PAMIEC
    _PAMIEC = ObserwatorPamieci(k.prog_pamieci_mb)
    app.on_startup.append(_lektor_restore)        # NAJPIERW wczytaj kolejkę
    app.on_startup.append(_cleanup_parts)         # potem sprzątaj porzucone
    app.on_startup.append(_pamiec_start)
    app.on_startup.append(_soffice_start)          # piaskownica sieci soffice (C9)
    app.on_cleanup.append(_pamiec_stop)
    app.on_cleanup.append(_dziennik_stop)
    app.router.add_get('/{path:.*}', serve)
    app.router.add_post('/{path:.*}', upload)
    app.router.add_delete('/{path:.*}', delete_item)
    return app


def main():
    """Start serwera: ustawienia → warunki startu → nasłuch. Każdy błąd
    ustawień lub warunku startu = komunikat na stderr i kod 2 (bez startu)."""
    try:
        k = _konf.wczytaj()
    except _konf.BladKonfiguracji as e:
        print(f'AnberFiles: błąd ustawień — {e}', file=sys.stderr, flush=True)
        sys.exit(2)
    bledy = _konf.sprawdz_przy_starcie(k)
    if bledy:
        for b in bledy:
            print(f'AnberFiles: ODMOWA STARTU — {b}', file=sys.stderr, flush=True)
        sys.exit(2)
    try:
        app = utworz_aplikacje(k)
    except OSError as e:
        print(f'AnberFiles: ODMOWA STARTU — katalog logowania {k.katalog_auth}: {e}',
              file=sys.stderr, flush=True)
        sys.exit(2)
    _evlog('start', f'serwer wystartował na :{PORT} ({_konf.opis(k)})')
    if k.logowanie == 'formularz':
        auth_info = ('logowanie formularzem' if _LOGOWANIE.haslo_ustawione(k.katalog_auth)
                     else 'logowanie formularzem — hasło NIEUSTAWIONE (ekran „Ustaw hasło")')
    else:
        auth_info = f'user={USER}' if PASSW else 'OPEN (no auth)'
    print(f'AnberFiles [{k.nazwa_instancji}]: http://{HOST}:{PORT}/ — {auth_info}'
          f' — {_konf.opis(k)}', flush=True)
    web.run_app(app, host=HOST, port=PORT, **opcje_dziennika_dostepu())


if __name__ == '__main__':
    main()
