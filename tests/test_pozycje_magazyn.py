"""Wybory przy pozycjach do decyzji w `Magazyn` (plan 98, porcja A): rekord,
idempotencja, migracja stanu 1 → 2, cofnięcie kodu, zakładka wg tabeli 3.4,
eksport dla agenta laptopa."""
import importlib.util
import json
import re
import threading
from datetime import datetime
from pathlib import Path

import pytest

import pozycje as poz
import przesluchania as prz

DANE = Path(__file__).resolve().parent / 'dane'


def _pozycja(id_='skill-scout:audyt', odcisk='o1', warianty=None, rodzaj='skill-scout'):
    return {'rodzaj': rodzaj, 'id': id_, 'tytul': 'Audyt skilli', 'tekst': 'Krótki opis.',
            'zrodlo_plik': poz.SCIEZKI['skill-scout'],
            'kotwica': {'plik': poz.SCIEZKI['skill-scout'], 'slug': 'audyt'},
            'warianty': warianty if warianty is not None else list(poz.WARIANTY_DOMYSLNE),
            'odcisk': odcisk, 'kolejnosc': 0, 'znaczniki': {}}


def _stan_v1(plik: Path):
    plik.write_text(json.dumps({
        'wersja': 1, 'nastepne_id': 8, 'potwierdzono_do': 3,
        'decyzje': [{'id': 7, 'sciezka': 'Zasoby/a.md', 'tytul': 'A', 'decyzja': 'akceptuje',
                     'status': 'zaakceptowane-niewdrozone', 'uwaga': '', 'zrodlo_uwagi': 'tekst',
                     'zakres': 'notatka', 'kotwica': {}, 'odcisk_notatki': 'x',
                     'utworzono': '2026-10-01 10:00:00'}],
        'odsluch': {'Zasoby/a.md': {'pozycja_s': 3.0}}, 'ui': {'zakladka': 'decyzja'}},
        ensure_ascii=False), encoding='utf-8')


# ── Rekord wyboru ───────────────────────────────────────────────────────────

def test_rekord_wyboru_wszystkie_pola(tmp_path):
    """Test 15: pola schematu (plan 3.2), `czas` ISO 8601 z przesunięciem,
    odcisk i migawka z POZYCJI (serwer), wspólny licznik z decyzjami notatek."""
    m = prz.Magazyn(tmp_path / 's.json')
    m.dodaj_decyzje('Zasoby/a.md', 'akceptuje', '', 'x')
    r, powt = m.dodaj_wybor(_pozycja(odcisk='abc'), 'zatwierdz', ' uwaga ')
    assert not powt
    assert set(r) == {'id', 'czas', 'zrodlo', 'id_pozycji', 'wybor', 'uwaga', 'odcisk',
                      'tytul', 'tekst', 'kotwica'}
    assert r['id'] == 2 and r['zrodlo'] == 'skill-scout' and r['wybor'] == 'zatwierdz'
    assert r['id_pozycji'] == 'skill-scout:audyt' and r['odcisk'] == 'abc'
    assert r['uwaga'] == 'uwaga' and r['tytul'] == 'Audyt skilli'
    assert r['kotwica'] == {'plik': poz.SCIEZKI['skill-scout'], 'slug': 'audyt'}
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d', r['czas'])
    assert datetime.fromisoformat(r['czas']).utcoffset() is not None
    zapis = json.loads((tmp_path / 's.json').read_text(encoding='utf-8'))
    assert zapis['wybory'] == [r] and zapis['nastepne_id'] == 3


def test_wybor_idempotentny_i_rownolegly(tmp_path):
    """Test 16: powtórzone kliknięcie → ten sam rekord (`powtorzenie`); inny
    wybór → nowy rekord; dwa wątki jednocześnie → jeden rekord."""
    m = prz.Magazyn(tmp_path / 's.json')
    r1, p1 = m.dodaj_wybor(_pozycja(), 'zatwierdz', '')
    r2, p2 = m.dodaj_wybor(_pozycja(), 'zatwierdz', '')
    assert (p1, p2) == (False, True) and r2['id'] == r1['id']
    r3, p3 = m.dodaj_wybor(_pozycja(), 'odrzuc', '')
    assert not p3 and r3['id'] == r1['id'] + 1
    r4, p4 = m.dodaj_wybor(_pozycja(), 'odrzuc', 'z uwagą')
    assert not p4 and r4['id'] == r3['id'] + 1
    assert m.ostatnie_wybory()['skill-scout:audyt']['id'] == r4['id']

    m2 = prz.Magazyn(tmp_path / 's2.json')
    start = threading.Barrier(8)
    wyniki = []

    def klik():
        start.wait()
        wyniki.append(m2.dodaj_wybor(_pozycja(id_='now:abc'), 'odrzuc', '')[0]['id'])
    watki = [threading.Thread(target=klik) for _ in range(8)]
    for w in watki:
        w.start()
    for w in watki:
        w.join(10)
    assert len(set(wyniki)) == 1 and len(m2.wybory()) == 1


def test_wybor_spoza_wariantow_i_zla_uwaga(tmp_path):
    """Test 17 (poziom magazynu): wybór spoza wariantów pozycji → ValueError,
    nic nie zapisane; uwaga ponad limit → ValueError."""
    m = prz.Magazyn(tmp_path / 's.json')
    ab = [{'kod': 'A', 'napis': 'A: tak'}, {'kod': 'B', 'napis': 'B: nie'}]
    with pytest.raises(ValueError):
        m.dodaj_wybor(_pozycja(warianty=ab), 'zatwierdz', '')
    with pytest.raises(ValueError):
        m.dodaj_wybor(_pozycja(), 'A', '')
    with pytest.raises(ValueError):
        m.dodaj_wybor(_pozycja(), 'zatwierdz', 'x' * (prz.LIMIT_UWAGI + 1))
    with pytest.raises(ValueError):
        m.dodaj_wybor(_pozycja(warianty=[], rodzaj='raport'), '', '')
    assert m.wybory() == [] and not (tmp_path / 's.json').exists()
    assert m.dodaj_wybor(_pozycja(warianty=ab), 'B', '')[0]['wybor'] == 'B'


def test_nieudany_zapis_wyboru_nie_zostawia_rekordu(tmp_path, monkeypatch):
    m = prz.Magazyn(tmp_path / 's.json')

    def awaria(*a, **k):
        raise OSError('dysk pełny')
    monkeypatch.setattr(prz, 'zapisz_atomowo', awaria)
    with pytest.raises(prz.BladStanu):
        m.dodaj_wybor(_pozycja(), 'zatwierdz', '')
    monkeypatch.undo()
    assert m.wybory() == []
    assert m.dodaj_wybor(_pozycja(), 'zatwierdz', '')[0]['id'] == 1


# ── Migracja i cofnięcie ────────────────────────────────────────────────────

def test_migracja_v1_do_v2_bez_utraty(tmp_path):
    """Test 13: plik wersji 1 ładuje się bez strat, licznik kontynuowany,
    po pierwszym zapisie `wersja` = 2."""
    plik = tmp_path / 's.json'
    _stan_v1(plik)
    m = prz.Magazyn(plik)
    assert m.uszkodzony is None and m.tylko_odczyt is None
    assert [d['id'] for d in m.decyzje()] == [7] and m.potwierdzono_do() == 3
    assert m.odsluch('Zasoby/a.md') == {'pozycja_s': 3.0}
    r, _ = m.dodaj_wybor(_pozycja(), 'odrzuc', '')
    assert r['id'] == 8
    zapis = json.loads(plik.read_text(encoding='utf-8'))
    assert zapis['wersja'] == prz.WERSJA_STANU == 2
    assert zapis['decyzje'][0]['id'] == 7 and zapis['ui'] == {'zakladka': 'decyzja'}
    assert zapis['nastepne_id'] == 9 and zapis['potwierdzono_do'] == 3


def test_stan_z_nowszej_wersji_tylko_do_odczytu(tmp_path):
    """Test 13 (przegląd, ustalenie 10): `wersja` > WERSJA_STANU → stan
    czytelny, każdy zapis kończy się BladStanu, plik bajt w bajt bez zmian."""
    plik = tmp_path / 's.json'
    _stan_v1(plik)
    stan = json.loads(plik.read_text(encoding='utf-8'))
    stan['wersja'] = 3
    stan['przyszle'] = {'cos': 1}
    plik.write_text(json.dumps(stan), encoding='utf-8')
    przed = plik.read_bytes()
    m = prz.Magazyn(plik)
    assert m.uszkodzony is None and 'nowszej wersji 3' in m.tylko_odczyt
    assert [d['id'] for d in m.decyzje()] == [7]
    for zapis in (lambda: m.dodaj_wybor(_pozycja(), 'odrzuc', ''),
                  lambda: m.ustaw_ui('zakladka', 'decyzja'),
                  lambda: m.dodaj_decyzje('Zasoby/a.md', 'odrzuca', '', 'x')):
        with pytest.raises(prz.BladStanu, match='nowszej wersji 3'):
            zapis()
    assert plik.read_bytes() == przed
    assert [p.name for p in tmp_path.iterdir()] == ['s.json']


def _stary_modul():
    spec = importlib.util.spec_from_file_location('przesluchania_v1',
                                                  DANE / 'przesluchania_v1.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cofniecie_kodu_do_v1_zachowuje_wybory(tmp_path):
    """Test 14 (przegląd, ustalenie 11): kod z a567a59 (zamrożona kopia
    w tests/dane) wczytuje stan v2 i przy zapisie zachowuje `wybory`."""
    stary = _stary_modul()
    assert stary.WERSJA_STANU == 1
    plik = tmp_path / 's.json'
    m = prz.Magazyn(plik)
    r, _ = m.dodaj_wybor(_pozycja(), 'zatwierdz', 'uwaga')
    s = stary.Magazyn(plik)
    assert s.uszkodzony is None
    s.ustaw_ui('zakladka', 'rozstrzygniete')
    s.dodaj_decyzje('Zasoby/a.md', 'akceptuje', '', 'x')
    zapis = json.loads(plik.read_text(encoding='utf-8'))
    assert zapis['wybory'] == [r]
    assert zapis['nastepne_id'] == 3
    assert prz.Magazyn(plik).ostatnie_wybory()['skill-scout:audyt'] == r


# ── Zakładka wg tabeli 3.4 ──────────────────────────────────────────────────

def test_zakladka_wyboru_tabela_3_4():
    """Test 18: pięć wierszy tabeli 3.4 planu."""
    rek = {'odcisk': 'o1'}
    assert poz.zakladka_wyboru(True, None, 'o1') == ('decyzja', '')
    assert poz.zakladka_wyboru(True, rek, 'o1') == ('rozstrzygniete', 'zgodny')
    assert poz.zakladka_wyboru(True, rek, 'o2') == ('decyzja', 'zmieniona')
    assert poz.zakladka_wyboru(False, rek, None) == ('rozstrzygniete', 'usuniete')
    assert poz.zakladka_wyboru(False, None, None) == (None, '')


# ── Eksport ─────────────────────────────────────────────────────────────────

def test_eksport_z_wyborami_i_regresja_starego_stanu(tmp_path, monkeypatch):
    """Test 24 (przegląd, ustalenie 12): stan bez wyborów → Markdown bajt
    w bajt jak w kodzie v1, JSON z tymi samymi starymi polami; z wyborami —
    sekcja „Wybory w pozycjach”, `wybory` w JSON, `do` = maksimum obu list."""
    stary = _stary_modul()
    monkeypatch.setattr(prz, '_teraz_iso', lambda teraz=None: '2026-10-10 20:00:00')
    monkeypatch.setattr(stary, '_teraz_iso', lambda teraz=None: '2026-10-10 20:00:00')
    plik = tmp_path / 's.json'
    _stan_v1(plik)
    m = prz.Magazyn(plik)
    stan = json.loads(plik.read_text(encoding='utf-8'))
    assert prz.eksport_markdown(m._stan) == stary.eksport_markdown(stan)
    js = prz.eksport_json(m)
    assert js.pop('wybory') == []
    assert js == stary.eksport_json(stary.Magazyn(plik))

    m.dodaj_wybor(_pozycja(), 'zatwierdz', 'pilne')
    m.dodaj_decyzje('Zasoby/a.md', 'odrzuca', '', 'x')
    m.dodaj_wybor(_pozycja(id_='now:1', warianty=[{'kod': 'A', 'napis': 'A: tak'}],
                           rodzaj='now'), 'A', '')
    js = prz.eksport_json(m)
    assert [w['id'] for w in js['wybory']] == [8, 10]
    assert js['ostatnie_id'] == 10
    assert all(w['przeniesione'] is False and w['kotwica'] for w in js['wybory'])
    md = prz.eksport_markdown(m._stan)
    assert '## Wybory w pozycjach' in md and 'pilne' in md
    assert '`{"do": 10}`' in md
    assert 'slug: audyt' in md
    m.potwierdz(10)
    assert prz.eksport_json(m)['wybory'] == []
    assert '## Wybory w pozycjach' not in prz.eksport_markdown(m._stan)
