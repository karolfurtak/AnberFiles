#!/usr/bin/env python3
"""Porównanie dwóch wersji serwera (np. sprawozdania-server.py i app/server.py)
funkcja po funkcji — podstawa tabeli różnic (załącznik A planu ujednolicenia).

Funkcje najwyższego poziomu (def / async def) z obu plików są normalizowane:
bez docstringów i komentarzy, jednolity zapis (ast.unparse). Dla funkcji
wspólnych liczona jest liczba zmienionych linii po normalizacji (difflib).

Użycie:
    python narzedzia/porownaj_wersje.py STARY.py NOWY.py            # tabela Markdown
    python narzedzia/porownaj_wersje.py STARY.py NOWY.py --diff DIR # + pliki różnic
    python narzedzia/porownaj_wersje.py STARY.py NOWY.py --json      # dane maszynowe

Kod wyjścia: 0 = porównanie wykonane, 2 = błąd wejścia (brak pliku, błąd składni).
Ostatnia linia wyjścia (stderr): SUKCES albo BŁĄD z powodem.
Tylko biblioteka standardowa; nic nie zapisuje poza katalogiem --diff.
"""
import argparse
import ast
import difflib
import json
import sys
from pathlib import Path


def _bez_docstringow(wezel):
    for n in ast.walk(wezel):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            ciało = n.body
            if ciało and isinstance(ciało[0], ast.Expr) and \
                    isinstance(getattr(ciało[0], 'value', None), ast.Constant) and \
                    isinstance(ciało[0].value.value, str):
                n.body = ciało[1:] or [ast.Pass()]
    return wezel


def funkcje(zrodlo: str) -> dict:
    """Tekst modułu → {nazwa: (linie_oryginalne, tekst_znormalizowany)}."""
    drzewo = ast.parse(zrodlo)
    wynik = {}
    for n in drzewo.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            linie = (n.end_lineno or n.lineno) - n.lineno + 1
            wynik[n.name] = (linie, ast.unparse(_bez_docstringow(n)))
    return wynik


def porownaj(stary: str, nowy: str) -> dict:
    """Dwa teksty modułów → {'tylko_stary', 'tylko_nowy', 'wspolne': [...]}."""
    a, b = funkcje(stary), funkcje(nowy)
    wspolne = []
    for nazwa in sorted(set(a) & set(b)):
        la, ta = a[nazwa]
        lb, tb = b[nazwa]
        d = [x for x in difflib.unified_diff(ta.splitlines(), tb.splitlines(), lineterm='', n=0)
             if x[:1] in '+-' and not x.startswith(('+++', '---'))]
        wspolne.append({'nazwa': nazwa, 'linie_stary': la, 'linie_nowy': lb,
                        'zmienione': len(d), 'identyczne': ta == tb})
    return {'tylko_stary': sorted(set(a) - set(b)), 'tylko_nowy': sorted(set(b) - set(a)),
            'wspolne': sorted(wspolne, key=lambda w: (-w['zmienione'], w['nazwa']))}


def diff_funkcji(stary: str, nowy: str, nazwa: str) -> str:
    a, b = funkcje(stary), funkcje(nowy)
    return '\n'.join(difflib.unified_diff(a[nazwa][1].splitlines(), b[nazwa][1].splitlines(),
                                          'stary', 'nowy', lineterm=''))


def tabela_md(w: dict) -> str:
    wiersze = ['| # | Funkcja | Linie stary | Linie nowy | Zmienione linie (po normalizacji) |',
               '|---|---|---|---|---|']
    for i, f in enumerate(w['wspolne'], 1):
        wiersze.append(f"| {i} | `{f['nazwa']}` | {f['linie_stary']} | {f['linie_nowy']} | "
                       f"{'0 (identyczne)' if f['identyczne'] else f['zmienione']} |")
    return '\n'.join(wiersze)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('stary', type=Path)
    ap.add_argument('nowy', type=Path)
    ap.add_argument('--diff', type=Path, help='katalog na pliki różnic <funkcja>.diff')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    try:
        ts = a.stary.read_text(encoding='utf-8')
        tn = a.nowy.read_text(encoding='utf-8')
        w = porownaj(ts, tn)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        print(f'BŁĄD: {e}', file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(w, ensure_ascii=False, indent=1))
    else:
        print(f"Tylko stary ({len(w['tylko_stary'])}): {', '.join(w['tylko_stary'])}")
        print(f"Tylko nowy: {len(w['tylko_nowy'])}; wspólne: {len(w['wspolne'])}\n")
        print(tabela_md(w))
    if a.diff:
        a.diff.mkdir(parents=True, exist_ok=True)
        for f in w['wspolne']:
            if not f['identyczne']:
                (a.diff / f"{f['nazwa']}.diff").write_text(
                    diff_funkcji(ts, tn, f['nazwa']) + '\n', encoding='utf-8')
    print(f"SUKCES: wspólne {len(w['wspolne'])}, tylko stary {len(w['tylko_stary'])}, "
          f"tylko nowy {len(w['tylko_nowy'])}", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
