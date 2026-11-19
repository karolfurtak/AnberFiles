"""Przenoszenie wpisu do innego folderu i obrót obrazu (moduł „przenoszenie”).

Przeniesione z sprawozdania-server.py (move_item, rotate_item). Moduł nie zna
serwera: dostaje katalog główny, kosz i funkcję rejestru zdarzeń. Uprawnienia
(moduł włączony, tryb tylko do odczytu, nagłówek strony) sprawdza serwer.
"""
import shutil
import time
from pathlib import Path

KATY = {90: 'ROTATE_270', 270: 'ROTATE_90', 180: 'ROTATE_180'}   # PIL: ROTATE_90 = CCW


class Blad(Exception):
    def __init__(self, status: int, tekst: str):
        super().__init__(tekst)
        self.status = status
        self.tekst = tekst


def _w_korzeniu(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def przenies(root: Path, raw: str, cel_rel: str) -> dict:
    """Przenosi wpis raw (względem root) do folderu cel_rel (względem root).
    Zachowuje nazwę, nigdy nie nadpisuje (409). Wynik: {'moved', 'to'[, 'noop']}."""
    root = Path(root).resolve()
    try:
        target = (root / raw.strip('/')).resolve()
    except (OSError, ValueError):
        raise Blad(400, 'Zła ścieżka') from None
    if root not in target.parents:                 # samego katalogu głównego nie ruszamy
        raise Blad(403, 'Poza katalogiem głównym')
    if not target.exists():
        raise Blad(404, 'Brak pliku')
    cel_rel = (cel_rel or '').strip().strip('/')
    if any(c in cel_rel for c in '\x00\r\n'):
        raise Blad(400, 'Zły folder docelowy')
    try:
        cel = (root / cel_rel).resolve() if cel_rel else root
    except (OSError, ValueError):
        raise Blad(400, 'Zły folder docelowy') from None
    if not _w_korzeniu(cel, root) or not cel.is_dir():
        raise Blad(400, 'Zły folder docelowy')
    if any(cz.startswith('.') for cz in cel.relative_to(root).parts):
        raise Blad(400, 'Folder docelowy ukryty')
    if target.is_dir() and (cel == target or target in cel.parents):
        raise Blad(400, 'Nie można przenieść folderu do samego siebie')
    if cel == target.parent:
        return {'moved': target.name, 'noop': True}
    dest = cel / target.name
    if dest.exists() or dest.is_symlink():
        raise Blad(409, f'W folderze docelowym jest już „{target.name}”')
    shutil.move(str(target), str(dest))
    return {'moved': target.name, 'to': cel_rel or '/',
            'opis': f'{target.relative_to(root)} → {dest.relative_to(root)}'}


def obroc(root: Path, kosz: Path, raw: str, kat: str, tryb: str, rozszerzenia) -> dict:
    """Obrót obrazu o wielokrotność 90° (PIL transpose, bez strat geometrii;
    EXIF wypieczony). kat: 90 = w prawo, -90 = w lewo, 180. tryb overwrite:
    kopia oryginału do kosza (<nazwa>.orig-<czas>) + zapis w miejscu;
    copy: nowy plik <stem>_rot<ext>."""
    root = Path(root).resolve()
    try:
        target = (root / raw.strip('/')).resolve()
    except (OSError, ValueError):
        raise Blad(400, 'Zła ścieżka') from None
    if root not in target.parents:
        raise Blad(403, 'Poza katalogiem głównym')
    suf = target.suffix.lower()
    if not target.is_file() or suf not in rozszerzenia:
        raise Blad(400, 'To nie jest obraz')
    if suf in ('.svg', '.svgz'):
        raise Blad(400, 'SVG nie obsługuje obrotu tutaj')
    try:
        k = int(kat) % 360
    except (TypeError, ValueError):
        raise Blad(400, 'Zły kąt') from None
    if k not in KATY:
        raise Blad(400, 'Kąt musi być ±90 lub 180')
    if tryb not in ('overwrite', 'copy'):
        raise Blad(400, 'Tryb: overwrite albo copy')
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise Blad(500, 'Brak Pillow na serwerze') from None
    with Image.open(target) as zrodlo:
        im = ImageOps.exif_transpose(zrodlo)
        im = im.transpose(getattr(Image.Transpose, KATY[k]))

    def zapisz(img, sciezka):
        if suf in ('.jpg', '.jpeg'):
            img.convert('RGB').save(sciezka, quality=90, optimize=True)
        elif suf == '.webp':
            img.save(sciezka, quality=92)
        else:
            img.save(sciezka)

    if tryb == 'copy':
        out = target.with_name(f'{target.stem}_rot{target.suffix}')
        i = 2
        while out.exists():
            out = target.with_name(f'{target.stem}_rot{i}{target.suffix}')
            i += 1
        zapisz(im, out)
    else:
        kosz = Path(kosz)
        kosz.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, kosz / (target.name + '.orig-' + time.strftime('%Y%m%d_%H%M%S')))
        zapisz(im, target)
        out = target
    return {'ok': True, 'file': out.name, 'mode': tryb,
            'opis': f'{target.relative_to(root)} -> {out.name} ({k}°, {tryb})'}


# Przeciąganie wiersza listingu na folder (podfolder, „..”, okruszek ścieżki) →
# POST <źródło>?move=<folder względem katalogu głównego>. Nagłówek X-AnberFiles
# dokłada afFetch (JS_ZAPIS serwera, wklejany obok).
JS_LISTINGU = (
    '<style>tr[data-name].dragging{opacity:.45}'
    'a.dropok{outline:2px solid #2a7;background:#dfffe8;border-radius:3px}</style>'
    '<script>(function(){'
    'let DN=null;'
    'const base=location.pathname.replace(/\\/+$/,"");'
    'document.querySelectorAll("tr[data-name]").forEach(function(tr){'
    'tr.setAttribute("draggable","true");'
    'tr.querySelectorAll("a,img").forEach(function(x){x.setAttribute("draggable","false");});'
    'tr.addEventListener("dragstart",function(e){DN=tr.getAttribute("data-name");'
    'e.dataTransfer.effectAllowed="move";try{e.dataTransfer.setData("text/plain",DN);}catch(_){}'
    'tr.classList.add("dragging");});'
    'tr.addEventListener("dragend",function(){tr.classList.remove("dragging");DN=null;'
    'document.querySelectorAll(".dropok").forEach(function(x){x.classList.remove("dropok");});});'
    '});'
    'function destRel(a){var u=new URL(a.getAttribute("href"),location.href);'
    'return decodeURIComponent(u.pathname).replace(/^\\/+|\\/+$/g,"");}'
    'document.querySelectorAll("a[href$=\'/\']").forEach(function(a){'
    'if(a.closest("tr[data-name]")===null&&!a.closest("h2,.crumb,.crumbs"))return;'
    'a.addEventListener("dragover",function(e){if(DN===null)return;e.preventDefault();'
    'e.dataTransfer.dropEffect="move";a.classList.add("dropok");});'
    'a.addEventListener("dragleave",function(){a.classList.remove("dropok");});'
    'a.addEventListener("drop",async function(e){if(DN===null)return;e.preventDefault();'
    'a.classList.remove("dropok");var name=DN;'
    'var src=base+"/"+encodeURIComponent(name);var dest=destRel(a);'
    'try{var r=await afFetch(src+"?move="+encodeURIComponent(dest),{method:"POST"});'
    'if(r.ok){location.reload();}else{alert("Nie przeniesiono: "+(await r.text()));}'
    '}catch(err){alert("Błąd: "+err);}'
    '});'
    '});'
    '})();</script>'
)

# ↺ / ↻ w podglądzie obrazu: obrót + nadpisanie (oryginał do kosza), odświeżenie.
PRZYCISKI_OBRAZU = (
    '<a href="#" id="rotl" title="Obróć w lewo 90° (zapis; oryginał do Kosza)">↺</a>'
    '<a href="#" id="rotr" title="Obróć w prawo 90° (zapis; oryginał do Kosza)">↻</a>')

JS_OBRAZU = (
    '<script>(function(){'
    'const Q=location.pathname;'
    'function rot(deg){return async function(e){e.preventDefault();'
    'try{const r=await afFetch(Q+"?rotate="+deg+"&mode=overwrite",{method:"POST"});'
    'const j=await r.json().catch(()=>({}));'
    'if(j&&j.ok){const im=document.querySelector(".stage img")||document.querySelector("img");'
    'if(im)im.src=Q+"?v="+Date.now();}'
    'else{alert((j&&j.error)||"Błąd obrotu");}'
    '}catch(err){alert("Błąd: "+err);}};}'
    'const l=document.getElementById("rotl");if(l)l.onclick=rot(-90);'
    'const p=document.getElementById("rotr");if(p)p.onclick=rot(90);'
    '})();</script>')
