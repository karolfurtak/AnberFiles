"""Nagrania lektora: przycisk 🗑 w podglądzie (także przy vaulcie tylko do
odczytu) i wygasanie po wiek_nagran_dni (sztuczny czas, wpis w rejestrze).

Karol 02.10: „Muszę mieć możliwość generowania audio, odtwarzania i jego
usuwania z widoku podglądu. Każde nagranie ma zniknąć po 3 dniach.”"""
import asyncio
import os
import time

from conftest import uruchom, wczytaj, zbuduj_anbernic
from conftest import zbuduj_jarvis as _zbuduj_jarvis

DOBA = 86400


def zbuduj_jarvis(tmp_path, **nadpisz):
    nadpisz.setdefault('logowanie', 'basic')
    return _zbuduj_jarvis(tmp_path, **nadpisz)


def _nagranie(tmp_path, wiek_s=0.0, nazwa='notatka'):
    """vault/a/<nazwa>.md + nagranie w katalogu lektora (z plikami towarzyszącymi)."""
    korzen = tmp_path / 'srv' / 'korzen'
    a = korzen / 'vault' / 'a'
    a.mkdir(parents=True, exist_ok=True)
    (a / f'{nazwa}.md').write_text('# A\n\nZdanie.\n', encoding='utf-8')
    kl = korzen / 'lektor' / 'vault' / 'a'
    kl.mkdir(parents=True, exist_ok=True)
    aud = kl / f'{nazwa}_lektor.mp3'
    aud.write_bytes(b'ID3' + bytes(64))
    pliki = [aud, kl / f'{nazwa}_lektor.cues.json', kl / f'{nazwa}_lektor.chapters.json']
    for p in pliki[1:]:
        p.write_text('[]', encoding='utf-8')
    t = time.time() - wiek_s
    for p in pliki:
        os.utime(p, (t, t))
    return a, pliki


def _rejestr(tmp_path):
    p = tmp_path / 'srv' / 'dane' / 'anberfiles-events.log'
    return p.read_text(encoding='utf-8') if p.exists() else ''


# ── Usuwanie z podglądu ─────────────────────────────────────────────────────

def test_podglad_ma_przycisk_usun_i_czas_wygasniecia(tmp_path, auth):
    a, pliki = _nagranie(tmp_path, wiek_s=DOBA - 1800)   # zostało 2 dni 0,5 h
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        return await (await cl.get('/vault/a/notatka.md?view=1', auth=auth)).text()
    html = uruchom(k, sc)
    assert 'id="lekdel"' in html
    assert 'data-u="/lektor/vault/a/notatka_lektor.mp3"' in html
    assert '⏳ 2 dni<' in html


def test_podglad_bez_nagrania_bez_przycisku_usun(tmp_path, auth):
    korzen = tmp_path / 'srv' / 'korzen' / 'vault'
    korzen.mkdir(parents=True)
    (korzen / 'n.md').write_text('# N\n', encoding='utf-8')
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        return await (await cl.get('/vault/n.md?view=1', auth=auth)).text()
    assert 'id="lekdel"' not in uruchom(k, sc)


def test_usuniecie_nagrania_przy_tylko_odczycie(tmp_path, auth):
    a, pliki = _nagranie(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))
    assert k.tylko_odczyt

    async def sc(cl):
        r = await cl.delete('/lektor/vault/a/notatka_lektor.mp3', auth=auth)
        return r.status, await r.json()
    s, j = uruchom(k, sc)
    assert s == 200 and j['nagranie'] is True
    assert not any(p.exists() for p in pliki), 'nagranie lub pliki towarzyszące zostały'
    assert (a / 'notatka.md').exists()
    assert 'nagranie usunięte ręcznie: lektor/vault/a/notatka_lektor.mp3' in _rejestr(tmp_path)


def test_usuwanie_innych_plikow_dalej_zablokowane(tmp_path, auth):
    a, pliki = _nagranie(tmp_path)
    (pliki[0].parent / 'notatka.txt').write_text('x', encoding='utf-8')
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        return ((await cl.delete('/vault/a/notatka.md', auth=auth)).status,
                (await cl.delete('/lektor/vault/a/notatka.txt', auth=auth)).status,
                (await cl.delete('/lektor/vault/a/notatka_lektor.cues.json',
                                 auth=auth)).status)
    assert uruchom(k, sc) == (403, 403, 403)
    assert (a / 'notatka.md').exists() and pliki[0].exists()


def test_usuniecie_nagrania_bez_naglowka_strony_403(tmp_path, auth):
    a, pliki = _nagranie(tmp_path)
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        return (await cl.delete('/lektor/vault/a/notatka_lektor.mp3', auth=auth)).status
    assert uruchom(k, sc, naglowek=False) == 403
    assert pliki[0].exists()


# ── Wygasanie po 3 dniach ───────────────────────────────────────────────────

def test_wygasanie_sztuczny_czas(tmp_path):
    import server
    _, stare = _nagranie(tmp_path, nazwa='stara')
    _, swieze = _nagranie(tmp_path, nazwa='swieza')
    k = wczytaj(zbuduj_jarvis(tmp_path))
    assert k.wiek_nagran_dni == 3
    server.zastosuj_konfiguracje(k)
    teraz = time.time()
    t_stare = teraz - 3 * DOBA - 60                     # 3 dni i 1 minuta
    t_swieze = teraz - 3 * DOBA + 3600                  # 2 dni 23 h
    for p in stare:
        os.utime(p, (t_stare, t_stare))
    for p in swieze:
        os.utime(p, (t_swieze, t_swieze))
    wynik = server.sprzataj_wygasle_nagrania(teraz=teraz)
    assert [p.name for p, _, blad in wynik if blad is None] == ['stara_lektor.mp3']
    assert not any(p.exists() for p in stare)
    assert all(p.exists() for p in swieze)
    rej = _rejestr(tmp_path)
    assert 'nagranie wygasło' in rej and 'stara_lektor.mp3' in rej
    assert 'można wygenerować ponownie' in rej


def test_wiek_zero_nic_nie_usuwa(tmp_path):
    import server
    _, pliki = _nagranie(tmp_path, wiek_s=30 * DOBA)
    k = wczytaj(zbuduj_jarvis(tmp_path, wiek_nagran_dni='0'))
    server.zastosuj_konfiguracje(k)
    assert server.sprzataj_wygasle_nagrania() == []
    assert pliki[0].exists()


def test_anbernic_nagrania_nie_wygasaja(tmp_path):
    k = wczytaj(zbuduj_anbernic(tmp_path))
    assert k.wiek_nagran_dni == 0 and k.katalog_lektora is None


def test_sprzatanie_przy_starcie_serwera(tmp_path, auth):
    _, pliki = _nagranie(tmp_path, wiek_s=4 * DOBA)
    k = wczytaj(zbuduj_jarvis(tmp_path))

    async def sc(cl):
        for _ in range(50):                             # pętla startuje w tle
            if not pliki[0].exists():
                return True
            await asyncio.sleep(0.1)
        return False
    assert uruchom(k, sc), 'nagranie starsze niż 3 dni przetrwało start serwera'


def test_opis_pozostalo():
    import nagrania
    assert nagrania.opis_pozostalo(2 * DOBA + 5 * 3600 + 10) == '2 dni 5 h'
    assert nagrania.opis_pozostalo(DOBA + 10) == '1 dzień'
    assert nagrania.opis_pozostalo(7 * 3600 + 1) == '7 h'
    assert nagrania.opis_pozostalo(100) == '< 1 h'
    assert nagrania.opis_pozostalo(float('inf')) == ''
