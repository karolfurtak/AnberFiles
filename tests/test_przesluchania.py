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
    assert '🎧 odsłuchane' in lista and '🔊 nagranie' in lista


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
