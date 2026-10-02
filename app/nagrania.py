"""Nagrania lektora: rozpoznanie pliku nagrania, usunięcie z plikami
towarzyszącymi i wygasanie po N dniach.

Jedno miejsce dla serwera (przycisk 🗑 w podglądzie, sprzątanie co godzinę)
i testów. Bez zależności spoza biblioteki standardowej.

Nagranie lektora = plik `<nazwa>_lektor.<rozszerzenie audio>`; obok mogą leżeć
`<nazwa>_lektor.cues.json` (czasy zdań dla ?read=1) i `<nazwa>_lektor.chapters.json`
(rozdziały) i `<nazwa>_lektor.zrodlo.json` (odcisk treści dokumentu — moduł
odswiezanie) — usuwane razem z nagraniem, bo bez niego nie mają sensu.
"""
import os
import time
from pathlib import Path

# Preferowana kolejność: FLAC > MP3 > ... (serwer szuka nagrania w tej kolejności)
AUDIO_EXT = ('.flac', '.mp3', '.wav', '.ogg', '.m4a', '.opus')
PRZYROSTEK = '_lektor'
TOWARZYSZACE = ('.cues.json', '.chapters.json', '.zrodlo.json')
SEKUND_NA_DOBE = 86400


def jest_nagraniem_lektora(p: Path) -> bool:
    """Plik audio lektora (`*_lektor.<audio>`); inne pliki — nigdy."""
    p = Path(p)
    return p.suffix.lower() in AUDIO_EXT and p.stem.endswith(PRZYROSTEK)


def pliki_nagrania(audio: Path) -> list:
    """Nagranie i jego pliki towarzyszące, które istnieją."""
    audio = Path(audio)
    wynik = [audio] if audio.exists() else []
    for przyrostek in TOWARZYSZACE:
        p = audio.with_name(audio.stem + przyrostek)
        if p.exists():
            wynik.append(p)
    return wynik


def usun_nagranie(audio: Path) -> list:
    """Usuwa nagranie z plikami towarzyszącymi (trwale — nagranie da się
    wygenerować ponownie). Zwraca nazwy usuniętych plików. Plik niebędący
    nagraniem lektora → ValueError (ochrona przed usunięciem dokumentu)."""
    audio = Path(audio)
    if not jest_nagraniem_lektora(audio):
        raise ValueError(f'{audio.name} nie jest nagraniem lektora')
    usuniete = []
    for p in pliki_nagrania(audio):
        p.unlink()
        usuniete.append(p.name)
    return usuniete


def pozostalo_s(audio: Path, wiek_dni: int, teraz: float = None) -> float:
    """Sekundy do wygaśnięcia nagrania (ujemne = już wygasło); wiek_dni ≤ 0
    = nagrania nie wygasają → +inf."""
    if wiek_dni <= 0:
        return float('inf')
    teraz = time.time() if teraz is None else teraz
    return Path(audio).stat().st_mtime + wiek_dni * SEKUND_NA_DOBE - teraz


# warstwa III: przepływność [kb/s] i częstotliwość [Hz] wg wersji MPEG (bity 4–3
# drugiego bajtu nagłówka: 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5). Piper przez
# ffmpeg zapisuje MPEG-2 mono 22 050 Hz z nagłówkiem Info (pomiar 02.10).
_MP3_KBPS = {3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),
             2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)}
_MP3_KBPS[0] = _MP3_KBPS[2]
_MP3_HZ = {3: (44100, 48000, 32000, 0), 2: (22050, 24000, 16000, 0), 0: (11025, 12000, 8000, 0)}


def czas_trwania_s(audio: Path):
    """Czas trwania nagrania w sekundach z nagłówka pliku (WAV, FLAC, MP3
    MPEG-1/2/2.5 warstwa III: Xing/Info albo stała przepływność); None = nie da się
    ustalić. Czyta najwyżej kilka kB — lista „Do przesłuchania” liczy to przy
    każdym wyświetleniu."""
    audio = Path(audio)
    ext = audio.suffix.lower()
    try:
        rozmiar = audio.stat().st_size
        with open(audio, 'rb') as f:
            glowa = f.read(16384)
    except OSError:
        return None
    try:
        if ext == '.wav':
            import wave
            with wave.open(str(audio), 'rb') as w:
                return w.getnframes() / float(w.getframerate())
        if ext == '.flac':
            if glowa[:4] != b'fLaC':
                return None
            si = glowa[8:8 + 34]                       # STREAMINFO (pierwszy blok)
            hz = (si[10] << 12) | (si[11] << 4) | (si[12] >> 4)
            probki = ((si[13] & 0x0F) << 32) | int.from_bytes(si[14:18], 'big')
            return probki / hz if hz and probki else None
        if ext == '.mp3':
            pocz = 0
            if glowa[:3] == b'ID3':
                pocz = 10 + ((glowa[6] & 0x7F) << 21 | (glowa[7] & 0x7F) << 14
                             | (glowa[8] & 0x7F) << 7 | (glowa[9] & 0x7F))
                with open(audio, 'rb') as f:
                    f.seek(pocz)
                    glowa = f.read(4096)
            else:
                glowa = glowa[:4096]
            i = next((k for k in range(len(glowa) - 3)
                      if glowa[k] == 0xFF and (glowa[k + 1] & 0xE6) == 0xE2
                      and (glowa[k + 1] >> 3) & 3 != 1), None)
            if i is None:
                return None
            wersja = (glowa[i + 1] >> 3) & 3
            kbps = _MP3_KBPS[wersja][glowa[i + 2] >> 4]
            hz = _MP3_HZ[wersja][(glowa[i + 2] >> 2) & 3]
            if not kbps or not hz:
                return None
            mono = (glowa[i + 3] >> 6) == 3
            if wersja == 3:
                probek, boczne = 1152, (17 if mono else 32)
            else:
                probek, boczne = 576, (9 if mono else 17)
            x = i + 4 + boczne                         # nagłówek Xing/Info
            if glowa[x:x + 4] in (b'Xing', b'Info') and glowa[x + 7] & 1:
                ramki = int.from_bytes(glowa[x + 8:x + 12], 'big')
                return ramki * probek / hz
            return (rozmiar - pocz - i) * 8 / (kbps * 1000)
    except (OSError, ValueError, IndexError, EOFError, Exception):
        return None
    return None


def opis_czasu(sekundy) -> str:
    """„12 min 30 s”, „45 s”, „1 h 05 min”."""
    if sekundy is None:
        return ''
    s = int(round(sekundy))
    if s >= 3600:
        return f'{s // 3600} h {s % 3600 // 60:02d} min'
    if s >= 60:
        return f'{s // 60} min {s % 60:02d} s'
    return f'{s} s'


def opis_pozostalo(sekundy: float) -> str:
    """„2 dni 5 h”, „7 h”, „< 1 h” — do paska podglądu."""
    if sekundy == float('inf'):
        return ''
    if sekundy <= 3600:
        return '< 1 h'
    godz = int(sekundy // 3600)
    dni, godz = divmod(godz, 24)
    if dni:
        return f'{dni} {"dzień" if dni == 1 else "dni"}' + (f' {godz} h' if godz else '')
    return f'{godz} h'


def wygasle(katalog: Path, wiek_dni: int, teraz: float = None) -> list:
    """Nagrania lektora w katalogu (rekurencyjnie) starsze niż wiek_dni
    (czas modyfikacji). wiek_dni ≤ 0 albo brak katalogu → pusta lista."""
    if wiek_dni <= 0 or katalog is None or not Path(katalog).is_dir():
        return []
    teraz = time.time() if teraz is None else teraz
    granica = teraz - wiek_dni * SEKUND_NA_DOBE
    wynik = []
    for korzen, katalogi, pliki in os.walk(katalog):
        katalogi[:] = [d for d in katalogi if not d.startswith('.')]
        for nazwa in pliki:
            p = Path(korzen) / nazwa
            if not jest_nagraniem_lektora(p):
                continue
            try:
                if p.stat().st_mtime < granica:
                    wynik.append(p)
            except OSError:
                continue
    return sorted(wynik)


def sprzataj(katalog: Path, wiek_dni: int, teraz: float = None) -> list:
    """Usuwa wygasłe nagrania. Zwraca [(ścieżka nagrania, wiek w godzinach,
    błąd albo None)] — błąd jednego pliku nie zatrzymuje pozostałych."""
    teraz = time.time() if teraz is None else teraz
    wynik = []
    for p in wygasle(katalog, wiek_dni, teraz):
        try:
            wiek_h = (teraz - p.stat().st_mtime) / 3600
            usun_nagranie(p)
            wynik.append((p, wiek_h, None))
        except OSError as e:
            wynik.append((p, 0.0, str(e)))
    return wynik
