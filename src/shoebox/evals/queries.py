"""Synthetic query set tooling (design D9, tasks 4.1-4.2).

- `write_sources`: a stratified, seeded draw of known-item source photos, each with an assigned style.
- `write_packets`: per-batch JSON packets (with 768px thumbnails) for the Claude Code subagents that write queries.
- `corpus_summary`: a markdown summary of a set (no file names) for the category writer.
- `ingest`: validate the writers' JSONL, store each query keyed like any runner's output, write
  `queries-v1.jsonl` with relevance and dev/test split, and `QUERIES.md`.

Era rule: the capture year is read from the frame `date` only when `date_kind` is `exif` or `filename`. A `scan`
date is when the film or print was scanned, and an `mtime` date is when the file was last written, so both (and a
missing date) count as era `unknown`.
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from shoebox import derive, privacy, store
from shoebox.config import REPO_ROOT, Config
from shoebox.evals.pool import _folder

STYLE_MIX = {"vague_recall": 0.20, "visual": 0.25, "attribute": 0.20, "place_time": 0.15, "people_role": 0.10,
             "text": 0.10}
FACETS = ("subject", "scene", "event", "attribute", "place_time", "combo")
SOURCES_FILE = "query-sources.jsonl"
SOURCES_META = "query-sources.json"
QUERIES_FILE = "queries-v1.jsonl"
QUERIES_MD = "QUERIES.md"
PROMPTS_DIR = REPO_ROOT / "config" / "prompts"
PROMPT_FILES = {"known_item": "query-writer-v1.md", "category": "category-writer-v1.md"}
NOTES_PATH = REPO_ROOT / "evals" / "sets" / "pilot-v0-pool5k" / "search-known-item.jsonl"
FRAME_COLUMNS = ("source", "relpath", "asset_id", "date", "date_kind", "make", "model", "software", "colour_guess")
DATED_KINDS = ("exif", "filename")
IMAGE_DESC = "infer-v1 derived, 768px thumbnail"
WRITER = "Claude Code subagent"
THUMB_PX = 768
DEV_FRACTION = 1 / 3

_SCAN_MODEL = re.compile(r"coolscan|ls-\d+|scanner|epson|perfection|noritsu|frontier", re.IGNORECASE)
_SCAN_SOFTWARE = re.compile(r"vuescan|silverfast|nikon scan|epson scan", re.IGNORECASE)
CAMERA_CODE = re.compile(r"\b[a-z]{1,4}_?\d{3,}\b")


def is_scan(row: dict) -> bool:
    """A film/print scan, by scanner make/model or scanning software in the frame row."""
    hw = f"{row.get('make') or ''} {row.get('model') or ''}"
    return bool(_SCAN_MODEL.search(hw) or _SCAN_SOFTWARE.search(str(row.get("software") or "")))


def capture_year(row: dict) -> int | None:
    """The capture year, only for `exif`/`filename` dates (see the module doc)."""
    s = str(row.get("date") or "")
    if row.get("date_kind") in DATED_KINDS and len(s) >= 4 and s[:4].isdigit():
        return int(s[:4])
    return None


def era(row: dict) -> str:
    y = capture_year(row)
    if y is None:
        return "unknown"
    if y < 1990:
        return "<1990"
    if y < 2000:
        return "1990s"
    if y < 2010:
        return "2000-09"
    if y < 2015:
        return "2010-14"
    if y < 2020:
        return "2015-19"
    return "2020+"


def stratum(row: dict) -> str:
    colour = "mono" if row.get("colour_guess") == "mono" else "other"
    return f"{era(row)}|{colour}|{'scan' if is_scan(row) else 'noscan'}"


def _order(keys, seed: int) -> list[str]:
    return sorted(keys, key=lambda a: (hashlib.sha256(f"{seed}:{a}".encode()).hexdigest(), a))


def _largest_remainder(sizes: dict[str, float], n: int, floor_min: int = 0, cap: dict | None = None
                       ) -> dict[str, int]:
    total = sum(sizes.values())
    quota = {k: n * v / total for k, v in sizes.items()}
    out = {k: max(floor_min, int(q)) for k, q in quota.items()}
    if cap:
        out = {k: min(v, cap[k]) for k, v in out.items()}
    by_rem = sorted(sizes, key=lambda k: (-(quota[k] - int(quota[k])), k))
    while sum(out.values()) < n:
        grown = False
        for k in by_rem:
            if sum(out.values()) >= n:
                break
            if cap is None or out[k] < cap[k]:
                out[k] += 1
                grown = True
        if not grown:
            break
    while sum(out.values()) > n:  # the minimum over-allocated: take back where the share is most exceeded
        k = max((k for k in out if out[k] > floor_min), key=lambda k: (out[k] - quota[k], k), default=None)
        if k is None:
            break
        out[k] -= 1
    return out


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Proportional allocation of n over non-empty strata: largest remainder, at least 1 each, capped at size."""
    sizes = {k: v for k, v in sizes.items() if v > 0}
    if len(sizes) > n:
        raise ValueError(f"{len(sizes)} non-empty strata but n={n}: can't give each at least one")
    if sum(sizes.values()) < n:
        raise ValueError(f"only {sum(sizes.values())} candidates for n={n}")
    return _largest_remainder(sizes, n, floor_min=1, cap=sizes)


def assign_styles(n: int, seed: int) -> list[str]:
    """Exactly the STYLE_MIX share of n per style (largest remainder), in a seeded random order."""
    counts = _largest_remainder(dict(STYLE_MIX), n)
    styles = [s for s in STYLE_MIX for _ in range(counts[s])]
    perm = np.random.default_rng(seed).permutation(len(styles))
    return [styles[int(i)] for i in perm]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def _jsonl(rows) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def read_frame(path: Path) -> list[dict]:
    names = pq.read_schema(path).names
    missing = [c for c in FRAME_COLUMNS if c not in names]
    if missing:
        raise ValueError(f"frame {path} lacks columns {missing}")
    return pq.read_table(path, columns=list(FRAME_COLUMNS)).to_pylist()


def frame_rows_for(items: list[dict], frame_path: Path) -> dict[str, dict]:
    """{asset_id: frame row}: the row at the item's own (source, relpath), else any row for the asset."""
    rows = read_frame(frame_path)
    by_path = {(r["source"], r["relpath"]): r for r in rows}
    by_id: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: (r["source"] or "", r["relpath"] or "")):
        by_id.setdefault(r["asset_id"], r)
    out = {}
    for it in items:
        r = by_path.get((it.get("source"), it.get("relpath"))) or by_id.get(it["asset_id"])
        out[it["asset_id"]] = r or {}
    return out


def group_key(it: dict) -> str:
    return it.get("group_id") or it["asset_id"]


@dataclass
class Sources:
    path: Path
    sidecar: Path
    rows: list[dict]
    meta: dict


def draw_sources(items: list[dict], frows: dict[str, dict], n: int, seed: int) -> tuple[list[dict], dict]:
    # one candidate per capture group: the group's first member in the seeded order
    seen, cands = set(), []
    by_id = {it["asset_id"]: it for it in items}
    for aid in _order(by_id, seed):
        g = group_key(by_id[aid])
        if g not in seen:
            seen.add(g)
            cands.append(aid)
    strata: dict[str, list[str]] = {}
    for aid in cands:  # cands are already in seeded order, so each stratum list is too
        strata.setdefault(stratum(frows.get(aid, {})), []).append(aid)
    alloc = allocate({k: len(v) for k, v in strata.items()}, n)
    chosen = [(aid, s) for s, ids in sorted(strata.items()) for aid in ids[:alloc[s]]]
    chosen = sorted(chosen, key=lambda t: (hashlib.sha256(f"{seed}:key:{t[0]}".encode()).hexdigest(), t[0]))
    styles = assign_styles(len(chosen), seed)
    rows = []
    for i, ((aid, s), style) in enumerate(zip(chosen, styles, strict=True), 1):
        it, fr = by_id[aid], frows.get(aid, {})
        rel = Path(it["relpath"])
        rows.append({"key": f"s-{i:03d}", "asset_id": aid, "group_id": it.get("group_id"),
                     "style_assigned": style, "stratum": s, "date": fr.get("date"),
                     "folder": rel.parent.as_posix(), "filename": rel.name})
    meta = {"seed": seed, "n": len(rows), "mix": STYLE_MIX, "candidates": len(cands), "items": len(items),
            "strata": {s: {"candidates": len(strata[s]), "drawn": alloc[s]} for s in sorted(strata)},
            "styles": dict(sorted(Counter(r["style_assigned"] for r in rows).items())),
            "rules": {"stratum": "era|colour|scan", "era": "capture year from frame date when date_kind is exif or "
                      "filename; scan, mtime or missing dates are unknown", "colour": "frame colour_guess mono vs "
                      "other", "scan": "make/model matches coolscan|ls-N|scanner|epson|perfection|noritsu|frontier (lab scanners) or software "
                      "matches vuescan|silverfast|nikon scan|epson scan",
                      "groups": "at most one source per capture group (group_id, else asset_id)"}}
    return rows, meta


def write_sources(set_dir: Path, frame_path: Path, n: int, seed: int) -> Sources:
    set_dir = Path(set_dir)
    items = _read_jsonl(set_dir / "items.jsonl")
    rows, meta = draw_sources(items, frame_rows_for(items, frame_path), n, seed)
    path, sidecar = set_dir / SOURCES_FILE, set_dir / SOURCES_META
    path.write_text(_jsonl(rows))
    sidecar.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return Sources(path, sidecar, rows, meta)


# ---------- packets ----------

@dataclass
class Packets:
    paths: list[Path]
    skipped: list[dict] = field(default_factory=list)


def write_packets(cfg: Config, set_dir: Path, out_dir: Path, batch: int = 25, others: list[dict] = (),
                  never: privacy.NeverSend | None = None, profile: str = "infer-v1") -> Packets:
    """Batch JSON files for the query writers. Items Claude may not see are refused (listed in `skipped`)."""
    set_dir, out_dir = Path(set_dir), Path(out_dir)
    items = {it["asset_id"]: it for it in _read_jsonl(set_dir / "items.jsonl")}
    sources = _read_jsonl(set_dir / SOURCES_FILE)
    elig = privacy.filter_items([items[s["asset_id"]] for s in sources], cfg, never=never, others=others,
                                provider="claude")
    bad = {s.asset_id: s.reason for s in elig.skipped}
    thumbs = out_dir / "images"
    thumbs.mkdir(parents=True, exist_ok=True)
    import pyvips

    entries, skipped = [], []
    for s in sources:
        if s["asset_id"] in bad:
            skipped.append({"key": s["key"], "reason": bad[s["asset_id"]]})
            continue
        src = derive.derived_path(cfg, profile, s["asset_id"])
        if not src.exists():
            skipped.append({"key": s["key"], "reason": "no_derived"})
            continue
        dst = thumbs / f"{s['key']}.jpg"
        pyvips.Image.thumbnail(str(src), THUMB_PX).write_to_file(str(dst), Q=85, strip=True)
        entries.append({"key": s["key"], "style_assigned": s["style_assigned"], "date": s["date"],
                        "folder": s["folder"], "filename": s["filename"], "image": str(dst)})
    paths = []
    for i in range(0, len(entries), batch):
        p = out_dir / f"batch-{i // batch + 1:03d}.json"
        p.write_text(json.dumps(entries[i:i + batch], indent=2, ensure_ascii=False) + "\n")
        paths.append(p)
    return Packets(paths, skipped)


# ---------- corpus summary ----------

def _md_table(head: tuple[str, str], counter, limit: int | None = None, by_key: bool = False) -> list[str]:
    items = sorted(counter.items(), key=(lambda kv: str(kv[0])) if by_key else (lambda kv: (-kv[1], str(kv[0]))))
    rows = [f"| {head[0]} | {head[1]} |", "|---|---|", *(f"| {k} | {v} |" for k, v in items[:limit])]
    return [*rows, ""]


def corpus_summary(set_dir: Path, frame_path: Path) -> str:
    """Markdown summary of a set for the category writer: folders, years, scans, mono, camera makes."""
    items = _read_jsonl(Path(set_dir) / "items.jsonl")
    frows = frame_rows_for(items, frame_path)
    folders = Counter(_folder(it["relpath"]) for it in items)
    years = Counter(capture_year(frows[it["asset_id"]]) for it in items)
    undated = years.pop(None, 0)
    scans = sum(is_scan(frows[it["asset_id"]]) for it in items)
    mono = sum(frows[it["asset_id"]].get("colour_guess") == "mono" for it in items)
    makes = Counter((frows[it["asset_id"]].get("make") or "(none)").strip() for it in items)
    out = [f"# Archive summary ({len(items)} photos)", "",
           f"Scans: {scans} (film or print scans, by scanner or scanning software)",
           f"Mono: {mono} (black and white)",
           f"Undated: {undated} (no capture date, or only a scan or file date)", "",
           "## Top folders", "", *_md_table(("folder", "photos"), folders, 40),
           "## Capture years", "", *_md_table(("year", "photos"), years, by_key=True),
           "## Camera makes", "", *_md_table(("make", "photos"), makes, 15)]
    return "\n".join(out)


# ---------- ingest ----------

class IngestError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__(f"{len(errors)} error(s)")
        self.errors = errors


def _check_text(where: str, q, max_words: int) -> list[str]:
    if not isinstance(q, str) or not q.strip():
        return [f"{where}: empty query"]
    errs = []
    if "\n" in q or "\r" in q:
        errs.append(f"{where}: newline in query")
    if q != q.lower():
        errs.append(f"{where}: not lowercase")
    words = len(q.split())
    if not 1 <= words <= max_words:
        errs.append(f"{where}: {words} words (1-{max_words})")
    if CAMERA_CODE.search(q.lower()):
        errs.append(f"{where}: camera code token")
    return errs


def validate_known(lines: list[dict], sources: list[dict]) -> list[str]:
    errs = []
    want = {s["key"]: s for s in sources}
    seen = Counter(x.get("key") for x in lines)
    errs += [f"{k}: missing" for k in want if seen[k] == 0]
    errs += [f"{k}: appears {c} times" for k, c in seen.items() if c > 1]
    errs += [f"{k}: unknown key" for k in seen if k not in want]
    for x in lines:
        k = x.get("key")
        if k not in want:
            continue
        sa, su, fb = x.get("style_assigned"), x.get("style_used"), x.get("fallback_reason")
        if sa != want[k]["style_assigned"]:
            errs.append(f"{k}: style_assigned {sa!r} is not the assigned {want[k]['style_assigned']!r}")
        if su not in STYLE_MIX:
            errs.append(f"{k}: style_used {su!r} not a style")
        elif fb:
            if su != "visual":
                errs.append(f"{k}: fallback must use visual, not {su!r}")
        elif su != sa:
            errs.append(f"{k}: style_used {su!r} differs from {sa!r} without a fallback_reason")
        errs += _check_text(k, x.get("query"), 12)
    return errs


def validate_category(lines: list[dict]) -> list[str]:
    errs = []
    seen: Counter = Counter()
    texts: Counter = Counter()
    for x in lines:
        n = x.get("n")
        where = f"category {n}"
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            errs.append(f"{where}: n must be a positive integer")
        elif seen[n]:
            errs.append(f"{where}: duplicate n")
        seen[n] += 1
        if x.get("facet") not in FACETS:
            errs.append(f"{where}: facet {x.get('facet')!r} not one of {list(FACETS)}")
        errs += _check_text(where, x.get("query"), 8)
        if isinstance(x.get("query"), str):
            if texts[x["query"]]:
                errs.append(f"{where}: duplicate query text")
            texts[x["query"]] += 1
    return errs


def query_id(kind: str, source: str | None, text: str) -> str:
    return "q-" + hashlib.sha256(f"{kind}|{source or ''}|{text}".encode()).hexdigest()[:12]


def _split_keys(keys: list[str], seed: int, pinned: set[str] = frozenset()) -> dict[str, str]:
    """dev for pinned keys, then seeded-order keys until about a third are dev; test for the rest."""
    keys = sorted(set(keys))
    target = round(len(keys) * DEV_FRACTION)
    out = {k: "dev" for k in keys if k in pinned}
    for k in _order([k for k in keys if k not in pinned], seed):
        out[k] = "dev" if sum(v == "dev" for v in out.values()) < target else "test"
    return out


def check_splits(rows: list[dict], groups: dict[str, str | None]) -> None:
    """Raise if a capture group (group_id, else asset_id) holds relevant items of both dev and test queries."""
    by: dict[str, set] = {"dev": set(), "test": set()}
    for r in rows:
        if r.get("split") in by:
            by[r["split"]] |= {groups.get(a) or a for a in r["relevant"]}
    both = by["dev"] & by["test"]
    if both:
        raise ValueError(f"capture groups in both dev and test: {sorted(both)[:5]}")


@dataclass
class Ingested:
    params_hash: str
    path: Path
    md_path: Path
    rows: list[dict]
    appended: int


def ingest(cfg: Config, set_dir: Path, known_paths: list[Path], category_path: Path, model_id: str, seed: int,
           dev_pin: Path | None = None, notes_path: Path | None = NOTES_PATH,
           prompts_dir: Path = PROMPTS_DIR, now: str | None = None) -> Ingested:
    set_dir = Path(set_dir)
    items = {it["asset_id"]: it for it in _read_jsonl(set_dir / "items.jsonl")}
    sources = _read_jsonl(set_dir / SOURCES_FILE)
    meta_path = set_dir / SOURCES_META
    mix = json.loads(meta_path.read_text())["mix"] if meta_path.exists() else STYLE_MIX
    known = [x for p in known_paths for x in _read_jsonl(p)]
    category = _read_jsonl(category_path)
    errors = validate_known(known, sources) + validate_category(category)
    if errors:
        raise IngestError(errors)

    prompts = {name: (Path(prompts_dir) / name).read_text() for name in PROMPT_FILES.values()}
    params = {"prompts": prompts, "style_mix": mix, "seed": seed, "n": len(sources), "image": IMAGE_DESC,
              "writer": WRITER}
    ph = store.params_hash(params)
    store.write_params(cfg, model_id, ph, {**params, "model_id": model_id}, kind="queries")
    have = store.index(cfg, model_id, ph, kind="queries")
    ts = now or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_key = {s["key"]: s for s in sources}
    appended = 0
    pending = []
    for x in known:
        s = by_key[x["key"]]
        pending.append((s["asset_id"], {"query": x["query"], "kind": "known_item", "key": x["key"],
                                        "style_assigned": x["style_assigned"], "style_used": x["style_used"],
                                        "fallback_reason": x.get("fallback_reason") or None}))
    for x in category:
        pending.append((f"category-{x['n']:03d}", {"query": x["query"], "kind": "category", "n": x["n"],
                                                   "facet": x["facet"]}))
    for aid, output in pending:
        if store.is_final(have.get(aid)):
            continue
        store.append(cfg, {"asset_id": aid, "model_id": model_id, "params_hash": ph, "status": "ok",
                           "output": output, "ts": ts}, kind="queries")
        appended += 1
    recs = store.index(cfg, model_id, ph, kind="queries")

    pinned: set[str] = set()
    if dev_pin:
        for it in _read_jsonl(dev_pin):
            pinned |= {it["asset_id"], group_key(it)}
    prov = {"model_id": model_id, "params_hash": ph}
    rows = []
    known_groups = {}
    for s in sources:
        out = recs[s["asset_id"]]["output"]
        it = items[s["asset_id"]]
        g = it.get("group_id")
        mates = sorted(a for a, o in items.items() if g and o.get("group_id") == g and a != s["asset_id"])
        known_groups[s["asset_id"]] = group_key(it)
        rows.append({"query_id": query_id("known_item", s["asset_id"], out["query"]), "text": out["query"],
                     "kind": "known_item", "style": out["style_used"], "style_assigned": out["style_assigned"],
                     "fallback_reason": out["fallback_reason"], "source_asset_id": s["asset_id"],
                     "relevant": [s["asset_id"], *mates], "split": None,
                     "provenance": {**prov, "prompt": f"config/prompts/{PROMPT_FILES['known_item']}"}})
    pin_groups = {g for a, g in known_groups.items() if a in pinned or g in pinned}
    ksplit = _split_keys(list(known_groups.values()), seed, pin_groups)
    for r in rows:
        r["split"] = ksplit[known_groups[r["source_asset_id"]]]
    cat_keys = sorted(a for a in recs if a.startswith("category-"))
    csplit = _split_keys(cat_keys, seed)
    for a in cat_keys:
        out = recs[a]["output"]
        rows.append({"query_id": query_id("category", None, out["query"]), "text": out["query"],
                     "kind": "category", "facet": out["facet"], "source_asset_id": None, "relevant": [],
                     "split": csplit[a],
                     "provenance": {**prov, "prompt": f"config/prompts/{PROMPT_FILES['category']}"}})
    if notes_path and Path(notes_path).exists():
        rel_notes = Path(notes_path)
        try:
            rel_notes = rel_notes.relative_to(REPO_ROOT)
        except ValueError:
            pass
        for note in _read_jsonl(notes_path):
            rel = [a for a in note.get("relevant", []) if a in items]
            if not rel:
                continue
            rows.append({"query_id": query_id("owner_note", None, note["text"]), "text": note["text"],
                         "kind": "owner_note", "source_asset_id": None, "relevant": rel, "split": "owner",
                         "provenance": {"writer": "owner", "file": rel_notes.as_posix(),
                                        "pilot_query_id": note.get("query_id")}})
    check_splits(rows, {a: it.get("group_id") for a, it in items.items()})

    path = set_dir / QUERIES_FILE
    path.write_text(_jsonl(rows))
    md = set_dir / QUERIES_MD
    md.write_text(queries_md(rows, model_id, ph, seed))
    return Ingested(ph, path, md, rows, appended)


def queries_md(rows: list[dict], model_id: str, ph: str, seed: int) -> str:
    kinds = Counter(r["kind"] for r in rows)
    styles = Counter(r["style"] for r in rows if r["kind"] == "known_item")
    facets = Counter(r["facet"] for r in rows if r["kind"] == "category")
    splits = Counter((r["kind"], r["split"]) for r in rows)
    fallbacks = sum(1 for r in rows if r.get("fallback_reason"))
    out = ["# Query set queries-v1", "",
           (f"Known-item and category queries written by `{model_id}` ({WRITER}), params_hash `{ph}`, from "
           f"`config/prompts/{PROMPT_FILES['known_item']}` and `config/prompts/{PROMPT_FILES['category']}`. "
           f"Owner notes come from the pilot (writer: owner). Split seed {seed}: dev/test about 1:2 by the source "
           "photo's capture group; owner notes are their own split."), "",
           *_md_table(("kind", "queries"), kinds),
           "## Known-item styles (used)", "", *_md_table(("style", "queries"), styles),
           f"Fallbacks to visual: {fallbacks}", "",
           "## Category facets", "", *_md_table(("facet", "queries"), facets),
           "## Splits", "", *_md_table(("kind / split", "queries"),
                                        {f"{k} / {s}": v for (k, s), v in splits.items()}, by_key=True),
           "owner realism check: pending", ""]
    return "\n".join(out)
