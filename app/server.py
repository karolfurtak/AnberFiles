#!/usr/bin/env python3
"""File server dla /mnt/data/sprawozdania/ — port 8765, HTTP basic auth.

Konfiguracja przez env (systemd EnvironmentFile=/etc/sprawozdania-server.env):
    SERVER_PORT (default 8765)
    SERVER_USER (default anbernic)
    SERVER_PASS — WYMAGANE
    SERVER_HOST (default 0.0.0.0 = LAN, można 127.0.0.1 = tylko SSH tunnel)

Listing katalogu = sortowalna tabela (Nazwa, Rozmiar, Modyfikacja, Utworzono).
Kliknięcie nagłówka kolumny sortuje (rozmiar i daty sortowane numerycznie).
"""
import os
import re
import html as _html
import base64
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from aiohttp import web

try:
    import markdown as _markdown      # renderowany podgląd .md (opcjonalny)
except Exception:
    _markdown = None


def _natkey(s: str):
    """Klucz sortowania naturalnego: 'plik_10' PO 'plik_9' (liczby jako liczby)."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', s)]

ROOT  = Path('/mnt/data/sprawozdania')
PORT  = int(os.environ.get('SERVER_PORT', '8765'))
HOST  = os.environ.get('SERVER_HOST', '0.0.0.0')
USER  = os.environ.get('SERVER_USER', 'anbernic')
PASSW = os.environ.get('SERVER_PASS', '')

ICONS = {'pdf': '📕', 'html': '🌐', 'md': '📝', 'jpg': '🖼', 'jpeg': '🖼',
         'png': '🖼', 'xlsx': '📊', 'xls': '📊', 'csv': '📊', 'txt': '📄',
         'docx': '📘', 'doc': '📘', 'svg': '🖼', 'json': '🔧'}

IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}

MD_STYLE = (
    'body{margin:0;background:#f7f7f9;color:#222;font-family:system-ui,sans-serif}'
    '.bar{position:sticky;top:0;display:flex;gap:.6em;align-items:center;'
    'padding:.5em 1em;background:#26292f;color:#ddd;flex-wrap:wrap;z-index:10}'
    '.bar a,.bar button{color:#7ab7ff;background:none;border:1px solid #3a3f47;'
    'border-radius:5px;padding:.25em .7em;text-decoration:none;cursor:pointer;'
    'font:inherit;font-size:.95em}'
    '.bar a:hover,.bar button:hover{background:#32363d}'
    '.bar .name{color:#eee;font-weight:600;word-break:break-all}'
    '.bar button.on{background:#1a5fb4;border-color:#1a5fb4;color:#fff}'
    '#rendered{max-width:900px;margin:0 auto;padding:1.5em 2em;background:#fff;'
    'min-height:92vh;box-shadow:0 0 12px rgba(0,0,0,.07)}'
    '#rendered img{max-width:100%}'
    '#rendered table{border-collapse:collapse;margin:.8em 0}'
    '#rendered th,#rendered td{border:1px solid #999;padding:.35em .6em}'
    '#rendered code{background:#eef1f6;padding:0 .25em;border-radius:3px}'
    '#rendered pre{background:#1b1d21;color:#e8e8e8;padding:1em;border-radius:6px;'
    'overflow-x:auto}'
    '#rendered pre code{background:none}'
    '#rendered blockquote{border-left:4px solid #bcd;margin-left:0;'
    'padding-left:1em;color:#555}'
    '#raw{display:none;max-width:1100px;margin:0 auto;padding:1em}'
    '#raw pre{background:#1b1d21;color:#d8e0ea;padding:1.2em;border-radius:8px;'
    'overflow-x:auto;font-size:.9em;line-height:1.45;white-space:pre-wrap;'
    "font-family:'Cascadia Mono',Consolas,monospace}"
)


def _fix_md_imgs(body: str, md_dir: Path) -> str:
    """Obrazki w .md bywaja zapisane gola nazwa, a fizycznie leza w raw/
    obok (md w processed/). Znajdz plik i przepisz src na sciezke wzgledna
    dzialajaca z URL-a strony podgladu."""
    def repl(m):
        src = m.group(2)
        if src.startswith(('http://', 'https://', 'data:', '/')):
            return m.group(0)
        name = Path(src).name
        cands = [md_dir / src, md_dir / name,
                 md_dir.parent / 'raw' / name, md_dir / 'raw' / name,
                 md_dir.parent / 'exports' / name,
                 md_dir.parent / 'processed' / name]
        for c in cands:
            try:
                cr = c.resolve()
            except Exception:
                continue
            if cr.exists() and (ROOT in cr.parents):
                rel = os.path.relpath(cr, md_dir).replace(os.sep, '/')
                return m.group(1) + quote(rel) + m.group(3)
        return m.group(0)
    return re.sub(r'(<img[^>]*?src=")([^"]+)(")', repl, body)


def render_md_page(target: Path) -> str:
    """Podgląd .md: zakładki Render (markdown+MathJax) / Kod (surowe źródło).
    Względne ścieżki obrazków działają — strona żyje w katalogu pliku."""
    src = target.read_text(encoding='utf-8', errors='replace')
    if _markdown is not None:
        body = _markdown.markdown(
            src, extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists'])
        body = _fix_md_imgs(body, target.parent)
    else:
        body = '<p><i>(brak biblioteki python-markdown — dostępny tylko Kod)</i></p>'
    raw = _html.escape(src)
    q = quote(target.name)
    # nawigacja po plikach .md w tym katalogu (jak w viewerze zdjec)
    sibs = sorted((x.name for x in target.parent.iterdir()
                   if x.is_file() and x.suffix.lower() == '.md'
                   and not x.name.startswith('.')), key=_natkey)
    idx = sibs.index(target.name) if target.name in sibs else 0
    prv = quote(sibs[idx - 1]) + '?view=1' if idx > 0 else None
    nxt = quote(sibs[idx + 1]) + '?view=1' if idx < len(sibs) - 1 else None
    a_prev = (f'<a href="{prv}" id="prev">← poprzedni</a>' if prv
              else '<a style="opacity:.3;pointer-events:none">← poprzedni</a>')
    a_next = (f'<a href="{nxt}" id="next">następny →</a>' if nxt
              else '<a style="opacity:.3;pointer-events:none">następny →</a>')
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{target.name}</title><style>{MD_STYLE}</style>'
        '<script>window.MathJax={tex:{inlineMath:[["$","$"]],'
        'displayMath:[["$$","$$"]]}};</script>'
        '<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/'
        'tex-mml-chtml.js"></script>'
        f'<div class="bar"><a href="./">📁 folder</a>{a_prev}{a_next}'
        f'<button id="bren" class="on">Render</button>'
        f'<button id="braw">Kod</button>'
        f'<span class="name">{target.name}</span>'
        f'<span style="color:#888">{idx + 1} / {len(sibs)}</span>'
        f'<a href="{q}?dl=1">⬇ pobierz</a></div>'
        f'<div id="rendered">{body}</div>'
        f'<div id="raw"><pre>{raw}</pre></div>'
        '<script>(function(){'
        'const r=document.getElementById("rendered"),'
        'w=document.getElementById("raw"),'
        'br=document.getElementById("bren"),bw=document.getElementById("braw");'
        'function show(ren){r.style.display=ren?"block":"none";'
        'w.style.display=ren?"none":"block";'
        'br.classList.toggle("on",ren);bw.classList.toggle("on",!ren);}'
        'br.onclick=()=>show(true);bw.onclick=()=>show(false);'
        'document.addEventListener("keydown",e=>{'
        'if(e.key==="ArrowLeft"){const a=document.getElementById("prev");'
        'if(a)location=a.href}'
        'if(e.key==="ArrowRight"){const a=document.getElementById("next");'
        'if(a)location=a.href}});'
        '})();</script>')

STYLE = (
    'body{font-family:system-ui,sans-serif;max-width:1000px;margin:1.2em auto;'
    'padding:0 1em;background:#f7f7f9;color:#222}'
    'h2{color:#333;font-size:1.1em;word-break:break-all}'
    'table{width:100%;border-collapse:collapse;background:#fff;border-radius:6px;'
    'overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}'
    'th,td{text-align:left;padding:.5em .7em;border-bottom:1px solid #eee;'
    'font-size:.92em;white-space:nowrap}'
    'th{background:#eef2f7;cursor:pointer;user-select:none}'
    'th:hover{background:#e0e8f0}th::after{content:" \\2195";color:#aaa;font-size:.8em}'
    'td:first-child,th:first-child{white-space:normal;word-break:break-all}'
    'tr:hover td{background:#f3f7fc}'
    'a{text-decoration:none;color:#0066cc}a:hover{text-decoration:underline}'
    '.dir{font-weight:600}'
    'td:nth-child(2),th:nth-child(2){text-align:right;color:#555}'
    '.muted{color:#999;font-size:.85em;margin:.6em 0}'
    'h2 a{color:#0066cc}h2 .sep{color:#bbb;font-weight:400}'
    '.dl{margin-right:.45em;text-decoration:none;opacity:.55}'
    '.dl:hover{opacity:1;text-decoration:none}'
    '.crumb{position:relative;display:inline-block}'
    '.crumb>.dd{display:none;position:absolute;top:100%;left:0;background:#fff;'
    'border:1px solid #c5cdd8;border-radius:6px;box-shadow:0 4px 14px rgba(0,0,0,.18);'
    'z-index:60;max-height:320px;overflow-y:auto;min-width:200px;padding:.25em 0}'
    '.crumb:hover>.dd{display:block}'
    '.crumb>.dd a{display:block;padding:.3em .9em;white-space:nowrap;font-size:.85em}'
    '.crumb>.dd a:hover{background:#eef2f7}'
    '.crumb>.dd a.cur{font-weight:700;color:#1a5fb4}'
    '#dropov{position:fixed;inset:0;display:flex;align-items:center;'
    'justify-content:center;background:rgba(15,50,110,.88);color:#fff;'
    'font-size:1.5em;z-index:50;visibility:hidden;pointer-events:none;'
    'text-align:center;padding:1em}'
    '#dropov.on{visibility:visible}'
)

# Drag & drop upload — upuszczenie plików na listing wgrywa je do bieżącego
# katalogu (POST multipart); tabela odświeży się sama (auto-refresh).
DROP_JS = (
    '<script>(function(){'
    'const ov=document.createElement("div");ov.id="dropov";'
    'ov.textContent="⬆ Upuść pliki, aby wgrać do tego katalogu";'
    'document.body.appendChild(ov);let d=0;'
    'window.addEventListener("dragenter",e=>{e.preventDefault();d++;ov.classList.add("on");});'
    'window.addEventListener("dragleave",e=>{e.preventDefault();d--;'
    'if(d<=0){d=0;ov.classList.remove("on");}});'
    'window.addEventListener("dragover",e=>e.preventDefault());'
    'window.addEventListener("drop",async e=>{e.preventDefault();d=0;'
    'const fs=[...e.dataTransfer.files];'
    'if(!fs.length){ov.classList.remove("on");return;}'
    'ov.textContent="⬆ Wgrywam "+fs.length+" plik(ów)…";'
    'const fd=new FormData();fs.forEach(f=>fd.append("file",f,f.name));'
    'try{const r=await fetch(location.pathname,{method:"POST",body:fd});'
    'const j=await r.json();'
    'ov.textContent=r.ok?("✓ Wgrano: "+(j.saved||[]).join(", ")):"✗ Błąd wgrywania";'
    '}catch(err){ov.textContent="✗ Błąd wgrywania";}'
    'setTimeout(()=>{ov.classList.remove("on");'
    'ov.textContent="⬆ Upuść pliki, aby wgrać do tego katalogu";},2500);'
    '});})();</script>'
)

VIEWER_STYLE = (
    'body{margin:0;background:#1b1d21;color:#ddd;font-family:system-ui,sans-serif;'
    'display:flex;flex-direction:column;min-height:100vh}'
    '.bar{display:flex;align-items:center;gap:1em;padding:.55em 1em;background:#26292f;'
    'font-size:.95em;flex-wrap:wrap}'
    '.bar a{color:#7ab7ff;text-decoration:none;padding:.25em .7em;border:1px solid #3a3f47;'
    'border-radius:5px;white-space:nowrap}'
    '.bar a:hover{background:#32363d}'
    '.bar .name{color:#eee;font-weight:600;word-break:break-all}'
    '.bar .cnt{color:#888}'
    '.stage{flex:1;display:flex;align-items:center;justify-content:center;'
    'padding:.8em;overflow:hidden;cursor:grab}'
    '.stage.drag{cursor:grabbing}'
    '.stage img{max-width:100%;max-height:calc(100vh - 5em);'
    'box-shadow:0 2px 14px rgba(0,0,0,.5);transform-origin:center center;'
    'user-select:none;-webkit-user-drag:none}'
    '.nav-off{opacity:.3;pointer-events:none}'
)

# Zoom: kółko / CTRL+kółko wokół kursora, drag = pan, dwuklik = fit/100%,
# klawisze +/-/0; % powiększenia widoczny na pasku (#zl).
VIEWER_JS = (
    '<script>(function(){'
    'const st=document.querySelector(".stage"),im=st.querySelector("img"),'
    'zl=document.getElementById("zl");'
    'let s=1,tx=0,ty=0;'
    'function ap(){im.style.transform=`translate(${tx}px,${ty}px) scale(${s})`;'
    'zl.textContent=Math.round(s*100)+"%";}'
    'function clamp(){s=Math.min(Math.max(s,0.2),20);}'
    'st.addEventListener("wheel",e=>{e.preventDefault();'
    'const r=im.getBoundingClientRect(),'
    'cx=e.clientX-(r.left+r.width/2),cy=e.clientY-(r.top+r.height/2),'
    'k=e.deltaY<0?1.2:1/1.2,o=s;s*=k;clamp();const f=s/o;'
    'tx=tx*f - cx*(f-1);ty=ty*f - cy*(f-1);ap();},{passive:false});'
    'let dr=null;'
    'st.addEventListener("mousedown",e=>{dr={x:e.clientX-tx,y:e.clientY-ty};'
    'st.classList.add("drag");e.preventDefault();});'
    'window.addEventListener("mousemove",e=>{if(!dr)return;'
    'tx=e.clientX-dr.x;ty=e.clientY-dr.y;ap();});'
    'window.addEventListener("mouseup",()=>{dr=null;st.classList.remove("drag");});'
    'function fit(){s=1;tx=ty=0;ap();}'
    'function nat(){s=im.naturalWidth&&im.clientWidth?im.naturalWidth/im.clientWidth:1;'
    'clamp();tx=ty=0;ap();}'
    # dwuklik: powiększone/przesunięte -> dopasuj do okna; dopasowane -> 1:1
    'st.addEventListener("dblclick",()=>{if(s!==1||tx||ty){fit();}else{nat();}});'
    'const fb=document.getElementById("fitb");if(fb)fb.onclick=e=>{e.preventDefault();fit();};'
    'const nb=document.getElementById("natb");if(nb)nb.onclick=e=>{e.preventDefault();nat();};'
    'document.addEventListener("keydown",e=>{'
    'if(e.key==="+"||e.key==="="){s*=1.2;clamp();ap();}'
    'if(e.key==="-"){s/=1.2;clamp();ap();}'
    'if(e.key==="0"){fit();}'
    'if(e.key==="1"){nat();}});'
    'ap();})();</script>'
)

# Sortowanie zapamiętywane w localStorage (przeżywa odświeżenie i nawigację);
# wiersz ".." (class="up") zawsze przypięty na górze.
SORT_JS = (
    '<script>(function(){'
    'const ths=[...document.querySelectorAll("th")];'
    'const tb=document.querySelector("table").tBodies[0];'
    # sortowanie NATURALNE tekstów: liczby w nazwach porównywane numerycznie
    'function nat(s){return s.split(/(\\d+)/).map('
    'p=>/^\\d+$/.test(p)?p.padStart(14,"0"):p.toLowerCase()).join("\\u0001");}'
    'function srt(i,asc){'
    'const up=tb.querySelector("tr.up");'
    '[...tb.rows].filter(r=>!r.classList.contains("up")).sort((a,b)=>{'
    'const dx=a.cells[i].dataset.sort,dy=b.cells[i].dataset.sort;'
    'let x=dx!==undefined?parseFloat(dx):nat(a.cells[i].textContent.trim());'
    'let y=dy!==undefined?parseFloat(dy):nat(b.cells[i].textContent.trim());'
    'return (x>y?1:x<y?-1:0)*(asc?1:-1);'
    '}).forEach(r=>tb.appendChild(r));'
    'if(up)tb.prepend(up);}'
    'ths.forEach((th,i)=>{th.onclick=()=>{'
    'const asc=th._a=!th._a;srt(i,asc);'
    'try{localStorage.setItem("dirSort",JSON.stringify({i:i,asc:asc}))}catch(e){}'
    '};});'
    'window._applySaved=function(){try{const s=JSON.parse(localStorage.getItem("dirSort"));'
    'if(s&&ths[s.i]!==undefined){ths[s.i]._a=s.asc;srt(s.i,s.asc);}}catch(e){}};'
    'window._applySaved();'
    # AUTO-ODŚWIEŻANIE: co 4 s pobierz w tle tę samą stronę, porównaj <tbody>
    # serwerowe z poprzednim pobraniem; przy zmianie podmień tabelę i licznik,
    # po czym przywróć zapamiętane sortowanie. Nowe pliki (np. świeży DOCX)
    # pojawiają się bez ręcznego odświeżania.
    '(function(){let sig="";'
    'async function tick(){try{'
    'const r=await fetch(location.pathname+location.search,{cache:"no-store"});'
    'if(!r.ok)return;'
    'const doc=new DOMParser().parseFromString(await r.text(),"text/html");'
    'const nb=doc.querySelector("tbody");if(!nb)return;'
    'const ns=nb.innerHTML;'
    'if(sig&&ns!==sig){'
    'document.querySelector("tbody").innerHTML=ns;'
    'const m=doc.querySelector("p.muted"),lm=document.querySelector("p.muted");'
    'if(m&&lm)lm.innerHTML=m.innerHTML;'
    'window._applySaved();}'
    'sig=ns;}catch(e){}}'
    'setInterval(tick,4000);})();'
    '})();</script>'
)


@web.middleware
async def auth(request, handler):
    # Pusty SERVER_PASS = brak autoryzacji (open access — tylko dla prywatnych sieci!)
    if not PASSW:
        return await handler(request)
    h = request.headers.get('Authorization', '')
    if not h.startswith('Basic '):
        return web.Response(status=401, headers={'WWW-Authenticate': 'Basic realm="Anbernic"'})
    try:
        u, p = base64.b64decode(h[6:]).decode().split(':', 1)
    except Exception:
        return web.Response(status=401, headers={'WWW-Authenticate': 'Basic realm="Anbernic"'})
    if u != USER or p != PASSW:
        return web.Response(status=401, text='Niepoprawne dane',
                            headers={'WWW-Authenticate': 'Basic realm="Anbernic"'})
    return await handler(request)


def _fmt_size(s):
    if s >= 1 << 30:
        return f'{s/(1<<30):.1f} GB'
    if s >= 1 << 20:
        return f'{s>>20} MB'
    if s >= 1024:
        return f'{s>>10} KB'
    return f'{s} B'


def _fmt_time(ts):
    try:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return '—'


async def serve(request):
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    # path traversal guard
    if ROOT not in target.parents and target != ROOT:
        return web.Response(status=403)
    if not target.exists():
        return web.Response(status=404, text=f'Not found: {raw}')

    if target.is_dir():
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), _natkey(p.name)))
        rel = target.relative_to(ROOT)
        rel_url = f'/{rel}/' if str(rel) != '.' else '/'

        # breadcrumb — każdy segment klikalny; najechanie rozwija listę
        # folderów-RODZEŃSTWA z danego poziomu (skok w inną gałąź drzewa)
        crumbs = ['<a href="/" class="dir">📁 root</a>']
        acc = ''
        if str(rel) != '.':
            parent = ROOT
            for seg in str(rel).replace('\\', '/').split('/'):
                parent_url = quote(acc)          # ścieżka rodzica ('' dla 1. poziomu)
                acc += '/' + seg
                try:
                    sibs = sorted((d.name for d in parent.iterdir()
                                   if d.is_dir() and not d.name.startswith('.')),
                                  key=_natkey)
                except Exception:
                    sibs = []
                dd_items = []
                for s in sibs:
                    cls = ' class="cur"' if s == seg else ''
                    dd_items.append(f'<a href="{parent_url}/{quote(s)}/"{cls}>📁 {s}</a>')
                dd = (f'<div class="dd">{"".join(dd_items)}</div>') if dd_items else ''
                crumbs.append(f'<span class="crumb">'
                              f'<a href="{quote(acc)}/">{seg}</a>{dd}</span>')
                parent = parent / seg
        breadcrumb = ' <span class="sep">/</span> '.join(crumbs) + ' <span class="sep">/</span>'

        rows = []
        n = 0
        if target != ROOT:
            rows.append('<tr class="up"><td><a href="../" class="dir">📁 ..</a></td>'
                        '<td data-sort="-2">—</td><td data-sort="0"></td>'
                        '<td data-sort="0"></td></tr>')
        for item in items:
            if item.name.endswith('.meta.json') or item.name.startswith('.'):
                continue
            try:
                st = item.stat()
                bt = getattr(st, 'st_birthtime', st.st_ctime)   # crtime jeśli dostępny, inaczej ctime
                mt_s, bt_s = _fmt_time(st.st_mtime), _fmt_time(bt)
                if item.is_dir():
                    rows.append(
                        f'<tr><td><a href="{item.name}/" class="dir">📁 {item.name}/</a></td>'
                        f'<td data-sort="-1">—</td>'
                        f'<td data-sort="{st.st_mtime:.0f}">{mt_s}</td>'
                        f'<td data-sort="{bt:.0f}">{bt_s}</td></tr>')
                else:
                    s = st.st_size
                    icon = ICONS.get(item.suffix.lower().lstrip('.'), '📄')
                    q = quote(item.name)
                    # zdjęcia → przeglądarka z nawigacją; reszta → plik wprost
                    ext = item.suffix.lower()
                    href = f'{q}?view=1' if (ext in IMG_EXT or ext == '.md') else q
                    rows.append(
                        f'<tr><td><a href="{q}?dl=1" class="dl" title="Pobierz">⬇</a>'
                        f'<a href="{href}">{icon} {item.name}</a></td>'
                        f'<td data-sort="{s}">{_fmt_size(s)}</td>'
                        f'<td data-sort="{st.st_mtime:.0f}">{mt_s}</td>'
                        f'<td data-sort="{bt:.0f}">{bt_s}</td></tr>')
                n += 1
            except Exception:
                pass

        html = [
            '<!doctype html><meta charset=utf-8>',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            f'<title>{rel_url}</title>',
            f'<style>{STYLE}</style>',
            f'<h2>{breadcrumb}</h2>',
            f'<p class="muted">{n} pozycji · kliknij nagłówek aby sortować · ⟳ auto-odświeżanie</p>',
            '<table><thead><tr>'
            '<th>Nazwa</th><th>Rozmiar</th><th>Modyfikacja</th><th>Utworzono</th>'
            '</tr></thead><tbody>',
            *rows,
            '</tbody></table>',
            SORT_JS,
            DROP_JS,
        ]
        return web.Response(text='\n'.join(html), content_type='text/html')

    # plik — ?dl=1 wymusza pobieranie, ?view=1 dla zdjęć otwiera przeglądarkę
    return await _serve_file(request, target)


async def upload(request):
    """POST multipart na katalog = wgranie plików (drag&drop z przeglądarki).
    Duplikaty nazw dostają sufiks z timestampem (jak w bocie) — nic nie nadpisujemy."""
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents and target != ROOT:
        return web.Response(status=403)
    if not target.is_dir():
        return web.Response(status=400, text='Cel nie jest katalogiem')
    saved = []
    reader = await request.multipart()
    async for part in reader:
        if part.name != 'file' or not part.filename:
            continue
        name = Path(part.filename).name
        if not name or name.startswith('.'):
            continue
        dest = target / name
        if dest.exists():
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            dest = target / f'{dest.stem}_{ts}{dest.suffix}'
        with open(dest, 'wb') as f:
            while True:
                chunk = await part.read_chunk(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        saved.append(dest.name)
    return web.json_response({'saved': saved})


async def _serve_file(request, target):
    if 'dl' in request.query:
        return web.FileResponse(target, headers={
            'Cache-Control': 'no-cache',
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(target.name)}"})

    if 'view' in request.query and target.suffix.lower() == '.md':
        return web.Response(text=render_md_page(target), content_type='text/html')

    if 'view' in request.query and target.suffix.lower() in IMG_EXT:
        siblings = sorted(
            (p for p in target.parent.iterdir()
             if p.is_file() and p.suffix.lower() in IMG_EXT
             and not p.name.startswith('.')),
            key=lambda p: p.name.lower())
        names = [p.name for p in siblings]
        idx = names.index(target.name) if target.name in names else 0
        prv = quote(names[idx-1]) + '?view=1' if idx > 0 else None
        nxt = quote(names[idx+1]) + '?view=1' if idx < len(names)-1 else None
        a_prev = (f'<a href="{prv}" id="prev">← poprzednie</a>' if prv
                  else '<a class="nav-off">← poprzednie</a>')
        a_next = (f'<a href="{nxt}" id="next">następne →</a>' if nxt
                  else '<a class="nav-off">następne →</a>')
        html = (
            '<!doctype html><meta charset=utf-8>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{target.name}</title><style>{VIEWER_STYLE}</style>'
            f'<div class="bar"><a href="./">📁 folder</a>{a_prev}{a_next}'
            f'<span class="name">{target.name}</span>'
            f'<span class="cnt">{idx+1} / {len(names)}</span>'
            f'<span class="cnt" id="zl">100%</span>'
            f'<a href="#" id="fitb" title="Dopasuj do okna (dwuklik / klawisz 0)">⊡ dopasuj</a>'
            f'<a href="#" id="natb" title="Rozmiar naturalny (klawisz 1)">1:1</a>'
            f'<a href="{quote(target.name)}?dl=1">⬇ pobierz</a></div>'
            f'<div class="stage"><img src="{quote(target.name)}" alt="{target.name}"></div>'
            '<script>document.addEventListener("keydown",e=>{'
            'if(e.key==="ArrowLeft"){const a=document.getElementById("prev");if(a)location=a.href}'
            'if(e.key==="ArrowRight"){const a=document.getElementById("next");if(a)location=a.href}'
            '});</script>' + VIEWER_JS)
        return web.Response(text=html, content_type='text/html')

    return web.FileResponse(target, headers={'Cache-Control': 'no-cache'})


def main():
    # client_max_size: limit żądania POST (upload) — domyślny 1 MB to za mało
    app = web.Application(middlewares=[auth], client_max_size=512 * 1024 ** 2)
    app.router.add_get('/{path:.*}', serve)
    app.router.add_post('/{path:.*}', upload)
    auth_info = f'user={USER}' if PASSW else 'OPEN (no auth)'
    print(f'sprawozdania-server: http://{HOST}:{PORT}/ — {auth_info}', flush=True)
    web.run_app(app, host=HOST, port=PORT, access_log=None)


if __name__ == '__main__':
    main()
