"""`shoebox screen review-page`: an offline page on which the owner rules on screen-flagged assets.

- Items: every quarantine row still `pending`, plus an optional seeded random sample of screened, unflagged
  assets (recall check). Shuffled together, numbered 1..N.
- The page folder (default `~/shoebox-review/screen/`) holds `index.html` and `img/NNNN.jpg` thumbnails made
  from the derived JPEGs. The HTML holds item numbers, the screen column and optional source/folder; never
  asset ids. The page-map (item number -> asset id) is written outside the page folder.
- The page and the page-map are refused under the repo, Desktop, Documents, iCloud Drive and /Volumes.
- Thumbnails are written, never read back. Callers print counts only.
"""

import hashlib
import html as htmlmod
import json
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

from shoebox import config, store
from shoebox.config import Config

PAGE_VERSION = "screen-review-1"
THUMB_PX = 512
SAMPLE_SCREEN = "sample"
DEFAULT_SEED = 20261011
SCREEN_KIND = "screen"


def default_out() -> Path:
    return Path.home() / "shoebox-review" / "screen"


def default_page_map() -> Path:
    return Path.home() / "shoebox-review" / "screen-page-map.json"


def refused_roots() -> list[tuple[str, Path]]:
    home = Path.home()
    return [("the repo", config.REPO_ROOT), ("Desktop", home / "Desktop"), ("Documents", home / "Documents"),
            ("iCloud Drive", home / "Library" / "Mobile Documents")]


def check_location(path: Path) -> None:
    """Refuse (ValueError) a page or page-map path that is synced, in the repo or on a NAS mount."""
    p = Path(path).expanduser()
    resolved = p.resolve()
    if config.under_volumes(p) or config.under_volumes(resolved):
        raise ValueError(f"refusing to write the screen review under /Volumes: {p}")
    for name, root in refused_roots():
        for r in {root, root.resolve()}:
            if resolved == r or resolved.is_relative_to(r) or p == r or p.is_relative_to(r):
                raise ValueError(f"refusing to write the screen review under {name} ({r}): {p}")


def unflagged_from_store(cfg: Config) -> list[str]:
    """Asset ids the screen ran on and did not flag (`results/screen/...` records with `sensitive` false)."""
    out = set()
    for d in store.store_dirs(cfg, SCREEN_KIND):
        for rec in store.index_records(store.iter_dir(d)).values():
            if rec.get("status", "ok") == "ok" and rec.get("sensitive") is False:
                out.add(rec["asset_id"])
    return sorted(out)


@dataclass(frozen=True)
class PageResult:
    page_id: str
    out: Path
    page_map: Path
    flagged: int  # flagged items on the page
    sampled: int  # unflagged items on the page
    no_derived: int  # pending assets left off for want of a derived image (they stay pending)


def build_page(cfg: Config, out: Path, rows: dict[str, dict], unflagged: list[str] = (), sample: int = 0,
               seed: int = DEFAULT_SEED, page_map: Path | None = None, profile: str = "infer-v1",
               info: dict[str, dict] | None = None) -> PageResult:
    """`rows` is `quarantine.load()`; `unflagged` the screened, unflagged asset ids to sample from; `info` an
    optional `{asset_id: {source, folder}}` shown next to each item."""
    import pyvips

    from shoebox import derive

    out = Path(out).expanduser()
    page_map = Path(page_map).expanduser() if page_map else out.parent / f"{out.name}-page-map.json"
    check_location(out)
    check_location(page_map)
    if page_map.resolve().is_relative_to(out.resolve()):
        raise ValueError(f"the page-map must be outside the page folder {out}: {page_map}")
    info = info or {}

    pending = sorted(a for a, r in rows.items() if r.get("verdict") == "pending")
    pool = sorted(set(unflagged) - set(rows))
    rng = random.Random(seed)
    sampled = rng.sample(pool, min(sample, len(pool))) if sample else []
    wanted = [(a, rows[a].get("screen") or "nudity", True) for a in pending] + \
             [(a, SAMPLE_SCREEN, False) for a in sampled]
    have = [w for w in wanted if derive.derived_path(cfg, profile, w[0]).is_file()]
    no_derived = sum(1 for w in wanted if w[2]) - sum(1 for w in have if w[2])
    rng.shuffle(have)

    img = out / "img"
    if img.exists():
        shutil.rmtree(img)
    img.mkdir(parents=True)
    items, page_items = {}, []
    for n, (a, screen, flagged) in enumerate(have, 1):
        name = f"{n:04d}.jpg"
        src = derive.derived_path(cfg, profile, a)
        pyvips.Image.thumbnail(str(src), THUMB_PX).write_to_file(str(img / name), Q=80, strip=True)
        items[str(n)] = {"asset_id": a, "screen": screen, "flagged": flagged}
        meta = info.get(a, {})
        page_items.append({"n": n, "img": f"img/{name}", "screen": screen,
                           "source": meta.get("source"), "folder": meta.get("folder")})
    core = {"version": PAGE_VERSION, "seed": seed, "items": items}
    pid = "s-" + hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]
    page_map.parent.mkdir(parents=True, exist_ok=True)
    page_map.write_text(json.dumps({"page_id": pid, **core, "page_dir": str(out), "thumbs_dir": str(img)},
                                   indent=1, sort_keys=True) + "\n")
    (out / "index.html").write_text(render({"page_id": pid, "items": page_items}))
    return PageResult(pid, out, page_map, sum(1 for w in have if w[2]), sum(1 for w in have if not w[2]),
                      no_derived)


def render(data: dict) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    title = htmlmod.escape(f"Shoebox screen review {data['page_id']}")
    return _TEMPLATE.replace("__TITLE__", title).replace("__DATA__", blob)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>__TITLE__</title>
<style>
body{margin:0;font:14px/1.4 -apple-system,Helvetica,Arial,sans-serif;background:#f4f3ef;color:#1d1d1b}
header{position:sticky;top:0;z-index:2;background:#f4f3ef;border-bottom:1px solid #ccc;padding:8px 14px;
  display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
header h1{font-size:15px;margin:0}
.grow{flex:1}
button{font:inherit;border:1px solid #bbb;border-radius:6px;background:#fff;padding:3px 9px;cursor:pointer}
main{display:grid;gap:12px;padding:12px;grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
.card{background:#fff;border:2px solid #ddd;border-radius:8px;overflow:hidden}
.card.focus{border-color:#2f5d8a}
.card.v-include{background:#eef7f0}.card.v-private{background:#fdf5e6}.card.v-exclude{background:#fbeaea}
.thumb{aspect-ratio:1;background:#ddd;display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:pointer}
.thumb img{max-width:100%;max-height:100%;filter:blur(24px);transition:filter .1s}
.card.revealed .thumb img{filter:none}
.row{padding:6px 8px;display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.screen{font-size:12px;background:#eceae4;border-radius:4px;padding:0 6px}
.meta{font-size:11px;color:#666;word-break:break-all}
.row button.on{background:#2f5d8a;color:#fff;border-color:#2f5d8a}
.keys{font-size:12px;color:#666}
</style>
</head>
<body>
<header>
<h1>Screen review</h1><span id="pid" class="keys"></span>
<span class="keys">keys: arrows / j k move, space reveal, i include, p private, x exclude</span>
<span class="grow"></span><span id="progress"></span>
<button id="all" type="button">mark page as include</button>
<button id="dl" type="button">Download answers</button>
</header>
<main id="grid"></main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
var D=JSON.parse(document.getElementById("data").textContent);
var KEY="shoebox-screen-"+D.page_id, A={}, F=0;
try{var s=localStorage.getItem(KEY);if(s){A=JSON.parse(s)||{};}}catch(e){}
function $(id){return document.getElementById(id);}
$("pid").textContent=D.page_id;
var V=["include","private","exclude"], cards=[];
function save(){try{localStorage.setItem(KEY,JSON.stringify(A));}catch(e){}progress();}
function progress(){var n=0;D.items.forEach(function(it){if(A[it.n])n++;});$("progress").textContent=n+" / "+D.items.length+" answered";}
function paint(i){var it=D.items[i],c=cards[i];c.className="card"+(c.revealed?" revealed":"")+(i===F?" focus":"")+(A[it.n]?" v-"+A[it.n]:"");
  c.querySelectorAll("button").forEach(function(b){b.className=b.dataset.v===A[it.n]?"on":"";});}
function set(i,v){A[D.items[i].n]=v;save();paint(i);}
function focus(i){var o=F;F=Math.max(0,Math.min(D.items.length-1,i));if(cards[o])paint(o);paint(F);cards[F].scrollIntoView({block:"nearest"});}
D.items.forEach(function(it,i){
  var c=document.createElement("div");c.revealed=false;
  var t=document.createElement("div");t.className="thumb";var im=document.createElement("img");im.src=it.img;im.alt="item "+it.n;
  t.appendChild(im);t.onclick=function(){c.revealed=!c.revealed;focus(i);paint(i);};c.appendChild(t);
  var r=document.createElement("div");r.className="row";
  var b=document.createElement("b");b.textContent="#"+it.n;r.appendChild(b);
  var sc=document.createElement("span");sc.className="screen";sc.textContent=it.screen;r.appendChild(sc);
  V.forEach(function(v){var x=document.createElement("button");x.type="button";x.dataset.v=v;x.textContent=v;x.onclick=function(){focus(i);set(i,v);};r.appendChild(x);});
  c.appendChild(r);
  if(it.source||it.folder){var m=document.createElement("div");m.className="row meta";m.textContent=[it.source,it.folder].filter(Boolean).join(": ");c.appendChild(m);}
  cards.push(c);$("grid").appendChild(c);paint(i);
});
$("all").onclick=function(){D.items.forEach(function(it,i){if(!A[it.n]){A[it.n]="include";paint(i);}});save();};
document.addEventListener("keydown",function(e){if(e.metaKey||e.ctrlKey||e.altKey||!cards.length)return;
  var k=e.key,cols=Math.max(1,Math.round($("grid").clientWidth/(cards[0].offsetWidth+12)));
  if(k==="ArrowRight"||k==="j"){focus(F+1);}else if(k==="ArrowLeft"||k==="k"){focus(F-1);}
  else if(k==="ArrowDown"){focus(F+cols);}else if(k==="ArrowUp"){focus(F-cols);}
  else if(k===" "){cards[F].revealed=!cards[F].revealed;paint(F);}
  else if(k==="i"){set(F,"include");focus(F+1);}else if(k==="p"){set(F,"private");focus(F+1);}
  else if(k==="x"){set(F,"exclude");focus(F+1);}else{return;}
  e.preventDefault();});
$("dl").onclick=function(){var t=JSON.stringify({page_id:D.page_id,verdicts:A,ts:new Date().toISOString()},null,1);
  var a=document.createElement("a");a.href=URL.createObjectURL(new Blob([t],{type:"application/json"}));
  a.download="screen-answers-"+D.page_id+".json";document.body.appendChild(a);a.click();
  setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);};
progress();
})();
</script>
</body>
</html>
"""
