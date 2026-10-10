"""Pozycje do decyzji z czterech źródeł vaulta i raporty automatów (plan 98,
porcja A): parsery `app/pozycje.py`.

Polecenie Karola 10.10.2026: „Najlepsze byłoby zatwierdzanie każdej propozycji
skilla oddzielnie” — „(wszystko co wymaga decyzji zwrotnej usera)”. Próbki
źródeł budowane w tmp_path według kształtu plików vaulta (plan 98, 1.4) —
nigdy czytane z vaulta Karola."""
import ast
import json
from pathlib import Path

import pytest

import pozycje as poz

APP = Path(__file__).resolve().parents[1] / 'app'


def _plik(v: Path, rel: str, tekst: str) -> Path:
    p = v / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(tekst, encoding='utf-8')
    return p


def _proposed(v: Path, elementy) -> Path:
    return _plik(v, poz.SCIEZKI['skill-scout'], json.dumps({'proposed': elementy},
                                                           ensure_ascii=False))


def _el(slug, priority=50, **inne):
    e = {'slug': slug, 'title': f'Tytuł {slug}', 'type': 'new', 'update_target': None,
         'freq': 3, 'minutes_per_run': 10, 'saved_per_week_min': 30, 'priority': priority,
         'what': f'Opis {slug}.', 'evidence': ['cytat (01.10)'], 'first_proposed': '2026-10-01'}
    e.update(inne)
    return e


# ── skill-scout ─────────────────────────────────────────────────────────────

def test_skill_scout_kazda_propozycja_osobno_wg_priorytetu(tmp_path):
    """Test 1: trzy elementy o RÓŻNYCH priorytetach → trzy pozycje, od
    najwyższego priorytetu; przyciski Zatwierdź/Odrzuć."""
    _proposed(tmp_path, [_el('niski', 10), _el('wysoki', 80), _el('sredni', 50)])
    pozycje, stan = poz.skill_scout(tmp_path)
    assert stan['stan'] == 'ok' and stan['liczba'] == 3
    assert [p['id'] for p in pozycje] == ['skill-scout:wysoki', 'skill-scout:sredni',
                                          'skill-scout:niski']
    p = pozycje[0]
    assert p['tytul'] == 'Tytuł wysoki' and p['tekst'] == 'Opis wysoki.'
    assert [w['kod'] for w in p['warianty']] == ['zatwierdz', 'odrzuc']
    assert p['kotwica'] == {'plik': poz.SCIEZKI['skill-scout'], 'slug': 'wysoki'}
    assert p['znaczniki']['priorytet'] == 80


def test_skill_scout_zle_elementy_pominiete_i_policzone(tmp_path):
    """Test 2: brak `what` → tekst z tytułu; brak `slug` i element-nie-obiekt
    pominięte i policzone; `evidence` nie-lista nie przeszkadza."""
    _proposed(tmp_path, [{'slug': 'bez-what', 'title': 'Tylko tytuł'},
                         {'title': 'bez sluga'}, 'tekst zamiast obiektu',
                         _el('ev', evidence='nie lista')])
    pozycje, stan = poz.skill_scout(tmp_path)
    assert [p['id'] for p in pozycje] == ['skill-scout:ev', 'skill-scout:bez-what']
    assert pozycje[1]['tekst'] == 'Tylko tytuł'
    assert stan['stan'] == 'ok' and stan['pominiete'] == 2


def test_skill_scout_pusta_lista_to_ok_nie_brak(tmp_path):
    """Test 3: poprawny plik bez propozycji ≠ brak pliku."""
    _proposed(tmp_path, [])
    pozycje, stan = poz.skill_scout(tmp_path)
    assert pozycje == [] and stan['stan'] == 'ok' and stan['liczba'] == 0
    assert 'brak' not in stan['komunikat']


def test_skill_scout_uszkodzony_i_brak_pliku(tmp_path):
    """Test 4: zły JSON → „nieczytelne” z powodem, bez wyjątku; brak pliku → „brak”."""
    pozycje, stan = poz.skill_scout(tmp_path)
    assert pozycje == [] and stan['stan'] == 'brak'
    assert poz.SCIEZKI['skill-scout'] in stan['komunikat']
    _plik(tmp_path, poz.SCIEZKI['skill-scout'], '{"proposed": [')
    pozycje, stan = poz.skill_scout(tmp_path)
    assert pozycje == [] and stan['stan'] == 'nieczytelne' and stan['powod']
    _plik(tmp_path, poz.SCIEZKI['skill-scout'], '{"inne": []}')
    pozycje, stan = poz.skill_scout(tmp_path)
    assert stan['stan'] == 'nieczytelne' and 'proposed' in stan['powod']


def test_skill_scout_element_rozstrzygniety_nie_jest_pozycja(tmp_path):
    """Test 5: agent laptopa dopisał `status`/`decyzja` → poza pulą."""
    _proposed(tmp_path, [_el('tak', status='zatwierdzone'), _el('nie', decyzja='odrzucone'),
                         _el('czeka', status='coś innego')])
    pozycje, stan = poz.skill_scout(tmp_path)
    assert [p['id'] for p in pozycje] == ['skill-scout:czeka']
    assert stan['rozstrzygniete'] == 2


def test_skill_scout_odcisk_bez_liczb_przeliczanych(tmp_path):
    """Test 6: priorytet, częstość, oszczędność i dowody są przeliczane przy
    każdym przebiegu skill-scouta — nie unieważniają decyzji; zmiana opisu tak."""
    _proposed(tmp_path, [_el('a', 50)])
    o1 = poz.skill_scout(tmp_path)[0][0]['odcisk']
    _proposed(tmp_path, [_el('a', 90, freq=9, saved_per_week_min=99, evidence=['inny'])])
    o2 = poz.skill_scout(tmp_path)[0][0]['odcisk']
    _proposed(tmp_path, [_el('a', 90, what='Nowy opis.')])
    o3 = poz.skill_scout(tmp_path)[0][0]['odcisk']
    assert o1 == o2 and o3 != o1


def test_skill_scout_propozycja_bedaca_juz_skillem(tmp_path):
    """Test 31 (przegląd, ustalenie 1): `type: new` ze slugiem, który ma
    `.claude/skills/<slug>/SKILL.md`, nie jest pozycją; `update` zostaje;
    sam katalog bez SKILL.md nie wyklucza."""
    _proposed(tmp_path, [_el('gotowy'), _el('pusty-katalog'),
                         _el('gotowy-upd', type='update', update_target='gotowy-upd'),
                         _el('nowy')])
    _plik(tmp_path, '.claude/skills/gotowy/SKILL.md', '# skill\n')
    _plik(tmp_path, '.claude/skills/gotowy-upd/SKILL.md', '# skill\n')
    (tmp_path / '.claude/skills/pusty-katalog').mkdir(parents=True)
    pozycje, stan = poz.skill_scout(tmp_path)
    assert sorted(p['id'] for p in pozycje) == ['skill-scout:gotowy-upd',
                                                'skill-scout:nowy',
                                                'skill-scout:pusty-katalog']
    assert stan['juz_skillem'] == 1
    assert '1 propozycja jest już skillem' in stan['komunikat']


# ── reflect ─────────────────────────────────────────────────────────────────

REFLECT = '''# Reflect — propozycje do przeglądu

*Wygenerowane: 2026-09-28, uzupełnione 2026-10-10 | Zakres: 21.09 → 10.10*

> Zaznacz checkbox `- [x]` przy zmianach, które chcesz nanieść.

> **Przebieg 10.10 (weekly).** Czeka 2 propozycji.

## persona.md → § 7 — pierwsza
**Typ:** ADD · **Powód:** polecenie z 03.09.
```diff
+ - Reguła pierwsza.
```
- [ ] ✅ Zatwierdź tę zmianę

## persona.md → § 7 — zatwierdzona już
treść
- [x] ✅ Zatwierdź tę zmianę

## persona.md → § 4 — trzecia
Treść trzeciej.
- [ ] ✅ Zatwierdź tę zmianę

### Odrzucone w tym przebiegu (świadomie)
- ODRZUCONA-TRESC nie jest pozycją

### Rozstrzygnięte przez CEO 10.10.2026 (usunięte z listy)
- też nie
'''


def test_reflect_sekcje_osobno_h3_konczy_sekcje(tmp_path):
    """Test 7: trzy sekcje `##`, jedna z `- [x]` → dwie pozycje; `### Odrzucone`
    kończy ostatnią sekcję i nie jest pozycją."""
    _plik(tmp_path, poz.SCIEZKI['reflect'], REFLECT)
    pozycje, stan = poz.reflect(tmp_path)
    assert stan['stan'] == 'ok' and stan['liczba'] == 2
    assert [p['tytul'] for p in pozycje] == ['persona.md → § 7 — pierwsza',
                                             'persona.md → § 4 — trzecia']
    assert 'ODRZUCONA' not in pozycje[1]['tekst'] and 'Odrzucone' not in pozycje[1]['tekst']
    assert 'Zatwierdź tę zmianę' not in pozycje[0]['tekst']
    assert 'Reguła pierwsza.' in pozycje[0]['tekst']
    assert pozycje[0]['kotwica'] == {'plik': poz.SCIEZKI['reflect'],
                                     'naglowek': '## persona.md → § 7 — pierwsza'}
    assert pozycje[0]['id'].startswith('reflect:') and len(pozycje[0]['id']) == 8 + 12


def test_reflect_bez_checkboxa_i_powtorzony_naglowek(tmp_path):
    """Test 8: sekcja bez linii checkboxa → pozycja z ostrzeżeniem; dwa takie
    same nagłówki → dwie pozycje o różnych identyfikatorach."""
    _plik(tmp_path, poz.SCIEZKI['reflect'],
          '# R\n\n## Bez pola\nTreść bez checkboxa.\n\n'
          '## Taki sam\nA\n- [ ] ✅ Zatwierdź tę zmianę\n\n'
          '## Taki sam\nB\n- [ ] ✅ Zatwierdź tę zmianę\n')
    pozycje, stan = poz.reflect(tmp_path)
    assert [p['tytul'] for p in pozycje] == ['Bez pola', 'Taki sam', 'Taki sam']
    assert 'brak checkboxa' in pozycje[0]['ostrzezenie']
    assert not pozycje[1].get('ostrzezenie')
    assert len({p['id'] for p in pozycje}) == 3


# ── NOW.md: Otwarte decyzje ─────────────────────────────────────────────────

NOW = '''# NOW — Bieżący kontekst

*Ostatni update: 2026-10-10 12:15 (daily; poprzedni: daily 09.10 20:40)*

> **08.10 — FAB: rdzeń C3D domknięty.** Kolejka wznowiona.
> Druga linia akapitu.

## Aktywne projekty
- [ ] **To nie jest decyzja:** inna sekcja

## Otwarte decyzje

- [ ] **Stara wersja na Anbernicu:** usunąć — tylko za zgodą
- [x] **Już rozstrzygnięte:** A tak · B nie
- [ ] **Nocna pętla kolejki (zadanie 8 Pulsa):** A włączyć · B zostawić wyłączoną — wisi od 26.09
- [ ] **Standard wtyku toru kamer:** USB-A czy USB-C (03.10, kamery dziś w obu)
- [ ] **Kalibracje kamer: ekspozycja ręczna −6 czy AUTO** (plan 78)
- [ ] **Odświeżanie kopii vaulta:** wyzwalacz po pushu + timer 15 min — czy zostają oba
- [ ] **Epic: Trader czy Non-Trader** oraz VAT
- [ ] **Quest 3/3S:** A awatar-lustro · B podgląd dla trenera
- [ ] **Kolejki propozycji:** 20 pozycji `/reflect` · 68 kandydatów skill-scout

## Ostatnie ustalenia
- [ ] **Też nie:** poza sekcją
'''


def test_now_otwarte_decyzje_warianty(tmp_path):
    """Test 9: `- [ ]` z sekcji `## Otwarte decyzje` osobno, `- [x]` i metawpis
    „Kolejki propozycji” pominięte; `A … · B …` → przyciski A/B; „X czy Y”
    regułą tokenową (cztery przypadki z przeglądu, ustalenie 3)."""
    _plik(tmp_path, poz.SCIEZKI['now'], NOW)
    pozycje, stan = poz.now(tmp_path)
    assert stan['stan'] == 'ok'
    tyt = [p['tytul'] for p in pozycje]
    assert tyt == ['Stara wersja na Anbernicu', 'Nocna pętla kolejki (zadanie 8 Pulsa)',
                   'Standard wtyku toru kamer', 'Kalibracje kamer: ekspozycja ręczna −6 czy AUTO',
                   'Odświeżanie kopii vaulta', 'Epic: Trader czy Non-Trader', 'Quest 3/3S']
    war = {p['tytul']: [(w['kod'], w['napis']) for w in p['warianty']] for p in pozycje}
    domyslne = [('zatwierdz', 'Zatwierdź'), ('odrzuc', 'Odrzuć')]
    assert war['Stara wersja na Anbernicu'] == domyslne
    assert war['Nocna pętla kolejki (zadanie 8 Pulsa)'] == [('A', 'A: włączyć'),
                                                            ('B', 'B: zostawić wyłączoną')]
    assert war['Quest 3/3S'] == [('A', 'A: awatar-lustro'), ('B', 'B: podgląd dla trenera')]
    assert war['Standard wtyku toru kamer'] == [('A', 'USB-A'), ('B', 'USB-C')]
    assert war['Kalibracje kamer: ekspozycja ręczna −6 czy AUTO'] == [('A', '−6'), ('B', 'AUTO')]
    assert war['Epic: Trader czy Non-Trader'] == [('A', 'Trader'), ('B', 'Non-Trader')]
    assert war['Odświeżanie kopii vaulta'] == domyslne
    p = pozycje[1]
    assert p['kotwica']['plik'] == poz.SCIEZKI['now']
    assert p['kotwica']['linia'].startswith('- [ ] **Nocna pętla')
    assert '**' not in p['tekst'] and 'A włączyć' in p['tekst']


def test_now_brak_sekcji_to_nieczytelne(tmp_path):
    _plik(tmp_path, poz.SCIEZKI['now'], '# NOW\n\n## Inne\n- [ ] x\n')
    pozycje, stan = poz.now(tmp_path)
    assert pozycje == [] and stan['stan'] == 'nieczytelne'
    assert 'Otwarte decyzje' in stan['powod']


# ── NOW-pending.md: Decyzje do briefingu ────────────────────────────────────

NOW_PENDING = '''# NOW-pending

<!-- NOW:START -->
## Otwarte decyzje
- [ ] **Kopia starej treści NOW:** A tak · B nie
### Wpis w kopii NOW
<!-- NOW:END -->

## Decyzje do briefingu

### ALARM automaty (10.10.2026 12:30, laptop)
- Jarvis Memory Daily: ostatni bieg 376 h temu

### Wykonawca: 86b: PRZEGLAD Opus - equilibra (2026-10-01 01:53, wykonawca, jarvis)

Bieg #4218, status success.

### Automat wypychania vaulta zatrzymany (2026-09-04 17:20, laptop)
Konflikt scalania.

## Stan pracy długotrwałej

### Dozorca — nie decyzja
treść

## Decyzje do briefingu

### Automat wypychania vaulta zatrzymany (2026-09-04 17:20, laptop)
Konflikt scalania.

### Aktualizacja pamięci uruchamiana potrójnie
Trzy wywołania.
'''


def test_now_pending_wszystkie_sekcje_briefingu(tmp_path):
    """Test 10 (przegląd, ustalenie 2): czytane WSZYSTKIE sekcje `## Decyzje do
    briefingu`, nic poza nimi (kopia NOW wyżej, „Stan pracy długotrwałej”);
    wpisy `### Wykonawca:` pominięte; ten sam wpis z dwóch sekcji = jedna
    pozycja z licznikiem; jedyny przycisk „Przyjąłem do wiadomości”."""
    _plik(tmp_path, poz.SCIEZKI['now-pending'], NOW_PENDING)
    pozycje, stan = poz.now_pending(tmp_path)
    assert [p['tytul'] for p in pozycje] == [
        'ALARM automaty (10.10.2026 12:30, laptop)',
        'Automat wypychania vaulta zatrzymany (2026-09-04 17:20, laptop)',
        'Aktualizacja pamięci uruchamiana potrójnie']
    assert pozycje[1]['znaczniki']['powtorzenia'] == 2
    assert [(w['kod'], w['napis']) for w in pozycje[0]['warianty']] == [
        ('przyjete', 'Przyjąłem do wiadomości')]
    assert stan['stan'] == 'ok' and stan['pominiete_wykonawca'] == 1
    assert pozycje[0]['kotwica'] == {'plik': 'NOW-pending.md',
                                     'naglowek': '### ALARM automaty (10.10.2026 12:30, laptop)'}
    assert 'Jarvis Memory Daily' in pozycje[0]['tekst']


def test_now_pending_bez_sekcji_briefingu(tmp_path):
    _plik(tmp_path, poz.SCIEZKI['now-pending'], '# x\n\n## Inne\n### a\n')
    pozycje, stan = poz.now_pending(tmp_path)
    assert pozycje == [] and stan['stan'] == 'nieczytelne'
    assert 'Decyzje do briefingu' in stan['powod']


# ── Raporty automatów ───────────────────────────────────────────────────────

def _dane_scouta(v, nazwa, data, tytuly):
    _plik(v, poz.SCIEZKI['skill-scout-dane'] + '/' + nazwa, json.dumps({
        'date': data, 'window_days': 7, 'stats': {'intents': 423, 'sessions': 4},
        'candidates': [{'slug': f's{i}', 'title': t} for i, t in enumerate(tytuly)]}))


def test_raporty_automatow_klucze_i_remis(tmp_path):
    """Test 11 (przegląd, ustalenie 9): skill-scout — najnowsza data z nazwy
    pliku, przy remisie dat plik BEZ przyrostka (laptop); reflect — klucz
    z OSTATNIEJ daty linii „Wygenerowane”; pamięć — z „Ostatni update”."""
    _dane_scouta(tmp_path, '2026-10-09.json', '2026-10-09', ['stary'])
    _dane_scouta(tmp_path, '2026-10-10.json', '2026-10-10', ['Pierwszy kandydat', 'Drugi'])
    _dane_scouta(tmp_path, '2026-10-10_jarvis.json', '2026-10-10', ['z jarvisa'])
    _plik(tmp_path, poz.SCIEZKI['reflect'], REFLECT)
    _plik(tmp_path, poz.SCIEZKI['now'], NOW)
    pozycje, stany = poz.raporty(tmp_path)
    po_id = {p['id']: p for p in pozycje}
    assert sorted(po_id) == ['raport:pamiec:2026-10-10 12:15', 'raport:reflect:2026-10-10',
                             'raport:skill-scout:2026-10-10']
    sc = po_id['raport:skill-scout:2026-10-10']
    assert 'Pierwszy kandydat' in sc['tekst'] and 'z jarvisa' not in sc['tekst']
    assert sc['warianty'] == [] and sc['rodzaj'] == 'raport'
    assert sc['zrodlo_plik'] == poz.SCIEZKI['skill-scout-dane'] + '/2026-10-10.json'
    assert 'Kolejka wznowiona. Druga linia akapitu.' in po_id['raport:pamiec:2026-10-10 12:15']['tekst']
    assert 'Czeka 2 propozycji' in po_id['raport:reflect:2026-10-10']['tekst']
    assert all(s['stan'] == 'ok' for s in stany)


def test_raporty_brak_zrodel_nie_da_sie_ustalic(tmp_path):
    pozycje, stany = poz.raporty(tmp_path)
    assert pozycje == []
    po = {s['zrodlo']: s for s in stany}
    assert po['raport-skill-scout']['stan'] == 'brak'
    assert po['raport-reflect']['stan'] == 'brak'
    assert 'nie da się ustalić' in po['raport-reflect']['komunikat']
    assert po['raport-pamiec']['stan'] == 'brak'


# ── Wspólne: limity, determinizm, tylko odczyt ──────────────────────────────

def test_limit_rozmiaru_i_obcinanie_na_granicy_slowa(tmp_path):
    """Test 27: źródło > 2 MiB → „nieczytelne — za duży”; tytuł i tekst
    obcinane do limitów z „…” na granicy słowa."""
    p = _proposed(tmp_path, [_el('a')])
    with open(p, 'a', encoding='utf-8') as f:
        f.write(' ' * (poz.LIMIT_ZRODLA_B + 1))
    pozycje, stan = poz.skill_scout(tmp_path)
    assert pozycje == [] and stan['stan'] == 'nieczytelne' and 'za duży' in stan['powod']
    dlugi = ' '.join(['słowo'] * 300)
    _proposed(tmp_path, [_el('a', title=dlugi, what=dlugi)])
    p = poz.skill_scout(tmp_path)[0][0]
    assert len(p['tytul']) <= poz.LIMIT_TYTULU and p['tytul'].endswith('słowo…')
    assert len(p['tekst']) <= poz.LIMIT_TEKSTU and p['tekst'].endswith('słowo…')


def test_skan_deterministyczny_i_identyfikatory_z_tresci(tmp_path):
    """Test 19 i 26: dwa skany tego samego drzewa → ta sama lista; dopisanie
    pozycji WYŻEJ nie zmienia identyfikatorów pozostałych (id z treści, nie
    z numeru wiersza)."""
    _proposed(tmp_path, [_el('a', 10), _el('b', 20)])
    _plik(tmp_path, poz.SCIEZKI['reflect'], REFLECT)
    _plik(tmp_path, poz.SCIEZKI['now'], NOW)
    _plik(tmp_path, poz.SCIEZKI['now-pending'], NOW_PENDING)
    _dane_scouta(tmp_path, '2026-10-10.json', '2026-10-10', ['k'])
    s1, s2 = poz.skanuj(tmp_path), poz.skanuj(tmp_path)
    assert s1 == s2
    ids = {p['id'] for p in s1[0]}
    po_tytule = {p['tytul']: p['id'] for p in s1[0]}
    _plik(tmp_path, poz.SCIEZKI['now'], NOW.replace(
        '## Otwarte decyzje\n\n', '## Otwarte decyzje\n\n- [ ] **Nowa na górze:** x\n'))
    _plik(tmp_path, poz.SCIEZKI['reflect'], REFLECT.replace(
        '## persona.md → § 7 — pierwsza', '## Nowa\nn\n- [ ] ✅ Zatwierdź tę zmianę\n\n'
        '## persona.md → § 7 — pierwsza'))
    s3 = poz.skanuj(tmp_path)[0]
    ids2 = {p['id'] for p in s3}
    assert ids < ids2 and len(ids2 - ids) == 2
    po_tytule2 = {p['tytul']: p['id'] for p in s3}
    assert all(po_tytule2[t] == i for t, i in po_tytule.items())
    assert {s['zrodlo'] for s in s1[1]} >= {'skill-scout', 'reflect', 'now', 'now-pending'}


def test_tytul_ze_skryptem_to_dane(tmp_path):
    """Tekst z vaulta to dane: parser niczego nie interpretuje (escapowanie
    robi widok — test w porcji B)."""
    _proposed(tmp_path, [_el('x', title='<script>alert(1)</script>')])
    assert poz.skill_scout(tmp_path)[0][0]['tytul'] == '<script>alert(1)</script>'


ZAPISUJACE = {'write_text', 'write_bytes', 'unlink', 'rename', 'replace', 'mkdir',
              'rmtree', 'touch', 'remove', 'rmdir', 'makedirs', 'symlink_to',
              'chmod', 'truncate'}


def test_parsery_nie_zapisuja_niczego_ast():
    """Test 12 (część strukturalna): `app/pozycje.py` nie ma wywołań zapisu ani
    `open` w trybie innym niż odczyt — vault jest tylko do odczytu."""
    drzewo = ast.parse((APP / 'pozycje.py').read_text(encoding='utf-8'))
    zle = []
    for w in ast.walk(drzewo):
        if not isinstance(w, ast.Call):
            continue
        f = w.func
        nazwa = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')
        modul = getattr(getattr(f, 'value', None), 'id', '')
        # str.replace(a, b) to nie zapis; Path.replace(cel) i os.replace(a, b) tak
        tekstowy = nazwa == 'replace' and len(w.args) == 2 and modul not in ('os', 'shutil')
        if nazwa in ZAPISUJACE and not tekstowy:
            zle.append(f'{nazwa} (linia {w.lineno})')
        if nazwa == 'open':
            tryb = w.args[1] if len(w.args) > 1 else next(
                (k.value for k in w.keywords if k.arg == 'mode'), None)
            if tryb is not None and not (isinstance(tryb, ast.Constant)
                                         and set(str(tryb.value)) <= set('rbt')):
                zle.append(f'open w trybie zapisu (linia {w.lineno})')
    assert zle == []


@pytest.mark.parametrize('zrodlo', ['skill-scout', 'reflect', 'now', 'now-pending'])
def test_kazde_zrodlo_brak_pliku_to_stan_brak(tmp_path, zrodlo):
    """Stany bez ciszy: każde z czterech źródeł bez pliku → „brak” z adresem."""
    fun = {'skill-scout': poz.skill_scout, 'reflect': poz.reflect, 'now': poz.now,
           'now-pending': poz.now_pending}[zrodlo]
    pozycje, stan = fun(tmp_path)
    assert pozycje == [] and stan['stan'] == 'brak'
    assert poz.SCIEZKI[zrodlo] in stan['komunikat']
