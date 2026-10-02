"""Lektor (tools/czytaj_tts.py) — drugi silnik: lokalny Piper przez usługę HTTP.

Testy budują własne pliki w tmp_path i własną atrapę usługi Piper
(http.server w wątku, stałe PCM). Żadnej sieci zewnętrznej, żadnego urządzenia.
"""
import json
import re
import shutil
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import czytaj_tts  # noqa: E402

SR = 16000
PCM_ZDANIA = 8000 * 2           # 0,5 s ciszy s16le mono przy 16 kHz
TEKST = ('Pierwsze zdanie testowe. Drugie zdanie testowe. '
         'Trzecie zdanie testowe.')


class AtrapaPiper:
    """Atrapa serwer_tts.py: /zdrowie, /syntezuj. Liczy żądania syntezy."""

    def __init__(self, gotowy=True, bledy_na_poczatku=0, opoznienie=0.0):
        self.gotowy = gotowy
        self.bledy = bledy_na_poczatku
        self.opoznienie = opoznienie
        self.zdania = []
        atrapa = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path != '/zdrowie':
                    self.send_error(404)
                    return
                b = json.dumps({'gotowy': atrapa.gotowy,
                                'sample_rate': SR}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                n = int(self.headers.get('Content-Length', 0))
                txt = self.rfile.read(n).decode('utf-8')
                if self.path != '/syntezuj':
                    self.send_error(404)
                    return
                if atrapa.opoznienie:
                    time.sleep(atrapa.opoznienie)
                if atrapa.bledy > 0:
                    atrapa.bledy -= 1
                    self.send_error(500)
                    return
                atrapa.zdania.append(txt)
                b = bytes(PCM_ZDANIA)
                self.send_response(200)
                self.send_header('Content-Type', f'audio/L16; rate={SR}')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                try:
                    self.wfile.write(b)
                except OSError:
                    pass

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.adres = f'http://127.0.0.1:{self.srv.server_address[1]}'
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def _wolny_adres():
    import socket
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return f'http://127.0.0.1:{p}'


@pytest.fixture
def lektor(tmp_path, monkeypatch):
    """Przygotowuje plik .md, conf w tmp_path i zwraca funkcję uruchom(...)."""
    monkeypatch.setattr(czytaj_tts, 'PROGRESS_FILE', tmp_path / 'postep.json')
    monkeypatch.setattr(czytaj_tts, 'LOCK_FILE', tmp_path / 'lektor.lock')
    monkeypatch.setattr(czytaj_tts, 'PIPER_PAUZA', 0)
    md = tmp_path / 'raport.md'
    md.write_text(TEKST, encoding='utf-8')
    conf = tmp_path / 'lektor-ustawienia.conf'
    monkeypatch.setattr(czytaj_tts, 'CONF_FILE', conf)

    def uruchom(conf_linie=(), argv=(), wyjscie='raport_lektor.wav'):
        conf.write_text('kopiuj_do = nie\nopisuj_grafiki = nie\n'
                        + ''.join(f'{x}\n' for x in conf_linie),
                        encoding='utf-8')
        out = tmp_path / wyjscie
        monkeypatch.setattr(sys, 'argv', ['czytaj_tts.py', str(md),
                                          '-o', str(out), *argv])
        czytaj_tts.main()
        return out
    return uruchom


def _cues(out: Path):
    return json.loads(out.with_name(out.stem + '.cues.json')
                      .read_text(encoding='utf-8'))


def test_piper_wav_dlugosc_zgodna_z_pcm_i_cues(lektor):
    with AtrapaPiper() as p:
        out = lektor(['silnik = piper', f'piper_adres = {p.adres}'])
    assert len(p.zdania) == 3
    assert out.exists()
    with wave.open(str(out)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        dl = w.getnframes() / SR
    oczek = 3 * PCM_ZDANIA / (2 * SR) + 2 * czytaj_tts.CISZA_S
    assert dl > 0
    assert dl == pytest.approx(oczek, abs=0.002)
    cues = _cues(out)
    assert len(cues) == 3
    t = [c['t'] for c in cues]
    assert t[0] == 0
    assert all(b > a for a, b in zip(t, t[1:]))
    assert t[1] == pytest.approx(0.5 + czytaj_tts.CISZA_S, abs=0.002)
    assert [c['text'] for c in cues] == p.zdania
    # po sukcesie: brak checkpointu i części surowego PCM
    assert not list(out.parent.glob('*.resume.json'))
    assert not list(out.parent.glob('*.pcm'))


def test_piper_cli_ma_pierwszenstwo_przed_conf(lektor):
    with AtrapaPiper() as p:
        out = lektor([f'piper_adres = {p.adres}'], argv=['--silnik', 'piper'])
    assert len(p.zdania) == 3 and out.exists()


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='brak ffmpeg')
@pytest.mark.parametrize('fmt', ['mp3', 'flac'])
def test_piper_konwersja_ffmpeg(lektor, fmt):
    with AtrapaPiper() as p:
        out = lektor(['silnik = piper', f'piper_adres = {p.adres}'],
                     argv=['--format', fmt], wyjscie=f'raport_lektor.{fmt}')
    assert out.exists() and out.stat().st_size > 0
    assert len(_cues(out)) == 3


def test_piper_ponawia_zdanie_do_3_prob(lektor):
    with AtrapaPiper(bledy_na_poczatku=2) as p:
        out = lektor(['silnik = piper', f'piper_adres = {p.adres}'])
    assert out.exists() and len(p.zdania) == 3


def test_piper_3_nieudane_proby_kod_bledu(lektor, capsys):
    with AtrapaPiper(bledy_na_poczatku=3) as p:
        with pytest.raises(SystemExit) as e:
            lektor(['silnik = piper', f'piper_adres = {p.adres}'])
    assert e.value.code not in (0, None)
    err = capsys.readouterr().err
    assert 'Piper' in err and '3' in err


def test_piper_limit_czasu_zadania(lektor, capsys, monkeypatch):
    monkeypatch.setattr(czytaj_tts, 'PIPER_TIMEOUT', 0.3)
    with AtrapaPiper(opoznienie=1.0) as p:
        with pytest.raises(SystemExit) as e:
            lektor(['silnik = piper', f'piper_adres = {p.adres}'])
    assert e.value.code not in (0, None)
    assert 'Piper' in capsys.readouterr().err


def test_piper_usluga_niegotowa(lektor, capsys):
    with AtrapaPiper(gotowy=False) as p:
        with pytest.raises(SystemExit) as e:
            lektor(['silnik = piper', f'piper_adres = {p.adres}'])
        assert p.zdania == []
    assert e.value.code not in (0, None)
    assert f'usługa Piper niedostępna pod {p.adres}' in capsys.readouterr().err


def test_piper_usluga_nie_odpowiada(lektor, capsys):
    adres = _wolny_adres()
    with pytest.raises(SystemExit) as e:
        lektor(['silnik = piper', f'piper_adres = {adres}'])
    assert e.value.code not in (0, None)
    assert f'usługa Piper niedostępna pod {adres}' in capsys.readouterr().err


def test_piper_wznawia_z_punktu_kontrolnego(lektor):
    # pierwszy bieg: zdanie 2 pada 3 razy → przerwanie po zdaniu 1
    with AtrapaPiper() as p:
        p.bledy = 0
        orig = czytaj_tts._piper_zdanie
        licz = {'n': 0}

        def padnij_po_pierwszym(*a, **kw):
            licz['n'] += 1
            if licz['n'] in (2, 3, 4):   # zdanie 2: wszystkie 3 próby
                raise czytaj_tts.PiperBlad('symulowane przerwanie')
            return orig(*a, **kw)
        czytaj_tts._piper_zdanie = padnij_po_pierwszym
        try:
            with pytest.raises(SystemExit):
                lektor(['silnik = piper', f'piper_adres = {p.adres}'])
        finally:
            czytaj_tts._piper_zdanie = orig
        assert len(p.zdania) == 1
        out = lektor(['silnik = piper', f'piper_adres = {p.adres}'])
    assert len(p.zdania) == 3            # drugi bieg syntezował tylko 2 zdania
    with wave.open(str(out)) as w:
        dl = w.getnframes() / SR
    assert dl == pytest.approx(3 * 0.5 + 2 * czytaj_tts.CISZA_S, abs=0.002)
    assert len(_cues(out)) == 3


class _AtrapaEdge:
    wywolania = []

    class Communicate:
        def __init__(self, text, voice, rate='+0%'):
            _AtrapaEdge.wywolania.append((voice, text))

        async def stream(self):
            yield {'type': 'audio', 'data': b'ID3' + bytes(32)}


def test_jawny_edge_bez_sieci_nie_wola_pipera(lektor, monkeypatch):
    """`silnik = edge` w ustawieniach: ani sprawdzenia, ani syntezy Pipera.
    (Brak linii `silnik` sprawdza usługę Piper — testy niżej.)"""
    _AtrapaEdge.wywolania = []
    monkeypatch.setattr(czytaj_tts, 'edge_tts', _AtrapaEdge)

    def zakaz(*a, **kw):
        raise AssertionError('ścieżka edge nie może wołać usługi Piper')
    monkeypatch.setattr(czytaj_tts.urllib.request, 'urlopen', zakaz)
    out = lektor(['silnik = edge'], wyjscie='raport_lektor.mp3')
    assert out.exists() and out.read_bytes().startswith(b'ID3')
    assert len(_AtrapaEdge.wywolania) >= 1
    assert _AtrapaEdge.wywolania[0][0] == czytaj_tts.VOICES['marek']


def test_cli_edge_wygrywa_z_conf_piper(lektor, monkeypatch):
    _AtrapaEdge.wywolania = []
    monkeypatch.setattr(czytaj_tts, 'edge_tts', _AtrapaEdge)
    out = lektor(['silnik = piper', f'piper_adres = {_wolny_adres()}'],
                 argv=['--silnik', 'edge'], wyjscie='raport_lektor.mp3')
    assert out.exists() and _AtrapaEdge.wywolania


def test_nieznany_silnik_w_conf_to_edge(lektor, monkeypatch):
    _AtrapaEdge.wywolania = []
    monkeypatch.setattr(czytaj_tts, 'edge_tts', _AtrapaEdge)
    out = lektor(['silnik = cos-innego'], wyjscie='raport_lektor.mp3')
    assert out.exists() and _AtrapaEdge.wywolania


def test_przyklad_conf_ma_silnik_edge_i_adres_piper():
    t = (REPO / 'konfiguracja' / 'lektor-ustawienia.conf.przyklad').read_text(
        encoding='utf-8')
    cfg = {}
    for ln in t.splitlines():
        ln = ln.split('#', 1)[0].strip()
        if '=' in ln:
            k, v = ln.split('=', 1)
            cfg[k.strip()] = v.strip()
    assert cfg.get('silnik') == 'edge'
    assert cfg.get('piper_adres') == 'http://127.0.0.1:8123'


# ── edge: sidecar cues z granic zdań (SentenceBoundary / WordBoundary) ──────
RAMKA_MP3 = bytes([0xFF, 0xF3, 0x64, 0xC4]) + bytes(140)  # MPEG-2 L3 24 kHz
RAMKA_S = 576 / 24000                                      # 0,024 s


def test_mp3_sekundy_z_ramek():
    assert czytaj_tts._mp3_sekundy(b'ID3' + bytes(7) + RAMKA_MP3 * 50) == \
        pytest.approx(50 * RAMKA_S)
    assert czytaj_tts._mp3_sekundy(b'') == 0


class _AtrapaEdgeZGranicami:
    """Każdy chunk: 50 ramek MP3 (1,2 s) + SentenceBoundary co 0,4 s."""
    wywolania = []

    class Communicate:
        def __init__(self, text, voice, rate='+0%'):
            self.text = text
            _AtrapaEdgeZGranicami.wywolania.append(text)

        async def stream(self):
            yield {'type': 'audio', 'data': RAMKA_MP3 * 50}
            for k, z in enumerate(czytaj_tts.zdania([('pl', self.text)])):
                yield {'type': 'SentenceBoundary', 'offset': k * 4_000_000,
                       'duration': 3_000_000, 'text': z}


def test_edge_tworzy_cues_z_przesunieciem_chunkow(lektor, tmp_path,
                                                 monkeypatch):
    _AtrapaEdgeZGranicami.wywolania = []
    monkeypatch.setattr(czytaj_tts, 'edge_tts', _AtrapaEdgeZGranicami)
    (tmp_path / 'raport.md').write_text(
        'Czujnik MAP (z ang. Manifold Absolute Pressure) mierzy ciśnienie. '
        'Drugie zdanie po polsku.', encoding='utf-8')
    out = lektor([], wyjscie='raport_lektor.mp3')
    n = len(_AtrapaEdgeZGranicami.wywolania)
    assert n == 3                         # PL, wstawka EN, PL
    cues = _cues(out)
    t = [c['t'] for c in cues]
    assert t[0] == 0
    assert all(b >= a for a, b in zip(t, t[1:]))
    # pierwsze zdanie każdego chunka zaczyna się po czasie poprzednich chunków
    starty = sorted({round(k * 50 * RAMKA_S, 3) for k in range(n)})
    assert set(starty) <= set(t)
    assert czytaj_tts._mp3_sekundy(out.read_bytes()) == \
        pytest.approx(n * 50 * RAMKA_S)


def test_cues_z_samych_granic_slow():
    zd = [{'type': 'WordBoundary', 'offset': k * 2_000_000, 'duration': 1}
          for k in range(5)]
    c = czytaj_tts._cues_z_granic(zd, 'Ala ma kota. Kot ma Alę.', 10.0)
    assert [x['text'] for x in c] == ['Ala ma kota.', 'Kot ma Alę.']
    assert c[0]['t'] == 10.0 and c[1]['t'] == pytest.approx(10.6)


# ── domyślny silnik: lokalny Piper, gdy odpowiada (audyt B8, prywatność) ────

def test_bez_silnika_piper_dostepny_to_piper(lektor, monkeypatch):
    """Brak linii `silnik`, usługa Piper odpowiada → tekst zostaje na urządzeniu."""
    _AtrapaEdge.wywolania = []
    monkeypatch.setattr(czytaj_tts, 'edge_tts', _AtrapaEdge)
    with AtrapaPiper() as p:
        out = lektor([f'piper_adres = {p.adres}'])
    assert len(p.zdania) == 3 and out.exists()
    assert _AtrapaEdge.wywolania == []


def test_bez_silnika_piper_niedostepny_to_edge(lektor, monkeypatch, capsys):
    """Kontrola rozróżniająca: ta sama konfiguracja, usługa nie odpowiada → edge
    (konsola Anbernic bez zmian) i ostrzeżenie o wysyłce tekstu."""
    _AtrapaEdge.wywolania = []
    monkeypatch.setattr(czytaj_tts, 'edge_tts', _AtrapaEdge)
    out = lektor([f'piper_adres = {_wolny_adres()}'], wyjscie='raport_lektor.mp3')
    assert out.exists() and _AtrapaEdge.wywolania
    assert 'tekst opuszcza urządzenie (usługa Microsoft)' in capsys.readouterr().err


def test_jawny_silnik_edge_przy_dostepnym_piper(lektor, monkeypatch):
    _AtrapaEdge.wywolania = []
    monkeypatch.setattr(czytaj_tts, 'edge_tts', _AtrapaEdge)
    with AtrapaPiper() as p:
        out = lektor(['silnik = edge', f'piper_adres = {p.adres}'],
                     wyjscie='raport_lektor.mp3')
    assert out.exists() and _AtrapaEdge.wywolania
    assert p.zdania == []


@pytest.mark.parametrize('conf,dostepny,oczek', [
    ({}, True, 'piper'), ({}, False, 'edge'), ({'silnik': 'edge'}, True, 'edge'),
    ({'silnik': 'piper'}, False, 'piper'), ({'silnik': 'xyz'}, True, 'edge')])
def test_wybor_silnika(conf, dostepny, oczek):
    assert czytaj_tts.wybierz_silnik(None, conf, piper_dostepny=lambda a: dostepny) == oczek
    assert czytaj_tts.wybierz_silnik('edge', conf, piper_dostepny=lambda a: True) == 'edge'


@pytest.mark.parametrize('linia,napis', [
    ('silnik = edge', 'Silnik edge: tekst opuszcza urządzenie (usługa Microsoft).'),
    ('', 'inaczej edge — wtedy tekst opuszcza urządzenie (usługa Microsoft).'),
    ('silnik = piper', None)])
def test_okno_lektora_ostrzega_o_silniku_edge(tmp_path, linia, napis):
    import json as _json
    import aiohttp
    from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic
    k = wczytaj(zbuduj_anbernic(tmp_path))
    (k.katalog_eksportu / 'lektor-ustawienia.conf').write_text(
        f'format = mp3\n{linia}\n', encoding='utf-8')
    (k.katalog_glowny / 'a.md').write_text('Tekst.', encoding='utf-8')

    async def sc(cl):
        r = await cl.get('/', auth=aiohttp.BasicAuth('anbernic', HASLO))
        return await r.text()
    html = uruchom(k, sc)
    assert 'class="dl lek"' in html
    if napis is None:
        assert '_LEK_UWAGA=' not in html
    else:
        m = re.search(r'window\._LEK_UWAGA=(".*?");', html)
        assert m and napis in _json.loads(m.group(1))
