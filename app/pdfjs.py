"""Podgląd PDF i DOCX rysowany lokalnym pdf.js (moduł „podglad_pdfjs”).

Przeglądarki telefonów nie pokazują PDF w <iframe>/<embed>; pdf.js (Apache-2.0,
kopia w app/static/pdfjs z LICENSE.txt, bez CDN) rysuje strony na <canvas>.
Przeniesione z sprawozdania-server.py (10.11.2026, „pdf.js lokalnie”).

Moduł nie zna serwera: dostaje katalog plików i gotowe fragmenty HTML.
"""
from pathlib import Path

# pliki serwowane pod /_pdfjs/<nazwa> — wyłącznie te (biała lista)
PLIKI = {'pdf.min.mjs': 'text/javascript',
         'pdf.worker.min.mjs': 'text/javascript',
         'LICENSE.txt': 'text/plain'}
PREFIKS = '/_pdfjs/'


def plik(katalog: Path, nazwa: str):
    """(ścieżka, typ MIME) pliku pdf.js z białej listy albo None."""
    typ = PLIKI.get(nazwa)
    if typ is None:
        return None
    p = Path(katalog) / nazwa
    if not p.is_file():
        return None
    return p, typ + '; charset=utf-8'


SKRYPT = r"""<script type="module">
import * as pdfjsLib from "/_pdfjs/pdf.min.mjs";
pdfjsLib.GlobalWorkerOptions.workerSrc="/_pdfjs/pdf.worker.min.mjs";
const Q=__Q__,SRC=__SRC__;let mt=__MT__;
const box=document.getElementById("pages"),cvt=document.getElementById("cvt");
let doc=null,gen=0,lastW=0;
function fail(m){cvt.textContent="⚠ "+m+" — odśwież stronę (F5).";cvt.classList.remove("hide");}
async function load(keepScroll){
  const my=++gen,top=keepScroll?box.scrollTop:0;
  let d;
  try{d=await pdfjsLib.getDocument({url:SRC+"&v="+mt,isEvalSupported:false}).promise;}
  catch(e){if(my===gen)fail("Nie udało się wczytać PDF ("+(e&&e.message||e)+")");return;}
  if(my!==gen){d.destroy();return;}
  if(doc)doc.destroy();doc=d;await layout(my);box.scrollTop=top;
}
async function layout(my){
  box.innerHTML="";lastW=box.clientWidth;
  const w=Math.max(box.clientWidth-16,200),dpr=Math.min(window.devicePixelRatio||1,2);
  const io=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting&&!e.target._r){e.target._r=1;draw(e.target,my);}});},{root:box,rootMargin:"600px 0px"});
  const cs=[];
  for(let i=1;i<=doc.numPages;i++){
    const pg=await doc.getPage(i);if(my!==gen)return;
    const v0=pg.getViewport({scale:1}),sc=w/v0.width,v=pg.getViewport({scale:sc*dpr});
    const c=document.createElement("canvas");c.width=Math.floor(v.width);c.height=Math.floor(v.height);
    c.style.width=w+"px";c.style.height=Math.floor(v.height/dpr)+"px";c._pg=pg;c._v=v;
    box.appendChild(c);cs.push(c);
  }
  cvt.classList.add("hide");
  cs.forEach(c=>io.observe(c));
}
async function draw(c,my){
  try{await c._pg.render({canvasContext:c.getContext("2d"),viewport:c._v}).promise;}
  catch(e){if(my===gen)fail("Błąd rysowania strony");}
}
let rt=null;
window.addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(()=>{
  if(doc&&Math.abs(box.clientWidth-lastW)>8){const t=box.scrollTop;layout(++gen).then(()=>{box.scrollTop=t;});}},250);});
setTimeout(()=>{if(!cvt.classList.contains("hide"))fail("Konwersja trwa nietypowo długo lub się nie powiodła");},200000);
async function chk(){try{
  const j=await(await fetch(Q+"?mt=1",{cache:"no-store"})).json();
  if(j.mt!==mt){mt=j.mt;cvt.textContent="⏳ Odświeżam podgląd…";cvt.classList.remove("hide");load(true);}
}catch(e){}}
setInterval(chk,3000);
load(false);
</script>"""

STYL = ('#pages{position:absolute;inset:0;overflow:auto;-webkit-overflow-scrolling:touch;'
        'padding:8px 0}'
        '#pages canvas{display:block;margin:0 auto 8px;background:#fff;'
        'box-shadow:0 1px 4px #0008}')


def obszar(komunikat_html: str) -> str:
    """Wnętrze .pvw: kontener stron + nakładka „konwertuję/wczytuję”."""
    return f'<div id="pages"></div><div id="cvt">{komunikat_html}</div>'


def skrypt(q_js: str, src_js: str, mt: float) -> str:
    """Skrypt podglądu. q_js, src_js — literały JS (już zserializowane JSON-em):
    adres pliku (do ?mt=1) i adres PDF (z „?pdf=1”, doklejany „&v=<mt>”)."""
    return (SKRYPT.replace('__Q__', q_js).replace('__SRC__', src_js)
            .replace('__MT__', repr(float(mt))))
