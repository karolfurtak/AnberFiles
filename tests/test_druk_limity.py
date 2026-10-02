"""D5: druk z limitem zadań na godzinę (limit_druku_na_godzine, domyślnie 20;
ponad → 429 z Retry-After) i czytelnym 400, gdy do druku .md brakuje
export_to_docx.py w katalogu eksportu (dawniej: 202 i cicha awaria w
print_errors.log)."""
import aiohttp
import pytest

from conftest import HASLO, uruchom, wczytaj, zbuduj_anbernic

AU = aiohttp.BasicAuth('anbernic', HASLO)


@pytest.fixture
def bez_drukarki(monkeypatch):
    """Atrapa programów druku (eksport, soffice, lp) — nic nie trafia do CUPS."""
    import server
    wywolania = []

    async def atrapa(*cmd, log=None):
        wywolania.append(cmd)
        return 0
    monkeypatch.setattr(server, '_polecenie', atrapa, raising=False)
    return wywolania


def _k(tmp_path, limit=None, eksport=True):
    conf = zbuduj_anbernic(tmp_path)
    if limit is not None:
        conf.write_text(conf.read_text(encoding='utf-8')
                        + f'[serwer]\nlimit_druku_na_godzine = {limit}\n', encoding='utf-8')
    if eksport:
        (tmp_path / 'sprawozdania' / 'EXPORT' / 'export_to_docx.py').write_text(
            '', encoding='utf-8')
    for n in ('a.pdf', 'b.md'):
        (tmp_path / 'sprawozdania' / n).write_text('x', encoding='utf-8')
    return wczytaj(conf)


def test_limit_druku_na_godzine_429(tmp_path, bez_drukarki):
    k = _k(tmp_path, limit=2)

    async def sc(cl):
        st = []
        for _ in range(3):
            r = await cl.post('/a.pdf?print=1', auth=AU)
            st.append((r.status, r.headers.get('Retry-After')))
        return st
    st = uruchom(k, sc)
    assert [s for s, _ in st] == [202, 202, 429]
    assert st[2][1] is not None and int(st[2][1]) > 0


def test_limit_domyslny_20(tmp_path):
    assert wczytaj(zbuduj_anbernic(tmp_path)).limit_druku_na_godzine == 20


def test_druk_md_bez_skryptu_eksportu_400(tmp_path, bez_drukarki):
    k = _k(tmp_path, eksport=False)

    async def sc(cl):
        r = await cl.post('/b.md?print=1', auth=AU)
        return r.status, await r.text()
    st, tekst = uruchom(k, sc)
    assert st == 400
    assert 'export_to_docx.py' in tekst
    assert bez_drukarki == []


def test_druk_md_ze_skryptem_202(tmp_path, bez_drukarki):
    """Kontrola rozróżniająca: skrypt eksportu jest → 202 jak dotąd."""
    k = _k(tmp_path, eksport=True)

    async def sc(cl):
        return (await cl.post('/b.md?print=1', auth=AU)).status
    assert uruchom(k, sc) == 202
