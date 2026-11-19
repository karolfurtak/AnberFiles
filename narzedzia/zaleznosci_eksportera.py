#!/usr/bin/env python3
"""Zależności zewnętrzne skryptów eksportera (export_to_docx.py, typografia.py,
lint_styl.py, czytaj_tts.py) wyprowadzone z ich importów — źródło wpisów
w requirements.in (plan ujednolicenia v3, pkt 2).

Czyta importy z całego drzewa składni: najwyższego poziomu, w funkcjach
i w blokach try/except ImportError (zależności opcjonalne, flagi HAS_*).
Pomija bibliotekę standardową i moduły leżące obok skryptów (np. typografia).

Użycie:
    python narzedzia/zaleznosci_eksportera.py EXPORT/export_to_docx.py EXPORT/typografia.py ...
        → lista pakietów PyPI (jeden na linię)
    ... --sprawdz-requirements requirements.in
        → kod 1, gdy któregoś pakietu brak w pliku wejściowym zależności
    ... --importuj
        → importuje każdy moduł w bieżącym interpreterze; kod 1 przy braku
          którejkolwiek zależności, także opcjonalnej (test T2b na Wykonawcy)
    ... --json

Kody wyjścia: 0 = komplet, 1 = brak zależności, 2 = błąd wejścia.
Ostatnia linia (stderr): SUKCES albo BŁĄD z powodem. Tylko odczyt.
"""
import argparse
import ast
import importlib
import json
import re
import sys
from pathlib import Path

# nazwa modułu w import → nazwa pakietu PyPI (gdy różne)
PAKIETY = {
    'docx': 'python-docx', 'PIL': 'Pillow', 'bs4': 'beautifulsoup4',
    'fontTools': 'fonttools', 'yaml': 'PyYAML', 'markdown': 'Markdown',
    'edge_tts': 'edge-tts', 'cv2': 'opencv-python', 'sklearn': 'scikit-learn',
}


def importy(zrodlo: str) -> set:
    """Nazwy modułów najwyższego poziomu ze wszystkich instrukcji import."""
    wynik = set()
    for n in ast.walk(ast.parse(zrodlo)):
        if isinstance(n, ast.Import):
            wynik.update(a.name.split('.')[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            wynik.add(n.module.split('.')[0])
    return wynik


def zewnetrzne(pliki) -> dict:
    """{moduł: pakiet PyPI} dla importów spoza biblioteki standardowej
    i spoza modułów leżących obok skryptów."""
    lokalne = set()
    moduly = set()
    for p in pliki:
        p = Path(p)
        lokalne.update(x.stem for x in p.parent.glob('*.py'))
        moduly |= importy(p.read_text(encoding='utf-8'))
    std = set(getattr(sys, 'stdlib_module_names', ())) | {'__future__'}
    return {m: PAKIETY.get(m, m) for m in sorted(moduly - std - lokalne)}


def _normuj(nazwa: str) -> str:
    return re.sub(r'[-_.]+', '-', nazwa).lower()


def pakiety_w_pliku(sciezka: Path) -> set:
    wynik = set()
    for linia in sciezka.read_text(encoding='utf-8').splitlines():
        linia = linia.split('#', 1)[0].strip()
        m = re.match(r'([A-Za-z0-9_.-]+)', linia)
        if m:
            wynik.add(_normuj(m.group(1)))
    return wynik


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('pliki', nargs='+', type=Path)
    ap.add_argument('--sprawdz-requirements', type=Path)
    ap.add_argument('--importuj', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    try:
        z = zewnetrzne(a.pliki)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        print(f'BŁĄD: {e}', file=sys.stderr)
        return 2
    print(json.dumps(z, ensure_ascii=False, indent=1) if a.json
          else '\n'.join(sorted(set(z.values()))))
    braki = []
    if a.sprawdz_requirements:
        try:
            jest = pakiety_w_pliku(a.sprawdz_requirements)
        except OSError as e:
            print(f'BŁĄD: {e}', file=sys.stderr)
            return 2
        braki += [f'{p} (brak w {a.sprawdz_requirements.name})'
                  for p in sorted(set(z.values())) if _normuj(p) not in jest]
    if a.importuj:
        for mod, pak in z.items():
            try:
                importlib.import_module(mod)
            except Exception as e:            # ImportError, OSError (biblioteka C)
                braki.append(f'{pak}: import {mod} nieudany ({type(e).__name__}: {e})')
    if braki:
        print('\n'.join(braki), file=sys.stderr)
        print(f'BŁĄD: brak zależności: {len(braki)}', file=sys.stderr)
        return 1
    print(f'SUKCES: zależności zewnętrzne {len(z)}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
