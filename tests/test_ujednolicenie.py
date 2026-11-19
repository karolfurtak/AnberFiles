"""Ujednolicenie instancji (19.11.2026, plan v3): jedna baza kodu dla :8790
(Jarvis) i :8766 (Sprawozdania, następca sprawozdania-server.py).

Polecenie Karola: „Te aplikacje nalezy ujednolicić do wersji najnowszej.
I warto dodać przycisk przejscia z jednej do drugiej.”

T1 DOCX przy tylko_odczyt · T2 kontrakt eksportera (zip, word/document.xml,
<m:oMath>) · T3 katalog główny nietknięty (SHA-256 drzewa) · T4 przycisk ⇄
w listingu, ?view=1 i logowaniu · T5 /_z_partnera · T6 przyciski nadzbiorem
wzorca · T7 pdf.js, przenoszenie, obrót, polecenie po wgraniu, język lektora
· T8 przykłady ustawień. Testy budują własne drzewa w tmp_path.
"""
import hashlib
import io
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import aiohttp
import pytest

from conftest import (ATRAPA_TTS, HASLO, REPO, uruchom, wczytaj, zbuduj_jarvis)

AU = aiohttp.BasicAuth('admin', HASLO)
AU_SPR = aiohttp.BasicAuth('anbernic', HASLO)
PRZYKLAD_SPR = REPO / 'konfiguracja' / 'sprawozdania.conf.przyklad'

# Atrapa export_to_docx.py o kontrakcie wywołania kanonicznego skryptu
# (md -o out --base-dir b): prawdziwy zip DOCX; równanie $…$ → <m:oMath>.
# Zapisuje też plik obok wyniku i w bieżącym katalogu (cwd = katalog silnika),
# żeby test T3 wykrył zapis do katalogu głównego.
ATRAPA_DOCX = r'''
import argparse, zipfile
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument('md'); ap.add_argument('-o'); ap.add_argument('--base-dir')
a = ap.parse_args()
md = Path(a.md).read_text(encoding='utf-8')
xml = '<w:document xmlns:w="w" xmlns:m="m"><w:body>'
xml += '<m:oMath>x</m:oMath>' if '$' in md else ''
xml += '</w:body></w:document>'
with zipfile.ZipFile(a.o, 'w') as z:
    z.writestr('[Content_Types].xml', '<Types/>')
    z.writestr('word/document.xml', xml)
Path(a.o).with_suffix('.base-dir.txt').write_text(a.base_dir, encoding='utf-8')
'''


def _skrot_drzewa(korzen: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(korzen.rglob('*')):
        h.update(p.relative_to(korzen).as_posix().encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _silnik(tmp_path) -> Path:
    s = tmp_path / 'mnt' / 'sprawozdania' / 'EXPORT'
    s.mkdir(parents=True, exist_ok=True)
    (s / 'export_to_docx.py').write_text(ATRAPA_DOCX, encoding='utf-8')
    return s


def _jarvis(tmp_path, **nadpisz):
    nadpisz.setdefault('logowanie', 'basic')       # testy jak test_serwer.py
    _silnik(tmp_path)
    conf = zbuduj_jarvis(tmp_path, **nadpisz)
    tekst = conf.read_text(encoding='utf-8').replace(
        '/mnt/data/sprawozdania', (tmp_path / 'mnt' / 'sprawozdania').as_posix())
    conf.write_text(tekst, encoding='utf-8')
    korzen = tmp_path / 'srv' / 'korzen'
    proj = korzen / 'vault' / 'proj'
    (proj / 'processed').mkdir(parents=True)
    (proj / 'processed' / 'a.md').write_text('# A\n\nWzór $E=mc^2$.\n', encoding='utf-8')
    (proj / 'b.pdf').write_bytes(b'%PDF-1.4\n%atrapa\n')
    return wczytaj(conf), korzen


def _sprawozdania(tmp_path, **nadpisz):
    """Instancja z konfiguracja/sprawozdania.conf.przyklad, katalogi w tmp_path."""
    spr = tmp_path / 'mnt' / 'sprawozdania'
    tekst = PRZYKLAD_SPR.read_text(encoding='utf-8')
    tekst = (tekst.replace('/mnt/data/sprawozdania', spr.as_posix())
             .replace('/mnt/data/', (tmp_path / 'mnt').as_posix() + '/')
             .replace('/srv/anberfiles', (tmp_path / 'srv').as_posix()))
    for klucz, wart in nadpisz.items():
        tekst, n = re.subn(rf'(?m)^{klucz}\s*=.*$', f'{klucz} = {wart}', tekst)
        assert n == 1, klucz
    for d in ('srv/dane-sprawozdania', 'mnt/sprawozdania/projekty/p1/processed',
              'mnt/sprawozdania/projekty/p1/exports', 'mnt/sprawozdania/projekty/p1/input/zalaczniki',
              'mnt/sprawozdania/inne'):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    silnik = _silnik(tmp_path)
    (silnik / 'czytaj_tts.py').write_text(ATRAPA_TTS, encoding='utf-8')
    (spr / 'projekty' / 'p1' / 'processed' / 'r.md').write_text('# R\n\n$a$\n', encoding='utf-8')
    conf = tmp_path / 'sprawozdania.conf'
    conf.write_text(tekst, encoding='utf-8')
    return wczytaj(conf), spr


# ── T1–T3: DOCX także przy tylko_odczyt, wynik w katalogu danych ─────────────

def test_t1_przycisk_docx_przy_tylko_odczycie(tmp_path):
    k, _ = _jarvis(tmp_path)
    assert k.tylko_odczyt and k.modul('eksport_docx')

    async def sc(cl):
        return await (await cl.get('/vault/proj/processed/a.md?view=1', auth=AU)).text()
    assert 'id="docxbtn"' in uruchom(k, sc)


def test_t1_bez_katalogu_wyniku_tylko_odczyt_bez_przycisku(tmp_path):
    """Kontrola czułości: tylko_odczyt bez katalog_wyniku_docx = jak dotąd (403)."""
    k, _ = _jarvis(tmp_path, katalog_wyniku_docx='')
    assert k.katalog_wyniku_docx is None

    async def sc(cl):
        md = await (await cl.get('/vault/proj/processed/a.md?view=1', auth=AU)).text()
        st = (await cl.post('/vault/proj/processed/a.md?docx=1', auth=AU)).status
        return md, st
    md, st = uruchom(k, sc)
    assert 'id="docxbtn"' not in md and st == 403


def test_t2_t3_eksport_docx_do_katalogu_danych(tmp_path):
    k, korzen = _jarvis(tmp_path)
    przed = _skrot_drzewa(korzen)

    async def sc(cl):
        r = await cl.post('/vault/proj/processed/a.md?docx=1', auth=AU)
        return r.status, r.headers.get('Content-Type', ''), await r.read()
    st, typ, tresc = uruchom(k, sc)
    assert st == 200, tresc[:300]
    assert typ.startswith('application/vnd.openxmlformats-officedocument.wordprocessingml')
    with zipfile.ZipFile(io.BytesIO(tresc)) as z:
        assert '<m:oMath' in z.read('word/document.xml').decode()
    assert _skrot_drzewa(korzen) == przed                    # T3: katalog główny nietknięty
    wynik = tmp_path / 'srv' / 'dane' / 'eksport-docx' / 'vault' / 'proj' / 'processed' / 'a.docx'
    assert wynik.is_file()


def test_t3_katalog_wyniku_w_katalogu_glownym_blokuje_start(tmp_path):
    import konfiguracja
    k, korzen = _jarvis(tmp_path, katalog_wyniku_docx=(tmp_path / 'srv' / 'korzen' / 'x').as_posix())
    bledy = konfiguracja.sprawdz_przy_starcie(k)
    assert any('katalog_wyniku_docx' in b for b in bledy)


def test_eksport_sprawozdan_do_exports_i_zalaczniki(tmp_path):
    """Instancja Sprawozdania: wynik w <projekt>/exports/ (jak stara usługa),
    --base-dir = input/, gdy jest input/zalaczniki/ (przeniesione z 8766)."""
    k, spr = _sprawozdania(tmp_path)

    async def sc(cl):
        r = await cl.post('/projekty/p1/processed/r.md?docx=1', auth=AU_SPR)
        return r.status
    assert uruchom(k, sc) == 200
    exp = spr / 'projekty' / 'p1' / 'exports'
    assert (exp / 'r.docx').is_file()
    assert Path((exp / 'r.base-dir.txt').read_text(encoding='utf-8')) == \
        spr / 'projekty' / 'p1' / 'input'


# ── T4–T5: przycisk ⇄ i trasa /_z_partnera ───────────────────────────────────

def test_t4_przycisk_partnera_w_listingu_podgladzie_i_logowaniu(tmp_path):
    k, _ = _jarvis(tmp_path)

    async def sc(cl):
        lst = await (await cl.get('/vault/proj/', auth=AU)).text()
        md = await (await cl.get('/vault/proj/processed/a.md?view=1', auth=AU)).text()
        return lst, md
    lst, md = uruchom(k, sc)
    assert '⇄ Sprawozdania' in lst and '⇄ Sprawozdania' in md
    assert 'http://wykonawca.local:8766/_z_partnera?p=vault/proj"' in lst \
        or 'http://wykonawca.local:8766/_z_partnera?p=vault/proj&' in lst
    assert '/_z_partnera?p=vault/proj/processed/a.md&amp;view=1' in md


def test_t4_przycisk_na_ekranie_logowania_formularzem(tmp_path):
    k, _ = _jarvis(tmp_path, logowanie='formularz')
    assert k.logowanie == 'formularz'
    import logowanie
    (k.katalog_auth).mkdir(parents=True, exist_ok=True)
    logowanie.ustaw_haslo(k.katalog_auth, HASLO)

    async def sc(cl):
        r = await cl.get('/vault/proj/processed/a.md?view=1', headers={'Accept': 'text/html'},
                         allow_redirects=False)
        return r.status, await r.text()
    st, html = uruchom(k, sc)
    assert st == 401 and 'name="haslo"' in html
    assert '⇄ Sprawozdania' in html
    assert '_z_partnera?p=vault/proj/processed/a.md&amp;view=1' in html


def test_t4_bez_partnera_bez_przycisku(tmp_path):
    k, _ = _jarvis(tmp_path, partner_adres='')

    async def sc(cl):
        return await (await cl.get('/vault/proj/', auth=AU)).text()
    assert '⇄' not in uruchom(k, sc)


def test_t5_z_partnera_przekierowania(tmp_path):
    k, spr = _sprawozdania(tmp_path)
    (spr / 'inne' / 'x.md').write_text('x', encoding='utf-8')

    async def sc(cl):
        wynik = {}
        for p in ('inne/x.md', 'inne', 'inne/brak/brak.md', 'brak/brak.md', '',
                  'projekty/p1/processed/r.md'):
            r = await cl.get('/_z_partnera', params={'p': p, 'view': '1'}, auth=AU_SPR,
                             allow_redirects=False)
            wynik[p] = (r.status, r.headers.get('Location'))
        return wynik
    w = uruchom(k, sc)
    assert w['inne/x.md'] == (302, '/inne/x.md?view=1')
    assert w['inne'] == (302, '/inne/')
    assert w['inne/brak/brak.md'] == (302, '/inne/')
    assert w['brak/brak.md'] == (302, '/')
    assert w[''] == (302, '/')
    assert w['projekty/p1/processed/r.md'][1] == '/projekty/p1/processed/r.md?view=1'


@pytest.mark.parametrize('p', ['../x', 'a/../../x', '/etc/passwd', 'C:/Windows', 'a\\..\\b',
                               'a\x00b', 'a\nb', './a', '//evil.example/x'])
def test_t5_z_partnera_zle_sciezki_400(tmp_path, p):
    k, _ = _sprawozdania(tmp_path)

    async def sc(cl):
        r = await cl.get('/_z_partnera', params={'p': p}, auth=AU_SPR, allow_redirects=False)
        return r.status, r.headers.get('Location')
    assert uruchom(k, sc) == (400, None)


def test_t5_z_partnera_dowiazanie_na_zewnatrz_400(tmp_path):
    k, spr = _sprawozdania(tmp_path)
    na_zewnatrz = tmp_path / 'tajne'
    na_zewnatrz.mkdir()
    (na_zewnatrz / 's.txt').write_text('tajne', encoding='utf-8')
    try:
        os.symlink(na_zewnatrz, spr / 'wyjscie', target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('system bez dowiązań symbolicznych dla tego użytkownika')

    async def sc(cl):
        r = await cl.get('/_z_partnera', params={'p': 'wyjscie/s.txt'}, auth=AU_SPR,
                         allow_redirects=False)
        return r.status
    assert uruchom(k, sc) == 400


def test_t5_z_partnera_wymaga_logowania_i_wraca_po_nim(tmp_path):
    """:8790 (formularz): bez sesji ekran logowania z dalej=/_z_partnera?…;
    dalej z obcym hostem → katalog główny (brak otwartego przekierowania)."""
    k, _ = _jarvis(tmp_path, logowanie='formularz')
    import logowanie
    k.katalog_auth.mkdir(parents=True, exist_ok=True)
    logowanie.ustaw_haslo(k.katalog_auth, HASLO)

    async def sc(cl):
        r = await cl.get('/_z_partnera?p=vault/proj/b.pdf&view=1',
                         headers={'Accept': 'text/html'}, allow_redirects=False)
        html = await r.text()
        return r.status, html
    st, html = uruchom(k, sc)
    assert st == 401
    assert 'value="/_z_partnera?p=vault/proj/b.pdf&amp;view=1"' in html
    assert logowanie._bezpieczne_dalej('//evil.example/') == '/'
    assert logowanie._bezpieczne_dalej('/\\evil.example/') == '/'
    assert logowanie._bezpieczne_dalej('/_z_partnera?p=a&view=1') == '/_z_partnera?p=a&view=1'


# ── T6: przyciski instancji Sprawozdania ⊇ wzorzec starej usługi :8766 ───────

def test_t6_przyciski_sprawozdan_nadzbiorem_wzorca(tmp_path):
    k, spr = _sprawozdania(tmp_path)

    async def sc(cl):
        lst = await (await cl.get('/projekty/p1/processed/', auth=AU_SPR)).text()
        md = await (await cl.get('/projekty/p1/processed/r.md?view=1', auth=AU_SPR)).text()
        return lst, md
    lst, md = uruchom(k, sc)
    # etykiety ze zrzutu wzorca :8766 (19.11 09:20) + nowe (⇄)
    for e in ('⧉ kopiuj nazwy plików', '🌳 drzewo', '📁+ nowy folder', '🔊 kolejka lektora',
              '🗜 ZIP folderu', '⇄ Jarvis'):
        assert e in lst, e
    for e in ('A+', 'A−', 'Kod', 'Render', '⬇ DOCX', '⬇ pobierz', '🖨 drukuj',
              '🔊 lektor', '⇄ Jarvis'):
        assert e in md, e
    assert 'afFetch(src+"?move="' in lst                    # przeciąganie do folderu


# ── T7: pdf.js, przenoszenie, obrót, polecenie po wgraniu, język lektora ─────

def test_t7_pdfjs_pliki_w_repozytorium():
    kat = REPO / 'app' / 'static' / 'pdfjs'
    for n in ('pdf.min.mjs', 'pdf.worker.min.mjs'):
        assert (kat / n).stat().st_size > 100 * 1024, n
    assert 'Apache License' in (kat / 'LICENSE.txt').read_text(encoding='utf-8')


def test_t7_pdfjs_trasa_i_podglad_pdf(tmp_path):
    k, spr = _sprawozdania(tmp_path)
    (spr / 'inne' / 'd.pdf').write_bytes(b'%PDF-1.4\n%atrapa\n')

    async def sc(cl):
        js = await cl.get('/_pdfjs/pdf.min.mjs', auth=AU_SPR)
        zly = await cl.get('/_pdfjs/../konfiguracja.py', auth=AU_SPR)
        inny = await cl.get('/_pdfjs/server.py', auth=AU_SPR)
        widok = await (await cl.get('/inne/d.pdf?view=1', auth=AU_SPR)).text()
        surowy = await cl.get('/inne/d.pdf?pdf=1', auth=AU_SPR)
        return (js.status, js.headers['Content-Type'], zly.status, inny.status, widok,
                (await surowy.read())[:4], surowy.headers['Content-Type'])
    st, typ, zly, inny, widok, pdf, typ_pdf = uruchom(k, sc)
    assert st == 200 and typ.startswith('text/javascript')
    assert zly == 404 and inny == 404
    assert 'import * as pdfjsLib from "/_pdfjs/pdf.min.mjs"' in widok
    assert 'cdn.' not in widok.split('<script type="module">')[1]      # bez CDN
    assert '⇄ Jarvis' in widok and '⬇ PDF' in widok
    assert pdf == b'%PDF' and typ_pdf == 'application/pdf'


def test_t7_pdfjs_wylaczony_404(tmp_path):
    k, _ = _sprawozdania(tmp_path, podglad_pdfjs='nie')

    async def sc(cl):
        return (await cl.get('/_pdfjs/pdf.min.mjs', auth=AU_SPR)).status
    assert uruchom(k, sc) == 404


def test_t7_przeniesienie_i_obrot(tmp_path):
    from PIL import Image
    k, spr = _sprawozdania(tmp_path)
    (spr / 'inne' / 'm.txt').write_text('m', encoding='utf-8')
    Image.new('RGB', (40, 20), 'red').save(spr / 'inne' / 'o.png')

    async def sc(cl):
        w = {}
        w['move'] = (await cl.post('/inne/m.txt?move=projekty/p1', auth=AU_SPR)).status
        (spr / 'projekty' / 'p1' / 'processed' / 'm.txt').write_text('inny', encoding='utf-8')
        w['409'] = (await cl.post('/projekty/p1/m.txt?move=projekty/p1/processed',
                                  auth=AU_SPR)).status
        w['poza'] = (await cl.post('/projekty/p1/m.txt?move=../..', auth=AU_SPR)).status
        w['obrot'] = (await cl.post('/inne/o.png?rotate=90', auth=AU_SPR)).status
        lst = await (await cl.get('/inne/o.png?view=1', auth=AU_SPR)).text()
        return w, lst
    w, html = uruchom(k, sc)
    assert w == {'move': 200, '409': 409, 'poza': 400, 'obrot': 200}, w
    assert (spr / 'projekty' / 'p1' / 'm.txt').read_text(encoding='utf-8') == 'm'
    assert (spr / 'projekty' / 'p1' / 'processed' / 'm.txt').read_text(encoding='utf-8') == 'inny'
    assert Image.open(spr / 'inne' / 'o.png').size == (20, 40)
    assert list((spr / '.kosz').glob('o.png.orig-*'))             # oryginał w Koszu
    assert 'id="rotl"' in html and 'id="rotr"' in html
    assert 'afFetch(Q+"?rotate="' in html and '🖨 drukuj' in html


def test_t7_przenoszenie_403_przy_tylko_odczycie_i_wylaczonym(tmp_path):
    k, korzen = _jarvis(tmp_path, przenoszenie='tak')
    przed = _skrot_drzewa(korzen)

    async def sc(cl):
        return ((await cl.post('/vault/proj/b.pdf?move=vault', auth=AU)).status,
                (await cl.post('/vault/proj/b.pdf?rotate=90', auth=AU)).status)
    assert uruchom(k, sc) == (403, 403)
    assert _skrot_drzewa(korzen) == przed
    k2, spr = _sprawozdania(tmp_path / 'b', przenoszenie='nie')
    (spr / 'inne' / 'm.txt').write_text('m', encoding='utf-8')

    async def sc2(cl):
        lst = await (await cl.get('/inne/', auth=AU_SPR)).text()
        return (await cl.post('/inne/m.txt?move=projekty', auth=AU_SPR)).status, lst
    st, lst = uruchom(k2, sc2)
    assert st == 403 and '?move=' not in lst


def test_t7_polecenie_po_wgraniu_tylko_do_exports(tmp_path, monkeypatch):
    prog = tmp_path / 'sync.sh'
    prog.write_text('#!/bin/sh\n', encoding='utf-8')
    k, spr = _sprawozdania(tmp_path, po_wgraniu_polecenie=prog.as_posix())
    wywolania = []
    prawdziwy = subprocess.Popen

    def atrapa(argv, *a, **kw):
        if argv == [prog.as_posix()]:
            wywolania.append(argv)
            return None
        return prawdziwy(argv, *a, **kw)            # inne podprocesy serwera bez zmian
    monkeypatch.setattr(subprocess, 'Popen', atrapa)

    async def sc(cl):
        for cel in ('/projekty/p1/exports/', '/inne/'):
            fd = aiohttp.FormData()
            fd.add_field('file', io.BytesIO(b'xyz'), filename='n.mp3')
            assert (await cl.post(cel, data=fd, auth=AU_SPR)).status == 200
    uruchom(k, sc)
    assert wywolania == [[prog.as_posix()]]


def test_t7_polecenie_po_wgraniu_walidacja(tmp_path):
    import konfiguracja
    for zle in ('sync.sh', '/usr/bin/x; rm -rf /', '/a b'):
        with pytest.raises(konfiguracja.BladKonfiguracji):
            _sprawozdania(tmp_path / str(abs(hash(zle))), po_wgraniu_polecenie=zle)


ATRAPA_TTS_LANG = ATRAPA_TTS.replace(
    "ap.add_argument('--silnik')", "ap.add_argument('--lang')")


def test_t7_jezyk_lektora_tylko_gdy_silnik_go_zna(tmp_path):
    """Silnik bota (EXPORT/czytaj_tts.py) zna --lang, nie zna --silnik: okno
    lektora pokazuje wybór języka i nie pokazuje silnika; --silnik nie trafia
    do wywołania (nieznany argument wywracał lektora)."""
    assert ATRAPA_TTS_LANG != ATRAPA_TTS
    k, spr = _sprawozdania(tmp_path)
    tts = tmp_path / 'mnt' / 'sprawozdania' / 'EXPORT' / 'czytaj_tts.py'
    tts.write_text(ATRAPA_TTS_LANG, encoding='utf-8')
    from conftest import czekaj_na_lektora

    async def sc(cl):
        md = await (await cl.get('/projekty/p1/processed/r.md?view=1', auth=AU_SPR)).text()
        r = await cl.post('/projekty/p1/processed/r.md?lektor=1&lang=en&silnik=edge&fmt=mp3',
                          auth=AU_SPR)
        assert r.status == 202, await r.text()
        await czekaj_na_lektora(cl, AU_SPR)
        return md
    md = uruchom(k, sc)
    assert 'window._LEK_JEZYKI=' in md and 'window._LEK_BEZ_SILNIKA=true' in md
    argv = (tts.parent / 'ostatnie_argv.txt').read_text(encoding='utf-8')
    assert '--lang' in argv and 'en' in argv and '--silnik' not in argv


# ── T8: przykłady ustawień obu instancji ─────────────────────────────────────

def test_t8_przyklady_wczytuja_sie_i_zly_klucz_to_blad(tmp_path):
    import konfiguracja
    k, _ = _sprawozdania(tmp_path)
    assert k.nazwa_instancji == 'Sprawozdania' and k.logowanie == 'basic'
    assert not k.tylko_odczyt and k.port == 8766
    assert k.partner_nazwa == 'Jarvis' and k.partner_adres.endswith(':8790')
    assert k.modul('podglad_pdfjs') and k.modul('przenoszenie') and k.modul('eksport_docx')
    assert not k.modul('przesluchania') and k.katalog_wyniku_docx is None
    assert k.kolejka_lektora == tmp_path / 'mnt' / 'lektor_queue.json'
    assert k.kosz == tmp_path / 'mnt' / 'sprawozdania' / '.kosz'
    zly = tmp_path / 'zly.conf'
    zly.write_text(PRZYKLAD_SPR.read_text(encoding='utf-8') + '\n[moduly]\n', encoding='utf-8')
    with pytest.raises(konfiguracja.BladKonfiguracji):
        konfiguracja.wczytaj(zly, env={})
    zly.write_text('[serwer]\npartner_adress = http://x\n', encoding='utf-8')
    with pytest.raises(konfiguracja.BladKonfiguracji):
        konfiguracja.wczytaj(zly, env={})
    zly.write_text('[serwer]\npartner_adres = javascript:alert(1)\n', encoding='utf-8')
    with pytest.raises(konfiguracja.BladKonfiguracji):
        konfiguracja.wczytaj(zly, env={})


def test_t8_przyklad_sprawozdan_wszystkie_klucze_bez_hasel():
    import configparser
    tekst = PRZYKLAD_SPR.read_text(encoding='utf-8')
    assert 'SERVER_PASS=' not in tekst
    assert not re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', tekst.replace('0.0.0.0', ''))
    cp = configparser.ConfigParser(inline_comment_prefixes=(';', '#'), interpolation=None)
    cp.read_string(tekst)
    assert set(cp.sections()) == {'serwer', 'katalogi', 'moduly'}


def test_t8_domyslne_anbernica_bez_zmian():
    """Nowe ustawienia domyślnie nie zmieniają konsoli Anbernic."""
    import konfiguracja
    k = konfiguracja.domyslna()
    assert k.partner_adres == '' and k.po_wgraniu_polecenie == ''
    assert k.katalog_wyniku_docx is None and k.katalog_silnika_docx == k.katalog_eksportu
    assert not k.modul('podglad_pdfjs') and not k.modul('przenoszenie')
    assert k.eksport_docx_dostepny is True


def test_jednostka_sprawozdan_utwardzona():
    j = (REPO / 'konfiguracja' / 'anberfiles-sprawozdania.service.przyklad').read_text(
        encoding='utf-8')
    for w in ('User=anberbot', 'ProtectSystem=strict', 'NoNewPrivileges=yes', 'PrivateTmp=yes',
              'Environment=ANBERFILES_CONF=/etc/anberfiles/sprawozdania.conf',
              'EnvironmentFile=/etc/sprawozdania-server.env',
              'ReadWritePaths=/mnt/data/sprawozdania /mnt/data/lektor_queue.json '
              '/srv/anberfiles/dane-sprawozdania'):
        assert w in j, w
