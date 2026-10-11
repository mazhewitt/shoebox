"""Owner review page, owner review ingest, Opus usefulness rescore ingest, and their report numbers.

1. `build_page`: a self-contained offline HTML page (no server, no external resources; data inline as JSON,
   images copied to `img/`) on which the owner rates 30 blind caption sets 1-5 against the usefulness rubric
   and marks 30 queries as realistic or not. The A-E order is re-shuffled per photo (seeded); the page's own
   label -> arm map goes to `page-map.json`, never into the HTML.
2. `review_ingest`: validates the downloaded answers against `page-map.json`, un-blinds and stores owner
   records (kind `owner_reviews`, model_id `owner`); writes realism counts (no query text) into QUERIES.md.
3. `rescore_ingest`: stores Opus' rubric usefulness (`rescore-*.jsonl`, kind `caption_usefulness`).
4. `usefulness_report`: per-arm rubric usefulness with bootstrap CIs, paired difference vs the reference arm,
   the old unanchored score next to it, and owner means and owner-Opus agreement. Numbers only.
"""

import hashlib
import html as htmlmod
import json
import random
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from shoebox import store
from shoebox.config import Config
from shoebox.evals import caption_audit as ca
from shoebox.evals.audit import check_out_dir

RUBRIC_FILE = "caption-usefulness-v1.md"
LABELS = ca.LABELS
DEFAULT_N = 30
DEFAULT_SEED = 20261015
OWNER_KIND = "owner_reviews"
OWNER_MODEL_ID = "owner"
RESCORE_KIND = "caption_usefulness"
VERDICTS = ("would", "wouldnt")
PAGE_VERSION = "review-page-1"
QUERIES_MD_PREFIX = "owner realism check:"


class IngestError(ValueError):
    pass


def rubric_text() -> str:
    return (ca.PROMPTS_DIR / RUBRIC_FILE).read_text()


def rubric_levels(text: str) -> list[dict]:
    """[{score, name, text}] from lines like `- **5 – excellent.** ...`."""
    out = []
    for m in re.finditer(r"^- \*\*(\d)\s*[–-]\s*([^*]+?)\.?\*\*\s*(.*)$", text, re.MULTILINE):
        out.append({"score": int(m.group(1)), "name": m.group(2).strip(), "text": m.group(3).strip()})
    return out


def _h(seed, *parts) -> str:
    return hashlib.sha256(":".join(map(str, (seed, *parts))).encode()).hexdigest()


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ---------- page ----------


def _category(stratum: str) -> str:
    if "|mono|" in stratum:
        return "mono"
    return "scan" if stratum.endswith("|scan") else "other"


def pick(keys: list[str], strata: dict[str, str], n: int, seed: int) -> list[str]:
    """Seeded spread: up to n//3 each of mono, (colour) scan and other photos, topped up from the rest; the
    result is in a seeded order."""
    order = sorted(keys, key=lambda k: (_h(seed, "pick", k), k))
    cats = {c: [k for k in order if _category(strata.get(k, "")) == c] for c in ("mono", "scan", "other")}
    chosen = [k for c in cats.values() for k in c[:n // 3]]
    chosen += [k for k in order if k not in set(chosen)][:max(0, n - len(chosen))]
    return sorted(chosen[:n], key=lambda k: (_h(seed, "page", k), k))


def _read_packets(d: Path) -> dict[str, dict]:
    out = {}
    for path in sorted(Path(d).glob("batch-*.json")):
        for p in json.loads(path.read_text())["photos"]:
            out[p["key"]] = p
    return out


def page_id(page_map_core: dict) -> str:
    return _h(PAGE_VERSION, store.canonical_json(page_map_core))[:16]


def build_page(packets_dir: Path, sample_rows: list[dict], qs: list[dict], realism_ids: list[str], n: int,
               seed: int, out: Path, rubric: str) -> dict:
    out = Path(out)
    check_out_dir(out)
    packets_dir = Path(packets_dir)
    mapping = json.loads((packets_dir / "map.json").read_text())
    photos = _read_packets(packets_dir)
    by_key = {r["key"]: r for r in sample_rows}
    qby = {q["query_id"]: q for q in qs}
    unknown = [q for q in realism_ids if q not in qby]
    if unknown:
        raise ValueError(f"realism query ids not in the query file: {unknown[:10]}")
    keys = [k for k in photos if k in mapping and k in by_key]
    if len(keys) < n:
        raise ValueError(f"only {len(keys)} packet photos with a sample row, need {n}")
    chosen = pick(keys, {k: by_key[k]["stratum"] for k in keys}, n, seed)
    (out / "img").mkdir(parents=True, exist_ok=True)
    page_photos, pmap = [], {}
    for k in chosen:
        p = photos[k]
        caps = {c["label"]: c for c in p["captions"]}
        order = sorted(caps)
        random.Random(f"{seed}:page:{k}").shuffle(order)
        img = out / "img" / f"{k}.jpg"
        shutil.copyfile(p["image"], img)
        shown, packet_labels = [], {}
        for lab, src in zip(LABELS, order, strict=False):
            packet_labels[lab] = src
            c = caps[src]
            shown.append({"label": lab, "caption": c.get("caption"), "tags": list(c.get("tags") or [])})
        pmap[k] = {"asset_id": by_key[k]["asset_id"], "stratum": by_key[k]["stratum"],
                   "packet_labels": packet_labels, "labels": {lab: mapping[k][s] for lab, s in packet_labels.items()}}
        page_photos.append({"key": k, "image": f"img/{k}.jpg", "captions": shown})
    core = {"seed": seed, "photos": pmap, "queries": list(realism_ids), "rubric_sha256": _h("rubric", rubric)}
    pm = {"page_id": page_id(core), "version": PAGE_VERSION, **core}
    data = {"page_id": pm["page_id"], "seed": seed, "photos": page_photos, "levels": rubric_levels(rubric),
            "searches": [{"query_id": q, "text": qby[q]["text"]} for q in realism_ids]}
    (out / "index.html").write_text(render_page(data))
    (out / "page-map.json").write_text(json.dumps(pm, indent=1, sort_keys=True) + "\n")
    return pm


def _inline_json(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_page(data: dict) -> str:
    title = htmlmod.escape(f"Shoebox review {data['page_id']}")
    return (PAGE_TEMPLATE.replace("__TITLE__", title)
            .replace("__DATA__", _inline_json(data)))


PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}
body{margin:0;font:15px/1.4 -apple-system,system-ui,"Segoe UI",Helvetica,Arial,sans-serif;color:#222;background:#f6f6f4}
header{display:flex;gap:8px;align-items:center;padding:8px 14px;background:#fff;border-bottom:1px solid #ddd;position:sticky;top:0;z-index:2}
header .tab{border:1px solid #bbb;background:#eee;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:15px}
header .tab.on{background:#2d5d8a;color:#fff;border-color:#2d5d8a}
header .sp{flex:1}
header button.act{padding:6px 10px;border:1px solid #bbb;border-radius:6px;background:#fff;cursor:pointer}
#status{font-size:12px;color:#666;min-width:120px;text-align:right}
main{padding:10px 14px}
.hidden{display:none!important}
details.rub{background:#fff;border:1px solid #ddd;border-radius:6px;padding:4px 10px;margin-bottom:8px;font-size:13px}
details.rub li{margin:2px 0}
.prog{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.bar{flex:1;height:8px;background:#ddd;border-radius:4px;overflow:hidden}
.bar>div{height:100%;background:#3b8a3e;width:0}
.photo{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:14px;align-items:start}
.imgbox{background:#222;border-radius:6px;display:flex;justify-content:center;align-items:center;height:calc(100vh - 170px);min-height:300px}
.imgbox img{max-width:100%;max-height:100%;object-fit:contain}
.sets{display:flex;flex-direction:column;gap:6px;max-height:calc(100vh - 170px);overflow:auto;padding-right:4px}
.set{background:#fff;border:2px solid #ddd;border-radius:6px;padding:6px 9px;outline:none}
.set.focus{border-color:#2d5d8a;box-shadow:0 0 0 2px #cfe0f1}
.set .lab{font-weight:600;color:#2d5d8a;margin-right:6px}
.set .cap{margin:2px 0 4px}
.set .none{color:#999;font-style:italic}
.chip{display:inline-block;background:#eef1f4;border:1px solid #d5dbe1;border-radius:10px;padding:0 7px;margin:1px 2px 1px 0;font-size:12px;color:#444}
.rate{display:flex;gap:4px;margin-top:4px}
.rate button{width:34px;height:28px;border:1px solid #bbb;border-radius:5px;background:#fafafa;cursor:pointer;font-weight:600}
.rate button.sel{background:#2d5d8a;color:#fff;border-color:#2d5d8a}
.nav{display:flex;gap:8px;align-items:center;margin-top:6px}
.nav button{white-space:nowrap;padding:6px 12px;border:1px solid #bbb;border-radius:6px;background:#fff;cursor:pointer}
textarea,input[type=text]{width:100%;font:inherit;padding:5px;border:1px solid #ccc;border-radius:5px}
.hint{font-size:12px;color:#777}
.q{background:#fff;border:1px solid #ddd;border-radius:6px;padding:8px 10px;margin-bottom:6px;display:grid;grid-template-columns:28px 1fr auto;gap:8px;align-items:center}
.q .t{font-size:16px}
.q .btns button{padding:5px 10px;border:1px solid #bbb;border-radius:5px;background:#fafafa;cursor:pointer;margin-left:4px}
.q .btns button.yes.sel{background:#3b8a3e;color:#fff;border-color:#3b8a3e}
.q .btns button.no.sel{background:#b04a3a;color:#fff;border-color:#b04a3a}
.q .re{grid-column:2/4}
@media (max-width:900px){.photo{grid-template-columns:1fr}.imgbox{height:55vh}.sets{max-height:none}}
</style></head>
<body>
<header>
<button class="tab on" id="tab-c" type="button">Captions (<span id="nc"></span>)</button>
<button class="tab" id="tab-s" type="button">Searches (<span id="ns"></span>)</button>
<span class="sp"></span><span id="status"></span>
<button class="act" id="copy" type="button">Copy answers</button>
<button class="act" id="dl" type="button">Download answers</button>
</header>
<main>
<section id="pc">
<details class="rub"><summary>How to rate (1 = useless … 5 = excellent): could a natural search find this photo through this caption + tags?</summary><p class="facts">First note the photo's searchable facts: <b>1</b> the main subject(s); <b>2</b> the setting or scene; <b>3</b> the activity or occasion; <b>4</b> anything distinctive (legible text, a landmark, black-and-white, film scan or old print, night, snow). Wrong facts count more than missing ones; length earns nothing on its own.</p><ul id="levels"></ul>
<div class="hint">Wrong facts weigh more than missing ones. Ignore style and length.</div></details>
<div class="prog"><span id="pos"></span><div class="bar"><div id="barfill"></div></div><span id="done"></span></div>
<div class="photo"><div class="imgbox"><img id="img" alt="photo"></div>
<div><div class="sets" id="sets"></div>
<div class="nav"><button id="prev" type="button">← Previous</button><button id="next" type="button">Next →</button>
<span class="hint">Keys: 1–5 rate the highlighted set · Tab / ↑↓ move between sets · Enter or → next photo · ← previous</span></div>
<div style="margin-top:6px"><textarea id="note" rows="2" placeholder="Optional note about this photo"></textarea></div>
</div></div>
</section>
<section id="ps" class="hidden">
<p class="hint">Would you type this search to look for a photo in your library? Optionally say how you would phrase it.</p>
<div id="qs"></div>
</section>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
"use strict";
var D=JSON.parse(document.getElementById("data").textContent);
var KEY="shoebox-review-"+D.page_id;
var A={captions:{},searches:{}};
try{var s=localStorage.getItem(KEY);if(s){var p=JSON.parse(s);if(p&&p.captions&&p.searches)A=p;}}catch(e){}
var cur=0,foc=0;
function $(id){return document.getElementById(id);}
function el(t,cls,txt){var e=document.createElement(t);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e;}
function status(m){$("status").textContent=m;}
function save(){try{localStorage.setItem(KEY,JSON.stringify(A));status("saved "+new Date().toLocaleTimeString());}catch(e){status("not saved: download answers before closing");}}
function capA(k){if(!A.captions[k])A.captions[k]={labels:{},note:""};return A.captions[k];}
function complete(k){var c=A.captions[k];if(!c)return false;for(var i=0;i<5;i++){if(!c.labels["ABCDE"[i]])return false;}return true;}
$("nc").textContent=D.photos.length;$("ns").textContent=D.searches.length;
D.levels.slice().sort(function(a,b){return b.score-a.score;}).forEach(function(l){var li=el("li");var b=el("b",null,l.score+" "+l.name+". ");li.appendChild(b);li.appendChild(document.createTextNode(l.text));$("levels").appendChild(li);});
function tab(which){var c=which==="c";$("pc").classList.toggle("hidden",!c);$("ps").classList.toggle("hidden",c);$("tab-c").classList.toggle("on",c);$("tab-s").classList.toggle("on",!c);}
$("tab-c").onclick=function(){tab("c");};$("tab-s").onclick=function(){tab("s");};
function progress(){var n=0;D.photos.forEach(function(p){if(complete(p.key))n++;});$("barfill").style.width=(100*n/D.photos.length)+"%";$("done").textContent=n+" / "+D.photos.length+" done";$("pos").textContent="Photo "+(cur+1)+" of "+D.photos.length;}
function rate(k,lab,v){capA(k).labels[lab]=v;save();showSets();progress();}
function showSets(){var p=D.photos[cur],box=$("sets"),a=A.captions[p.key]||{labels:{}};box.innerHTML="";
p.captions.forEach(function(c,i){var d=el("div","set"+(i===foc?" focus":""));d.tabIndex=-1;
var h=el("div");h.appendChild(el("span","lab",c.label));d.appendChild(h);
if(c.caption==null){d.appendChild(el("div","cap none","(no caption)"));}else{d.appendChild(el("div","cap",c.caption));}
var tg=el("div");(c.tags||[]).forEach(function(t){tg.appendChild(el("span","chip",t));});d.appendChild(tg);
var r=el("div","rate");for(var v=1;v<=5;v++){(function(v){var b=el("button",a.labels[c.label]===v?"sel":"",String(v));b.type="button";b.title=String(v);b.onclick=function(){foc=i;rate(p.key,c.label,v);};r.appendChild(b);})(v);}
d.appendChild(r);d.onclick=function(ev){if(ev.target.tagName!=="BUTTON"){foc=i;showSets();}};box.appendChild(d);});
var f=box.children[foc];if(f&&f.scrollIntoView)f.scrollIntoView({block:"nearest"});}
function show(i){cur=Math.max(0,Math.min(D.photos.length-1,i));foc=0;var p=D.photos[cur];$("img").src=p.image;$("note").value=(A.captions[p.key]||{}).note||"";showSets();progress();}
$("prev").onclick=function(){show(cur-1);};$("next").onclick=function(){show(cur+1);};
$("note").oninput=function(){capA(D.photos[cur].key).note=this.value;save();};
document.addEventListener("keydown",function(e){
if($("pc").classList.contains("hidden"))return;
var t=e.target.tagName;if(t==="TEXTAREA"||t==="INPUT")return;if(e.metaKey||e.ctrlKey||e.altKey)return;
var p=D.photos[cur];
if(e.key>="1"&&e.key<="5"){rate(p.key,p.captions[foc].label,+e.key);if(foc<p.captions.length-1){foc++;showSets();}e.preventDefault();}
else if(e.key==="Tab"||e.key==="ArrowDown"||e.key==="ArrowUp"){var back=e.key==="ArrowUp"||(e.key==="Tab"&&e.shiftKey);foc=(foc+(back?p.captions.length-1:1))%p.captions.length;showSets();e.preventDefault();}
else if(e.key==="Enter"||e.key==="ArrowRight"){show(cur+1);e.preventDefault();}
else if(e.key==="ArrowLeft"){show(cur-1);e.preventDefault();}});
function showQs(){var box=$("qs");box.innerHTML="";D.searches.forEach(function(q,i){var a=A.searches[q.query_id]||{};var d=el("div","q");
d.appendChild(el("span","hint",String(i+1)));d.appendChild(el("span","t",q.text));
var bs=el("span","btns");[["would","I'd type this","yes"],["wouldnt","I wouldn't","no"]].forEach(function(o){var b=el("button",o[2]+(a.verdict===o[0]?" sel":""),o[1]);b.type="button";
b.onclick=function(){var s=A.searches[q.query_id]||(A.searches[q.query_id]={verdict:null,rephrase:""});s.verdict=s.verdict===o[0]?null:o[0];save();showQs();};bs.appendChild(b);});
d.appendChild(bs);var re=el("input","re");re.type="text";re.placeholder="how I'd phrase it (optional)";re.value=a.rephrase||"";
re.oninput=function(){var s=A.searches[q.query_id]||(A.searches[q.query_id]={verdict:null,rephrase:""});s.rephrase=this.value;save();};d.appendChild(re);box.appendChild(d);});
var n=0;D.searches.forEach(function(q){if((A.searches[q.query_id]||{}).verdict)n++;});$("ns").textContent=n+"/"+D.searches.length;}
function answers(){return JSON.stringify({page_id:D.page_id,seed:D.seed,captions:A.captions,searches:A.searches,ts:new Date().toISOString()},null,1);}
$("dl").onclick=function(){var b=new Blob([answers()],{type:"application/json"});var a=document.createElement("a");a.href=URL.createObjectURL(b);a.download="answers-"+D.page_id+".json";document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},500);status("downloaded");};
$("copy").onclick=function(){var t=answers();function fb(){var x=document.createElement("textarea");x.value=t;document.body.appendChild(x);x.select();try{document.execCommand("copy");status("copied");}catch(e){status("copy failed: use Download");}x.remove();}
try{if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(function(){status("copied");},fb);}else fb();}catch(e){fb();}};
show(0);showQs();
})();
</script>
</body></html>
"""


# ---------- owner review ingest ----------


@dataclass
class IngestResult:
    params_hash: str
    stored: int = 0
    already: int = 0
    incomplete: int = 0
    conflicts: list[str] = field(default_factory=list)


def owner_params(rubric: str, seed: int) -> dict:
    return {"rubric_sha256": hashlib.sha256(rubric.encode()).hexdigest(), "rubric": RUBRIC_FILE,
            "seed": seed, "writer": "owner", "presentation": PAGE_VERSION}


def validate_answers(ans, pm: dict) -> list[str]:
    if not isinstance(ans, dict):
        return ["answers are not a JSON object"]
    errs = []
    if ans.get("page_id") != pm["page_id"]:
        errs.append(f"page_id {ans.get('page_id')!r} is not this page's {pm['page_id']!r}")
    if ans.get("seed") != pm["seed"]:
        errs.append(f"seed {ans.get('seed')!r} is not {pm['seed']!r}")
    caps, srch = ans.get("captions"), ans.get("searches")
    if not isinstance(caps, dict) or not isinstance(srch, dict):
        return [*errs, "captions and searches must be objects"]
    for k, v in caps.items():
        if k not in pm["photos"]:
            errs.append(f"unknown photo {k!r}")
            continue
        labs = v.get("labels") if isinstance(v, dict) else None
        if not isinstance(labs, dict):
            errs.append(f"{k}: labels missing")
            continue
        for lab, n in labs.items():
            if lab not in pm["photos"][k]["labels"]:
                errs.append(f"{k}: unknown label {lab!r}")
            elif not _is_int(n) or not 1 <= n <= 5:
                errs.append(f"{k}.{lab}: {n!r} not an int 1-5")
        if v.get("note") is not None and not isinstance(v.get("note"), str):
            errs.append(f"{k}: note not a string")
    for q, v in srch.items():
        if q not in pm["queries"]:
            errs.append(f"unknown query {q!r}")
            continue
        if not isinstance(v, dict) or v.get("verdict") not in (*VERDICTS, None):
            errs.append(f"{q}: verdict {v.get('verdict') if isinstance(v, dict) else v!r} not in {list(VERDICTS)}")
        elif v.get("rephrase") is not None and not isinstance(v.get("rephrase"), str):
            errs.append(f"{q}: rephrase not a string")
    return errs


def realism_line(recs: list[dict], total: int) -> str:
    """Counts only (no query text): would/wouldn't, by kind and by style."""
    yes = sum(r["verdict"] == "would" for r in recs)
    no = len(recs) - yes

    def by(f):
        groups = {}
        for r in recs:
            g = groups.setdefault(r.get(f) or "unknown", [0, 0])
            g[0] += r["verdict"] == "would"
            g[1] += 1
        return ", ".join(f"{k} {a}/{n}" for k, (a, n) in sorted(groups.items()))

    share = f"{yes / len(recs):.1%}" if recs else "n/a"
    return (f"{QUERIES_MD_PREFIX} {len(recs)} of {total} answered; would type {yes}, wouldn't {no} of {len(recs)} "
            f"({share}); by kind (would/answered): {by('query_kind')}; by style: {by('query_style')}")


def update_queries_md(path: Path, line: str) -> None:
    text = Path(path).read_text()
    pat = re.compile(rf"^{re.escape(QUERIES_MD_PREFIX)}.*$", re.MULTILINE)
    if not pat.search(text):
        raise IngestError(f"{path}: no '{QUERIES_MD_PREFIX}' line to replace")
    new = pat.sub(lambda _: line, text, count=1)
    if new != text:
        Path(path).write_text(new)


def review_ingest(cfg: Config, ans: dict, page_dir: Path, rubric: str, qs: list[dict], queries_md: Path
                  ) -> IngestResult:
    """Validate everything first (nothing stored on any error); store complete photo ratings and answered
    queries not already final; rewrite the QUERIES.md realism line from the stored search records."""
    pm = json.loads((Path(page_dir) / "page-map.json").read_text())
    errs = validate_answers(ans, pm)
    if errs:
        raise IngestError("; ".join(errs))
    qby = {q["query_id"]: q for q in qs}
    missing = [q for q in pm["queries"] if q not in qby]
    if missing:
        raise IngestError(f"page queries not in the query file: {missing[:10]}")
    if not Path(queries_md).read_text().count(QUERIES_MD_PREFIX):
        raise IngestError(f"{queries_md}: no '{QUERIES_MD_PREFIX}' line to replace")
    params = owner_params(rubric, int(pm["seed"]))
    ph = store.params_hash(params)
    res = IngestResult(ph)
    recs = []
    for k in sorted(ans["captions"]):
        v = ans["captions"][k]
        e = pm["photos"][k]
        if sorted(v["labels"]) != sorted(e["labels"]):
            res.incomplete += 1
            continue
        recs.append({"asset_id": e["asset_id"], "key": k, "review": "caption", "stratum": e.get("stratum"),
                     "useful": {e["labels"][lab]: n for lab, n in v["labels"].items()},
                     "labels": dict(v["labels"]), "page_labels": e["labels"], "note": v.get("note") or ""})
    for q in sorted(ans["searches"]):
        v = ans["searches"][q]
        if v.get("verdict") is None:
            res.incomplete += 1
            continue
        recs.append({"asset_id": q, "key": q, "review": "search", "verdict": v["verdict"],
                     "query_kind": qby[q].get("kind"), "query_style": qby[q].get("style"), "rephrase": v.get("rephrase") or ""})
    have = store.index(cfg, OWNER_MODEL_ID, ph, OWNER_KIND)
    store.write_params(cfg, OWNER_MODEL_ID, ph, {"candidate": "owner-review", "model_id": OWNER_MODEL_ID,
                                                 "params_hash": ph, "params": params, "page_id": pm["page_id"]},
                       OWNER_KIND)
    cmp = ("useful", "note", "verdict", "rephrase")
    for r in recs:
        rec = {**r, "model_id": OWNER_MODEL_ID, "params_hash": ph, "status": "ok", "kind": OWNER_KIND,
               "page_id": pm["page_id"]}
        old = have.get(r["asset_id"])
        if store.is_final(old):
            res.already += 1
            if any(old.get(f) != rec.get(f) for f in cmp):
                res.conflicts.append(r["key"])
            continue
        store.append(cfg, rec, OWNER_KIND)
        have[r["asset_id"]] = rec
        res.stored += 1
    searches = [r for r in have.values() if r.get("status") == "ok" and r.get("review") == "search"
                and r["asset_id"] in pm["queries"]]
    update_queries_md(queries_md, realism_line(searches, len(pm["queries"])))
    return res


# ---------- rescore ingest ----------


def rescore_params(rubric: str) -> dict:
    return {"rubric_sha256": hashlib.sha256(rubric.encode()).hexdigest(), "rubric": RUBRIC_FILE,
            "presentation": ca.PRESENTATION, "writer": ca.WRITER}


def read_rescores(d: Path, mapping: dict[str, dict]) -> dict[str, dict]:
    out, errors, seen = {}, [], {}
    for path in sorted(Path(d).glob("rescore-*.jsonl")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path.name}:{i}"
            try:
                row = json.loads(line)
            except ValueError as e:
                errors.append(f"{where}: not JSON: {e}")
                continue
            k = row.get("key") if isinstance(row, dict) else None
            if k not in mapping:
                errors.append(f"{where}: unknown key {k!r}")
                continue
            if k in seen:
                errors.append(f"{where}: key {k} already at {seen[k]}")
                continue
            seen[k] = where
            u = row.get("useful")
            if not isinstance(u, dict) or sorted(u) != sorted(mapping[k]):
                errors.append(f"{where}: {k}: useful labels {sorted(u) if isinstance(u, dict) else u!r} "
                              f"are not {sorted(mapping[k])}")
            else:
                errors += [f"{where}: {k}.{lab}: {v!r} not an int 1-5" for lab, v in sorted(u.items())
                           if not _is_int(v) or not 1 <= v <= 5]
            if "facts" in row and not isinstance(row["facts"], str):
                errors.append(f"{where}: {k}: facts not a string")
            out[k] = row
    errors += [f"{k}: no rescore line" for k in sorted(set(mapping) - set(seen))]
    if errors:
        raise IngestError("; ".join(errors))
    return out


def rescore_ingest(cfg: Config, d: Path, sample_rows: list[dict], rubric: str, model_id: str = ca.MODEL_ID
                   ) -> IngestResult:
    d = Path(d)
    mapping = json.loads((d / "map.json").read_text())
    lines = read_rescores(d, mapping)
    by_key = {r["key"]: r for r in sample_rows}
    unknown = sorted(k for k in mapping if k not in by_key)
    if unknown:
        raise IngestError(f"keys not in the sample: {unknown[:10]}")
    params = rescore_params(rubric)
    ph = store.params_hash(params)
    res = IngestResult(ph)
    have = store.index(cfg, model_id, ph, RESCORE_KIND)
    store.write_params(cfg, model_id, ph, {"candidate": "opus-caption-usefulness", "model_id": model_id,
                                           "params_hash": ph, "params": params}, RESCORE_KIND)
    for k in sorted(lines):
        row, a = lines[k], by_key[k]["asset_id"]
        useful = {mapping[k][lab]: v for lab, v in row["useful"].items()}
        rec = {"asset_id": a, "model_id": model_id, "params_hash": ph, "status": "ok", "kind": RESCORE_KIND,
               "key": k, "stratum": by_key[k].get("stratum"), "useful": useful, "labels": mapping[k],
               "facts": row.get("facts", ""), "writer": ca.WRITER}
        old = have.get(a)
        if store.is_final(old):
            res.already += 1
            if old.get("useful") != useful:
                res.conflicts.append(k)
            continue
        store.append(cfg, rec, RESCORE_KIND)
        have[a] = rec
        res.stored += 1
    return res


def records(cfg: Config, kind: str, model_id: str, params_hash: str | None = None) -> tuple[str | None, list[dict]]:
    """(params_hash, ok records) for a kind; ([], None) when nothing is stored. Several params dirs need
    an explicit params_hash."""
    if params_hash is None:
        dirs = [p.name for p in (cfg.results_dir / kind / store.model_dir(model_id)).glob("*") if p.is_dir()]
        if not dirs:
            return None, []
        if len(dirs) != 1:
            raise ValueError(f"{len(dirs)} {kind} params dirs for {model_id}; pass the params hash ({sorted(dirs)})")
        params_hash = dirs[0]
    idx = store.index(cfg, model_id, params_hash, kind)
    return params_hash, sorted((r for r in idx.values() if r.get("status") == "ok"), key=lambda r: r["key"])


# ---------- report ----------


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for t in range(i, j + 1):
            r[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> float | None:
    """Pearson on average ranks; None when either side is constant."""
    if len(x) < 2:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    return None if sx == 0 or sy == 0 else sxy / (sx * sy)


def usefulness_report(audits: list[dict], rescore: list[dict], owner: list[dict] | None = None,
                      b: int = ca.BOOTSTRAP, seed: int = ca.DEFAULT_SEED, reference: str = ca.REFERENCE_ARM) -> dict:
    """Numbers only. `audits`: caption_audits records (old unanchored `useful`); `rescore`: caption_usefulness
    records; `owner`: owner_reviews records (only `review == "caption"` are used)."""
    old = {r["asset_id"]: {arm: s.get("useful") for arm, s in r["sets"].items()} for r in audits}
    new = {r["asset_id"]: r["useful"] for r in rescore}
    arms = sorted({arm for u in new.values() for arm in u})
    out_arms, paired = {}, {}
    for arm in arms:
        ids = sorted(a for a in new if arm in new[a])
        rng = random.Random(f"{seed}:rubric:{arm}")
        same = [a for a in ids if old.get(a, {}).get(arm) is not None]
        out_arms[arm] = {"n": len(ids), "rubric": ca._mean_ci([new[a][arm] for a in ids], b, rng),
                         "unanchored": ca._mean_ci([old[a][arm] for a in same], b, rng), "unanchored_n": len(same)}
    for arm in arms:
        if arm == reference or reference not in arms:
            continue
        common = sorted(a for a in new if arm in new[a] and reference in new[a])
        d = [new[a][arm] - new[a][reference] for a in common]
        n = len(d)
        rng = random.Random(f"{seed}:rubric-paired:{arm}")
        boots = [sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(b)] if n else []
        paired[arm] = {"n": n, "diff": ca._r(sum(d) / n) if n else None, "ci95": ca._pct(boots)}
    rep = {"reference_arm": reference, "bootstrap": b, "arms": out_arms, "paired_vs_reference": paired}
    own = {r["asset_id"]: r["useful"] for r in (owner or []) if r.get("review") == "caption"}
    if own:
        photos = sorted(a for a in own if a in new)
        o_arms = {}
        for arm in sorted({arm for a in photos for arm in own[a]}):
            ids = [a for a in photos if arm in own[a] and arm in new[a]]
            rng = random.Random(f"{seed}:owner:{arm}")
            o_arms[arm] = {"n": len(ids), "owner": ca._mean_ci([own[a][arm] for a in ids], b, rng),
                           "opus_rubric": ca._mean_ci([new[a][arm] for a in ids], b, rng),
                           "opus_unanchored": ca._mean_ci([old[a][arm] for a in ids
                                                           if old.get(a, {}).get(arm) is not None], b, rng)}
        rhos, diffs, pooled_o, pooled_n = [], [], [], []
        for a in photos:
            common = sorted(set(own[a]) & set(new[a]))
            xo, xn = [own[a][arm] for arm in common], [new[a][arm] for arm in common]
            rho = spearman(xo, xn)
            if rho is not None:
                rhos.append(rho)
            diffs += [abs(p - q) for p, q in zip(xo, xn, strict=True)]
            pooled_o += xo
            pooled_n += xn
        rep["owner"] = {"photos": len(photos), "arms": o_arms, "agreement": {
            "spearman_per_photo": {"mean": ca._r(sum(rhos) / len(rhos)) if rhos else None, "n": len(rhos)},
            "spearman_pooled": ca._r(spearman(pooled_o, pooled_n)) if pooled_o else None,
            "mean_abs_diff": ca._r(sum(diffs) / len(diffs)) if diffs else None, "pairs": len(diffs)}}
    rep["rules"] = {"rubric": f"Opus usefulness against config/prompts/{RUBRIC_FILE} (1-5), mean per photo",
                    "unanchored": "the caption audit's original 1-5 usefulness (no anchored rubric), same photos",
                    "paired": "candidate minus reference, photos resampled jointly; 95% percentile bootstrap",
                    "owner": "owner's blind 1-5 on the review page; agreement vs the Opus rubric score: Spearman "
                             "across the arms of each photo (photos where either side is constant are skipped), "
                             "averaged; pooled Spearman over all (photo, arm) pairs; mean absolute difference"}
    return rep


def render_md(u: dict) -> str:
    f, ci = ca._f, ca._ci
    lines = ["## Usefulness against the rubric (Opus rescore)", "",
             "| arm | n | rubric useful (95% CI) | unanchored useful (95% CI) | paired diff vs reference (95% CI) |",
             "|---|---|---|---|---|"]
    for arm, a in u["arms"].items():
        p = u["paired_vs_reference"].get(arm)
        pd = "reference" if p is None else f"{f(p['diff'])} ({ci(p['ci95'])})"
        lines.append(f"| {arm} | {a['n']} | {f(a['rubric']['mean'])} ({ci(a['rubric']['ci95'])}) | "
                     f"{f(a['unanchored']['mean'])} ({ci(a['unanchored']['ci95'])}) | {pd} |")
    o = u.get("owner")
    if o:
        g = o["agreement"]
        lines += ["", f"### Owner review ({o['photos']} photos)", "",
                  "| arm | n | owner (95% CI) | Opus rubric (95% CI) | Opus unanchored (95% CI) |", "|---|---|---|---|---|"]
        for arm, a in o["arms"].items():
            lines.append(f"| {arm} | {a['n']} | {f(a['owner']['mean'])} ({ci(a['owner']['ci95'])}) | "
                         f"{f(a['opus_rubric']['mean'])} ({ci(a['opus_rubric']['ci95'])}) | "
                         f"{f(a['opus_unanchored']['mean'])} ({ci(a['opus_unanchored']['ci95'])}) |")
        lines += ["", (f"- Owner–Opus Spearman per photo-set: mean {f(g['spearman_per_photo']['mean'])} over "
                      f"{g['spearman_per_photo']['n']} photos; pooled {f(g['spearman_pooled'])}"),
                  f"- Mean absolute difference (owner vs Opus rubric): {f(g['mean_abs_diff'])} over {g['pairs']} pairs"]
    lines += ["", *(f"- {k}: {v}" for k, v in u["rules"].items())]
    return "\n".join(lines) + "\n"
