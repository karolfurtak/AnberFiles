"""Znacznik `<!-- lektor: koniec -->`: od tej linii do końca pliku lektor nie
czyta (załącznik ze szczegółami i źródłami — Karol 02.10, druga redakcja
opracowań). Podgląd i widok słuchania pokazują całość; odcisk treści liczony
z części czytanej (zmiana tylko w załączniku nie wymusza nowego nagrania).
Testy budują własne pliki w tmp_path."""
import json
import sys
from pathlib import Path

import pytest

from conftest import uruchom, wczytaj
from conftest import zbuduj_jarvis as _zbuduj_jarvis

REPO = Path(__file__).resolve().parents[1]
if str(REPO / 'tools') not in sys.path:
    sys.path.insert(0, str(REPO / 'tools'))

import czytaj_tts  # noqa: E402
import odswiezanie as _odsw  # noqa: E402

CZYTANA = '# Opracowanie\n\nWniosek główny: próg spełniony.\n'
ZALACZNIK = '## Załącznik\n\nSzczegóły pomiaru i źródło ZRODLO-XYZ.\n'
ZNACZNIK = '<!-- lektor: koniec -->'

PRZYPADKI = [
    CZYTANA,                                                   # brak znacznika
    CZYTANA + ZNACZNIK + '\n' + ZALACZNIK,
    CZYTANA + '  <!--  Lektor:Koniec  -->  \r\n' + ZALACZNIK,  # odstępy, wielkość liter
    CZYTANA + 'Tekst ' + ZNACZNIK + ' w środku linii.\n' + ZALACZNIK,   # nie znacznik
    ZNACZNIK + '\n' + ZALACZNIK,                               # sam załącznik
    CZYTANA + ZNACZNIK,                                        # znacznik na końcu pliku
]


@pytest.mark.parametrize('tekst', PRZYPADKI)
def test_lektor_i_odcisk_tna_tak_samo(tekst):
    """Jedna reguła w dwóch plikach (czytaj_tts.py kopiowany osobno) — zgodność."""
    assert czytaj_tts.czesc_czytana(tekst) == _odsw.czesc_czytana(tekst)


def test_znacznik_obcina_reszte_brak_znacznika_calosc():
    assert _odsw.czesc_czytana(CZYTANA + ZNACZNIK + '\n' + ZALACZNIK) == CZYTANA
    assert _odsw.czesc_czytana(CZYTANA + ZALACZNIK) == CZYTANA + ZALACZNIK
    # znacznik w środku linii (np. cytowany w tekście) nie jest znacznikiem
    t = CZYTANA + 'Tekst ' + ZNACZNIK + ' w środku.\n' + ZALACZNIK
    assert _odsw.czesc_czytana(t) == t


@pytest.fixture
def zrzut(tmp_path, monkeypatch, capsys):
    """czytaj_tts.py --dump-text na pliku z tmp_path → tekst po normalizacji."""
    monkeypatch.setattr(czytaj_tts, 'PROGRESS_FILE', tmp_path / 'postep.json')
    monkeypatch.setattr(czytaj_tts, 'LOCK_FILE', tmp_path / 'lektor.lock')
    if hasattr(czytaj_tts, 'PID_FILE'):
        monkeypatch.setattr(czytaj_tts, 'PID_FILE', tmp_path / 'lektor.pid')
    conf = tmp_path / 'lektor-ustawienia.conf'
    conf.write_text('kopiuj_do = nie\nopisuj_grafiki = nie\n', encoding='utf-8')
    monkeypatch.setattr(czytaj_tts, 'CONF_FILE', conf)

    def run(tekst):
        md = tmp_path / 'opracowanie.md'
        md.write_text(tekst, encoding='utf-8')
        monkeypatch.setattr(sys, 'argv', ['czytaj_tts.py', str(md), '--dump-text'])
        capsys.readouterr()
        czytaj_tts.main()
        return capsys.readouterr().out
    return run


def test_lektor_nie_czyta_zalacznika(zrzut):
    z = zrzut(CZYTANA + ZNACZNIK + '\n' + ZALACZNIK)
    assert 'Wniosek główny' in z
    assert 'ZRODLO' not in z and 'Szczegóły pomiaru' not in z


def test_bez_znacznika_lektor_czyta_calosc(zrzut):
    z = zrzut(CZYTANA + ZALACZNIK)
    assert 'Wniosek główny' in z and 'ZRODLO' in z


def test_odcisk_z_czesci_czytanej(tmp_path):
    p = tmp_path / 'o.md'
    p.write_text(CZYTANA + ZNACZNIK + '\n' + ZALACZNIK, encoding='utf-8')
    a = _odsw.odcisk_dokumentu(p)
    p.write_text(CZYTANA + ZNACZNIK + '\n' + ZALACZNIK + 'Nowe źródło.\n', encoding='utf-8')
    assert _odsw.odcisk_dokumentu(p) == a, 'zmiana tylko w załączniku zmieniła odcisk'
    p.write_text(CZYTANA + 'Nowe zdanie.\n' + ZNACZNIK + '\n' + ZALACZNIK, encoding='utf-8')
    assert _odsw.odcisk_dokumentu(p) != a
    p.write_text(CZYTANA + ZALACZNIK, encoding='utf-8')          # znacznik usunięty
    assert _odsw.odcisk_dokumentu(p) != a


def test_widok_sluchania_pokazuje_zalacznik_pod_napisem(tmp_path, auth):
    korzen = tmp_path / 'srv' / 'korzen'
    a = korzen / 'vault' / 'a'
    a.mkdir(parents=True)
    (a / 'notatka.md').write_text('---\nstatus: do-akceptacji\n---\n' + CZYTANA + ZNACZNIK
                                  + '\n' + ZALACZNIK, encoding='utf-8')
    (a / 'bez.md').write_text('---\nstatus: do-akceptacji\n---\n' + CZYTANA + ZALACZNIK,
                              encoding='utf-8')
    kl = korzen / 'lektor' / 'vault' / 'a'
    kl.mkdir(parents=True)
    (kl / 'notatka_lektor.mp3').write_bytes(b'ID3' + bytes(64))
    (kl / 'notatka_lektor.cues.json').write_text(json.dumps(
        [{'t': 0.0, 'text': 'Opracowanie.'}, {'t': 1.0, 'text': 'Wniosek główny: próg.'}]),
        encoding='utf-8')
    k = wczytaj(_zbuduj_jarvis(tmp_path, logowanie='basic'))

    async def sc(cl):
        z = await (await cl.get('/vault/a/notatka.md?sluchaj=1', auth=auth)).text()
        b = await (await cl.get('/vault/a/bez.md?sluchaj=1', auth=auth)).text()
        v = await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
        return z, b, v
    z, b, v = uruchom(k, sc)
    assert 'Dalej: załącznik — tylko do czytania' in z
    assert z.index('data-i="1"') < z.index('Dalej: załącznik') < z.index('ZRODLO-XYZ')
    assert z.count('class=s ') == 2                       # podświetlane tylko czytane zdania
    assert 'Dalej: załącznik' not in b and 'ZRODLO-XYZ' in b     # bez znacznika: całość
    assert 'ZRODLO-XYZ' in v                              # podgląd: całość
