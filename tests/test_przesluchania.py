"""Lista „Do przesłuchania”: notatki do decyzji, decyzja z uwagą, odsłuch,
eksport dla agenta laptopa, kafelek strony startowej.

Wymagania Karola 02.10 (to_do.md, pozycja „AnberFiles na Jarvisie: lektor
w podglądzie…”). Testy budują własny vault w tmp_path; po każdym scenariuszu
vault ma te same sumy kontrolne co przed nim (vault tylko do odczytu)."""
import hashlib
import json
import os
import stat
import sys

import pytest

from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic
from conftest import zbuduj_jarvis as _zbuduj_jarvis

ORIGIN_STARTOWA = 'http://serwer-testowy:8080'


def zbuduj_jarvis(tmp_path, **nadpisz):
    nadpisz.setdefault('logowanie', 'basic')
    return _zbuduj_jarvis(tmp_path, **nadpisz)


def _vault(tmp_path):
    """Vault z notatkami w różnych stanach + plik propozycji klasyfikacji."""
    v = tmp_path / 'srv' / 'korzen' / 'vault'
    (v / 'Zasoby' / 'MoCap').mkdir(parents=True)
    (v / 'Zasoby' / 'Rhino').mkdir(parents=True)
    (v / 'Zasoby' / 'prompty').mkdir(parents=True)
    (v / '.obsidian').mkdir()
    (v / 'Zasoby' / 'MoCap' / 'plan-kamer.md').write_text(
        '---\nstatus: do-akceptacji\nrodzaj: plan\n---\n# Plan kamer\n\nAkapit pierwszy.\n\n'
        'Akapit drugi.\n', encoding='utf-8')
    (v / 'Zasoby' / 'MoCap' / 'notatka-bez-statusu.md').write_text(
        '# Zwykła notatka\n\nBez statusu.\n', encoding='utf-8')
    (v / 'Zasoby' / 'Rhino' / 'koncepcja.md').write_text(
        '# Koncepcja wtyczki\n\nTreść.\n', encoding='utf-8')
    (v / 'Zasoby' / 'Rhino' / 'wdrozyc.md').write_text(
        '---\nstatus: zaakceptowane-niewdrozone\n---\n# Do wdrożenia\n', encoding='utf-8')
    (v / 'Zasoby' / 'prompty' / 'prompt.md').write_text('# Prompt\n', encoding='utf-8')
    (v / 'Zasoby' / 'MoCap' / 'zlosliwa.md').write_text(
        '---\nstatus: do-akceptacji\n---\n# <script>alert(1)</script>\n', encoding='utf-8')
    (v / '.obsidian' / 'ukryta.md').write_text('---\nstatus: do-akceptacji\n---\n',
                                               encoding='utf-8')
    kand = v / 'Zasoby' / 'klasyfikacja.md'
    kand.write_text(
        '# Propozycja\n\n| Ścieżka | Rodzaj | Status |\n|---|---|---|\n'
        '| `Zasoby/Rhino/koncepcja.md` | koncepcja | do akceptacji |\n'
        '| `Zasoby/../../poza.md` | plan | do akceptacji |\n', encoding='utf-8')
    return v, kand


def _sumy(katalog):
    wynik = {}
    for korzen, _, pliki in os.walk(katalog):
        for n in pliki:
            p = os.path.join(korzen, n)
            with open(p, 'rb') as f:
                wynik[p] = hashlib.sha256(f.read()).hexdigest()
    return wynik


def _konf(tmp_path, **nadpisz):
    v, kand = _vault(tmp_path)
    nadpisz.setdefault('przesluchania_kandydaci', kand.as_posix())
    nadpisz.setdefault('strona_startowa', ORIGIN_STARTOWA + '/')
    return wczytaj(zbuduj_jarvis(tmp_path, **nadpisz)), v


async def _lista(cl, auth, z='decyzja'):
    r = await cl.get(f'/?przesluchania=1&z={z}', auth=auth)
    assert r.status == 200
    return await r.text()


async def _licznik(cl, auth):
    return await (await cl.get('/?przesluchania=licznik', auth=auth)).json()


async def _decyzja(cl, auth, plik, decyzja, uwaga=''):
    return await cl.post(f'/vault/Zasoby/{plik}?decyzja=1', auth=auth,
                         json={'decyzja': decyzja, 'uwaga': uwaga})


# ── Konfiguracja: Anbernic bez listy ────────────────────────────────────────

def test_anbernic_domyslnie_bez_listy(tmp_path):
    import aiohttp
    k = wczytaj(zbuduj_anbernic(tmp_path))
    assert not k.modul('przesluchania')
    auth = aiohttp.BasicAuth('anbernic', HASLO)

    async def sc(cl):
        return ((await cl.get('/?przesluchania=1', auth=auth)).status,
                (await cl.get('/?przesluchania=licznik', auth=auth)).status)
    assert uruchom(k, sc) == (403, 403)


def test_modul_bez_zakresu_odmowa_startu(tmp_path):
    import konfiguracja
    k = wczytaj(zbuduj_jarvis(tmp_path, przesluchania_zakres=''))
    bledy = konfiguracja.sprawdz_przy_starcie(k)
    assert any('przesluchania_zakres' in b for b in bledy)


# ── Lista ───────────────────────────────────────────────────────────────────

def test_lista_pokazuje_notatki_ze_statusem_i_z_propozycji(tmp_path, auth):
    k, v = _konf(tmp_path)

    async def sc(cl):
        return (await _lista(cl, auth), await _lista(cl, auth, 'zaakceptowane'),
                await _licznik(cl, auth))
    dec, zaakc, lic = uruchom(k, sc)
    assert 'Plan kamer' in dec                          # status w nagłówku
    assert 'Koncepcja wtyczki' in dec                   # z pliku propozycji
    assert 'propozycja' in dec
    assert 'Zwykła notatka' not in dec                  # bez statusu — poza listą
    assert 'Prompt' not in dec
    assert 'ukryta' not in dec                          # katalogi ukryte pomijane
    assert 'poza.md' not in dec                         # „..” w propozycji odrzucone
    assert 'Do wdrożenia' in zaakc and 'Do wdrożenia' not in dec
    assert lic['do_decyzji'] == 3 and lic['zaakceptowane'] == 1
    assert 'href="/vault/Zasoby/MoCap/plan-kamer.md?sluchaj=1"' in dec


def test_tytul_notatki_escapowany(tmp_path, auth):
    k, v = _konf(tmp_path)

    async def sc(cl):
        return await _lista(cl, auth)
    html = uruchom(k, sc)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html


# ── Decyzja ─────────────────────────────────────────────────────────────────

def test_decyzja_zmienia_liste_przezywa_restart_i_nie_dotyka_vaulta(tmp_path, auth):
    k, v = _konf(tmp_path)
    przed = _sumy(v)

    async def sc(cl):
        r1 = await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'do-poprawy', '')
        r2 = await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'do-poprawy',
                            'Dodać rozstaw kamer w mm')
        r3 = await _decyzja(cl, auth, 'Rhino/koncepcja.md', 'akceptuje')
        return r1.status, r2.status, (await r2.json()), r3.status
    s1, s2, wpis, s3 = uruchom(k, sc)
    assert s1 == 400                                    # „do poprawy” bez uwagi
    assert s2 == 200 and wpis['status'] == 'do-poprawy' and wpis['id'] == 1
    assert s3 == 200

    async def po_restarcie(cl):                          # nowa aplikacja, ten sam stan
        return (await _lista(cl, auth), await _lista(cl, auth, 'rozstrzygniete'),
                await _lista(cl, auth, 'zaakceptowane'), await _licznik(cl, auth))
    dec, rozstrz, zaakc, lic = uruchom(k, po_restarcie)
    assert 'Plan kamer' not in dec and 'Plan kamer' in rozstrz
    assert 'Koncepcja wtyczki' in zaakc
    assert 'czeka na przeniesienie do vaulta' in rozstrz
    assert lic['do_decyzji'] == 1 and lic['nieprzeniesione'] == 2
    assert _sumy(v) == przed, 'vault zmieniony — miał być tylko do odczytu'
    stan = json.loads((tmp_path / 'srv' / 'dane' / 'przesluchania.json').read_text(
        encoding='utf-8'))
    assert [d['id'] for d in stan['decyzje']] == [1, 2]
    # miejsce na kolejne porcje: zakres i kotwica (ocena akapitu), źródło uwagi (głos)
    assert stan['decyzje'][0]['zakres'] == 'notatka'
    assert stan['decyzje'][0]['kotwica'] == {}
    assert stan['decyzje'][0]['zrodlo_uwagi'] == 'tekst'


@pytest.mark.skipif(sys.platform == 'win32' or (hasattr(os, 'geteuid') and os.geteuid() == 0),
                    reason='chmod bez skutku na Windows i dla roota')
def test_decyzja_dziala_przy_vaulcie_fizycznie_tylko_do_odczytu(tmp_path, auth):
    k, v = _konf(tmp_path)
    katalogi = [v] + [p for p in v.rglob('*') if p.is_dir()]
    for d in katalogi:
        d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        async def sc(cl):
            return (await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'odrzuca')).status
        assert uruchom(k, sc) == 200
    finally:
        for d in katalogi:
            d.chmod(0o755)


def test_decyzja_bez_naglowka_strony_403(tmp_path, auth):
    k, v = _konf(tmp_path)

    async def sc(cl):
        return (await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'akceptuje')).status
    assert uruchom(k, sc, naglowek=False) == 403


def test_decyzja_poza_zakresem_i_bledne_dane(tmp_path, auth):
    k, v = _konf(tmp_path)
    (tmp_path / 'srv' / 'korzen' / 'inny.md').write_text('# Inny\n', encoding='utf-8')

    async def sc(cl):
        poza = await cl.post('/inny.md?decyzja=1', auth=auth, json={'decyzja': 'akceptuje'})
        nieznana = await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'kasuj')
        dluga = await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'akceptuje', 'x' * 4001)
        zly_json = await cl.post('/vault/Zasoby/MoCap/plan-kamer.md?decyzja=1', auth=auth,
                                 data='nie-json', headers={'Content-Type': 'application/json'})
        return poza.status, nieznana.status, dluga.status, zly_json.status
    assert uruchom(k, sc) == (404, 400, 400, 400)


def test_zmiana_tresci_notatki_wraca_status_z_naglowka(tmp_path, auth):
    """Decyzja dotyczy KONKRETNEJ treści: poprawiona notatka wraca na listę."""
    k, v = _konf(tmp_path)

    async def sc(cl):
        await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'do-poprawy', 'popraw')
        return await _lista(cl, auth)
    assert 'Plan kamer' not in uruchom(k, sc)
    p = v / 'Zasoby' / 'MoCap' / 'plan-kamer.md'
    p.write_text(p.read_text(encoding='utf-8') + '\nAkapit poprawiony.\n', encoding='utf-8')

    async def sc2(cl):
        return await _lista(cl, auth)
    assert 'Plan kamer' in uruchom(k, sc2)


# ── Eksport dla agenta laptopa ──────────────────────────────────────────────

def test_eksport_i_potwierdzenie_przeniesienia(tmp_path, auth):
    k, v = _konf(tmp_path)

    async def sc(cl):
        await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'do-poprawy', 'Dodać rozstaw | w mm')
        await _decyzja(cl, auth, 'Rhino/koncepcja.md', 'akceptuje')
        eks = await (await cl.get('/?przesluchania=eksport', auth=auth)).json()
        p1 = await cl.post('/?przesluchania=potwierdz', auth=auth, json={'do': 2})
        p2 = await cl.post('/?przesluchania=potwierdz', auth=auth, json={'do': 1})
        p3 = await cl.post('/?przesluchania=potwierdz', auth=auth, json={'do': 99})
        eks2 = await (await cl.get('/?przesluchania=eksport', auth=auth)).json()
        return eks, (await p1.json()), (await p2.json()), p3.status, eks2
    eks, p1, p2, s3, eks2 = uruchom(k, sc)
    assert eks['ostatnie_id'] == 2
    assert eks['statusy'] == {'Zasoby/MoCap/plan-kamer.md': 'do-poprawy',
                              'Zasoby/Rhino/koncepcja.md': 'zaakceptowane-niewdrozone'}
    assert eks['do_kolejki'] == [
        '- [ ] do poprawy: Plan kamer (`Zasoby/MoCap/plan-kamer.md`) — Dodać rozstaw | w mm']
    assert p1['potwierdzono_do'] == 2 and p2['potwierdzono_do'] == 2   # bez cofania
    assert s3 == 400
    assert eks2['decyzje'] == [] and eks2['ostatnie_id'] == 2
    md = (tmp_path / 'srv' / 'dane' / 'przesluchania-eksport.md').read_text(encoding='utf-8')
    assert 'nowych decyzji: 0' in md                    # plik odświeżony po potwierdzeniu


def test_eksport_md_ma_linie_do_kolejki(tmp_path, auth):
    k, v = _konf(tmp_path)

    async def sc(cl):
        await _decyzja(cl, auth, 'MoCap/plan-kamer.md', 'do-poprawy', 'Dodać rozstaw')
    uruchom(k, sc)
    md = (tmp_path / 'srv' / 'dane' / 'przesluchania-eksport.md').read_text(encoding='utf-8')
    assert '- [ ] do poprawy: Plan kamer (`Zasoby/MoCap/plan-kamer.md`) — Dodać rozstaw' in md
    assert '| `Zasoby/MoCap/plan-kamer.md` | do-poprawy |' in md


# ── Widok jednoczesny i odsłuch ─────────────────────────────────────────────

def test_widok_sluchaj_bez_nagrania(tmp_path, auth):
    k, v = _konf(tmp_path)

    async def sc(cl):
        r = await cl.get('/vault/Zasoby/MoCap/plan-kamer.md?sluchaj=1', auth=auth)
        return r.status, await r.text()
    s, html = uruchom(k, sc)
    assert s == 200
    assert 'Akapit pierwszy.' in html                   # tekst notatki
    assert 'status: do-akceptacji' not in html          # bez nagłówka
    assert 'id="lekgen"' in html                        # generowanie lektora
    for d in ('akceptuje', 'do-poprawy', 'odrzuca'):
        assert f'data-d="{d}"' in html
    assert 'id="au"' not in html
    assert 'fetch(encodeURIComponent' not in html.replace('afFetch(encodeURIComponent', '')


def test_widok_sluchaj_z_nagraniem_podswietla_i_wznawia_pozycje(tmp_path, auth):
    k, v = _konf(tmp_path)
    kl = tmp_path / 'srv' / 'korzen' / 'lektor' / 'vault' / 'Zasoby' / 'MoCap'
    kl.mkdir(parents=True)
    (kl / 'plan-kamer_lektor.mp3').write_bytes(b'ID3' + bytes(64))
    (kl / 'plan-kamer_lektor.cues.json').write_text(
        json.dumps([{'t': 0.0, 'text': 'Plan kamer.'}, {'t': 1.5, 'text': 'Akapit <b>.'}]),
        encoding='utf-8')

    async def sc(cl):
        u = '/vault/Zasoby/MoCap/plan-kamer.md'
        r1 = await cl.post(u + '?odsluch=1', auth=auth, json={'pozycja': 42.25})
        r2 = await cl.post(u + '?odsluch=1', auth=auth, json={'pozycja': 60, 'koniec': True})
        html = await (await cl.get(u + '?sluchaj=1', auth=auth)).text()
        return r1.status, (await r2.json()), html, await _lista(cl, auth)
    s1, o, html, lista = uruchom(k, sc)
    assert s1 == 200 and o['odsluchane']
    assert 'id="au"' in html and 'plan-kamer_lektor.mp3' in html
    assert '<span class=s data-i="1"' in html and 'Akapit &lt;b&gt;.' in html
    assert 'const P=60.0' in html                       # wznowienie od zapisanej pozycji
    assert 'id="lekdel"' in html                        # 🗑 nagranie w widoku
    assert '🎧 odsłuchane' in lista and '🔊 gotowe' in lista


def test_zakladka_zapamietana_na_serwerze(tmp_path, auth):
    k, v = _konf(tmp_path)

    async def sc(cl):
        r = await cl.post('/?przesluchania=ui', auth=auth, json={'zakladka': 'zaakceptowane'})
        zla = await cl.post('/?przesluchania=ui', auth=auth, json={'zakladka': 'x'})
        return r.status, zla.status
    assert uruchom(k, sc) == (200, 400)

    async def sc2(cl):                                   # inne urządzenie, bez ?z=
        return await (await cl.get('/?przesluchania=1', auth=auth)).text()
    html = uruchom(k, sc2)
    assert 'data-z="zaakceptowane" class="on"' in html


# ── Kafelek strony startowej: licznik z CORS tylko dla niej ─────────────────

def test_licznik_cors_tylko_dla_strony_startowej(tmp_path, auth):
    k, v = _konf(tmp_path)

    async def sc(cl):
        swoja = await cl.get('/?przesluchania=licznik', auth=auth,
                             headers={'Origin': ORIGIN_STARTOWA})
        obca = await cl.get('/?przesluchania=licznik', auth=auth,
                            headers={'Origin': 'http://zlo.example'})
        bez = await cl.get('/?przesluchania=licznik')
        return swoja.headers, obca.headers, bez.status
    swoja, obca, s_bez = uruchom(k, sc)
    assert swoja.get('Access-Control-Allow-Origin') == ORIGIN_STARTOWA
    assert swoja.get('Access-Control-Allow-Credentials') == 'true'
    assert 'Access-Control-Allow-Origin' not in obca
    assert s_bez == 401                                  # bez logowania liczby nie ma


# ── Moduł stanu (bez serwera) ───────────────────────────────────────────────

def test_magazyn_nieudany_zapis_krzyczy_i_nie_zostawia_wpisu(tmp_path, monkeypatch):
    import przesluchania as prz
    m = prz.Magazyn(tmp_path / 'stan.json')

    def awaria(*a, **kw):
        raise OSError('dysk pełny')
    monkeypatch.setattr(prz, 'zapisz_atomowo', awaria)
    with pytest.raises(prz.BladStanu):
        m.dodaj_decyzje('a.md', 'akceptuje', '', 'x')
    assert m.decyzje() == []
    monkeypatch.undo()
    assert m.dodaj_decyzje('a.md', 'akceptuje', '', 'x')['id'] == 1


def test_magazyn_uszkodzony_plik_odlozony_nie_nadpisany(tmp_path):
    import przesluchania as prz
    p = tmp_path / 'stan.json'
    p.write_text('{nie json', encoding='utf-8')
    m = prz.Magazyn(p)
    assert m.uszkodzony and m.decyzje() == []
    odlozone = list(tmp_path.glob('stan.json.uszkodzony-*'))
    assert len(odlozone) == 1 and odlozone[0].read_text(encoding='utf-8') == '{nie json'


def test_zapis_atomowy_nie_zostawia_plikow_tymczasowych(tmp_path):
    import przesluchania as prz
    m = prz.Magazyn(tmp_path / 'stan.json', tmp_path / 'eksport.md')
    for i in range(5):
        m.dodaj_decyzje(f'n{i}.md', 'akceptuje', '', 'x')
    assert sorted(p.name for p in tmp_path.iterdir()) == ['eksport.md', 'stan.json']
    assert len(json.loads((tmp_path / 'stan.json').read_text(encoding='utf-8'))['decyzje']) == 5


def test_naglowek_i_odcisk():
    import przesluchania as prz
    t = '---\nstatus: "do-akceptacji"\ntagi:\n  - a\n---\n# Tytuł\n\nTreść.\n'
    assert prz.naglowek(t) == {'status': 'do-akceptacji', 'tagi': ''}
    assert prz.tytul(t, 'x.md') == 'Tytuł'
    # zmiana samego nagłówka nie zmienia odcisku; zmiana treści — tak
    assert prz.odcisk(t) == prz.odcisk(t.replace('do-akceptacji', 'wdrozone'))
    assert prz.odcisk(t) != prz.odcisk(t + 'Nowe.\n')
    assert prz.naglowek('# Bez nagłówka\n') == {}
    assert HASLO  # conftest w użyciu


def test_przycisk_strony_startowej_na_liscie_i_w_widoku_sluchania(tmp_path, auth):
    """02.10 wieczór: przycisk „🏠 Jarvis” także na liście „Do przesłuchania”
    i w widoku słuchania."""
    k, _ = _konf(tmp_path)

    async def sc(cl):
        lista = await (await cl.get('/?przesluchania=1', auth=auth)).text()
        sluchaj = await (await cl.get('/vault/Zasoby/MoCap/plan-kamer.md?sluchaj=1',
                                      auth=auth)).text()
        return lista, sluchaj
    for html in uruchom(k, sc):
        assert 'class="af-start"' in html and f'href="{ORIGIN_STARTOWA}/"' in html
        assert 'class="af-kolejka" href="/?lektorq=1"' in html     # 🔊 kolejka lektora obok


def test_widok_sluchania_po_odswiezeniu_pokazuje_postep(tmp_path, auth, monkeypatch):
    """02.10: F5 w widoku słuchania w trakcie generowania — pasek wraca."""
    import server
    k, v = _konf(tmp_path)
    monkeypatch.setattr(server, '_lektor_progress',
                        lambda: {'pct': 55, 'chunk': 2, 'chunks': 4, 'out': 'x'})
    cel = (v / 'Zasoby' / 'MoCap' / 'plan-kamer.md').resolve()

    async def sc(cl):
        server._LEKTOR_QUEUE.append({
            'id': 5, 'out': str(cel.with_name('plan-kamer_lektor.mp3')), 'src': str(cel),
            'plik': str(cel), 'fmt': 'mp3', 'state': 'running', 'cancelled': False,
            'proc': None, 'started': 0})
        try:
            return await (await cl.get('/vault/Zasoby/MoCap/plan-kamer.md?sluchaj=1',
                                       auth=auth)).text()
        finally:
            server._LEKTOR_QUEUE.clear()
    html = uruchom(k, sc)
    assert 'data-job="5"' in html and 'value="55"' in html and 'część 2/4 · 55%' in html


# ── stan nagrania przy pozycjach listy (Karol 02.10: „tu muszę też widzieć,
# które dokumenty mają już gotowe pliki do odsłuchu”) ──────────────────────

def _mp3_cbr(plik, ramek=100):
    """MP3 MPEG-1 warstwa III 128 kb/s 44,1 kHz bez nagłówka Xing: 417 B na ramkę."""
    ramka = bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(413)
    plik.write_bytes(b'ID3\x04\x00\x00\x00\x00\x00\x00' + ramka * ramek)


def test_lista_pokazuje_stan_nagrania_kazdej_pozycji(tmp_path, auth, monkeypatch):
    import server
    k, v = _konf(tmp_path, wiek_nagran_dni='3')
    (v / 'Zasoby' / 'MoCap' / 'druga.md').write_text(
        '---\nstatus: do-akceptacji\n---\n# Druga notatka\n', encoding='utf-8')
    kl = tmp_path / 'srv' / 'korzen' / 'lektor' / 'vault' / 'Zasoby' / 'MoCap'
    kl.mkdir(parents=True)
    _mp3_cbr(kl / 'plan-kamer_lektor.mp3')
    monkeypatch.setattr(server, '_lektor_progress',
                        lambda: {'pct': 30, 'chunk': 3, 'chunks': 10, 'out': 'x'})
    koncepcja = (v / 'Zasoby' / 'Rhino' / 'koncepcja.md').resolve()

    async def sc(cl):
        server._LEKTOR_QUEUE.append({
            'id': 9, 'out': str(tmp_path / 'x_lektor.mp3'), 'src': str(koncepcja),
            'plik': str(koncepcja), 'fmt': 'mp3', 'state': 'running', 'cancelled': False,
            'proc': None, 'started': 0})
        server._LEKTOR_BLEDY.append({'id': 3, 'out': 'druga_lektor.mp3',
                                     'src': 'vault/Zasoby/MoCap/druga.md',
                                     'plik': 'vault/Zasoby/MoCap/druga.md', 'state': 'failed',
                                     'blad': 'Piper nie odpowiada', 'czas': ''})
        try:
            return await _lista(cl, auth)
        finally:
            server._LEKTOR_QUEUE.clear()
            server._LEKTOR_BLEDY.clear()
    html = uruchom(k, sc)

    def wiersz(rel):
        i = html.index(f'data-p="{rel}"')
        return html[i:html.index('</div>', i)]
    gotowe = wiersz('vault/Zasoby/MoCap/plan-kamer.md')
    assert 'class="lek gotowe"' in html and '🔊 gotowe · 3 s · zniknie za 2 dni 23 h' in gotowe
    trwa = wiersz('vault/Zasoby/Rhino/koncepcja.md')
    assert 'data-job="9"' in trwa and 'część 3/10 · 30%' in trwa and 'value="30"' in trwa
    blad = wiersz('vault/Zasoby/MoCap/druga.md')
    assert '⚠ błąd generowania: Piper nie odpowiada' in blad and 'class="gen"' in blad
    brak = wiersz('vault/Zasoby/MoCap/zlosliwa.md')
    assert '○ brak nagrania' in brak and '🔊 generuj' in brak
    assert 'class="gen"' not in gotowe and 'class="gen"' not in trwa
    # wyświetlenie listy niczego nie generuje
    assert not (kl / 'druga_lektor.mp3').exists()


def test_filtr_tylko_z_nagraniem_zapamietany_na_serwerze(tmp_path, auth):
    k, v = _konf(tmp_path)
    kl = tmp_path / 'srv' / 'korzen' / 'lektor' / 'vault' / 'Zasoby' / 'MoCap'
    kl.mkdir(parents=True)
    _mp3_cbr(kl / 'plan-kamer_lektor.mp3')

    async def sc(cl):
        wszystkie = await _lista(cl, auth)
        r = await cl.post('/?przesluchania=ui', auth=auth, json={'tylko_nagrane': True})
        tylko = await _lista(cl, auth)                 # po F5 / innym urządzeniu
        zly = await cl.post('/?przesluchania=ui', auth=auth, json={'tylko_nagrane': 'tak'})
        return wszystkie, r.status, tylko, zly.status
    wszystkie, st, tylko, zly = uruchom(k, sc)
    assert 'koncepcja.md' in wszystkie and st == 200 and zly == 400
    assert 'plan-kamer.md' in tylko and 'koncepcja.md' not in tylko
    assert 'id="tnagr" class="on"' in tylko


def test_czas_trwania_nagran_z_naglowka(tmp_path):
    import wave
    import nagrania
    _mp3_cbr(tmp_path / 'a.mp3')
    assert abs(nagrania.czas_trwania_s(tmp_path / 'a.mp3') - 100 * 417 * 8 / 128000) < 0.01
    with wave.open(str(tmp_path / 'b.wav'), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(bytes(2 * 22050 * 3))
    assert abs(nagrania.czas_trwania_s(tmp_path / 'b.wav') - 3.0) < 0.01
    (tmp_path / 'c.mp3').write_bytes(b'ID3' + bytes(64))       # bez ramek
    assert nagrania.czas_trwania_s(tmp_path / 'c.mp3') is None
    assert nagrania.opis_czasu(754) == '12 min 34 s' and nagrania.opis_czasu(None) == ''


# ── plik klasyfikacji v2: stan wdrożenia → trzy zakładki (decyzja Karola 02.10) ──

KLASYFIKACJA_V2 = (
    '# Klasyfikacja v2\n\n## 1. Do wdrożenia\n\n'
    '| Ścieżka | Rodzaj | Status | Stan wdrożenia | Do wdrożenia | Pilność i dowód |\n'
    '|---|---|---|---|---|---|\n'
    '| `Zasoby/Rhino/koncepcja.md` | koncepcja | do-akceptacji | CZĘŚCIOWO | Etap 2: siatka '
    '\| powierzchnia | pilne — zwrot 08.10 |\n'
    '| `Zasoby/MoCap/plan-kamer.md` | plan | do-akceptacji | NIEZLECONE | Kalibracja K0–K4 | — |\n'
    '\n## 2. Wdrożone lub informacyjne\n\n'
    '| Ścieżka | Rodzaj | Status | Stan wdrożenia | Do wdrożenia | Pilność i dowód |\n'
    '|---|---|---|---|---|---|\n'
    '| `Zasoby/MoCap/notatka-bez-statusu.md` | analiza | do-akceptacji | **WDROŻONE** | Moduł X '
    '| `abc1234`, zlecenie 82 |\n'
    '| `Zasoby/prompty/prompt.md` | prompt | do-akceptacji | NIE DOTYCZY | — | — |\n')


def test_klasyfikacja_v2_trzy_zakladki_stan_i_kolejnosc_z_pliku(tmp_path, auth):
    k, v = _konf(tmp_path)
    (v / 'Zasoby' / 'klasyfikacja.md').write_text(KLASYFIKACJA_V2, encoding='utf-8')

    async def sc(cl):
        dec = await _lista(cl, auth, 'decyzja')
        wdr = await _lista(cl, auth, 'wdrozenia')
        inf = await _lista(cl, auth, 'informacyjne')
        r = await _decyzja(cl, auth, 'Rhino/koncepcja.md', 'akceptuje')
        dec2 = await _lista(cl, auth, 'decyzja')
        zaak = await _lista(cl, auth, 'zaakceptowane')
        return dec, wdr, inf, r.status, dec2, zaak
    dec, wdr, inf, st, dec2, zaak = uruchom(k, sc)
    for nazwa in ('Do decyzji (3)', 'Zlecone i wdrożone (1)', 'Informacyjne (1)'):
        assert nazwa in dec, nazwa
    # kolejność = kolejność wierszy pliku (Rhino przed MoCap), pozycja spoza pliku na końcu
    i_konc, i_plan = dec.index('Rhino/koncepcja.md'), dec.index('MoCap/plan-kamer.md')
    i_zl = dec.index('MoCap/zlosliwa.md')
    assert i_konc < i_plan < i_zl
    assert '<b class="sw czesciowo">częściowo</b>' in dec
    assert '<b class="sw niezlecone">niezlecone</b>' in dec
    assert 'Etap 2: siatka | powierzchnia' in dec and 'pilne — zwrot 08.10' in dec
    assert '<b class="sw nieznany">stan nieznany</b>' in dec[i_zl - 600:]
    assert 'notatka-bez-statusu.md' in wdr and 'abc1234, zlecenie 82' in wdr
    assert '<b class="sw wdrozone">wdrożone</b>' in wdr and 'koncepcja.md' not in wdr
    assert 'prompty/prompt.md' in inf and 'plan-kamer.md' not in inf
    # decyzja Karola nadal przenosi pozycję do zakładek decyzji
    assert st == 200 and 'Rhino/koncepcja.md' not in dec2 and 'Rhino/koncepcja.md' in zaak


def test_klasyfikacja_v1_bez_kolumny_stanu_dziala_jak_dotad(tmp_path, auth):
    """Plik bez nowych kolumn: brak oznaczeń stanu, kolejność projektami."""
    k, v = _konf(tmp_path)

    async def sc(cl):
        return await _lista(cl, auth, 'decyzja')
    dec = uruchom(k, sc)
    assert 'class="sw' not in dec and 'stan nieznany' not in dec
    assert dec.index('MoCap/plan-kamer.md') < dec.index('Rhino/koncepcja.md')


def test_czas_trwania_mp3_mpeg2_mono_z_info_jak_z_pipera(tmp_path):
    """Nagłówek nagrania lektora z Jarvisa (02.10): ID3, ramka FF F3 A0 C0
    (MPEG-2 warstwa III, 22 050 Hz, mono), Info: 53 908 ramek → 1408,2 s."""
    import nagrania
    ramka = (bytes([0xFF, 0xF3, 0xA0, 0xC0]) + bytes(9) + b'Info' + bytes([0, 0, 0, 0x0F])
             + (53908).to_bytes(4, 'big') + bytes(200))
    (tmp_path / 'p.mp3').write_bytes(b'ID3\x04\x00\x00\x00\x00\x00\x00' + ramka)
    assert abs(nagrania.czas_trwania_s(tmp_path / 'p.mp3') - 1408.209) < 0.01
    assert nagrania.opis_czasu(1408.2) == '23 min 28 s'


# ── nazwa pliku jak w Obsidianie i data powstania (Karol 02.10 19:18: „Nazwy
# plików są inne, niż ja je widzę”; plik 2026-09-28-… miał na liście 29.09) ──

def test_lista_i_widok_sluchania_nazwa_pliku_i_data_powstania_z_gita(tmp_path, auth):
    import time
    k, v = _konf(tmp_path)
    plik = v / 'Zasoby' / 'MoCap' / '2026-09-28-plan-wtyczka-fab.md'
    plik.write_text('---\nstatus: do-akceptacji\n---\n# Plan produktu — wtyczka UE\n',
                    encoding='utf-8')
    (v / '.git').mkdir()
    powstanie = time.mktime((2026, 9, 29, 12, 0, 0, 0, 0, -1))
    (v / '.git' / 'anberfiles-czasy.json').write_text(json.dumps({
        'wersja': 1, 'pliki': {'Zasoby/MoCap/2026-09-28-plan-wtyczka-fab.md':
                               [powstanie + 3600, powstanie]}, 'katalogi': {}}),
        encoding='utf-8')

    async def sc(cl):
        lista = await _lista(cl, auth)
        sl = await (await cl.get('/vault/Zasoby/MoCap/2026-09-28-plan-wtyczka-fab.md?sluchaj=1',
                                 auth=auth)).text()
        return lista, sl
    lista, sl = uruchom(k, sc)
    i = lista.index('2026-09-28-plan-wtyczka-fab.md?sluchaj=1')
    wiersz = lista[i:lista.index('</a>', i)]
    assert '<div class="tt">2026-09-28-plan-wtyczka-fab</div>' in wiersz
    assert 'Plan produktu — wtyczka UE · Zasoby/MoCap' in wiersz
    assert 'w nazwie 28.09 · powstanie 29.09.2026' in wiersz
    assert ('<div class="nazwa"><b>2026-09-28-plan-wtyczka-fab</b> · '
            'w nazwie 28.09 · powstanie 29.09.2026</div>') in sl


def test_data_zgodna_z_nazwa_jedna_data(tmp_path):
    """Przedrostek daty zgodny z powstaniem (albo brak przedrostka) → jedna data."""
    import server
    from datetime import date
    dzis = tmp_path / f'{date.today():%Y-%m-%d}-notatka.md'
    dzis.write_text('x', encoding='utf-8')
    bez = tmp_path / 'notatka.md'
    bez.write_text('x', encoding='utf-8')
    for plik in (dzis, bez):
        opis = server.opis_daty_notatki(plik)
        assert opis.startswith(f'powstanie {date.today():%d.%m.%Y}') and 'w nazwie' not in opis


# ── pliki pomocnicze Wykonawcy i pierwszeństwo stanu wdrożenia (Karol 02.10
# 19:20, „Zaakceptowane, niewdrożone”: „Czy to nie dubel?”) ─────────────────

def _dubel(v):
    d = v / 'Zasoby' / 'Wykonawca-i-Puls'
    d.mkdir(parents=True)
    for n in ('2026-09-28-plan-blokada', '2026-09-28-plan-blokada-wykonawca-plan',
              '2026-09-28-plan-blokada-wykonawca-przeglad', 'inny-wykonawca-plan',
              'inny-wykonawca-przeglad'):
        (d / f'{n}.md').write_text(f'# {n}\n', encoding='utf-8')
    w = 'Zasoby/Wykonawca-i-Puls/'
    (v / 'Zasoby' / 'klasyfikacja.md').write_text(
        '| Ścieżka | Rodzaj | Status | Stan wdrożenia | Do wdrożenia | Pilność i dowód |\n'
        '|---|---|---|---|---|---|\n'
        f'| `{w}2026-09-28-plan-blokada.md` | plan | do-akceptacji | WDROŻONE | Blokada | zlecenie 67, `98ce603` |\n'
        f'| `{w}2026-09-28-plan-blokada-wykonawca-plan.md` | plan | do-akceptacji | WDROŻONE | — | 67 |\n'
        f'| `{w}2026-09-28-plan-blokada-wykonawca-przeglad.md` | przegląd | do-akceptacji | NIE DOTYCZY | — | — |\n'
        f'| `{w}inny-wykonawca-plan.md` | plan | do-akceptacji | NIEZLECONE | X | — |\n'
        f'| `{w}inny-wykonawca-przeglad.md` | przegląd | do-akceptacji | NIE DOTYCZY | — | — |\n',
        encoding='utf-8')


def test_pliki_pomocnicze_wykonawcy_sa_zalacznikami_pozycji_glownej(tmp_path, auth):
    k, v = _konf(tmp_path)
    _dubel(v)

    async def sc(cl):
        return (await _lista(cl, auth, 'wdrozenia'), await _lista(cl, auth, 'informacyjne'),
                await _lista(cl, auth, 'decyzja'), await _licznik(cl, auth))
    wdr, inf, dec, lic = uruchom(k, sc)
    # jedna pozycja główna z dwoma załącznikami, a nie trzy pozycje
    assert wdr.count('<div class="row">') == 1
    assert '<div class="tt">2026-09-28-plan-blokada</div>' in wdr
    assert '📎 pliki pomocnicze (2)' in wdr
    assert 'plan-blokada-wykonawca-plan.md?sluchaj=1' in wdr
    assert 'plan-blokada-wykonawca-przeglad.md?sluchaj=1' in wdr
    assert 'plan-blokada-wykonawca-przeglad' not in inf      # przegląd nie jest osobno
    # brak pliku głównego → pozycją jest plan wykonawczy, przegląd jego załącznikiem
    assert '<div class="tt">inny-wykonawca-plan</div>' in dec
    assert '📎 pliki pomocnicze (1)' in dec and 'inny-wykonawca-przeglad.md?sluchaj=1' in dec
    assert 'inny-wykonawca-przeglad</div>' not in inf
    assert lic['do_decyzji'] == dec.count('<div class="row">')


def test_akceptacja_pozycji_wdrozonej_zostaje_w_zleconych_i_wdrozonych(tmp_path, auth):
    k, v = _konf(tmp_path)
    _dubel(v)

    async def sc(cl):
        r1 = await _decyzja(cl, auth, 'Wykonawca-i-Puls/2026-09-28-plan-blokada-wykonawca-przeglad.md',
                            'akceptuje')
        r = await _decyzja(cl, auth, 'Wykonawca-i-Puls/2026-09-28-plan-blokada.md', 'akceptuje')
        wdr = await _lista(cl, auth, 'wdrozenia')
        zaak = await _lista(cl, auth, 'zaakceptowane')
        eks = await (await cl.get('/?przesluchania=eksport', auth=auth)).json()
        return r1.status, r.status, wdr, zaak, eks
    s1, st, wdr, zaak, eks = uruchom(k, sc)
    assert s1 == 200 and st == 200
    assert '2026-09-28-plan-blokada.md?sluchaj=1' in wdr
    assert '2026-09-28-plan-blokada.md?sluchaj=1' not in zaak
    # obie decyzje zapisane (także ta przy załączniku) — nic nie skasowane
    zapisane = {d['sciezka'] for d in eks['decyzje']}
    assert {'Zasoby/Wykonawca-i-Puls/2026-09-28-plan-blokada.md',
            'Zasoby/Wykonawca-i-Puls/2026-09-28-plan-blokada-wykonawca-przeglad.md'} <= zapisane
