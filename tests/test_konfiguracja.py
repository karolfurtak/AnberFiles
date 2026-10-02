"""Ustawienia instancji: wartości domyślne Anbernica, instancja Jarvis,
priorytet zmiennych środowiska, warunki startu, brak ścieżek /mnt w kodzie."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import APP, PRZYKLAD_JARVIS, wolny_port, zbuduj_jarvis


def test_bez_pliku_ustawien_wartosci_anbernica():
    import konfiguracja
    k = konfiguracja.wczytaj(env={})
    assert k.katalog_glowny == Path('/mnt/data/sprawozdania')
    assert k.katalog_danych == Path('/mnt/data')
    assert k.port == 8765
    assert k.host == '0.0.0.0'
    assert k.uzytkownik_www == 'anbernic'
    assert k.haslo_wymagane is False
    assert k.tylko_odczyt is False
    assert k.katalog_lektora is None
    assert k.logowanie == 'basic'
    # konsola: moduły włączone poza tymi, które z założenia jej nie dotyczą
    assert all(k.modul(n) for n in konfiguracja.MODULY
               if n not in konfiguracja.MODULY_DOMYSLNIE_WYLACZONE)
    assert not k.modul('przesluchania')
    # dzisiejsze ścieżki konsoli, jedna po drugiej
    assert k.rejestr_zdarzen == Path('/mnt/data/anberfiles-events.log')
    assert k.kolejka_lektora == Path('/mnt/data/lektor_queue.json')
    assert k.bledy_lektora == Path('/mnt/data/lektor_errors.log')
    assert k.bledy_druku == Path('/mnt/data/print_errors.log')
    assert k.pamiec_podr_docx == Path('/mnt/data/.cache/docx-preview')
    assert k.katalog_eksportu == Path('/mnt/data/sprawozdania/EXPORT')
    assert k.czytaj_tts == Path('/mnt/data/sprawozdania/EXPORT/czytaj_tts.py')
    assert k.lektor_conf == Path('/mnt/data/sprawozdania/EXPORT/lektor-ustawienia.conf')
    assert k.favikona == Path('/mnt/data/dev-skills/favicons/favicon.ico')
    assert k.katalog_zip_tmp == Path('/mnt/data/sprawozdania/.zip_tmp')
    assert k.kosz == Path('/mnt/data/sprawozdania/.kosz')


def test_zmienne_srodowiska_anbernica_nadal_dzialaja():
    import konfiguracja
    k = konfiguracja.wczytaj(env={'SERVER_PORT': '9001', 'SERVER_USER': 'ktos',
                                  'SERVER_HOST': '127.0.0.1', 'SERVER_PASS': 'x'})
    assert (k.port, k.uzytkownik_www, k.host, k.haslo) == (9001, 'ktos', '127.0.0.1', 'x')


def test_instancja_jarvis_z_przykladu(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path)
    k = konfiguracja.wczytaj(env={'ANBERFILES_CONF': str(conf)})
    assert k.nazwa_instancji == 'Jarvis'
    assert k.port == 8790
    assert k.uzytkownik_www == 'admin'
    assert k.haslo_wymagane is True
    assert k.tylko_odczyt is True
    assert k.logowanie == 'formularz'
    assert k.katalog_glowny == tmp_path / 'srv' / 'korzen'
    assert k.katalog_lektora == tmp_path / 'srv' / 'korzen' / 'lektor'
    assert k.katalog_eksportu == tmp_path / 'srv' / 'eksport'
    oczek = {'podglad_docx': True, 'eksport_docx': False, 'lektor': True,
             'lektor_opisy_ai': False, 'wylaczanie': False, 'druk': True,
             'bateria': False, 'kadrowanie': False}
    assert {n: k.modul(n) for n in oczek} == oczek


def test_przyklad_jarvis_bez_adresow_ip_i_hasel():
    import re
    tekst = PRZYKLAD_JARVIS.read_text(encoding='utf-8')
    for ogolny in ('0.0.0.0', '192.168.0.0/16', '100.64.0.0/10'):   # zakresy ogólne
        tekst = tekst.replace(ogolny, '')
    assert not re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', tekst)
    assert 'SERVER_PASS=' not in tekst


def test_zmienna_srodowiska_wygrywa_z_plikiem(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path)
    k = konfiguracja.wczytaj(conf, env={'SERVER_PORT': '9100'})
    assert k.port == 9100


def test_haslo_nie_z_pliku(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path)
    conf.write_text(conf.read_text(encoding='utf-8')
                    .replace('[serwer]', '[serwer]\nhaslo = sekret'), encoding='utf-8')
    with pytest.raises(konfiguracja.BladKonfiguracji):
        konfiguracja.wczytaj(conf, env={})


def test_wskazany_brakujacy_plik_to_blad(tmp_path):
    import konfiguracja
    with pytest.raises(konfiguracja.BladKonfiguracji):
        konfiguracja.wczytaj(env={'ANBERFILES_CONF': str(tmp_path / 'brak.conf')})


def test_zla_wartosc_przelacznika_to_blad(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path, druk='moze')
    with pytest.raises(konfiguracja.BladKonfiguracji):
        konfiguracja.wczytaj(conf, env={})


def _start_serwera(conf: Path, haslo: str):
    env = dict(os.environ, ANBERFILES_CONF=str(conf), SERVER_PASS=haslo,
               SERVER_PORT=str(wolny_port()), PYTHONIOENCODING='utf-8')
    return subprocess.run([sys.executable, str(APP / 'server.py')], env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          timeout=30)


def test_haslo_wymagane_puste_odmowa_startu(tmp_path):
    conf = zbuduj_jarvis(tmp_path, logowanie='basic')
    r = _start_serwera(conf, '')
    assert r.returncode != 0
    assert 'Brak hasła' in r.stderr
    assert 'SERVER_PASS' in r.stderr


def test_katalog_danych_niezapisywalny_odmowa_startu(tmp_path):
    # katalog_danych wskazuje PLIK — zapis próbny musi się nie udać na każdym systemie
    plik = tmp_path / 'to-nie-katalog'
    plik.write_text('x', encoding='utf-8')
    conf = zbuduj_jarvis(tmp_path, katalog_danych=plik.as_posix())
    r = _start_serwera(conf, 'haslo')
    assert r.returncode != 0
    assert 'katalog_danych' in r.stderr


@pytest.mark.skipif(sys.platform == 'win32' or (hasattr(os, 'geteuid') and os.geteuid() == 0),
                    reason='chmod nie odbiera prawa zapisu na Windows ani administratorowi')
def test_katalog_danych_bez_prawa_zapisu_odmowa_startu(tmp_path):
    conf = zbuduj_jarvis(tmp_path)
    dane = tmp_path / 'srv' / 'dane'
    dane.chmod(0o555)
    try:
        r = _start_serwera(conf, 'haslo')
    finally:
        dane.chmod(0o755)
    assert r.returncode != 0
    assert 'katalog_danych' in r.stderr


def test_brak_katalogu_glownego_odmowa_startu(tmp_path):
    conf = zbuduj_jarvis(tmp_path, katalog_glowny=(tmp_path / 'nie-ma').as_posix(),
                         katalog_lektora='')
    r = _start_serwera(conf, 'haslo')
    assert r.returncode != 0
    assert 'katalog_glowny' in r.stderr


def test_katalog_lektora_poza_glownym_odmowa(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path, katalog_lektora=(tmp_path / 'gdzies').as_posix())
    k = konfiguracja.wczytaj(conf, env={'SERVER_PASS': 'x'})
    bledy = konfiguracja.sprawdz_przy_starcie(k)
    assert any('katalog_lektora' in b for b in bledy)


def test_poprawne_ustawienia_bez_bledow_startu(tmp_path):
    import konfiguracja
    conf = zbuduj_jarvis(tmp_path)
    k = konfiguracja.wczytaj(conf, env={'SERVER_PASS': 'x'})
    assert konfiguracja.sprawdz_przy_starcie(k) == []
    assert not list((tmp_path / 'srv' / 'dane').iterdir())   # plik próbny sprzątnięty


def test_brak_sciezek_mnt_w_kodzie_poza_konfiguracja():
    wiersze = []
    for f in sorted(APP.glob('*.py')):
        if f.name == 'konfiguracja.py':
            continue
        for i, w in enumerate(f.read_text(encoding='utf-8').splitlines(), 1):
            if '/mnt/' in w:
                wiersze.append(f'{f.name}:{i}: {w.strip()}')
    assert wiersze == []


def test_brak_python3_z_path_w_kodzie():
    tekst = (APP / 'server.py').read_text(encoding='utf-8')
    assert "'python3'" not in tekst and '"python3"' not in tekst


@pytest.mark.skipif(not os.environ.get('ANBERFILES_CONF'),
                    reason='tylko wpis macierzy CI z plikiem ustawień instancji')
def test_ustawienia_instancji_z_srodowiska_ci():
    import konfiguracja
    k = konfiguracja.wczytaj()
    assert konfiguracja.sprawdz_przy_starcie(k) == []


def test_przyklad_jarvis_bez_nazw_osobowych_i_ze_wszystkimi_kluczami():
    """D4: przykład w publicznym repozytorium — login „admin", żadnych imion;
    każdy klucz ustawień (także nowe: dozwolone_hosty, soffice_bez_sieci,
    limit_druku_na_godzine, dziennik_dostepu) opisany w przykładzie."""
    import configparser
    import re
    import konfiguracja
    from conftest import PRZYKLAD_JARVIS
    tekst = PRZYKLAD_JARVIS.read_text(encoding='utf-8')
    assert re.search(r'(?m)^uzytkownik_www\s*=\s*admin\b', tekst)
    assert 'karol' not in tekst.lower()
    cp = configparser.ConfigParser(inline_comment_prefixes=(';', '#'), interpolation=None)
    cp.read_string(tekst)
    for sekcja, klucze in konfiguracja.KLUCZE.items():
        brak = [kl for kl in klucze if not cp.has_option(sekcja, kl)]
        assert brak == [], f'[{sekcja}] brak w przykładzie: {brak}'
