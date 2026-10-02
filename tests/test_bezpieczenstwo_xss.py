"""Bezpieczeństwo treści (audyt: A3, B1, B2, C5).

A3 — treść vaulta nie może wykonać skryptu w originie aplikacji: Markdown
     sanityzowany, pliki .html/.svg/.xml serwowane w piaskownicy, nagłówki
     bezpieczeństwa (CSP, nosniff) na KAŻDEJ odpowiedzi.
B1 — nazwy plików i katalogów escapowane w HTML (tekst, atrybuty, href).
B2 — JSON wstawiany do <script> nie zamyka znacznika skryptu.
C5 — nagłówek Server nie zdradza wersji Pythona i aiohttp.

Każdy test buduje własne pliki w tmp_path. Testy „kontrola" dowodzą, że
poprawka nie wyłącza funkcji (zwykły Markdown, zwykła nazwa nadal działają).
"""
import json
import re
import sys
from urllib.parse import quote

import aiohttp
import pytest

from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic, zbuduj_jarvis

AU = aiohttp.BasicAuth('anbernic', HASLO)


def _anbernic(tmp_path):
    k = wczytaj(zbuduj_anbernic(tmp_path))
    return k, tmp_path / 'sprawozdania'


def _get(k, sciezka, auth=AU):
    async def sc(cl):
        r = await cl.get(sciezka, auth=auth)
        return r.status, r.headers, await r.text()
    return uruchom(k, sc)


def _rendered(html):
    m = re.search(r'<div id="rendered">(.*?)</div><div id="raw">', html, re.S)
    assert m, 'brak sekcji #rendered'
    return m.group(1)


# ── A3: Markdown ────────────────────────────────────────────────────────────

ZLY_MD = ('# Raport\n\n'
          '<script>alert(1)</script>\n\n'
          '<img src=x onerror=alert(2)>\n\n'
          '[klik](javascript:alert(3))\n\n'
          'Wzór $<script>alert(4)</script>$ w tekście.\n\n'
          '<iframe src="https://example.com"></iframe>\n')


def test_md_bez_wykonywalnych_znacznikow(tmp_path):
    k, korzen = _anbernic(tmp_path)
    (korzen / 'zly.md').write_text(ZLY_MD, encoding='utf-8')
    status, _, html = _get(k, '/zly.md?view=1')
    assert status == 200
    body = _rendered(html).lower()
    assert '<script' not in body
    assert 'onerror' not in body
    assert 'javascript:' not in body
    assert '<iframe' not in body


def test_md_kontrola_tabela_kod_wzory_obrazek(tmp_path):
    """Kontrola rozróżniająca: zwykły Markdown renderuje się jak dotąd."""
    k, korzen = _anbernic(tmp_path)
    (korzen / 'obraz.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    (korzen / 'dobry.md').write_text(
        '# Nagłówek\n\n'
        '| a | b |\n|:--|--:|\n| 1 | 2 |\n\n'
        '```python\nif a < b:\n    pass\n```\n\n'
        'Wzór $x^2$ oraz $a_1 < b_2$.\n\n'
        '$$\\frac{a_1}{b_2}$$\n\n'
        '![rys](obraz.png)\n\n'
        '[łącze](inny.md)\n', encoding='utf-8')
    status, _, html = _get(k, '/dobry.md?view=1')
    assert status == 200
    body = _rendered(html)
    assert '<h1>Nagłówek</h1>' in body
    assert '<table>' in body and '<td' in body and '>2</td>' in body
    assert re.search(r'<th style="text-align: ?right;?">b</th>', body)   # wyrównanie kolumny
    assert '<pre><code class="language-python">' in body
    assert 'if a &lt; b:' in body
    assert '$x^2$' in body
    assert '$a_1 &lt; b_2$' in body                     # MathJax czyta tekst
    assert '$$\\frac{a_1}{b_2}$$' in body
    assert re.search(r'<img [^>]*src="obraz\.png"', body)
    assert 'href="inny.md"' in body


def test_md_bez_nh3_tekst_escapowany(tmp_path, monkeypatch):
    """Brak biblioteki nh3 (np. konsola bez wheela) = surowy HTML wyświetlony
    jako tekst, nigdy wykonany."""
    import server
    monkeypatch.setattr(server, '_nh3', None)
    k, korzen = _anbernic(tmp_path)
    (korzen / 'zly.md').write_text(ZLY_MD, encoding='utf-8')
    status, _, html = _get(k, '/zly.md?view=1')
    assert status == 200
    body = _rendered(html).lower()
    assert '<script' not in body and '<img' not in body
    assert '&lt;script&gt;' in body


# ── A3: pliki aktywne (HTML, SVG, XML) ──────────────────────────────────────

@pytest.mark.parametrize('nazwa,tresc', [
    ('strona.html', '<script>alert(1)</script>'),
    ('strona.htm', '<script>alert(1)</script>'),
    ('rys.svg', '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
    ('dane.xml', '<a><script xmlns="http://www.w3.org/1999/xhtml">alert(1)</script></a>'),
    ('strona.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><script>alert(1)</script></html>'),
])
def test_pliki_aktywne_w_piaskownicy(tmp_path, nazwa, tresc):
    k, korzen = _anbernic(tmp_path)
    (korzen / nazwa).write_text(tresc, encoding='utf-8')
    status, h, _ = _get(k, '/' + nazwa)
    assert status == 200
    csp = h.get('Content-Security-Policy', '')
    assert re.search(r'(^|;)\s*sandbox(\s*;|\s*$)', csp), csp   # bez allow-scripts
    assert 'allow-scripts' not in csp


def test_kontrola_svg_w_przegladarce_zdjec(tmp_path):
    """Kontrola: SVG nadal wyświetla się w przeglądarce zdjęć jako <img>,
    a zwykły plik tekstowy NIE dostaje piaskownicy."""
    k, korzen = _anbernic(tmp_path)
    (korzen / 'rys.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>',
                                    encoding='utf-8')
    (korzen / 'a.txt').write_text('tekst', encoding='utf-8')
    status, _, html = _get(k, '/rys.svg?view=1')
    assert status == 200
    assert re.search(r'<img src="rys\.svg\?v=\d+"', html)
    status, h, tekst = _get(k, '/a.txt')
    assert status == 200 and tekst == 'tekst'
    assert 'sandbox' not in h['Content-Security-Policy']


# ── A3 + C5: nagłówki na każdej odpowiedzi ──────────────────────────────────

def _sprawdz_naglowki(h, opis):
    csp = h.get('Content-Security-Policy', '')
    assert "connect-src 'self'" in csp, (opis, csp)
    assert "default-src 'self'" in csp, (opis, csp)
    assert "object-src 'none'" in csp or 'application/pdf' in h.get('Content-Type', ''), opis
    assert "frame-ancestors 'self'" in csp, (opis, csp)
    img = re.search(r"img-src ([^;]*)", csp).group(1)
    assert 'http' not in img, (opis, csp)          # żadnych obcych domen dla obrazków
    assert h.get('X-Content-Type-Options') == 'nosniff', opis
    assert h.get('Referrer-Policy') == 'no-referrer', opis
    assert h.get('X-Frame-Options') == 'SAMEORIGIN', opis
    assert h.get('Server') == 'AnberFiles', (opis, h.get('Server'))


def test_naglowki_200_404_401_403_anbernic(tmp_path):
    k, korzen = _anbernic(tmp_path)
    (korzen / 'a.txt').write_text('x', encoding='utf-8')

    async def sc(cl):
        wyn = {}
        for opis, metoda, url, au in [
                ('200 listing', 'get', '/', AU),
                ('200 plik', 'get', '/a.txt', AU),
                ('200 json', 'get', '/?lektorqj=1', AU),
                ('404', 'get', '/nie-ma.txt', AU),
                ('401', 'get', '/', None),
                ('404 metoda', 'put', '/', AU)]:
            r = await getattr(cl, metoda)(url, auth=au)
            wyn[opis] = (r.status, r.headers)
        return wyn
    wyn = uruchom(k, sc)
    assert wyn['404'][0] == 404 and wyn['401'][0] == 401
    for opis, (_, h) in wyn.items():
        _sprawdz_naglowki(h, opis)


def test_naglowki_403_i_formularz_jarvis(tmp_path):
    korzen = tmp_path / 'srv' / 'korzen'
    k_basic = wczytaj(zbuduj_jarvis(tmp_path, logowanie='basic'))
    (korzen / 'a.txt').write_text('x', encoding='utf-8')

    async def sc(cl):
        r = await cl.delete('/a.txt', auth=aiohttp.BasicAuth('admin', HASLO))
        return r.status, r.headers
    st, h = uruchom(k_basic, sc)
    assert st == 403
    _sprawdz_naglowki(h, '403')

    k_form = wczytaj(zbuduj_jarvis(tmp_path / 'f'))

    async def sc2(cl):
        r = await cl.get('/', headers={'Accept': 'text/html'})
        return r.status, r.headers, await r.text()
    st, h, html = uruchom(k_form, sc2)
    assert st in (200, 401) and '<form' in html      # ekran „Ustaw hasło"
    _sprawdz_naglowki(h, 'ekran logowania')


# ── B1: nazwy plików w HTML ─────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform == 'win32', reason='Windows nie pozwala na < > " w nazwie')
def test_listing_escapuje_znaczniki_w_nazwie(tmp_path):
    """Znacznik w nazwie pliku/katalogu (bez „/", który jest separatorem ścieżki)."""
    k, korzen = _anbernic(tmp_path)
    (korzen / '<b>x<b>.txt').write_text('x', encoding='utf-8')
    (korzen / '<i>k<i>').mkdir()
    (korzen / '<i>k<i>' / '"q" <s>.md').write_text('# t', encoding='utf-8')
    _, _, html = _get(k, '/')
    assert '<b>x<b>' not in html and '<i>k<i>' not in html
    assert '&lt;b&gt;x&lt;b&gt;.txt' in html
    assert '&lt;i&gt;k&lt;i&gt;/' in html
    _, _, html = _get(k, '/' + quote('<i>k<i>') + '/')
    assert '<i>k<i>' not in html                      # okruszki i <title>
    assert '&quot;q&quot; &lt;s&gt;.md' in html
    _, _, html = _get(k, '/' + quote('<i>k<i>/"q" <s>.md') + '?view=1')
    assert '<s>' not in html and '"q"' not in html


def test_listing_escapuje_ampersand_w_nazwie(tmp_path):
    """Wariant uruchamialny także na Windows: encja w nazwie pliku/katalogu
    musi wyjść jako tekst (&amp;lt;), a nie jako znak „<"."""
    k, korzen = _anbernic(tmp_path)
    (korzen / '&lt;b&gt;.txt').write_text('x', encoding='utf-8')
    (korzen / 'k&lt;i&gt;').mkdir()
    (korzen / 'k&lt;i&gt;' / 'p&lt;s&gt;.md').write_text('# t', encoding='utf-8')
    _, _, html = _get(k, '/')
    assert '&amp;lt;b&amp;gt;.txt' in html
    assert '>📁 k&amp;lt;i&amp;gt;/<' in html
    assert 'href="k&lt;i&gt;/"' not in html
    _, _, html = _get(k, '/' + quote('k&lt;i&gt;') + '/')
    assert '<title>/k&amp;lt;i&amp;gt;/</title>' in html
    assert '>k&amp;lt;i&amp;gt;</a>' in html            # okruszek
    _, _, html = _get(k, '/' + quote('k&lt;i&gt;/p&lt;s&gt;.md') + '?view=1')
    assert '<title>p&amp;lt;s&amp;gt;.md</title>' in html


def test_kontrola_polskie_znaki_i_spacja_w_href(tmp_path):
    """Kontrola: zwykła nazwa nadal klikalna, href poprawnie zakodowany."""
    k, korzen = _anbernic(tmp_path)
    (korzen / 'zażółć gęślą.txt').write_text('x', encoding='utf-8')
    (korzen / 'mój katalog').mkdir()
    _, _, html = _get(k, '/')
    assert f'href="{quote("zażółć gęślą.txt")}"' in html
    assert '📄 zażółć gęślą.txt</a>' in html or 'zażółć gęślą.txt</a>' in html
    assert f'href="{quote("mój katalog")}/" class="dir">📁 mój katalog/</a>' in html
    status, _, tekst = _get(k, '/' + quote('zażółć gęślą.txt'))
    assert status == 200 and tekst == 'x'


# ── B2: JSON w <script> ─────────────────────────────────────────────────────

def test_json_do_script_jednostkowo():
    import server
    s = server._json_do_script({'t': '</script><script>alert(1)</script><!-- \u2028\u2029'})
    assert '<' not in s and '\u2028' not in s and '\u2029' not in s
    assert json.loads(s) == {'t': '</script><script>alert(1)</script><!-- \u2028\u2029'}


def test_rozdzialy_audio_nie_zamykaja_skryptu(tmp_path):
    k, korzen = _anbernic(tmp_path)
    tytul = '</script><script>alert(1)</script>'
    (korzen / 'doc.md').write_text(f'# {tytul}\n\nTreść.\n\n# Drugi\n\nDalej.\n',
                                   encoding='utf-8')
    (korzen / 'doc_lektor.mp3').write_bytes(b'ID3' + bytes(64))
    status, _, html = _get(k, '/doc.md?view=1')
    assert status == 200
    assert '<script>alert(1)' not in html
    m = re.search(r'const CH=(.*?);const au=', html)
    assert m, 'brak rozdziałów w stronie'
    ch = json.loads(m.group(1))
    assert [c['title'] for c in ch] == [tytul, 'Drugi']   # kontrola: treść nietknięta


def test_jedna_funkcja_json_do_script():
    """Wszystkie wstawienia JSON do <script> idą przez _json_do_script."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'app' / 'server.py').read_text(encoding='utf-8')
    assert ".replace('</'" not in src
    assert 'json.dumps(chaps' not in src.replace('_json_do_script(chaps', '')


def test_js_escape_obejmuje_cudzyslow():
    """Escapowanie po stronie przeglądarki (CSV/XLSX, kolejka lektora) obejmuje
    cudzysłów — komórka CSV wstawiana jest też do atrybutu title="…"."""
    import server
    assert '&quot;' in server.JS_ESC
    assert server.JS_ESC in server.CSV_TABLE_JS and server.JS_ESC in server.XLSX_TABLE_JS
    for wyr in ('esc(x.out)', 'esc(x.src)', 'esc(b.out)', 'esc(b.blad)'):
        assert wyr in server.LEKTORQ_PAGE, wyr
