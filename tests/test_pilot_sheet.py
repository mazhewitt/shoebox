"""Tests for the pilot contact sheet / blind labelling page generator."""

import json
import re
from pathlib import Path

from shoebox import config
from shoebox.pilot.sheet import ENUMS, build_data, write_sheet

AID1 = "ab" + "1" * 62
AID2 = "cd" + "2" * 62


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def setup(tmp_path: Path) -> tuple[config.Config, Path, Path]:
    toml = tmp_path / "shoebox.toml"
    toml.write_text(
        f'shoebox_root = "{tmp_path / "sb"}"\n'
        f'[sources.fake]\nroot = "{tmp_path / "src"}"\nperson = "owner"\nsynced = true\nhosted_consent = false\n'
    )
    cfg = config.load(toml)
    items = tmp_path / "set" / "items.jsonl"
    write_jsonl(items, [
        {"asset_id": AID1, "source": "fake", "relpath": "P/a.DNG", "ext": "dng", "bytes": 1, "mtime": 0.0,
         "bucket": "pair", "group_id": "g-ab1111111111", "group_rule": "raw_jpeg_basename"},
        {"asset_id": AID2, "source": "fake", "relpath": "P/b.jpg", "ext": "jpg", "bytes": 1, "mtime": 0.0,
         "bucket": "phone", "group_id": None, "group_rule": None},
    ])
    labels_root = cfg.results_dir / "labels"
    store = labels_root / "ollama__gemma4_e4b" / "0123456789abcdef"
    (store / "params.json").parent.mkdir(parents=True)
    (store / "params.json").write_text(json.dumps({"candidate": "gemma-e4b", "model_id": "ollama/gemma4:e4b"}))
    out = {"caption": "A beach </script><b>x</b>", "tags": ["beach"], "medium": "digital",
           "digitiser": "camera", "colour_class": "colour", "confidence": {}}
    write_jsonl(store / "part-0000.jsonl", [
        {"asset_id": AID1, "model_id": "ollama/gemma4:e4b", "params_hash": "0123456789abcdef", "status": "error",
         "output": None, "raw": "", "error": "timeout", "latency_ms": 5},
        {"asset_id": AID1, "model_id": "ollama/gemma4:e4b", "params_hash": "0123456789abcdef", "status": "ok",
         "output": out, "raw": "", "error": None, "latency_ms": 1234},
    ])
    bare = labels_root / "rules-v1" / "fedcba9876543210"
    write_jsonl(bare / "part-0000.jsonl", [
        {"asset_id": AID2, "model_id": "rules-v1", "params_hash": "fedcba9876543210", "status": "ok",
         "output": {"caption": None, "tags": [], "medium": "digital", "digitiser": "phone",
                    "colour_class": "unknown"}, "latency_ms": 1},
    ])
    labels = tmp_path / "set" / "labels.jsonl"
    write_jsonl(labels, [
        {"asset_id": AID2, "medium": "digital", "digitiser": "phone", "colour_class": "colour", "notes": "",
         "labelled_by": "owner", "method": "blind", "ts": "2026-10-01T00:00:00Z"},
    ])
    return cfg, items, labels


def embedded(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.DOTALL)
    assert m
    return json.loads(m.group(1))


def test_build_data_discovers_candidates_and_latest_records(tmp_path, monkeypatch):
    monkeypatch.setattr("shoebox.pilot.sheet.blind_slice", lambda ids: set())
    cfg, items, labels = setup(tmp_path)
    data = build_data(cfg, items, labels)
    names = {c["name"] for c in data["candidates"]}
    assert names == {"gemma-e4b", "rules-v1/fedcba9876543210"}
    gemma = next(c for c in data["candidates"] if c["name"] == "gemma-e4b")
    first = data["items"][0]
    assert first["img"] == f"../../derived/infer-v1/ab/{AID1}.jpg"
    o = first["outputs"][gemma["key"]]
    assert o["status"] == "ok" and o["latency_ms"] == 1234 and o["medium"] == "digital"
    assert data["labels"][AID2]["method"] == "blind"
    assert data["enums"] == ENUMS


def test_write_sheet_is_self_contained(tmp_path, monkeypatch):
    cfg, items, labels = setup(tmp_path)
    path = write_sheet(cfg, items, labels)
    assert path == cfg.results_dir / "pilot-v0" / "sheet.html"
    html = path.read_text()
    assert not re.search(r"(src|href)=[\"']?(https?:)?//", html)
    assert "@import" not in html and "<link" not in html
    data = embedded(html)
    assert len(data["items"]) == 2
    assert "</script><b>" not in html.split('id="data">', 1)[1].split("</script>", 1)[0]
    for field in ("medium", "digitiser", "colour_class"):
        for value in ENUMS[field]:
            assert value in html
    assert "localStorage" in html and "labels.jsonl" in html


def test_write_sheet_tolerates_missing_labels_and_stores(tmp_path):
    cfg, items, _ = setup(tmp_path)
    import shutil

    shutil.rmtree(cfg.results_dir / "labels")
    path = write_sheet(cfg, items, tmp_path / "nope.jsonl", out=tmp_path / "x" / "s.html")
    data = embedded(path.read_text())
    assert data["candidates"] == [] and data["labels"] == {}
    assert data["items"][0]["img"].endswith(f"derived/infer-v1/ab/{AID1}.jpg")



def test_sheet_script_parses_with_node(tmp_path):
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    cfg, items, labels = setup(tmp_path)
    html = write_sheet(cfg, items, labels).read_text()
    script = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    js = tmp_path / "sheet.js"
    js.write_text(script)
    subprocess.run([node, "--check", str(js)], check=True)


# A minimal DOM for running the sheet's script in node (no jsdom needed). `run(steps)` loads the page
# script against fresh stub elements sharing one localStorage, so a second `load()` is a page reload.
_STUB = r"""
const vm = require("vm");
const fs = require("fs");
const [scriptPath, dataPath] = process.argv.slice(2);
const SCRIPT = fs.readFileSync(scriptPath, "utf8");
const DATA_TEXT = fs.readFileSync(dataPath, "utf8");
const store = {};
const out = {};
class ClassList {
  constructor() { this.s = new Set(); }
  add(c) { this.s.add(c); } remove(c) { this.s.delete(c); } contains(c) { return this.s.has(c); }
  toggle(c, f) { if (f === undefined) f = !this.s.has(c); if (f) this.s.add(c); else this.s.delete(c); return f; }
}
let page;
class Node {
  constructor(tag) {
    this.tagName = tag; this.attrs = {}; this.children = []; this.listeners = {};
    this.classList = new ClassList(); this.style = {}; this._text = ""; this.value = ""; this.checked = false;
    page.all.push(this);
  }
  setAttribute(k, v) {
    v = String(v); this.attrs[k] = v;
    if (k === "class") this.className = v;
    if (k === "href") this.href = v;
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  fire(t) { (this.listeners[t] || []).forEach(f => f({ target: this })); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text; }
  set className(v) { this.classList = new ClassList(); v.split(" ").filter(Boolean).forEach(c => this.classList.add(c)); }
  get className() { return [...this.classList.s].join(" "); }
  click() { this.fire("click"); }
  remove() {}
}
function load() {
  page = { all: [], alerts: [], blob: null };
  const byId = {};
  for (const id of ["grid", "progress", "toggle", "mode", "bucket", "export"]) byId[id] = new Node("x");
  byId.data = new Node("script"); byId.data.textContent = DATA_TEXT;
  const body = new Node("body");
  const document = {
    body, createElement: t => new Node(t), createTextNode: t => ({ text: t }),
    getElementById: id => byId[id],
  };
  const ctx = {
    document, JSON, Object, Date, setTimeout: () => 0,
    window: { localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } } },
    Blob: class { constructor(parts) { page.blob = parts.join(""); } },
    URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
    alert: m => page.alerts.push(m),
  };
  vm.runInNewContext(SCRIPT, ctx);
  page.byId = byId; page.body = body;
  return page;
}
function radio(id, field, value) {
  const r = page.all.find(n => n.attrs.type === "radio" && n.attrs.name === id + ":" + field && n.attrs.value === value);
  r.checked = true; r.fire("change");
}
function label(id, m, d, c) { radio(id, "medium", m); radio(id, "digitiser", d); radio(id, "colour_class", c); }
const ITEMS = JSON.parse(DATA_TEXT).items;
const CANDS = JSON.parse(DATA_TEXT).candidates;
function notes(id, text) {
  const t = page.all.filter(n => n.tagName === "textarea")[ITEMS.findIndex(it => it.asset_id === id)];
  t.value = text; t.fire("input");
}
function toggle(on) { page.byId.toggle.checked = on; page.byId.toggle.fire("change"); }
function usePre(id, candidate) {
  // "use as pre-label" buttons are created card by card in item order, one per usable output
  const key = CANDS.find(c => c.name === candidate).key;
  const owners = ITEMS.filter(it => it.outputs[key] && it.outputs[key].status === "ok").map(it => it.asset_id);
  page.all.filter(n => n.tagName === "button" && n.attrs["data-candidate"] === candidate)[owners.indexOf(id)].click();
}
function exported() {
  page.alerts = []; page.byId.export.click();
  const recs = {};
  (page.blob || "").split("\n").filter(Boolean).map(JSON.parse).forEach(r => { recs[r.asset_id] = r; });
  return { recs, alerts: page.alerts };
}
__STEPS__
process.stdout.write(JSON.stringify(out));
"""

_GEMMA = {"key": "ollama__g/1111", "name": "gemma", "model_id": "ollama/gemma", "params_hash": "1111"}
_RULES = {"key": "rules-v1/2222", "name": "rules", "model_id": "rules-v1", "params_hash": "2222"}


def _out(model_id: str, ph: str, medium: str, digitiser: str, colour: str) -> dict:
    return {"status": "ok", "caption": "c", "tags": [], "medium": medium, "digitiser": digitiser,
            "colour_class": colour, "latency_ms": 1, "error": None, "model_id": model_id, "params_hash": ph}


def _sheet_data() -> dict:
    g = _out("ollama/gemma", "1111", "digital", "camera", "colour")
    r = _out("rules-v1", "2222", "digital", "phone", "mono")
    item = {"bucket": "b", "ext": "jpg", "relpath": "p", "group_id": None, "group_rule": None, "img": "x.jpg"}
    return {
        "set": "pilot-v0", "generated": "", "enums": ENUMS, "candidates": [_GEMMA, _RULES],
        "items": [
            {**item, "asset_id": "X1", "blind_slice": False, "outputs": {_GEMMA["key"]: g}},
            {**item, "asset_id": "X2", "blind_slice": False, "outputs": {_GEMMA["key"]: g, _RULES["key"]: r}},
            {**item, "asset_id": "X3", "blind_slice": True, "outputs": {}},
            {**item, "asset_id": "X4", "blind_slice": False, "outputs": {_GEMMA["key"]: g}},
            {**item, "asset_id": "X5", "blind_slice": False, "outputs": {_GEMMA["key"]: g}},
        ],
        "labels": {
            "X4": {"asset_id": "X4", "medium": "digital", "digitiser": "camera", "colour_class": "colour",
                   "notes": "", "labelled_by": "owner", "method": "blind", "ts": "2026-10-01T00:00:00Z"},
            "X5": {"asset_id": "X5", "medium": "digital", "digitiser": "camera", "colour_class": "colour",
                   "notes": "", "labelled_by": "owner", "method": "confirmed", "ts": "2026-10-01T00:00:00Z",
                   "model_id": "ollama/gemma", "params_hash": "1111", "candidate": "gemma"},
        },
    }


def _run_sheet(tmp_path: Path, steps: str) -> dict:
    import shutil
    import subprocess

    import pytest

    from shoebox.pilot.sheet import render

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = render(_sheet_data())
    (tmp_path / "page.js").write_text(html.rsplit("<script>", 1)[1].split("</script>", 1)[0])
    data = html.split('id="data">', 1)[1].split("</script>", 1)[0]
    (tmp_path / "data.json").write_text(data)
    (tmp_path / "run.js").write_text(_STUB.replace("__STEPS__", steps))
    res = subprocess.run([node, str(tmp_path / "run.js"), str(tmp_path / "page.js"), str(tmp_path / "data.json")],
                         check=True, capture_output=True, text=True)
    return json.loads(res.stdout)


def test_sheet_provenance_is_sticky_and_named(tmp_path):
    out = _run_sheet(tmp_path, """
load();
out.startHidden = !page.body.classList.contains("show-models");
out.mode0 = page.byId.mode.textContent;
label("X1", "print", "flatbed_scanner", "mono");           // blind, before any outputs were seen
out.e1 = exported();
toggle(true); out.shown = page.body.classList.contains("show-models"); toggle(false);
out.mode1 = page.byId.mode.textContent;
label("X2", "film_negative", "film_scanner", "colour");    // outputs of two candidates were seen
label("X3", "print", "camera_rephoto", "toned");           // blind slice: never seen
radio("X1", "medium", "digital");                          // X1 edited after seeing gemma's output
out.e2 = exported();
usePre("X2", "rules");                                     // owner names the pre-label used
out.e3 = exported();
radio("X2", "colour_class", "colour");
out.e4 = exported();
load();                                                    // reload: "seen" survives
notes("X4", "now with a note");
out.e5 = exported();
out.progress = page.byId.progress.textContent;
""")
    assert out["startHidden"] and out["shown"] and out["mode0"] == "blind"
    assert "outputs seen earlier for 4 items" in out["mode1"]

    e1 = out["e1"]["recs"]
    assert e1["X1"]["method"] == "blind" and "model_id" not in e1["X1"]
    assert e1["X4"]["method"] == "blind" and e1["X5"]["method"] == "confirmed"

    e2, alerts = out["e2"]["recs"], out["e2"]["alerts"]
    assert e2["X1"]["method"] == "edited"                   # never downgraded to blind after toggling off
    assert (e2["X1"]["model_id"], e2["X1"]["params_hash"], e2["X1"]["candidate"]) == ("ollama/gemma", "1111", "gemma")
    assert "X2" not in e2 and any("1 item(s) labelled after model outputs were shown" in a for a in alerts)
    assert e2["X3"]["method"] == "blind"
    assert e2["X4"]["method"] == "blind"                    # labelled blind before outputs were seen, not edited
    assert e2["X5"] == e1["X5"]
    assert all(r["labelled_by"] == "owner" and r["ts"] for r in e2.values())

    x2 = out["e3"]["recs"]["X2"]
    assert (x2["method"], x2["model_id"], x2["params_hash"]) == ("confirmed", "rules-v1", "2222")
    assert (x2["medium"], x2["digitiser"], x2["colour_class"]) == ("digital", "phone", "mono")
    assert out["e4"]["recs"]["X2"]["method"] == "edited"

    x4 = out["e5"]["recs"]["X4"]
    assert x4["method"] == "confirmed" and x4["model_id"] == "ollama/gemma" and x4["notes"] == "now with a note"
    assert out["e5"]["recs"]["X1"]["method"] == "edited" and out["e5"]["recs"]["X3"]["method"] == "blind"
    assert out["progress"] == "5 / 5 labelled"


def test_build_data_blind_slice_hides_outputs(tmp_path):
    from shoebox.pilot.sheet import blind_slice

    ids = [f"{i:064x}" for i in range(45)]
    chosen = blind_slice(ids)
    assert len(chosen) == 5 and chosen == blind_slice(list(reversed(ids)))
    assert len(blind_slice(ids[:2])) == 1 and blind_slice([]) == set()

    cfg, items, labels = setup(tmp_path)
    data = build_data(cfg, items, labels)
    flagged = [r for r in data["items"] if r["blind_slice"]]
    assert len(flagged) == 1 and flagged[0]["outputs"] == {}
    other = next(r for r in data["items"] if not r["blind_slice"])
    assert all(o["model_id"] and o["params_hash"] for o in other["outputs"].values())


def test_build_data_uses_the_store_cache_rule(tmp_path, monkeypatch):
    """The sheet shows the record the store treats as the cache hit: the first final one, not the latest."""
    monkeypatch.setattr("shoebox.pilot.sheet.blind_slice", lambda ids: set())
    cfg, items, labels = setup(tmp_path)
    store = cfg.results_dir / "labels" / "ollama__gemma4_e4b" / "0123456789abcdef"
    later = {"asset_id": AID1, "model_id": "ollama/gemma4:e4b", "params_hash": "0123456789abcdef",
             "status": "ok", "output": {"caption": "later", "tags": [], "medium": "print",
                                        "digitiser": "flatbed_scanner", "colour_class": "mono"},
             "latency_ms": 9}
    with open(store / "part-0000.jsonl", "a") as f:
        f.write(json.dumps(later) + "\n" + '{"asset_id": "torn')
    data = build_data(cfg, items, labels)
    o = data["items"][0]["outputs"]["ollama__gemma4_e4b/0123456789abcdef"]
    assert o["medium"] == "digital" and o["latency_ms"] == 1234


def test_build_data_ignores_embedding_stores(tmp_path, monkeypatch):
    monkeypatch.setattr("shoebox.pilot.sheet.blind_slice", lambda ids: set())
    cfg, items, labels = setup(tmp_path)
    emb = cfg.results_dir / "embeddings" / "ollama__embeddinggemma-2_740m-bf16" / "aaaaaaaaaaaaaaaa"
    write_jsonl(emb / "part-0000.jsonl", [{"asset_id": AID1, "model_id": "ollama/embeddinggemma-2:740m-bf16",
                                           "params_hash": "a" * 16, "status": "ok", "output": {"image": {}}}])
    data = build_data(cfg, items, labels)
    assert {c["key"].split("/")[0] for c in data["candidates"]} == {"ollama__gemma4_e4b", "rules-v1"}
