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
import asyncio
import hashlib
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
AUDIO_EXT = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.opus'}
AUDIO_MIME = {'.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.wav': 'audio/wav',
              '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.opus': 'audio/opus'}

AUDIO_STYLE = (
    'body{margin:0;background:#14161c;color:#cfd6df;font-family:system-ui,sans-serif;'
    'display:flex;flex-direction:column;min-height:100vh}'
    '.bar{display:flex;gap:.6em;align-items:center;padding:.55em 1em;'
    'background:#1d2027;flex-wrap:wrap}'
    '.bar a,.bar button{color:#7ab7ff;background:none;border:1px solid #343a45;'
    'border-radius:5px;padding:.25em .7em;text-decoration:none;cursor:pointer;'
    'font:inherit;font-size:.95em}'
    '.bar a:hover,.bar button:hover{background:#272b34}'
    '.bar button.on{background:#1a5fb4;border-color:#1a5fb4;color:#fff}'
    '.wrap{flex:1;display:flex;flex-direction:column;align-items:center;'
    'justify-content:center;gap:1.2em;padding:1em}'
    '.tname{font-size:1.15em;color:#eee;word-break:break-all;text-align:center;'
    'max-width:90%}'
    'audio{width:min(680px,92vw)}'
    '.spd{display:flex;gap:.4em;align-items:center;color:#8a93a0;font-size:.9em}'
)


def render_audio_page(target: Path) -> str:
    """Odtwarzacz audio (?view=1): natywny <audio>, poprzedni/następny
    w katalogu, regulacja tempa (lektor!), autoodtwarzanie."""
    sibs = sorted((x.name for x in target.parent.iterdir()
                   if x.is_file() and x.suffix.lower() in AUDIO_EXT
                   and not x.name.startswith('.')), key=_natkey)
    idx = sibs.index(target.name) if target.name in sibs else 0
    prv = quote(sibs[idx - 1]) + '?view=1' if idx > 0 else None
    nxt = quote(sibs[idx + 1]) + '?view=1' if idx < len(sibs) - 1 else None
    a_prev = (f'<a href="{prv}" id="prev">← poprzedni</a>' if prv
              else '<a style="opacity:.3;pointer-events:none">← poprzedni</a>')
    a_next = (f'<a href="{nxt}" id="next">następny →</a>' if nxt
              else '<a style="opacity:.3;pointer-events:none">następny →</a>')
    q = quote(target.name)
    spd_btns = ''.join(
        f'<button class="sp" data-s="{s}">{s}×</button>'
        for s in ('0.75', '1', '1.25', '1.5', '2'))
    return (
        '<!doctype html><meta charset=utf-8>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>♪ {target.name}</title><style>{AUDIO_STYLE}</style>'
        f'<div class="bar"><a href="./">📁 folder</a>{a_prev}{a_next}'
        f'<span style="color:#8a93a0">{idx + 1} / {len(sibs)}</span>'
        f'<a href="{q}?dl=1">⬇ pobierz</a></div>'
        f'<div class="wrap"><div class="tname">🎵 {target.name}</div>'
        f'<audio id="au" controls autoplay src="{q}"></audio>'
        f'<div class="spd">tempo: {spd_btns}</div></div>'
        '<script>(function(){'
        'const au=document.getElementById("au");'
        'const sp=[...document.querySelectorAll(".sp")];'
        'function setr(r){au.playbackRate=parseFloat(r);'
        'sp.forEach(b=>b.classList.toggle("on",b.dataset.s===r));'
        'try{localStorage.setItem("audioRate",r)}catch(e){}}'
        'sp.forEach(b=>b.onclick=()=>setr(b.dataset.s));'
        'setr(localStorage.getItem("audioRate")||"1");'
        # koniec utworu → automatycznie następny (playlista po katalogu)
        'au.onended=()=>{const n=document.getElementById("next");if(n)location=n.href};'
        'document.addEventListener("keydown",e=>{'
        'if(e.key==="ArrowLeft"){const a=document.getElementById("prev");if(a)location=a.href}'
        'if(e.key==="ArrowRight"){const a=document.getElementById("next");if(a)location=a.href}'
        'if(e.key===" "){e.preventDefault();au.paused?au.play():au.pause()}});'
        '})();</script>')

DOCX_CACHE = Path('/mnt/data/.cache/docx-preview')
_LO_LOCK = asyncio.Lock()   # jedna konwersja naraz (A53)


async def docx_to_pdf(target: Path) -> Path | None:
    """Podglad .docx: konwersja LibreOffice -> PDF, cache per (sciezka, mtime).
    Pierwsze otwarcie ~8-12 s, kolejne natychmiast."""
    DOCX_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(str(target).encode()).hexdigest()[:16]
    cached = DOCX_CACHE / f'{key}_{int(target.stat().st_mtime)}.pdf'
    if cached.exists():
        return cached
    # sprzatnij stare wersje tego pliku
    for old_f in DOCX_CACHE.glob(f'{key}_*.pdf'):
        try:
            old_f.unlink()
        except Exception:
            pass
    async with _LO_LOCK:
        if cached.exists():
            return cached
        proc = await asyncio.create_subprocess_exec(
            'soffice', '--headless',
            '-env:UserInstallation=file:///tmp/lo_preview_profile',
            '--convert-to', 'pdf', '--outdir', str(DOCX_CACHE), str(target),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(proc.wait(), timeout=90)
        except asyncio.TimeoutError:
            proc.kill()
            return None
    produced = DOCX_CACHE / (target.stem + '.pdf')
    if produced.exists():
        produced.rename(cached)
        return cached
    return None


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
        # AUTO-ODSWIEZANIE: poll mtime co 3 s; przy zmianie pobierz strone,
        # podmien tresc obu widokow i przelicz wzory MathJax. Zakladka
        # i pozycja przewiniecia zostaja.
        'let _mt=0;'
        'async function chk(){try{'
        'const j=await(await fetch(location.pathname+"?mt=1",'
        '{cache:"no-store"})).json();'
        'if(_mt&&j.mt!==_mt){'
        'const doc=new DOMParser().parseFromString('
        'await(await fetch(location.pathname+"?view=1",'
        '{cache:"no-store"})).text(),"text/html");'
        'const nr=doc.getElementById("rendered"),nw=doc.getElementById("raw");'
        'if(nr)r.innerHTML=nr.innerHTML;'
        'if(nw)w.innerHTML=nw.innerHTML;'
        'if(window.MathJax&&MathJax.typesetPromise)MathJax.typesetPromise([r]);}'
        '_mt=j.mt;}catch(e){}}'
        'setInterval(chk,3000);chk();'
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
    '.del:hover{filter:drop-shadow(0 0 2px #c00)}'
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

# Akcje plikowe: 🗑 usuń (→.kosz), ✎ zmień nazwę (prompt → POST ?rename=),
# ⧉ kopiuj nazwę do schowka.
DEL_JS = (
    '<script>'
    # modal wyboru formatu — PRZYCISKI (nie wpisywanie rozszerzenia)
    'function pickFmt(name){return new Promise(res=>{'
    'const ov=document.createElement("div");'
    'ov.style.cssText="position:fixed;inset:0;background:rgba(0,0,0,.5);'
    'display:flex;align-items:center;justify-content:center;z-index:99";'
    'ov.innerHTML=\'<div style="background:#fff;border-radius:10px;'
    'padding:1.1em 1.4em;max-width:92vw;box-shadow:0 8px 30px rgba(0,0,0,.35)">'
    '<div style="font-weight:600;margin-bottom:.35em;word-break:break-all">'
    '🔊 Lektor: \'+name+\'</div>'
    '<div style="color:#556;font-size:.9em;margin-bottom:.9em">'
    'Wybierz format audio (generacja dłuższych dokumentów może potrwać '
    'kilkanaście minut):</div>'
    '<div style="display:flex;gap:.5em;flex-wrap:wrap">'
    '<button data-f="">⚙ wg ustawień</button>'
    '<button data-f="mp3">MP3</button>'
    '<button data-f="flac">FLAC</button>'
    '<button data-f="wav">WAV</button>'
    '<button data-f="__x" style="margin-left:auto">Anuluj</button>'
    '</div></div>\';'
    'ov.querySelectorAll("button").forEach(b=>{'
    'b.style.cssText+=";padding:.5em 1em;border:1px solid #b8c0cc;'
    'border-radius:6px;background:#f2f5f9;cursor:pointer;font-size:1em";'
    'b.onmouseenter=()=>b.style.background="#dde6f2";'
    'b.onmouseleave=()=>b.style.background="#f2f5f9";'
    'b.onclick=()=>{ov.remove();res(b.dataset.f==="__x"?null:b.dataset.f);};});'
    'ov.onclick=e=>{if(e.target===ov){ov.remove();res(null);}};'
    'document.body.appendChild(ov);});}'
    'document.addEventListener("click",async e=>{'
    'const a=e.target.closest("a.del,a.ren,a.cpy,a.lek");if(!a)return;'
    'e.preventDefault();'
    'const n=decodeURIComponent(a.dataset.n);'
    'if(a.classList.contains("lek")){'
    'const fm=await pickFmt(n);'
    'if(fm===null)return;'
    'a.textContent="⏳";'
    'try{'
    'let u=a.dataset.n+"?lektor=1"+(fm?"&fmt="+fm:"");'
    'let r=await fetch(u,{method:"POST"});'
    'let j=await r.json().catch(()=>({}));'
    'if(j.status==="busy"){'
    'a.textContent="🔊";'
    'if(!confirm("Lektor jest teraz zajęty — czyta inny dokument'
    '(np. na polecenie z Discorda)."+(j.pending?"\\nW kolejce czeka: "'
    '+j.pending+" plik(ów).":"")+"\\n\\nDopisać ten plik do kolejki? '
    'Audio wygeneruje się automatycznie, gdy przyjdzie jego kolej."))return;'
    'a.textContent="⏳";'
    'r=await fetch(u+"&queue=1",{method:"POST"});'
    'j=await r.json().catch(()=>({}));}'
    'if(r.status===202){'
    'a.title=(j.status==="queued"?"W kolejce (poz. "+j.position+"): "'
    ':"Generuję: ")+(j.out||"");'
    'setTimeout(()=>a.textContent="🔊",2500);}'
    'else{alert("Lektor: "+(j.status||r.status));a.textContent="🔊";}'
    '}catch(err){alert("Błąd lektora");a.textContent="🔊";}return;}'
    'if(a.classList.contains("cpy")){'
    'try{await navigator.clipboard.writeText(n);'
    'a.textContent="✓";setTimeout(()=>a.textContent="⧉",900);}'
    'catch(err){prompt("Skopiuj nazwę:",n);}return;}'
    'if(a.classList.contains("ren")){'
    'const nn=prompt("Nowa nazwa:",n);'
    'if(!nn||nn===n)return;'
    'try{const r=await fetch(a.dataset.n+"?rename="+encodeURIComponent(nn),'
    '{method:"POST"});'
    'if(r.ok){location.reload();}'
    'else{alert("Błąd zmiany nazwy: "+await r.text());}'
    '}catch(err){alert("Błąd zmiany nazwy");}return;}'
    'if(!confirm("Usunąć \\""+n+"\\"?\\n(plik trafi do kosza .kosz)"))return;'
    'try{const r=await fetch(a.dataset.n,{method:"DELETE"});'
    'if(r.ok){a.closest("tr").remove();}'
    'else{alert("Błąd usuwania: HTTP "+r.status);}'
    '}catch(err){alert("Błąd usuwania");}'
    '});</script>'
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
    # widok/stan kolejki lektora (dostępny z dowolnej ścieżki)
    if 'lektorq' in request.query:
        return web.Response(text=LEKTORQ_PAGE, content_type='text/html')
    if 'lektorqj' in request.query:
        return web.json_response(_lektor_queue_json())

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
                    href = (f'{q}?view=1'
                            if (ext in IMG_EXT or ext in AUDIO_EXT
                                or ext in ('.md', '.docx', '.doc'))
                            else q)
                    lek = ('<a href="#" class="dl lek" data-n="' + q
                           + '" title="Lektor → audio">🔊</a>'
                           if ext in ('.md', '.docx', '.txt') else '')
                    rows.append(
                        f'<tr><td><a href="{q}?dl=1" class="dl" title="Pobierz">⬇</a>'
                        f'<a href="#" class="dl del" data-n="{q}" title="Usuń (do .kosz)">🗑</a>'
                        f'<a href="#" class="dl ren" data-n="{q}" title="Zmień nazwę">✎</a>'
                        f'<a href="#" class="dl cpy" data-n="{q}" title="Kopiuj nazwę">⧉</a>'
                        f'{lek}'
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
            f'<p class="muted">{n} pozycji · kliknij nagłówek aby sortować · '
            f'⟳ auto-odświeżanie · <a href="/?lektorq=1">🔊 kolejka lektora</a></p>',
            '<table><thead><tr>'
            '<th>Nazwa</th><th>Rozmiar</th><th>Modyfikacja</th><th>Utworzono</th>'
            '</tr></thead><tbody>',
            *rows,
            '</tbody></table>',
            SORT_JS,
            DROP_JS,
            DEL_JS,
        ]
        return web.Response(text='\n'.join(html), content_type='text/html')

    # plik — ?dl=1 wymusza pobieranie, ?view=1 dla zdjęć otwiera przeglądarkę
    return await _serve_file(request, target)


async def delete_item(request):
    """DELETE na pliku = przeniesienie do ROOT/.kosz/ (nic nie znika trwale).
    Katalogi: tylko puste (rmdir). .kosz ukryty w listingu (dotfile)."""
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents:          # ROOT samego nie ruszamy
        return web.Response(status=403)
    if not target.exists():
        return web.Response(status=404)
    if target.is_dir():
        if any(target.iterdir()):
            return web.Response(status=400, text='Katalog niepusty')
        target.rmdir()
        return web.json_response({'deleted': raw})
    trash = ROOT / '.kosz'
    trash.mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = trash / f'{ts}_{target.name}'
    target.rename(dest)
    return web.json_response({'deleted': raw, 'kosz': dest.name})


CZYTAJ_TTS = '/mnt/data/sprawozdania/EXPORT/czytaj_tts.py'
LEKTOR_CONF = '/mnt/data/sprawozdania/EXPORT/lektor-ustawienia.conf'
_LEKTOR_LOCK = None        # asyncio.Lock tworzony leniwie (jeden lektor naraz)
_LEKTOR_SEQ = 0
_LEKTOR_QUEUE: list = []   # rejestr zadań: id/out/src/fmt/state/cancelled/proc


def _ext_lektor_pids() -> set:
    """PID-y czytaj_tts.py uruchomione POZA serwerem (agent z Discorda,
    CLI) — pgrep minus nasze własne dzieci z kolejki."""
    import subprocess
    r = subprocess.run(['pgrep', '-f', 'czytaj_tts.py'],
                       capture_output=True, text=True)
    pids = {int(x) for x in r.stdout.split()} if r.returncode == 0 else set()
    # tylko realne interpretery Pythona — pgrep -f łapie też powłoki/wrappery,
    # których CMDLINE zawiera nazwę skryptu (np. sesję ssh agenta!)
    real = set()
    for p in pids:
        try:
            comm = Path(f'/proc/{p}/comm').read_text().strip()
        except OSError:
            continue
        if comm.startswith('python'):
            real.add(p)
    ours = {j['proc'].pid for j in _LEKTOR_QUEUE
            if j.get('proc') is not None and j['proc'].returncode is None}
    return real - ours


def _ext_lektor_running() -> bool:
    return bool(_ext_lektor_pids())


def _lektor_busy() -> bool:
    global _LEKTOR_LOCK
    return bool(_LEKTOR_QUEUE) or _ext_lektor_running() \
        or (_LEKTOR_LOCK is not None and _LEKTOR_LOCK.locked())


def _lektor_progress():
    """Postęp bieżącej generacji (pisze czytaj_tts.py; lektor jest jeden,
    więc plik globalny wystarcza). None = brak danych."""
    import json
    import time as _t
    try:
        p = Path('/tmp/lektor_progress.json')
        if _t.time() - p.stat().st_mtime > 300:   # stęchły = po crashu
            return None
        return json.loads(p.read_text())
    except Exception:
        return None


def _lektor_queue_json() -> dict:
    prog = _lektor_progress()
    jobs = []
    for j in _LEKTOR_QUEUE:
        if j['cancelled']:
            continue
        e = {'id': j['id'], 'out': Path(j['out']).name,
             'src': str(Path(j['src']).relative_to(ROOT))
             if str(j['src']).startswith(str(ROOT)) else Path(j['src']).name,
             'fmt': j['fmt'], 'state': j['state']}
        if j['state'] == 'running' and prog:
            e['pct'] = prog.get('pct', 0)
            e['chunk'] = f"{prog.get('chunk', 0)}/{prog.get('chunks', 0)}"
        jobs.append(e)
    ext = _ext_lektor_running()
    out = {'jobs': jobs, 'external': ext}
    if ext and prog and not any(j['state'] == 'running' for j in jobs):
        out['ext_pct'] = prog.get('pct', 0)
        out['ext_chunk'] = f"{prog.get('chunk', 0)}/{prog.get('chunks', 0)}"
    return out


LEKTORQ_PAGE = (
    '<!doctype html><meta charset=utf-8>'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Kolejka lektora</title><style>'
    # styl 1:1 z listingiem serwera plików (jasny motyw)
    'body{font-family:system-ui,sans-serif;max-width:1000px;margin:1.2em auto;'
    'padding:0 1em;background:#f7f7f9;color:#222}'
    'h2{color:#333;font-size:1.1em;word-break:break-all}'
    'h2 a{text-decoration:none}h2 a:hover{text-decoration:underline}'
    '.sep{color:#999}'
    '.muted{color:#888;font-size:.85em}'
    'table{width:100%;border-collapse:collapse;background:#fff;'
    'border-radius:6px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}'
    'th,td{text-align:left;padding:.5em .7em;border-bottom:1px solid #eee;'
    'font-size:.92em}'
    'th{background:#eef2f7;user-select:none}'
    'tr:hover td{background:#f4f7fb}'
    '.run{color:#1d7a36;font-weight:600}.que{color:#9a7b00}'
    '.ext{color:#888;font-style:italic}'
    '.x{color:#c00;cursor:pointer;text-decoration:none;font-weight:700;'
    'opacity:.55}.x:hover{opacity:1}'
    '.empty{text-align:center;color:#888;padding:2em}'
    '.pb{width:130px;height:13px;background:#e4e8ee;border-radius:7px;'
    'overflow:hidden;display:inline-block;vertical-align:middle;'
    'margin-right:.5em}'
    '.pb>i{display:block;height:100%;background:linear-gradient(90deg,'
    '#3584e4,#33a02c);transition:width .8s}'
    '.pct{font-size:.85em;color:#888}'
    '</style>'
    '<h2><a href="/" class="dir">📁 root</a> <span class="sep">/</span> '
    '🔊 Kolejka lektora</h2>'
    '<p class="muted">podgląd na żywo (co 3 s) · ✕ usuwa pozycję '
    '(trwająca generacja zostaje przerwana) · <span id="st"></span></p>'
    '<table><thead><tr><th>#</th><th>plik wynikowy</th><th>źródło</th>'
    '<th>format</th><th>stan</th><th></th></tr></thead>'
    '<tbody id="tb"></tbody></table>'
    '<script>'
    'async function load(){'
    'try{const j=await(await fetch("/?lektorqj=1",{cache:"no-store"})).json();'
    'const tb=document.getElementById("tb");let h="";let i=0;'
    'function bar(p,c){return p==null?"GENERUJE":'
    '"<span class=pb><i style=\'width:"+p+"%\'></i></span>'
    '<span class=pct>"+p+"% ("+(c||"")+")</span>";}'
    'if(j.external){h+="<tr><td>—</td><td colspan=3 class=ext>'
    'lektor uruchomiony poza serwerem (agent Discord / CLI)</td>'
    '<td class=run>"+bar(j.ext_pct,j.ext_chunk)+"</td>'
    '<td><a class=x data-i=ext href=#>✕</a></td></tr>";}'
    'for(const x of j.jobs){i++;'
    'h+="<tr><td>"+i+"</td><td>"+x.out+"</td><td style=\'color:#8a93a0\'>"'
    '+x.src+"</td><td>"+x.fmt+"</td><td class="'
    '+(x.state==="running"?"run":"que")+">"'
    '+(x.state==="running"?bar(x.pct,x.chunk):"czeka")+"</td>'
    '<td><a class=x data-i="+x.id+" href=#>✕</a></td></tr>";}'
    'if(!h)h="<tr><td colspan=6 class=empty>Kolejka pusta — lektor wolny</td></tr>";'
    'tb.innerHTML=h;'
    'document.getElementById("st").textContent='
    '"odświeżono "+new Date().toLocaleTimeString();'
    '}catch(e){}}'
    'document.addEventListener("click",async e=>{'
    'const a=e.target.closest("a.x");if(!a)return;e.preventDefault();'
    'if(!confirm("Usunąć tę pozycję z kolejki lektora?"+'
    '"\\n(trwająca generacja zostanie przerwana)"))return;'
    'await fetch("/?lektorqdel="+a.dataset.i,{method:"POST"});load();});'
    'load();setInterval(load,3000);'
    '</script>')


def _lektor_fmt() -> str:
    """Format z lektor-ustawienia.conf (mp3|wav|flac), domyślnie mp3."""
    try:
        for line in Path(LEKTOR_CONF).read_text().splitlines():
            line = line.split('#', 1)[0]
            if '=' in line:
                k, v = line.split('=', 1)
                if k.strip() == 'format' and v.strip() in ('mp3', 'wav', 'flac'):
                    return v.strip()
    except Exception:
        pass
    return 'mp3'


def _docx_to_txt(p: Path) -> Path:
    """Awaryjne źródło dla lektora: tekst wprost z DOCX (python-docx)."""
    import docx
    d = docx.Document(str(p))
    parts = [par.text for par in d.paragraphs]
    for tbl in d.tables:
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(' . '.join(cells))
    tmp = Path('/tmp') / (p.stem + '_lektor_src.txt')
    tmp.write_text('\n'.join(parts), encoding='utf-8')
    return tmp


async def lektor_item(request):
    """POST ?lektor=1 na .md/.docx/.txt — generacja audio w TLE
    (czytaj_tts.py). Wynik: <nazwa>_lektor.<fmt> obok pliku (fmt z conf);
    auto-odświeżanie listingu pokaże go po zakończeniu."""
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents or not target.is_file():
        return web.Response(status=403)
    ext = target.suffix.lower()
    if ext not in ('.md', '.docx', '.txt'):
        return web.Response(status=400, text='Lektor czyta .md/.docx/.txt')

    # źródło tekstu: .md wprost; .docx → bliźniaczy .md (ten sam stem,
    # ten katalog lub ../processed/) albo ekstrakcja tekstu z DOCX
    src = target
    if ext == '.docx':
        for cand in (target.parent / (target.stem + '.md'),
                     target.parent.parent / 'processed' / (target.stem + '.md')):
            if cand.exists():
                src = cand
                break
        else:
            try:
                src = _docx_to_txt(target)
            except Exception as e:
                return web.Response(status=500, text=f'Ekstrakcja DOCX: {e}')

    fmt = request.query.get('fmt', '').lower() or _lektor_fmt()
    if fmt not in ('mp3', 'wav', 'flac'):
        fmt = 'mp3'
    # konwencja sprawozdań: źródło w processed/ → audio do exports/ obok
    # (ta sama lokalizacja co lektor agenta z Discorda); inaczej obok pliku
    out_dir = target.parent
    if out_dir.name == 'processed' and (out_dir.parent / 'exports').is_dir():
        out_dir = out_dir.parent / 'exports'
    out = out_dir / f'{target.stem}_lektor.{fmt}'
    if any(j['out'] == str(out) and not j['cancelled'] for j in _LEKTOR_QUEUE):
        return web.json_response({'status': 'duplikat', 'out': out.name})

    # lektor zajęty (przeglądarka ALBO agent z Discorda) → bez flagi queue
    # zwracamy 'busy'; UI pyta usera o dopisanie do kolejki
    if _lektor_busy() and 'queue' not in request.query:
        return web.json_response(
            {'status': 'busy', 'pending': len(_LEKTOR_QUEUE)})

    global _LEKTOR_LOCK, _LEKTOR_SEQ
    if _LEKTOR_LOCK is None:
        _LEKTOR_LOCK = asyncio.Lock()
    _LEKTOR_SEQ += 1
    import time as _t
    job = {'id': _LEKTOR_SEQ, 'out': str(out), 'src': str(src), 'fmt': fmt,
           'state': 'queued', 'cancelled': False, 'proc': None,
           'started': _t.time()}
    queued = bool(_LEKTOR_QUEUE) or _ext_lektor_running()
    _LEKTOR_QUEUE.append(job)

    async def _run():
        try:
            async with _LEKTOR_LOCK:
                if job['cancelled']:
                    return
                # przepuść lektora odpalonego poza serwerem (agent/Discord)
                while _ext_lektor_running():
                    await asyncio.sleep(5)
                    if job['cancelled']:
                        return
                job['state'] = 'running'
                proc = await asyncio.create_subprocess_exec(
                    'python3', CZYTAJ_TTS, str(src), '-o', str(out),
                    '--format', fmt,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL)
                job['proc'] = proc
                await proc.wait()
                if job['cancelled']:
                    # przerwane — sprzątnij TYLKO pliki powstałe w trakcie
                    # TEGO zadania (mtime > start); starsze gotowe audio
                    # o innym rozszerzeniu zostaje (incydent: kasowało
                    # wcześniejszy _lektor.mp3 przy anulowaniu .wav)
                    for suf in ('.mp3', '.wav', '.flac'):
                        p = out.with_suffix(suf)
                        try:
                            if p.stat().st_mtime >= job['started']:
                                p.unlink()
                        except FileNotFoundError:
                            pass
        finally:
            try:
                _LEKTOR_QUEUE.remove(job)
            except ValueError:
                pass

    asyncio.ensure_future(_run())
    return web.json_response(
        {'status': 'queued' if queued else 'start',
         'out': out.name, 'position': len(_LEKTOR_QUEUE)}, status=202)


async def rename_item(request):
    """POST ?rename=<nowa-nazwa> na pliku/katalogu = zmiana nazwy (ten sam
    katalog, bez nadpisywania — 409 gdy cel istnieje)."""
    raw = request.match_info.get('path', '').strip('/')
    try:
        target = (ROOT / raw).resolve()
    except Exception:
        return web.Response(status=400)
    if ROOT not in target.parents:
        return web.Response(status=403)
    if not target.exists():
        return web.Response(status=404)
    new_name = Path(request.query.get('rename', '')).name.strip()
    if not new_name or new_name.startswith('.'):
        return web.Response(status=400, text='Nieprawidłowa nazwa')
    dest = target.parent / new_name
    if dest.exists():
        return web.Response(status=409, text='Plik o tej nazwie już istnieje')
    target.rename(dest)
    return web.json_response({'renamed': target.name, 'to': new_name})


async def lektor_cancel(request):
    """POST ?lektorqdel=<id|ext> — usunięcie pozycji z kolejki lektora;
    trwająca generacja zostaje ubita (i sprzątnięte częściowe pliki).
    'ext' = przerwij lektora uruchomionego poza serwerem (agent/CLI)."""
    raw_id = request.query.get('lektorqdel', '')
    if raw_id == 'ext':
        import signal
        killed = []
        for pid in _ext_lektor_pids():
            try:
                import os
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
        return web.json_response({'cancelled': 'ext', 'pids': killed})
    try:
        jid = int(raw_id)
    except ValueError:
        return web.Response(status=400)
    for j in _LEKTOR_QUEUE:
        if j['id'] == jid:
            j['cancelled'] = True
            if j['state'] == 'running' and j.get('proc') is not None:
                try:
                    j['proc'].kill()
                except ProcessLookupError:
                    pass
            else:
                try:
                    _LEKTOR_QUEUE.remove(j)
                except ValueError:
                    pass
            return web.json_response({'cancelled': jid})
    return web.Response(status=404)


async def upload(request):
    if 'lektorqdel' in request.query:
        return await lektor_cancel(request)
    if 'rename' in request.query:
        return await rename_item(request)
    if 'lektor' in request.query:
        return await lektor_item(request)
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

    if 'view' in request.query and target.suffix.lower() in AUDIO_EXT:
        return web.Response(text=render_audio_page(target),
                            content_type='text/html')

    # audio bez ?view: poprawny MIME (mimetypes nie zna .flac → pobieranie
    # zamiast odtwarzania w <audio>)
    if target.suffix.lower() in AUDIO_EXT and 'dl' not in request.query:
        return web.FileResponse(target, headers={
            'Content-Type': AUDIO_MIME[target.suffix.lower()]})

    if 'view' in request.query and target.suffix.lower() in ('.docx', '.doc'):
        pdf = await docx_to_pdf(target)
        if pdf is None:
            return web.Response(status=500, text='Konwersja DOCX nie powiodla sie')
        return web.FileResponse(pdf, headers={
            'Content-Type': 'application/pdf',
            'Content-Disposition': f"inline; filename*=UTF-8''{quote(target.stem)}.pdf",
            'Cache-Control': 'no-cache'})

    if 'mt' in request.query and target.suffix.lower() == '.md':
        return web.json_response({'mt': target.stat().st_mtime})

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
    app.router.add_delete('/{path:.*}', delete_item)
    auth_info = f'user={USER}' if PASSW else 'OPEN (no auth)'
    print(f'sprawozdania-server: http://{HOST}:{PORT}/ — {auth_info}', flush=True)
    web.run_app(app, host=HOST, port=PORT, access_log=None)


if __name__ == '__main__':
    main()
