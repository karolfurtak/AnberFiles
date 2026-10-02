"""C8 (serwer): pliki robocze druku i lektora o NIEPRZEWIDYWALNYCH nazwach
(tempfile), a nie /tmp/<stem>_print.docx, /tmp/<stem>_lektor_src.txt,
/tmp/lo_print — inny użytkownik maszyny nie podłoży dowiązania pod znaną
nazwę, a dwa równoległe zadania dla tego samego pliku nie kolidują.
/tmp/lektor.lock i /tmp/lektor_progress.json zostają (rygiel dzielony
z innymi programami)."""
import asyncio
from pathlib import Path

import pytest

from conftest import uruchom, wczytaj, zbuduj_anbernic  # noqa: F401


@pytest.fixture
def k(tmp_path):
    import server
    k = wczytaj(zbuduj_anbernic(tmp_path))
    server.zastosuj_konfiguracje(k)
    (tmp_path / 'sprawozdania' / 'EXPORT' / 'export_to_docx.py').write_text('', encoding='utf-8')
    return k


def _docx(sciezka: Path, tekst: str):
    import docx
    d = docx.Document()
    d.add_paragraph(tekst)
    d.save(str(sciezka))


def test_tekst_z_docx_dla_lektora_nazwa_nieprzewidywalna(tmp_path, k):
    import server
    a = tmp_path / 'sprawozdania' / 'raport.docx'
    _docx(a, 'Pierwsze zdanie.')
    p1 = server._docx_to_txt(a)
    p2 = server._docx_to_txt(a)
    assert p1 != p2
    assert p1.read_text(encoding='utf-8') == 'Pierwsze zdanie.'
    assert p2.exists()
    przewidywalna = Path('/tmp') / 'raport_lektor_src.txt'
    assert p1 != przewidywalna and p2 != przewidywalna
    assert p1.name.startswith('raport_') and p1.name.endswith('_lektor_src.txt')
    p1.unlink()
    p2.unlink()


@pytest.mark.parametrize('nazwa', ['raport.md', 'raport.docx'])
def test_dwa_rownolegle_druki_tego_samego_pliku_nie_koliduja(tmp_path, k, monkeypatch, nazwa):
    import server
    cel = tmp_path / 'sprawozdania' / nazwa
    cel.write_text('# R\n', encoding='utf-8')
    polecenia = []

    async def atrapa(*cmd, log=None):
        polecenia.append(list(cmd))
        cmd = list(cmd)
        if '-o' in cmd:                                   # eksport md → docx
            Path(cmd[cmd.index('-o') + 1]).write_bytes(b'DOCX')
        if '--convert-to' in cmd:                         # soffice → pdf
            src = Path(cmd[-1])
            out = Path(cmd[cmd.index('--outdir') + 1]) / (src.stem + '.pdf')
            await asyncio.sleep(0.05)                     # okno na kolizję
            out.write_bytes(b'%PDF ' + str(src).encode())
        if cmd[0] == 'lp':
            assert Path(cmd[-1]).read_bytes().startswith(b'%PDF')
        return 0
    monkeypatch.setattr(server, '_polecenie', atrapa, raising=False)

    async def oba():
        await asyncio.gather(server._drukuj(cel), server._drukuj(cel))
    asyncio.run(oba())
    lp = [c[-1] for c in polecenia if c[0] == 'lp']
    assert len(lp) == 2 and lp[0] != lp[1]
    for c in polecenia:
        tekst = ' '.join(c)
        assert 'file:///tmp/lo_print' not in tekst
        assert '/tmp/' + Path(nazwa).stem + '_print' not in tekst
        if '--outdir' in c:
            assert c[c.index('--outdir') + 1] != '/tmp'
    # katalogi robocze sprzątnięte po wysłaniu do drukarki
    assert not any(Path(p).exists() for p in lp)
