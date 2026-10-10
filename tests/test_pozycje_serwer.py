"""Pozycje do decyzji na liście „Do przesłuchania” (plan 98, porcja B): trasa
wyboru, znikanie i „Rozstrzygnięte”, licznik, eksport, stany źródeł, stan
widoku po F5, lektor czytający ten sam tekst, bezpieczeństwo.

Vault budowany w tmp_path (próbki z test_pozycje_parsery); po każdym
scenariuszu vault ma te same sumy kontrolne co przed nim."""
import asyncio
import hashlib
import json
import os
import re
import time
from html import unescape


from conftest import czekaj_na_lektora, uruchom, wczytaj
from conftest import zbuduj_jarvis as _zbuduj_jarvis
from test_pozycje_parsery import NOW, NOW_PENDING, REFLECT, _dane_scouta, _el, _plik, _proposed

import pozycje as poz


def _vault(tmp_path, xss=False):
    v = tmp_path / 'srv' / 'korzen' / 'vault'
    v.mkdir(parents=True, exist_ok=True)
    zly = '<script>alert(1)</script>'
    _proposed(v, [_el('audyt', 72, title=zly if xss else 'Audyt skilli'), _el('drugi', 10)])
    _plik(v, poz.SCIEZKI['reflect'],
          REFLECT.replace('## persona.md → § 4 — trzecia', f'## {zly}') if xss else REFLECT)
    _plik(v, poz.SCIEZKI['now'],
          NOW.replace('**Stara wersja na Anbernicu:**', f'**{zly}:**') if xss else NOW)
    _plik(v, poz.SCIEZKI['now-pending'],
          NOW_PENDING.replace('### ALARM automaty', f'### {zly} ALARM') if xss else NOW_PENDING)
    _dane_scouta(v, '2026-10-10.json', '2026-10-10', ['Pierwszy kandydat'])
    return v


def _konf(tmp_path, xss=False, **nadpisz):
    v = _vault(tmp_path, xss)
    nadpisz.setdefault('logowanie', 'basic')
    return wczytaj(_zbuduj_jarvis(tmp_path, **nadpisz)), v


def _sumy(katalog):
    wynik = {}
    for korzen, _, pliki in os.walk(katalog):
        for n in pliki:
            p = os.path.join(korzen, n)
            with open(p, 'rb') as f:
                wynik[p] = hashlib.sha256(f.read()).hexdigest()
    return wynik


async def _lista(cl, auth, z='decyzja', odswiez=False):
    r = await cl.get(f'/?przesluchania=1&z={z}' + ('&odswiez=1' if odswiez else ''), auth=auth)
    assert r.status == 200
    return await r.text()


async def _wybor(cl, auth, id_, wybor, uwaga='', **inne):
    return await cl.post('/?przesluchania=wybor', auth=auth,
                         json=dict({'id_pozycji': id_, 'wybor': wybor, 'uwaga': uwaga}, **inne))


def _wiersz(html, id_):
    """HTML wiersza pozycji o danym identyfikatorze ('' = brak na stronie)."""
    m = re.search(r'<div class="row poz" data-id="' + re.escape(id_) + r'">(.*?)</div></div>'
                  r'(?=<div class="row poz"|</details>)', html, re.S)
    return m.group(1) if m else ''


ID_NOW = 'now:' + poz._sha('Nocna pętla kolejki (zadanie 8 Pulsa)')[:12]


# ── Wybór: trasa, idempotencja, błędy ───────────────────────────────────────

def test_wybor_przez_trase_idempotencja_400_404(tmp_path, auth):
    """Testy 15–17 przez trasę: rekord z odciskiem liczonym przez serwer
    (pole „odcisk” od klienta ignorowane), powtórzenie → ten sam id,
    wybór spoza wariantów → 400, pozycja nieobecna / raport → 404."""
    k, v = _konf(tmp_path)

    async def sc(cl):
        await _lista(cl, auth)
        r1 = await _wybor(cl, auth, ID_NOW, 'A', 'decyzja z listy', odcisk='podrobiony')
        r2 = await _wybor(cl, auth, ID_NOW, 'A', 'decyzja z listy')
        r3 = await _wybor(cl, auth, ID_NOW, 'zatwierdz')
        r4 = await _wybor(cl, auth, 'now:nieistnieje', 'A')
        r5 = await _wybor(cl, auth, 'raport:skill-scout:2026-10-10', 'zatwierdz')
        r6 = await cl.post('/?przesluchania=wybor', auth=auth, json={'id_pozycji': 1})
        return (await r1.json(), await r2.json(), r3.status, r4.status, r5.status, r6.status)
    j1, j2, s3, s4, s5, s6 = uruchom(k, sc)
    pozycja = next(p for p in poz.now(v)[0] if p['id'] == ID_NOW)
    assert j1['odcisk'] == pozycja['odcisk'] != 'podrobiony'
    assert j1['wybor'] == 'A' and j1['powtorzenie'] is False and j1['zrodlo'] == 'now'
    assert j2['id'] == j1['id'] and j2['powtorzenie'] is True
    assert (s3, s4, s5, s6) == (400, 404, 404, 400)


def test_pozycja_znika_do_rozstrzygnietych_i_wraca_po_zmianie(tmp_path, auth):
    """Test 18 przez listę (tabela 3.4): wybór → pozycja znika z „Do decyzji”
    i jest w „Rozstrzygnięte”; ta sama treść po odświeżeniu nie wraca; treść
    zmieniona → wraca ze znacznikiem; usunięta ze źródła → z migawki."""
    k, v = _konf(tmp_path)
    id_sc = 'skill-scout:audyt'

    async def sc(cl):
        przed = await _lista(cl, auth)
        assert (await _wybor(cl, auth, id_sc, 'zatwierdz')).status == 200
        assert (await _wybor(cl, auth, ID_NOW, 'B')).status == 200
        po_dec, po_roz = await _lista(cl, auth), await _lista(cl, auth, 'rozstrzygniete')
        odsw = await _lista(cl, auth, odswiez=True)
        _plik(v, poz.SCIEZKI['now'], NOW.replace('wisi od 26.09', 'wisi od 27.09'))
        _proposed(v, [_el('drugi', 10)])
        zm_dec = await _lista(cl, auth, odswiez=True)
        zm_roz = await _lista(cl, auth, 'rozstrzygniete')
        return przed, po_dec, po_roz, odsw, zm_dec, zm_roz
    przed, po_dec, po_roz, odsw, zm_dec, zm_roz = uruchom(k, sc)
    assert _wiersz(przed, id_sc) and _wiersz(przed, ID_NOW)
    assert not _wiersz(po_dec, id_sc) and not _wiersz(po_dec, ID_NOW)
    assert 'czeka na przeniesienie do vaulta' in _wiersz(po_roz, id_sc)
    assert 'wybór: B: zostawić wyłączoną' in _wiersz(po_roz, ID_NOW)
    assert not _wiersz(odsw, id_sc)
    assert 'treść zmieniona po decyzji' in _wiersz(zm_dec, ID_NOW)
    assert 'usunięte ze źródła — naniesione' in _wiersz(zm_roz, id_sc)
    assert 'Audyt skilli' in _wiersz(zm_roz, id_sc)


# ── Licznik, eksport, przeczytane ───────────────────────────────────────────

def test_licznik_sumuje_notatki_i_pozycje(tmp_path, auth):
    """Test 23: do_decyzji = notatki + pozycje (kafelek strony startowej czyta
    tę liczbę), pola dodatkowe, raporty osobno, problemy źródeł."""
    k, v = _konf(tmp_path)
    (v / 'Zasoby' / 'n.md').write_text('---\nstatus: do-akceptacji\n---\n# N\n', encoding='utf-8')

    async def sc(cl):
        l1 = await (await cl.get('/?przesluchania=licznik', auth=auth)).json()
        await _wybor(cl, auth, 'skill-scout:audyt', 'odrzuc')
        (v / poz.SCIEZKI['now-pending']).unlink()
        l2 = await (await cl.get('/?przesluchania=licznik&odswiez=1', auth=auth)).json()
        return l1, l2
    l1, l2 = uruchom(k, sc)
    poz_n = 2 + 2 + 7 + 3                       # skill-scout, reflect, NOW.md, briefing
    assert l1 == {'do_decyzji': 1 + poz_n, 'zaakceptowane': 0, 'nieprzeniesione': 0,
                  'do_decyzji_notatki': 1, 'do_decyzji_pozycje': poz_n,
                  'raporty_nieprzeczytane': 3, 'zrodla_problemy': 0}
    assert l2['do_decyzji_pozycje'] == poz_n - 1 - 3 and l2['nieprzeniesione'] == 1
    assert l2['zrodla_problemy'] == 1


def test_eksport_niesie_wybory_z_kotwica(tmp_path, auth):
    """Test 24 przez trasę: eksport JSON ma wybory z miejscem naniesienia,
    potwierdzenie przenosi je razem z decyzjami notatek."""
    k, v = _konf(tmp_path)

    async def sc(cl):
        await _wybor(cl, auth, 'skill-scout:audyt', 'zatwierdz', 'pilne')
        e1 = await (await cl.get('/?przesluchania=eksport', auth=auth)).json()
        r = await cl.post('/?przesluchania=potwierdz', auth=auth, json={'do': e1['ostatnie_id']})
        e2 = await (await cl.get('/?przesluchania=eksport', auth=auth)).json()
        return e1, r.status, e2
    e1, st, e2 = uruchom(k, sc)
    (w,) = e1['wybory']
    assert w['kotwica'] == {'plik': poz.SCIEZKI['skill-scout'], 'slug': 'audyt'}
    assert w['uwaga'] == 'pilne' and w['przeniesione'] is False
    assert e1['ostatnie_id'] == w['id'] and st == 200 and e2['wybory'] == []
    md = (tmp_path / 'srv' / 'dane' / 'przesluchania-eksport.md')
    assert md.exists()


def test_raport_przeczytany_znika(tmp_path, auth):
    """Test 32 (przegląd, ustalenie 8): „przeczytane” zapisuje odsłuch raportu,
    nie tworzy wyboru ani wpisu w eksporcie; inny identyfikator → 404."""
    k, v = _konf(tmp_path)
    rid = 'raport:skill-scout:2026-10-10'

    async def sc(cl):
        przed = await _lista(cl, auth)
        r = await cl.post('/?przesluchania=przeczytane', auth=auth, json={'id_pozycji': rid})
        r404 = await cl.post('/?przesluchania=przeczytane', auth=auth,
                             json={'id_pozycji': 'skill-scout:audyt'})
        r404b = await cl.post('/?przesluchania=przeczytane', auth=auth,
                              json={'id_pozycji': 'raport:skill-scout:1999-01-01'})
        po = await _lista(cl, auth)
        lic = await (await cl.get('/?przesluchania=licznik', auth=auth)).json()
        eks = await (await cl.get('/?przesluchania=eksport', auth=auth)).json()
        return przed, r.status, r404.status, r404b.status, po, lic, eks
    przed, s, s404, s404b, po, lic, eks = uruchom(k, sc)
    assert _wiersz(przed, rid) and '✓ przeczytane' in _wiersz(przed, rid)
    assert (s, s404, s404b) == (200, 404, 404)
    assert not _wiersz(po, rid) and lic['raporty_nieprzeczytane'] == 2
    assert eks['wybory'] == [] and eks['ostatnie_id'] == 0


# ── Stany źródeł bez ciszy ──────────────────────────────────────────────────

def test_trzy_stany_zrodel_na_liscie_i_w_rejestrze(tmp_path, auth):
    """Test 22: brak pliku i uszkodzony plik → czerwony pasek z powodem,
    poprawny pusty → „0 poz.”; rejestr zdarzeń dostaje wpis przy ZMIANIE
    stanu, nie przy każdym żądaniu."""
    k, v = _konf(tmp_path)
    (v / poz.SCIEZKI['now-pending']).unlink()
    _plik(v, poz.SCIEZKI['skill-scout'], '{"proposed": [')
    _plik(v, poz.SCIEZKI['reflect'], '# R\n')

    async def sc(cl):
        h1 = await _lista(cl, auth)
        await _lista(cl, auth, odswiez=True)
        await _lista(cl, auth, odswiez=True)
        _proposed(v, [])
        h2 = await _lista(cl, auth, odswiez=True)
        return h1, h2
    h1, h2 = uruchom(k, sc)
    paski = re.findall(r'<div class="zr-blad">(.*?)</div>', h1)
    assert any('brak pliku NOW-pending.md' in p for p in paski)
    assert any('nieczytelne — zły JSON' in unescape(p) for p in paski)
    assert 'Zmiany reguł (/reflect): źródło poprawne, 0 poz. do decyzji' in unescape(h1)
    assert not any('skill-scout' in p for p in re.findall(r'class="zr-blad">(.*?)</div>', h2))
    rej = k.rejestr_zdarzen.read_text(encoding='utf-8').splitlines()
    warn = [r for r in rej if '\twarn\tprzesluchania\t' in r]
    assert len([r for r in warn if 'NOW-pending.md' in r]) == 1
    assert len([r for r in warn if 'zły JSON' in r]) == 1
    assert any('źródło znów poprawne' in r for r in rej)


# ── Stan widoku po F5 ───────────────────────────────────────────────────────

def test_stan_widoku_przezywa_f5_i_restart(tmp_path, auth):
    """Test 25: zwinięte sekcje i filtr źródła zapisane na serwerze, odczytane
    przy nowym żądaniu i po ponownym uruchomieniu; wartość spoza listy → 400."""
    k, v = _konf(tmp_path)

    async def sc(cl):
        r1 = await cl.post('/?przesluchania=ui', auth=auth, json={'zwiniete': ['reflect']})
        r2 = await cl.post('/?przesluchania=ui', auth=auth, json={'zwiniete': ['zle']})
        r3 = await cl.post('/?przesluchania=ui', auth=auth, json={'zrodlo': 'kosmos'})
        r4 = await cl.post('/?przesluchania=ui', auth=auth, json={'zwiniete': 'reflect'})
        return r1.status, r2.status, r3.status, r4.status, await _lista(cl, auth)
    s1, s2, s3, s4, html = uruchom(k, sc)
    assert (s1, s2, s3, s4) == (200, 400, 400, 400)
    assert '<details class="grp" data-g="reflect">' in html
    assert '<details class="grp" data-g="now" open>' in html

    async def sc2(cl):
        await cl.post('/?przesluchania=ui', auth=auth, json={'zrodlo': 'now'})
        return await _lista(cl, auth)
    html2 = uruchom(k, sc2)

    async def sc3(cl):
        return await _lista(cl, auth)
    html3 = uruchom(k, sc3)                    # nowa instancja serwera, ten sam plik stanu
    for h in (html2, html3):
        assert '<option value="now" selected>' in h
        assert 'data-g="now"' in h and 'data-g="skill-scout"' not in h
        assert 'data-g="raporty"' not in h
    assert '<details class="grp" data-g="reflect">' not in html3     # filtr ukrywa reflect


# ── Interfejs i lektor ──────────────────────────────────────────────────────

def test_interfejs_przyciski_44px_i_ten_sam_tekst_dla_lektora(tmp_path, auth, puls):
    """Test 28: przyciski decyzji ≥ 44 px; plik dla lektora ma DOKŁADNIE tytuł
    i tekst z wiersza; „generuj” wskazuje plik w katalogu lektora; po nagraniu
    (atrapa) wiersz ma odtwarzacz z faktyczną ścieżką
    korzen/lektor/lektor/_pozycje/<rodzaj>/<skrót>_lektor.mp3 (ustalenie 6)."""
    k, v = _konf(tmp_path)
    kl = tmp_path / 'srv' / 'korzen' / 'lektor'

    async def sc(cl):
        html = await _lista(cl, auth)
        w = _wiersz(html, 'skill-scout:audyt')
        u = re.search(r'<button class="gen" data-u="([^"]+)"', w).group(1)
        r = await cl.post(u + '?lektor=1&queue=1', auth=auth)
        await czekaj_na_lektora(cl, auth)
        return html, w, u, r.status, await _lista(cl, auth)
    html, w, u, st, po = uruchom(k, sc)
    assert re.search(r'\.dec button\{min-height:44px', html)
    assert '<button class="wyb" data-k="zatwierdz">Zatwierdź</button>' in w
    assert u.startswith('/lektor/_pozycje/skill-scout/') and u.endswith('.md')
    plik = kl / u[len('/lektor/'):]
    tekst = plik.read_text(encoding='utf-8')
    tytul = unescape(re.search(r'<div class="tt">(.*?)</div>', w).group(1))
    opis = unescape(re.search(r'<div class="dw">(.*?)</div>', w).group(1))
    assert tekst == f'# {tytul}\n\n{opis}\n' and tytul == 'Audyt skilli'
    assert st == 202
    mp3 = kl / 'lektor' / '_pozycje' / 'skill-scout' / (plik.stem + '_lektor.mp3')
    assert mp3.is_file()
    wp = _wiersz(po, 'skill-scout:audyt')
    assert '🔊 gotowe' in wp
    assert f'<audio controls preload="none" src="/lektor/lektor/_pozycje/skill-scout/{mp3.name}">' in wp


def test_plik_tekstu_pozycji_tylko_przy_zmianie_i_sprzatanie(tmp_path, auth):
    """Test 33 (przegląd, ustalenie 6): dwa skany bez zmiany treści → czas
    modyfikacji bez zmian; zmiana treści → plik nadpisany; plik pozycji
    nieobecnej starszy niż wiek_nagran_dni → usunięty, młodszy zostaje."""
    k, v = _konf(tmp_path)
    kat = tmp_path / 'srv' / 'korzen' / 'lektor' / '_pozycje'
    stary_czas = time.time() - 10 * 86400

    async def sc(cl):
        await _lista(cl, auth)
        pliki = sorted(kat.rglob('*.md'))
        for f in pliki:
            os.utime(f, (stary_czas, stary_czas))
        await _lista(cl, auth, odswiez=True)
        mt = {f: f.stat().st_mtime for f in pliki}
        _proposed(v, [_el('audyt', 72, title='Audyt skilli', what='Zmieniony opis.'),
                      _el('drugi', 10)])
        sierota_stara = kat / 'now' / 'stara.md'
        sierota_mloda = kat / 'now' / 'mloda.md'
        sierota_stara.write_text('x', encoding='utf-8')
        sierota_mloda.write_text('x', encoding='utf-8')
        os.utime(sierota_stara, (stary_czas, stary_czas))
        await _lista(cl, auth, odswiez=True)
        return pliki, mt, sierota_stara.exists(), sierota_mloda.exists()
    pliki, mt, stara, mloda = uruchom(k, sc)
    assert len(pliki) == 2 + 2 + 7 + 3 + 3
    assert all(abs(m - stary_czas) < 0.01 for m in mt.values())
    audyt = [f for f in pliki if 'Zmieniony opis.' in f.read_text(encoding='utf-8')]
    assert len(audyt) == 1 and audyt[0].stat().st_mtime > stary_czas
    assert not stara and mloda


def test_skan_w_watku_nie_blokuje_innych_zadan(tmp_path, auth, monkeypatch):
    """Test 26: wolny odczyt źródeł nie wstrzymuje innych żądań (skan w
    run_in_executor) — krótkie żądanie kończy się przed listą."""
    import server
    k, v = _konf(tmp_path)
    prawdziwy = poz.skanuj

    def wolny(zakres):
        time.sleep(1.5)
        return prawdziwy(zakres)
    monkeypatch.setattr(server._poz, 'skanuj', wolny)

    async def sc(cl):
        czasy = {}

        async def lista():
            await cl.get('/?przesluchania=1', auth=auth)

        async def krotkie():
            t = time.monotonic()
            await asyncio.sleep(0.2)
            await cl.get('/?lektorqj=1', auth=auth)
            czasy['krotkie'] = time.monotonic() - t
        await asyncio.gather(lista(), krotkie())
        return czasy
    # od startu: 0,2 s uśpienia + krótkie żądanie; pętla stojąca na
    # 1,5-sekundowym odczycie dałaby ≥ 1,5 s
    assert uruchom(k, sc)['krotkie'] < 1.0


# ── Bezpieczeństwo ──────────────────────────────────────────────────────────

def test_xss_w_kazdym_zrodle_i_w_uwadze(tmp_path, auth):
    """Test 20: tytuł `<script>` w każdym z czterech źródeł → w HTML tylko
    `&lt;script&gt;`; uwaga z `<img onerror>` w eksporcie JSON jako dane."""
    k, v = _konf(tmp_path, xss=True)
    zla_uwaga = '<img src=x onerror=alert(1)>'

    async def sc(cl):
        html = await _lista(cl, auth)
        await _wybor(cl, auth, 'skill-scout:audyt', 'odrzuc', zla_uwaga)
        roz = await _lista(cl, auth, 'rozstrzygniete')
        return html, roz, await (await cl.get('/?przesluchania=eksport', auth=auth)).json()
    html, roz, eks = uruchom(k, sc)
    assert '<script>alert(1)' not in html and '<script>alert(1)' not in roz
    for rodzaj in ('skill-scout', 'reflect', 'now', 'now-pending'):
        wiersze = re.findall(r'<div class="row poz" data-id="' + rodzaj + r':[^"]*">(.*?)</details>',
                             html, re.S)
        assert any('&lt;script&gt;alert(1)&lt;/script&gt;' in w for w in wiersze), rodzaj
    assert eks['wybory'][0]['uwaga'] == zla_uwaga
    assert zla_uwaga not in roz


def test_logowanie_i_naglowek_strony(tmp_path, auth):
    """Test 21: tryb formularz — eksport i wybór bez sesji → 401; wybór bez
    nagłówka X-AnberFiles → 403 (straz_zrodla)."""
    kf, _ = _konf(tmp_path, logowanie='formularz')

    async def sc(cl):
        return ((await cl.get('/?przesluchania=eksport')).status,
                (await _wybor(cl, None, ID_NOW, 'A')).status,
                (await cl.post('/?przesluchania=przeczytane', json={})).status)
    assert uruchom(kf, sc) == (401, 401, 401)

    kb = wczytaj(_zbuduj_jarvis(tmp_path / 'b', logowanie='basic'))
    _vault(tmp_path / 'b')

    async def sc2(cl):
        return (await _wybor(cl, auth, ID_NOW, 'A')).status
    assert uruchom(kb, sc2, naglowek=False) == 403


def test_vault_nietkniety_caly_scenariusz(tmp_path, auth, puls):
    """Test 12 (scenariusz): sumy kontrolne vaulta przed i po skanie, wyborze,
    przeczytaniu, eksporcie, potwierdzeniu i nagraniu; pliki tekstu pozycji
    powstają tylko w katalogu lektora, poza przesluchania_zakres."""
    k, v = _konf(tmp_path)
    przed = _sumy(v)

    async def sc(cl):
        html = await _lista(cl, auth)
        await _wybor(cl, auth, 'skill-scout:audyt', 'zatwierdz', 'x')
        await _wybor(cl, auth, ID_NOW, 'A')
        await cl.post('/?przesluchania=przeczytane', auth=auth,
                      json={'id_pozycji': 'raport:reflect:2026-10-10'})
        e = await (await cl.get('/?przesluchania=eksport', auth=auth)).json()
        await cl.post('/?przesluchania=potwierdz', auth=auth, json={'do': e['ostatnie_id']})
        u = re.search(r'<button class="gen" data-u="([^"]+)"', html).group(1)
        await cl.post(u + '?lektor=1&queue=1', auth=auth)
        await czekaj_na_lektora(cl, auth)
        await _lista(cl, auth, 'rozstrzygniete', odswiez=True)
    uruchom(k, sc)
    assert _sumy(v) == przed
    kat = tmp_path / 'srv' / 'korzen' / 'lektor' / '_pozycje'
    assert kat.is_dir() and not str(kat.resolve()).startswith(str(v.resolve()))
    assert json.loads(k.przesluchania_stan.read_text(encoding='utf-8'))['wybory']
