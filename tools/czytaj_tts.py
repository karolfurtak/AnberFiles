#!/usr/bin/env python3
"""Lektor sprawozdań — .md → MP3 (edge-tts, głosy neuronowe pl-PL).

Pipeline: markdown → czysty tekst → NORMALIZACJA (liczby słownie przez
num2words, jednostki z polską odmianą, symbole °/%/≈/greka, odnośniki
Rys./Tab.) → edge-tts (chunki po akapitach) → sklejone MP3.

Użycie:
    python3 czytaj_tts.py plik.md [-o wyjście.mp3] [--voice marek|zofia]
                                  [--rate -10%..+30%]

Wymaga internetu (głosy online Microsoftu). Pakiety: edge-tts, num2words.
"""
import argparse
import asyncio
import re
import sys
from pathlib import Path

try:
    import edge_tts
    from num2words import num2words
except ImportError as e:
    print(f'BŁĄD: brak pakietu ({e}). Zainstaluj: pip3 install edge-tts num2words')
    sys.exit(1)

VOICES = {'marek': 'pl-PL-MarekNeural', 'zofia': 'pl-PL-ZofiaNeural'}

# Ustawienia domyślne — plik edytowany WYŁĄCZNIE z kanału #e-lektor-ustawienia
# (chroniony chattr +i przez bota). Parametry CLI mają pierwszeństwo.
CONF_FILE = Path(__file__).resolve().parent / 'lektor-ustawienia.conf'


def load_conf() -> dict:
    """Czyta lektor-ustawienia.conf (format: klucz = wartość, # komentarze)."""
    cfg = {}
    try:
        for line in CONF_FILE.read_text(encoding='utf-8').splitlines():
            line = line.split('#', 1)[0].strip()
            if '=' in line:
                k, v = line.split('=', 1)
                cfg[k.strip().lower()] = v.strip()
    except Exception:
        pass
    return cfg

# ── jednostki: formy dla 1 / 2-4 / 5+ (polska odmiana) ──────────────────────
UNITS = {
    'mm':  ('milimetr', 'milimetry', 'milimetrów'),
    'cm':  ('centymetr', 'centymetry', 'centymetrów'),
    'dm':  ('decymetr', 'decymetry', 'decymetrów'),
    'm':   ('metr', 'metry', 'metrów'),
    'km':  ('kilometr', 'kilometry', 'kilometrów'),
    'g':   ('gram', 'gramy', 'gramów'),
    'kg':  ('kilogram', 'kilogramy', 'kilogramów'),
    't':   ('tona', 'tony', 'ton'),
    's':   ('sekunda', 'sekundy', 'sekund'),
    'min': ('minuta', 'minuty', 'minut'),
    'h':   ('godzina', 'godziny', 'godzin'),
    'Hz':  ('herc', 'herce', 'herców'),
    'kHz': ('kiloherc', 'kiloherce', 'kiloherców'),
    'MHz': ('megaherc', 'megaherce', 'megaherców'),
    'W':   ('wat', 'waty', 'watów'),
    'kW':  ('kilowat', 'kilowaty', 'kilowatów'),
    'V':   ('wolt', 'wolty', 'woltów'),
    'A':   ('amper', 'ampery', 'amperów'),
    'Pa':  ('paskal', 'paskale', 'paskali'),
    'kPa': ('kilopaskal', 'kilopaskale', 'kilopaskali'),
    'MPa': ('megapaskal', 'megapaskale', 'megapaskali'),
    'bar': ('bar', 'bary', 'barów'),
    'l':   ('litr', 'litry', 'litrów'),
    'ml':  ('mililitr', 'mililitry', 'mililitrów'),
    'N':   ('niuton', 'niutony', 'niutonów'),
    'kN':  ('kiloniuton', 'kiloniutony', 'kiloniutonów'),
    'Nm':  ('niutonometr', 'niutonometry', 'niutonometrów'),
    'J':   ('dżul', 'dżule', 'dżuli'),
    'kJ':  ('kilodżul', 'kilodżule', 'kilodżuli'),
    '%':   ('procent', 'procent', 'procent'),
    '°':   ('stopień', 'stopnie', 'stopni'),
    '°C':  ('stopień Celsjusza', 'stopnie Celsjusza', 'stopni Celsjusza'),
}

GREEK = {'α': 'alfa', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta', 'ε': 'epsilon',
         'η': 'eta', 'θ': 'theta', 'λ': 'lambda', 'μ': 'mi', 'π': 'pi',
         'ρ': 'ro', 'σ': 'sigma', 'τ': 'tau', 'φ': 'fi', 'ω': 'omega',
         'Δ': 'delta', 'Σ': 'suma', 'Ω': 'omega'}

SYMBOLS = {'≈': ' około ', '≤': ' mniejsze lub równe ', '≥': ' większe lub równe ',
           '±': ' plus minus ', '×': ' razy ', '→': ' do ', '⇄': ' lub ',
           '–': ' ', '—': ' . ', '·': ' razy ', '½': 'jedna druga',
           '…': '.', '„': '', '“': '', '”': '', '"': '', '»': '', '«': ''}

ABBR = {'PM': 'pe em',
        'np.': 'na przykład', 'tzn.': 'to znaczy', 'tzw.': 'tak zwany',
        'itd.': 'i tak dalej', 'itp.': 'i tym podobne', 'm.in.': 'między innymi',
        'wg': 'według', 'ok.': 'około', 'nr': 'numer', 'tab.': 'tabela',
        'rys.': 'rysunek', 'str.': 'strona', 'pkt': 'punkt',
        'OWK': 'obrotu wału korbowego', 'GMP': 'górne martwe położenie',
        'DMP': 'dolne martwe położenie', 'GOZ': 'gospodarka o obiegu zamkniętym'}


def _plural(n: float, forms: tuple) -> str:
    """Polska odmiana po liczbie: 1 metr / 2-4 metry / 5+ metrów; ułamki → dopełniacz."""
    if n != int(n):
        return forms[2]
    n = int(abs(n))
    if n == 1:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


_FRAC_DEN = {1: ('dziesiąta', 'dziesiąte', 'dziesiątych'),
             2: ('setna', 'setne', 'setnych'),
             3: ('tysięczna', 'tysięczne', 'tysięcznych')}
_FEM = {1: 'jedna', 2: 'dwie'}


def _num_pl(raw: str) -> tuple:
    """'6,69' / '715' → (tekst słownie, wartość float).
    Ułamki <1 czytane naturalnie: 0,5 → "pięć dziesiątych",
    0,67 → "sześćdziesiąt siedem setnych" (poprawka z #e-lektor-ustawienia)."""
    val = float(raw.replace(',', '.').replace(' ', ''))
    if val == int(val):
        return num2words(int(val), lang='pl'), val
    całk, ułam = raw.replace('.', ',').split(',')
    licznik = int(ułam)
    if int(całk) == 0 and len(ułam) in _FRAC_DEN:
        formy = _FRAC_DEN[len(ułam)]
        lic = _FEM.get(licznik, num2words(licznik, lang='pl'))
        return f'{lic} {_plural(licznik, formy)}', val
    c = num2words(int(całk), lang='pl')
    u = num2words(licznik, lang='pl')
    return f'{c} przecinek {u}', val


_UNITS_RX = '|'.join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))
_RE_NUM_UNIT = re.compile(rf'(\d+(?:[.,]\d+)?)\s*({_UNITS_RX})(?![a-ząćęłńóśźż])')
_RE_NUM = re.compile(r'\d+(?:[.,]\d+)?')


MONTHS = ['', 'stycznia', 'lutego', 'marca', 'kwietnia', 'maja', 'czerwca',
          'lipca', 'sierpnia', 'września', 'października', 'listopada', 'grudnia']


def normalize(text: str, tabele: str = 'czytaj', naglowki: str = 'tak') -> str:
    """Markdown → tekst do czytania, liczby/jednostki/symbole słownie.
    tabele: 'czytaj' (wiersz po wierszu) | 'pomijaj' (wtrącenie).
    naglowki: 'tak' (czytaj tytuły sekcji) | 'nie' (usuń)."""
    # nagłówki sekcji — opcjonalne pominięcie (PRZED zdjęciem znaków #)
    if naglowki == 'nie':
        text = re.sub(r'^#{1,6}\s.*$', '', text, flags=re.M)
    # tagi HTML (np. <u>...</u> z szablonu) precz
    text = re.sub(r'</?[a-zA-Z][^>]*>', ' ', text)
    # daty ISO PRZED regułą zakresów (inaczej 2026-06-04 → "do")
    def date_pl(m):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            return (f'{num2words(d, ordinal=True, lang="pl")} {MONTHS[mo]} '
                    f'{num2words(y, lang="pl")} roku')
        return m.group(0)
    text = re.sub(r'(\d{4})-(\d{2})-(\d{2})', date_pl, text)
    def date_dmy(m):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            return (f'{num2words(d, ordinal=True, lang="pl")} {MONTHS[mo]} '
                    f'{num2words(y, lang="pl")} roku')
        return m.group(0)
    text = re.sub(r'(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)', date_dmy, text)
    # cyfra przyklejona do liter (GLA01) → odstęp
    text = re.sub(r'(?<=[A-Za-zżźćńółęąśŻŹĆŃÓŁĘĄŚ])(\d)', r' \1', text)
    # separator tysięcy: "3 000" / "13 000 000" (spacja/nbsp/wąska nbsp) → 3000
    # tylko pełne łańcuchy grup po 3 (lewa część 1-3 cyfry — "2016 200" zostaje)
    text = re.sub(r'(?<!\d)(\d{1,3}(?:[   ]\d{3})+)(?!\d)',
                  lambda m: re.sub(r'[   ]', '', m.group(1)), text)
    # markdown: obrazki precz, linki → opis, formatowanie precz
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'```.*?```', ' fragment kodu pominięty. ', text, flags=re.S)
    text = re.sub(r'[*_`#>]+', ' ', text)
    text = re.sub(r'^\s*-{3,}\s*$', '', text, flags=re.M)
    # tabele: separatorowe precz, dalej wg ustawienia
    text = re.sub(r'^\s*\|[\s\-:|]+\|\s*$', '', text, flags=re.M)
    if tabele == 'pomijaj':
        # blok kolejnych wierszy tabeli → jedno wtrącenie
        text = re.sub(r'(?:^\s*\|.+\|\s*$\n?)+', ' tabelę pominięto. ',
                      text, flags=re.M)
    else:
        text = re.sub(r'^\s*\|(.+)\|\s*$',
                      lambda m: ' . '.join(c.strip() for c in m.group(1).split('|') if c.strip()) + ' .',
                      text, flags=re.M)
    # LaTeX: proste podstawienia, reszta bez dolarów
    text = text.replace('\\alpha', 'alfa').replace('\\beta', 'beta')
    text = text.replace('\\approx', ' około ').replace('\\cdot', ' razy ')
    text = text.replace('\\bmod', ' modulo ').replace('\\to', ' do ')
    text = re.sub(r'\$\$?', ' ', text)
    text = re.sub(r'\\[a-zA-Z]+\{?', ' ', text).replace('}', ' ').replace('{', ' ')
    # zakresy 10–20 → "10 do 20" — MUSI być przed słownikiem symboli,
    # bo ten kasuje półpauzę (incydent: "350–500" → "trzysta pięćdziesiąt pięćset")
    text = re.sub(r'(\d)\s*[-–—]\s*(\d)', r'\1 do \2', text)
    # symbole i greka
    for s, r in SYMBOLS.items():
        text = text.replace(s, r)
    # indeksy dolne cyfrowe (CO₂ → CO 2) — przed konwersją liczb
    for sub_ch, sub_d in zip('₀₁₂₃₄₅₆₇₈₉', '0123456789'):
        text = text.replace(sub_ch, ' ' + sub_d)
    for g, r in GREEK.items():
        text = text.replace(g, f' {r} ')
    # indeksy h_0, alfa_p → "h zero", "alfa p"
    text = re.sub(r'_(\w)', r' \1 ', text)
    # skróty (całe słowa, bez względu na wielkość pierwszej litery)
    for a, r in ABBR.items():
        text = re.sub(rf'(?<![\w]){re.escape(a)}(?![\w])', r, text, flags=re.I)
    # liczba + jednostka (z odmianą)
    def nu(m):
        słownie, val = _num_pl(m.group(1))
        return f'{słownie} {_plural(val, UNITS[m.group(2)])}'
    text = _RE_NUM_UNIT.sub(nu, text)
    # odnośniki x.y → "x punkt y"
    text = re.sub(r'(\d+)\.(\d+)',
                  lambda m: f'{num2words(int(m.group(1)), lang="pl")} punkt '
                            f'{num2words(int(m.group(2)), lang="pl")}', text)
    # pozostałe liczby
    text = _RE_NUM.sub(lambda m: _num_pl(m.group(0))[0], text)
    # porządki
    text = text.replace('=', ' równa się ').replace('/', ' na ')
    # mianownik jednostki po "na" (z zapisu g/km, km/h): poprawna forma
    text = re.sub(r'\bna km\b', 'na kilometr', text)
    text = re.sub(r'\bna h\b', 'na godzinę', text)
    text = re.sub(r'\bna rok\b', 'na rok', text)
    text = re.sub(r'[|\\^~<>]', ' ', text)
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\.\s*\.', '.', text)
    return text.strip()


VOICE_EN_DEFAULT = 'en-US-ChristopherNeural'

_RE_ZANG = re.compile(r'\(z\s+ang\.\s*([^)]{2,80})\)', re.I)


def split_lang(text: str) -> list:
    """Tnie surowy tekst na segmenty [(lang, fragment)]: wstawki
    „(z ang. …)" idą do głosu angielskiego, reszta po polsku."""
    segs, pos = [], 0
    for m in _RE_ZANG.finditer(text):
        segs.append(('pl', text[pos:m.start()] + ' , z angielskiego: '))
        segs.append(('en', m.group(1)))
        pos = m.end()
    segs.append(('pl', text[pos:]))
    return [(lg, t) for lg, t in segs if t.strip()]


# ── OPISY ILUSTRACJI (model wizyjny; klucze z #e-lektor-ustawienia) ──────────
_DLUGOSC = {'krotki': '2-3 zdaniami', 'sredni': '4-6 zdaniami',
            'szczegolowy': '7-10 zdaniami'}
_STYL = {
    'ekspercki': ('jako specjalista tej dziedziny — używaj właściwej '
                  'terminologii i jednostek SI'),
    'popularny': 'prostym językiem, zrozumiale dla laika, bez żargonu',
}


def _opisz_obraz(img: Path, kontekst: str, cfg: dict) -> str:
    """Opis obrazu przez model wizyjny (Claude CLI — ta sama instalacja,
    z której korzysta agent). Cache w exports/.opisy_cache/<sha1>.txt."""
    import hashlib
    import os
    import subprocess
    cache = img.parent.parent / 'exports' / '.opisy_cache'
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except Exception:
        cache = Path('/tmp/opisy_cache')
        cache.mkdir(exist_ok=True)
    h = hashlib.sha1(img.read_bytes()).hexdigest()[:16]
    cf = cache / f'{h}.txt'
    if cf.exists():
        return cf.read_text(encoding='utf-8').strip()
    dl = _DLUGOSC.get(cfg.get('dlugosc_opisu', 'sredni'), _DLUGOSC['sredni'])
    st = _STYL.get(cfg.get('styl_opisu', 'ekspercki'), _STYL['ekspercki'])
    prompt = (f'Obejrzyj plik graficzny {img} i opisz jego treść {dl}, {st}. '
              f'Kontekst dokumentu: {kontekst}. Opis będzie CZYTANY przez '
              f'lektora osobie, która obrazu nie widzi — opisuj co widać '
              f'(elementy, relacje, wartości), bez zwrotów typu „na obrazku". '
              f'Zwróć WYŁĄCZNIE tekst opisu po polsku, bez nagłówków i uwag.')
    env = dict(os.environ, HOME='/root', IS_SANDBOX='1',
               PATH='/root/.local/bin:/usr/local/bin:/usr/bin:/bin')
    try:
        r = subprocess.run(
            ['claude', '--dangerously-skip-permissions', '-p', prompt],
            capture_output=True, text=True, timeout=240, env=env,
            cwd=str(img.parent))
        desc = (r.stdout or '').strip()
    except Exception as e:
        print(f'  opis {img.name}: BŁĄD {e}', flush=True)
        return ''
    if desc and len(desc) > 40:
        cf.write_text(desc, encoding='utf-8')
        return desc
    print(f'  opis {img.name}: pusta odpowiedź modelu', flush=True)
    return ''


def describe_images(raw: str, src: Path, cfg: dict) -> str:
    """Pre-pass: każdy ![alt](ścieżka) → opis_wstep + opis z modelu
    wizyjnego (wstawiony do strumienia TTS w miejscu obrazu).
    Działa tylko gdy opisuj_grafiki = tak."""
    if cfg.get('opisuj_grafiki', 'nie') != 'tak':
        return raw
    tytul = next((ln.lstrip('# ').strip() for ln in raw.splitlines()
                  if ln.startswith('#')), src.stem)
    wstep = cfg.get('opis_wstep', 'Opis ilustracji:')
    if wstep.lower() in ('puste', 'brak', 'nie'):
        wstep = ''

    def repl(m):
        alt, ref = m.group(1), m.group(2)
        name = Path(ref.split('?')[0]).name
        img = None
        for c in (src.parent / ref, src.parent / name,
                  src.parent.parent / 'raw' / name,
                  src.parent / 'raw' / name):
            if c.exists():
                img = c
                break
        if img is None:
            return ' '
        # nagłówek sekcji najbliższy przed obrazem (kontekst dziedzinowy)
        sekcja = ''
        for ln in reversed(raw[:m.start()].splitlines()):
            if ln.startswith('#'):
                sekcja = ln.lstrip('# ').strip()
                break
        kontekst = (f'sprawozdanie "{tytul}", sekcja "{sekcja}", '
                    f'podpis "{alt}"')
        print(f'  opisuję: {img.name}...', flush=True)
        desc = _opisz_obraz(img, kontekst, cfg)
        if not desc:
            return ' '
        return f' {wstep} {desc} ' if wstep else f' {desc} '

    return re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', repl, raw)


def _norm_en(text: str) -> str:
    """Minimalne czyszczenie segmentu angielskiego (markdown precz)."""
    text = re.sub(r'[*_`#|]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


PROGRESS_FILE = Path('/tmp/lektor_progress.json')


def _progress(out: Path, done: int, total: int):
    """Status postępu dla kolejki w serwerze plików (działa też, gdy lektora
    odpalił agent z Discorda — plik jest globalny, lektor i tak jest jeden)."""
    import json
    try:
        PROGRESS_FILE.write_text(json.dumps({
            'out': out.name, 'chunk': done, 'chunks': total,
            'pct': int(done * 100 / max(total, 1))}))
    except Exception:
        pass


def _chunks_sig(chunks: list, rate: str) -> str:
    """Odcisk zadania (tekst+głosy+tempo) — warunek bezpiecznego wznowienia."""
    import hashlib
    h = hashlib.sha1()
    for v, t in chunks:
        h.update(v.encode())
        h.update(t.encode())
    h.update(rate.encode())
    return h.hexdigest()[:16]


async def synth(chunks: list, out: Path, rate: str):
    """chunks = [(voice, tekst)] → edge-tts → MP3.
    Jakość: 96 kbps (EDGE_TTS_FORMAT przez patch biblioteki; darmowy
    endpoint Edge przyjmuje WYŁĄCZNIE formaty mp3 — PCM/opus odrzuca,
    zbadane empirycznie 2026-06-06). Bez patcha env jest ignorowany
    i wraca domyślne 48 kbps — degradacja łagodna.
    CHECKPOINT co chunk (plik <out>.resume.json): po przerwaniu
    (kill/pauza/reboot konsoli) ponowne wywołanie WZNAWIA od ostatniego
    ukończonego chunka, o ile tekst/głos/tempo się nie zmieniły."""
    import json
    import os
    os.environ['EDGE_TTS_FORMAT'] = 'audio-24khz-96kbitrate-mono-mp3'
    total = len(chunks)
    sig = _chunks_sig(chunks, rate)
    res_p = Path(str(out) + '.resume.json')
    start, mode = 0, 'wb'
    if res_p.exists() and out.exists():
        try:
            st = json.loads(res_p.read_text())
            if st.get('sig') == sig and 0 < st.get('done', 0) < total:
                start, mode = st['done'], 'ab'
                print(f'  WZNAWIAM od chunka {start + 1}/{total} '
                      f'(checkpoint)', flush=True)
        except Exception:
            pass
    with open(out, mode) as f:
        for i, (voice, ch) in enumerate(chunks, 1):
            if i <= start:
                continue
            print(f'  TTS {i}/{total} ({voice}, {len(ch)} znaków)...',
                  flush=True)
            _progress(out, i - 1, total)
            # RETRY per chunk: sieć mruga, a po pauzie (SIGSTOP/SIGCONT
            # z kolejki) websocket bywa zerwany — chunk odtwarzamy w całości
            for attempt in range(3):
                buf = b''
                try:
                    com = edge_tts.Communicate(ch, voice, rate=rate)
                    async for msg in com.stream():
                        if msg['type'] == 'audio':
                            buf += msg['data']
                    if not buf:
                        raise RuntimeError('pusty chunk')
                    f.write(buf)
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    print(f'  chunk {i}: {type(e).__name__} — '
                          f'ponawiam ({attempt + 2}/3)...', flush=True)
                    await asyncio.sleep(5)
            f.flush()
            res_p.write_text(json.dumps({'sig': sig, 'done': i}))
    _progress(out, total, total)
    try:
        res_p.unlink()
    except FileNotFoundError:
        pass


def build_chunks(segments: list, voice_pl: str, voice_en: str) -> list:
    """Segmenty (lang, txt) → chunki [(voice, tekst)] ~4000 znaków,
    łączone per głos (kolejne PL-fragmenty sklejane do limitu)."""
    chunks = []
    for lang, txt in segments:
        voice = voice_pl if lang == 'pl' else voice_en
        cur = ''
        for sent in re.split(r'(?<=[.!?])\s+', txt):
            if len(cur) + len(sent) > 4000 and cur:
                chunks.append((voice, cur))
                cur = sent
            else:
                cur = f'{cur} {sent}'.strip()
        if cur:
            # doklej do poprzedniego chunka, jeśli ten sam głos i jest miejsce
            if chunks and chunks[-1][0] == voice \
                    and len(chunks[-1][1]) + len(cur) <= 4000:
                chunks[-1] = (voice, f'{chunks[-1][1]} {cur}')
            else:
                chunks.append((voice, cur))
    return chunks


def _convert(mp3: Path, fmt: str) -> Path:
    """MP3 (natywne wyjście edge-tts) → WAV/FLAC.
    UWAGA: zysku jakości brak (źródło 48 kbps) — to opcja zgodności.
    Dekodowanie: mpg123 -w (moduł alsa zepsuty, ale dekoder działa);
    FLAC: enkoder `flac` (pakiet ~300 KB)."""
    import subprocess
    wav = mp3.with_suffix('.wav')
    subprocess.run(['mpg123', '-q', '-w', str(wav), str(mp3)], check=True)
    if fmt == 'wav':
        mp3.unlink()
        return wav
    out = mp3.with_suffix('.flac')
    subprocess.run(['flac', '-f', '-s', '-o', str(out), str(wav)], check=True)
    wav.unlink()
    mp3.unlink()
    return out


def main():
    ap = argparse.ArgumentParser(description='Lektor .md → MP3/WAV/FLAC (edge-tts pl-PL)')
    ap.add_argument('input', help='plik .md (lub .txt)')
    ap.add_argument('-o', '--output', help='plik wyjściowy (domyślnie obok wejścia)')
    ap.add_argument('--voice', choices=list(VOICES), default=None,
                    help='głos (domyślnie z lektor-ustawienia.conf)')
    ap.add_argument('--rate', default=None, help='tempo, np. +10%%')
    ap.add_argument('--format', choices=['mp3', 'wav', 'flac'], default=None,
                    help='format wyjścia (domyślnie z conf / rozszerzenia -o)')
    ap.add_argument('--opisy', choices=['tak', 'nie'], default=None,
                    help='opisy ilustracji modelem wizyjnym '
                         '(domyślnie z lektor-ustawienia.conf)')
    ap.add_argument('--dump-text', action='store_true',
                    help='wypisz znormalizowany tekst i zakończ (debug)')
    a = ap.parse_args()

    # priorytet: parametr CLI > lektor-ustawienia.conf > wartość bezpieczna
    cfg = load_conf()
    voice = a.voice or cfg.get('voice', 'marek')
    if voice not in VOICES:
        voice = 'marek'
    rate = a.rate or cfg.get('rate', '+0%')
    tabele = cfg.get('tabele', 'czytaj')
    naglowki = cfg.get('naglowki', 'tak')

    wstawki = cfg.get('wstawki_obce', 'tak')
    voice_en = cfg.get('voice_en', VOICE_EN_DEFAULT)

    src = Path(a.input)
    if not src.exists():
        print(f'BŁĄD: brak pliku {src}')
        sys.exit(1)

    # GLOBALNY RYGIEL: jeden lektor naraz w całym systemie (agent z Discorda,
    # serwer plików, CLI — wszyscy przechodzą tędy). flock blokuje do czasu
    # zwolnienia — wywołanie po prostu poczeka na swoją kolej.
    import fcntl
    _lock_f = open('/tmp/lektor.lock', 'w')
    try:
        fcntl.flock(_lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('Lektor zajęty — czekam na swoją kolej...', flush=True)
        fcntl.flock(_lock_f, fcntl.LOCK_EX)
    raw = src.read_text(encoding='utf-8', errors='replace')
    # opisy ilustracji (model wizyjny) — PRZED normalizacją, żeby opis
    # przeszedł przez pełną normalizację liczb/jednostek jak zwykły tekst
    if a.opisy is not None:
        cfg['opisuj_grafiki'] = a.opisy
    raw = describe_images(raw, src, cfg)
    # segmentacja językowa PRZED normalizacją (PL-normalizacja zniszczyłaby
    # nawiasy „(z ang. …)"); EN-segmenty dostają tylko lekkie czyszczenie
    raw_segs = split_lang(raw) if wstawki == 'tak' else [('pl', raw)]
    segments = [('pl', normalize(t, tabele=tabele, naglowki=naglowki))
                if lg == 'pl' else ('en', _norm_en(t))
                for lg, t in raw_segs]
    total = sum(len(t) for _, t in segments)
    if a.dump_text:
        for lg, t in segments:
            print(f'--[{lg}]-- {t}')
        return
    out = Path(a.output) if a.output else src.with_suffix('.mp3')
    fmt = (a.format or (out.suffix.lower().lstrip('.') if a.output else '')
           or cfg.get('format', 'mp3'))
    if fmt not in ('mp3', 'wav', 'flac'):
        fmt = 'mp3'
    chunks = build_chunks(segments, VOICES[voice], voice_en)
    n_en = sum(1 for v, _ in chunks if v == voice_en)
    print(f'Lektor: {VOICES[voice]}, rate {rate}, tabele={tabele}, '
          f'nagłówki={naglowki}, wstawki EN: {n_en}, {total} znaków '
          f'→ {out.with_suffix("." + fmt)}')
    mp3 = out.with_suffix('.mp3')
    try:
        asyncio.run(synth(chunks, mp3, rate))
        # WAV/FLAC: dekodowanie z mp3 96 kbps — zero DODATKOWEJ straty
        # (endpoint nie daje PCM; 96k to maksimum jakości tego serwisu)
        final = mp3 if fmt == 'mp3' else _convert(mp3, fmt)
    finally:
        try:
            PROGRESS_FILE.unlink()
        except FileNotFoundError:
            pass
    print(f'OK: {final} ({final.stat().st_size >> 10} KB)')

    # kopia do biblioteki muzycznej konsoli (TF-1/Music) — apka Music widzi
    # lektora obok zwykłej muzyki; wyłączenie: kopiuj_do = nie.
    # PODZIAŁ TEMATYCZNY: podfolder per projekt (z .../projekty/<nazwa>/...),
    # widoczny jako folder w playerze konsoli.
    dst_dir = cfg.get('kopiuj_do', '/mnt/mmc/Music/Lektor')
    if dst_dir.lower() not in ('', 'nie', 'off'):
        try:
            import shutil
            parts = final.resolve().parts
            proj = parts[parts.index('projekty') + 1] \
                if 'projekty' in parts else 'inne'
            d = Path(dst_dir) / proj
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final, d / final.name)
            print(f'Kopia w bibliotece Music: {d / final.name}')
        except Exception as e:
            print(f'UWAGA: kopia do {dst_dir} nieudana: {e}')


if __name__ == '__main__':
    main()
