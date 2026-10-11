"""`pilot sheet`: a self-contained local contact sheet and blind labelling form.

Writes `<shoebox_root>/results/pilot-v0/sheet.html`. Images load from the derived
`infer-v1` JPEGs by relative path; all data (items, every candidate's latest output
from the label store, existing owner labels) is embedded at generation time. Export
downloads `labels.jsonl` (docs/pilot.md section 7).

Label provenance (eval-ground-truth spec, "Label provenance" and "Blind slice"):
- Model outputs start hidden. A fixed pseudo-random blind slice (at least 10% of the
  set, chosen from the asset IDs alone) never shows model outputs.
- Turning outputs on marks every item with outputs as *seen*, for good (kept in
  localStorage). A label edited after its item was seen is never `blind`: it exports as
  `confirmed` or `edited` and names the pre-label's model_id and params_hash. When more
  than one candidate's output was visible, the owner must pick one ("use as pre-label");
  until then the label is held back from export.
"""

import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

from shoebox import store
from shoebox.config import Config
from shoebox.derive import derived_path

PROFILE = "infer-v1"
SET_NAME = "pilot-v0"
ENUMS = {
    "medium": ["digital", "film_negative", "film_slide", "print", "document", "screen", "unknown"],
    "digitiser": [
        "camera", "phone", "film_scanner", "flatbed_scanner", "camera_rephoto", "screenshot", "software", "unknown",
    ],
    "colour_class": ["colour", "mono", "toned", "unknown"],
}
OUTPUT_FIELDS = ("medium", "digitiser", "colour_class")
BLIND_FRACTION = 0.10


def blind_slice(asset_ids: list[str], set_name: str = SET_NAME, fraction: float = BLIND_FRACTION) -> set[str]:
    """A stable pseudo-random `ceil(fraction * n)` subset, fixed before labelling by hashing the IDs."""
    n = math.ceil(len(asset_ids) * fraction)
    ranked = sorted(asset_ids, key=lambda a: hashlib.sha256(f"{set_name}:{a}".encode()).hexdigest())
    return set(ranked[:n])


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _candidates(cfg: Config, wanted: set[str]) -> tuple[list[dict], dict[str, dict[str, dict]]]:
    """Every label store dir `results/labels/<model_dir>/<params_hash>/`, and the record per asset that the
    store treats as the cache hit (`store.index_records`: first final record, else the latest error)."""
    cands: list[dict] = []
    outputs: dict[str, dict[str, dict]] = {}
    for d in store.store_dirs(cfg, "labels"):
        key = f"{d.parent.name}/{d.name}"
        try:
            params = json.loads((d / "params.json").read_text())
        except (OSError, ValueError):
            params = {}
        if not isinstance(params, dict):
            params = {}
        latest = {aid: r for aid, r in store.index_records(store.iter_dir(d)).items() if aid in wanted}
        cands.append({
            "key": key,
            "name": params.get("candidate") or params.get("name") or key,
            "model_id": params.get("model_id") or next((r.get("model_id") for r in latest.values()), None),
            "params_hash": d.name,
        })
        for aid, rec in latest.items():
            out = rec.get("output") if isinstance(rec.get("output"), dict) else {}
            outputs.setdefault(aid, {})[key] = {
                "status": rec.get("status"),
                "caption": out.get("caption"),
                "tags": out.get("tags") or [],
                **{f: out.get(f) for f in OUTPUT_FIELDS},
                "latency_ms": rec.get("latency_ms"),
                "error": rec.get("error"),
                "model_id": rec.get("model_id"),
                "params_hash": rec.get("params_hash") or d.name,
            }
    return cands, outputs


def build_data(cfg: Config, items_path: Path, labels_path: Path, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or cfg.results_dir / SET_NAME
    items = _read_jsonl(items_path)
    # Quarantine (content-screen spec): the sheet may be viewed by Claude, so the hosted rule applies.
    held = _quarantined(cfg, [i["asset_id"] for i in items])
    items = [i for i in items if i["asset_id"] not in held]
    # Screen gate (same spec): an asset with no ok screen record is not shown either.
    gate = _screen_gate(cfg)
    unscreened = {i["asset_id"] for i in items if not gate.screened(i["asset_id"])}
    items = [i for i in items if i["asset_id"] not in unscreened]
    cands, outputs = _candidates(cfg, {i["asset_id"] for i in items})
    blind = blind_slice([i["asset_id"] for i in items])
    rows = []
    for it in items:
        aid = it["asset_id"]
        img = derived_path(cfg, PROFILE, aid)
        rows.append({
            "asset_id": aid,
            "bucket": it.get("bucket"),
            "ext": it.get("ext"),
            "relpath": it.get("relpath"),
            "group_id": it.get("group_id"),
            "group_rule": it.get("group_rule"),
            "img": Path(os.path.relpath(img, out_dir)).as_posix(),
            "blind_slice": aid in blind,
            "outputs": {} if aid in blind else outputs.get(aid, {}),
        })
    return {
        "set": SET_NAME,
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "enums": ENUMS,
        "candidates": cands,
        "items": rows,
        "labels": {lab["asset_id"]: lab for lab in _read_jsonl(labels_path)},
        "left_out": {"quarantined": len(held), "unscreened": len(unscreened)},
    }


def _quarantined(cfg: Config, asset_ids: list[str]) -> set[str]:
    """Assets (or their same-pixel copies) the quarantine refuses for Claude viewing."""
    from shoebox import privacy, quarantine

    chk = quarantine.Checker.load()
    if not chk.rows:
        return set()
    index = privacy.default_copy_index(cfg)
    return {a for a in asset_ids if chk.refused(a, "hosted", index.pixel_group(a))}


def _screen_gate(cfg: Config):
    from shoebox import privacy

    return privacy.default_screen_index(cfg)


def render(data: dict) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return _TEMPLATE.replace("__DATA__", blob)


def write_sheet(cfg: Config, items_path: Path, labels_path: Path, out: Path | None = None,
                left_out: dict | None = None) -> Path:
    """Write the sheet; `left_out`, if given, is updated with the counts left out (e.g. quarantined)."""
    out = out or cfg.results_dir / SET_NAME / "sheet.html"
    data = build_data(cfg, items_path, labels_path, out.parent)
    if left_out is not None:
        left_out.update(data["left_out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data))
    return out


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pilot Contact Sheet</title>
<style>
:root {
  --bg: #f6f5f2; --card: #ffffff; --ink: #1d1d1b; --muted: #6b6a66; --line: #dddbd5;
  --accent: #2f5d8a; --accent-ink: #ffffff; --done: #2e7d4f; --warn: #a8551f; --chip: #eceae4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161615; --card: #22221f; --ink: #ecebe6; --muted: #a09e97; --line: #3a3934;
    --accent: #7fb0e0; --accent-ink: #10202f; --done: #6cc893; --warn: #e79a63; --chip: #2e2d29;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
header { position: sticky; top: 0; z-index: 2; background: var(--bg); border-bottom: 1px solid var(--line);
  padding: 10px 16px; display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; }
header h1 { font-size: 16px; margin: 0; font-weight: 600; }
header .grow { flex: 1; }
.mode { padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.mode.blind { background: var(--chip); color: var(--muted); }
.mode.shown { background: var(--warn); color: var(--bg); }
button, select { font: inherit; color: var(--ink); background: var(--card); border: 1px solid var(--line);
  border-radius: 6px; padding: 4px 10px; cursor: pointer; }
button.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
main { padding: 16px; display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
  display: flex; flex-direction: column; }
.card.labelled { border-color: var(--done); }
.thumb { background: var(--chip); aspect-ratio: 4 / 3; display: flex; align-items: center; justify-content: center; }
.thumb img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
.thumb .missing { color: var(--muted); font-size: 12px; padding: 8px; text-align: center; }
.body { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.meta { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { background: var(--chip); border-radius: 4px; padding: 1px 6px; font-size: 12px; }
.path { color: var(--muted); font-size: 12px; word-break: break-all; }
fieldset { border: 0; margin: 0; padding: 0; }
legend { font-size: 12px; color: var(--muted); padding: 0; margin-bottom: 2px; }
.opts { display: flex; flex-wrap: wrap; gap: 2px 10px; font-size: 13px; }
.opts label { white-space: nowrap; cursor: pointer; }
textarea { width: 100%; font: inherit; color: var(--ink); background: var(--bg); border: 1px solid var(--line);
  border-radius: 6px; padding: 4px 6px; min-height: 2.2em; resize: vertical; }
.status { font-size: 12px; color: var(--muted); }
.status.done { color: var(--done); }
.models { border-top: 1px dashed var(--line); padding-top: 8px; display: none; flex-direction: column; gap: 6px; }
body.show-models .models { display: flex; }
.model { font-size: 12px; }
.model b { font-weight: 600; }
.model .cap { margin: 2px 0; }
.model .err { color: var(--warn); }
.empty { color: var(--muted); padding: 32px 16px; }
@media (max-width: 400px) { main { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>pilot-v0 contact sheet</h1>
  <span id="mode" class="mode blind">blind</span>
  <label><input type="checkbox" id="toggle"> show model outputs</label>
  <select id="bucket"><option value="">all buckets</option></select>
  <span class="grow"></span>
  <span id="progress" class="status"></span>
  <button id="export" class="primary">Export labels.jsonl</button>
</header>
<main id="grid"></main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("data").textContent);
  var KEY = "shoebox-" + DATA.set + "-labels";
  var SEEN_KEY = "shoebox-" + DATA.set + "-seen";
  var FIELDS = ["medium", "digitiser", "colour_class"];
  var ITEMS = {};
  DATA.items.forEach(function (it) { ITEMS[it.asset_id] = it; });

  function fieldsOf(o) {
    var r = {};
    FIELDS.forEach(function (f) { r[f] = (o && o[f]) || null; });
    return r;
  }
  // Bring a stored label (labels.jsonl or an older sheet's localStorage) to the in-page shape:
  // `seen` (made after model outputs were visible), `source` (the named pre-label) and `prelabel`.
  function normalise(l) {
    l = JSON.parse(JSON.stringify(l));
    if (l.seen === undefined) l.seen = !!l.method && l.method !== "blind";
    if (l.source === undefined) {
      l.source = l.model_id && l.params_hash ?
        { candidate: l.candidate || null, model_id: l.model_id, params_hash: l.params_hash } : null;
    }
    if (l.prelabel === undefined) l.prelabel = l.method === "confirmed" ? fieldsOf(l) : null;
    return l;
  }
  var labels = {}, seen = {};
  Object.keys(DATA.labels).forEach(function (id) { labels[id] = normalise(DATA.labels[id]); });
  try {
    var saved = JSON.parse(window.localStorage.getItem(KEY) || "{}");
    Object.keys(saved).forEach(function (id) { labels[id] = normalise(saved[id]); });
    seen = JSON.parse(window.localStorage.getItem(SEEN_KEY) || "{}") || {};
  } catch (e) { /* storage unavailable: start from the embedded labels */ }

  function save() {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(labels));
      window.localStorage.setItem(SEEN_KEY, JSON.stringify(seen));
    } catch (e) { /* ignore */ }
  }
  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function complete(l) { return !!l && FIELDS.every(function (f) { return !!l[f]; }); }
  // Candidates whose output for this item can serve as a pre-label.
  function prelabels(item) {
    return DATA.candidates.map(function (c) {
      var o = item.outputs[c.key];
      if (!o || o.status !== "ok" || !FIELDS.some(function (f) { return !!o[f]; })) return null;
      return { candidate: c.name, model_id: o.model_id || c.model_id, params_hash: o.params_hash || c.params_hash,
               values: fieldsOf(o) };
    }).filter(Boolean);
  }
  function hasOutputs(item) { return Object.keys(item.outputs || {}).length > 0; }
  // How the label was made: blind, or confirmed/edited from a named pre-label. null = seen but unnamed.
  function provenance(l) {
    if (!l.seen) return { method: "blind" };
    var s = l.source;
    if (!s || !s.model_id || !s.params_hash) return null;
    var same = !!l.prelabel && FIELDS.every(function (f) { return l.prelabel[f] === l[f]; });
    return { method: same ? "confirmed" : "edited", candidate: s.candidate || null,
             model_id: s.model_id, params_hash: s.params_hash };
  }
  var showing = false;
  var cards = {};

  function refreshCard(id) {
    var card = cards[id], l = labels[id];
    card.node.classList.toggle("labelled", complete(l));
    var s = card.status;
    if (!l) {
      s.textContent = seen[id] ? "unlabelled · outputs seen" : "unlabelled";
      s.className = "status";
      return;
    }
    var p = provenance(l);
    s.textContent = (complete(l) ? "labelled" : "partly labelled") + " · " +
      (p ? p.method + (p.candidate ? " from " + p.candidate : "") : "outputs seen: pick a pre-label") +
      (l.ts ? " · " + l.ts.replace("T", " ").slice(0, 16) : "");
    s.className = complete(l) && p ? "status done" : "status";
    Object.keys(card.radios).forEach(function (key) {
      var parts = key.split("\u0000");
      card.radios[key].checked = l[parts[0]] === parts[1];
    });
  }
  function refreshProgress() {
    var n = DATA.items.filter(function (it) {
      var l = labels[it.asset_id];
      return complete(l) && !!provenance(l);
    }).length;
    document.getElementById("progress").textContent = n + " / " + DATA.items.length + " labelled";
  }
  function touch(id) {
    var l = labels[id] || { asset_id: id, medium: null, digitiser: null, colour_class: null, notes: "",
                             seen: false, source: null, prelabel: null };
    // Sticky: once this item's outputs were visible, the label can never go back to blind.
    if (seen[id]) l.seen = true;
    if (l.seen && !l.source) {
      var pre = prelabels(ITEMS[id]);
      if (pre.length === 1) { l.source = pre[0]; l.prelabel = pre[0].values; }
    }
    l.labelled_by = "owner";
    l.ts = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    labels[id] = l;
    return l;
  }
  function changed(id) { save(); refreshCard(id); refreshProgress(); }
  function edit(id, field, value) {
    var l = touch(id);
    l[field] = value;
    changed(id);
  }
  function usePrelabel(id, pre) {
    seen[id] = true;
    var l = touch(id);
    l.seen = true;
    l.source = { candidate: pre.candidate, model_id: pre.model_id, params_hash: pre.params_hash };
    l.prelabel = pre.values;
    FIELDS.forEach(function (f) {
      if (pre.values[f] && DATA.enums[f].indexOf(pre.values[f]) >= 0) l[f] = pre.values[f];
    });
    changed(id);
  }

  function modelBlock(item) {
    var box = el("div", { "class": "models" });
    if (item.blind_slice) {
      box.appendChild(el("div", { "class": "model" }, "Blind slice: model outputs are never shown for this item."));
      return box;
    }
    if (!DATA.candidates.length) box.appendChild(el("div", { "class": "model" }, "No model outputs in the store."));
    var pres = {};
    prelabels(item).forEach(function (p) { pres[p.candidate + "\u0000" + p.params_hash] = p; });
    DATA.candidates.forEach(function (c) {
      var o = item.outputs[c.key];
      var m = el("div", { "class": "model" });
      m.appendChild(el("b", {}, c.name));
      if (!o) { m.appendChild(el("span", {}, " — no output")); box.appendChild(m); return; }
      m.appendChild(el("span", {}, " · " + (o.status || "?") +
        (o.latency_ms !== null && o.latency_ms !== undefined ? " · " + o.latency_ms + " ms" : "")));
      if (o.caption) m.appendChild(el("div", { "class": "cap" }, o.caption));
      var facts = FIELDS.map(function (f) { return f + ": " + (o[f] || "—"); }).join(" · ");
      m.appendChild(el("div", {}, facts));
      if (o.tags && o.tags.length) m.appendChild(el("div", {}, "tags: " + o.tags.join(", ")));
      if (o.error) m.appendChild(el("div", { "class": "err" }, String(o.error)));
      var pre = pres[c.name + "\u0000" + (o.params_hash || c.params_hash)];
      if (pre) {
        var use = el("button", { type: "button", "data-candidate": c.name }, "use as pre-label");
        use.addEventListener("click", function () { usePrelabel(item.asset_id, pre); });
        m.appendChild(use);
      }
      box.appendChild(m);
    });
    return box;
  }

  function card(item) {
    var id = item.asset_id;
    var node = el("article", { "class": "card", "data-bucket": item.bucket || "" });
    var thumb = el("div", { "class": "thumb" });
    var img = el("img", { src: item.img, alt: item.relpath || id, loading: "lazy" });
    img.addEventListener("error", function () {
      thumb.textContent = "";
      thumb.appendChild(el("div", { "class": "missing" }, "no derived image yet"));
    });
    thumb.appendChild(img);
    node.appendChild(thumb);
    var body = el("div", { "class": "body" });
    var meta = el("div", { "class": "meta" });
    meta.appendChild(el("span", { "class": "chip" }, item.bucket));
    meta.appendChild(el("span", { "class": "chip" }, item.ext));
    if (item.group_id) meta.appendChild(el("span", { "class": "chip" }, item.group_id + " (" + item.group_rule + ")"));
    if (item.blind_slice) meta.appendChild(el("span", { "class": "chip" }, "blind slice"));
    body.appendChild(meta);
    body.appendChild(el("div", { "class": "path" }, item.relpath));
    var l = labels[id] || {};
    var radios = {};
    FIELDS.forEach(function (f) {
      var fs = el("fieldset");
      fs.appendChild(el("legend", {}, f));
      var opts = el("div", { "class": "opts" });
      DATA.enums[f].forEach(function (v) {
        var lab = el("label");
        var r = el("input", { type: "radio", name: id + ":" + f, value: v });
        if (l[f] === v) r.checked = true;
        r.addEventListener("change", function () { edit(id, f, v); });
        radios[f + "\u0000" + v] = r;
        lab.appendChild(r);
        lab.appendChild(document.createTextNode(" " + v));
        opts.appendChild(lab);
      });
      fs.appendChild(opts);
      body.appendChild(fs);
    });
    var notes = el("textarea", { placeholder: "notes", rows: "1" });
    notes.value = l.notes || "";
    notes.addEventListener("input", function () { edit(id, "notes", notes.value); });
    body.appendChild(notes);
    var status = el("div", { "class": "status" });
    body.appendChild(status);
    body.appendChild(modelBlock(item));
    node.appendChild(body);
    cards[id] = { node: node, status: status, radios: radios };
    return node;
  }

  var grid = document.getElementById("grid");
  if (!DATA.items.length) grid.appendChild(el("div", { "class": "empty" }, "No items."));
  DATA.items.forEach(function (it) { grid.appendChild(card(it)); refreshCard(it.asset_id); });
  refreshProgress();

  var buckets = [];
  DATA.items.forEach(function (it) { if (buckets.indexOf(it.bucket) < 0) buckets.push(it.bucket); });
  var sel = document.getElementById("bucket");
  buckets.forEach(function (b) { sel.appendChild(el("option", { value: b }, b)); });
  sel.addEventListener("change", function () {
    Object.keys(cards).forEach(function (id) {
      var n = cards[id].node;
      n.style.display = !sel.value || n.getAttribute("data-bucket") === sel.value ? "" : "none";
    });
  });

  var toggle = document.getElementById("toggle"), mode = document.getElementById("mode");
  function refreshMode() {
    var n = Object.keys(seen).filter(function (id) { return seen[id]; }).length;
    mode.textContent = showing ? "model outputs shown" :
      "blind" + (n ? " (outputs seen earlier for " + n + " item" + (n === 1 ? "" : "s") + ")" : "");
    mode.className = "mode " + (showing ? "shown" : "blind");
  }
  toggle.checked = false;
  refreshMode();
  toggle.addEventListener("change", function () {
    showing = toggle.checked;
    if (showing) {
      DATA.items.forEach(function (it) {
        if (!it.blind_slice && hasOutputs(it)) seen[it.asset_id] = true;
      });
      save();
      DATA.items.forEach(function (it) { refreshCard(it.asset_id); });
    }
    document.body.classList.toggle("show-models", showing);
    refreshMode();
  });

  document.getElementById("export").addEventListener("click", function () {
    var lines = [], partial = 0, unnamed = 0;
    DATA.items.forEach(function (it) {
      var l = labels[it.asset_id];
      if (!l) return;
      if (!complete(l)) { partial++; return; }
      var p = provenance(l);
      if (!p) { unnamed++; return; }
      var rec = {
        asset_id: it.asset_id, medium: l.medium, digitiser: l.digitiser, colour_class: l.colour_class,
        notes: l.notes || "", labelled_by: l.labelled_by || "owner", method: p.method, ts: l.ts || null
      };
      if (p.method !== "blind") {
        rec.model_id = p.model_id;
        rec.params_hash = p.params_hash;
        rec.candidate = p.candidate;
      }
      lines.push(JSON.stringify(rec));
    });
    var blob = new Blob([lines.length ? lines.join("\n") + "\n" : ""], { type: "application/x-ndjson" });
    var a = el("a", { href: URL.createObjectURL(blob), download: "labels.jsonl" });
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    if (partial || unnamed) {
      alert(lines.length + " labels exported; " + partial + " partly labelled item(s) left out; " + unnamed +
        " item(s) labelled after model outputs were shown left out until you pick the pre-label used.");
    }
  });
})();
</script>
</body>
</html>
"""
