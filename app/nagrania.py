"""Nagrania lektora: rozpoznanie pliku nagrania, usunięcie z plikami
towarzyszącymi i wygasanie po N dniach.

Jedno miejsce dla serwera (przycisk 🗑 w podglądzie, sprzątanie co godzinę)
i testów. Bez zależności spoza biblioteki standardowej.

Nagranie lektora = plik `<nazwa>_lektor.<rozszerzenie audio>`; obok mogą leżeć
`<nazwa>_lektor.cues.json` (czasy zdań dla ?read=1) i `<nazwa>_lektor.chapters.json`
(rozdziały) — usuwane razem z nagraniem, bo bez niego nie mają sensu.
"""
import os
import time
from pathlib import Path

# Preferowana kolejność: FLAC > MP3 > ... (serwer szuka nagrania w tej kolejności)
AUDIO_EXT = ('.flac', '.mp3', '.wav', '.ogg', '.m4a', '.opus')
PRZYROSTEK = '_lektor'
TOWARZYSZACE = ('.cues.json', '.chapters.json')
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
