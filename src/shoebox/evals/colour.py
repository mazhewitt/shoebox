"""Colour/mono heuristic scoring and tuning (tasks 7.2-7.3, .scratch/catalogue/07).

The frame's `colour_guess` comes from `rules.classify_colour` over the CIELAB chroma stats it stores
(`chroma_p95` -> c95, `chroma_median` -> c50, `chroma_mean` -> cmean, `chromatic`, `hue_r`). The header `grey`
flag that also feeds it is not a frame column: a row whose frame guess is `mono` although its stats alone are not
mono under the default thresholds was header-grey, and stays `mono` under any thresholds. A row without stats
keeps its frame `colour_guess` (None for rows the inventory never probed).

1. `audit`: frame guess vs the Opus caption-audit reference (high/medium confidence) on the eval-1k sample.
2. `draw`: a seeded mono-stratified sample (`mono` bucket: mono/toned guesses; `near`: unknown, colour with
   c95 < 45, mono with c95 >= 2) across origin strata, privacy-filtered for provider `claude`, dev/test by a
   seeded hash of the capture group. Labelled later by opus-v2 (label-v2, which never shows `colour_guess`).
3. `tune`: grid over `ColourThresholds` on dev rows: maximise mono recall subject to mono precision >= the gate in
   every origin stratum with at least `min_pred` predicted-mono rows (others: `insufficient`).
4. `test_report`: score the frozen thresholds once on the test half; a second run for the same frozen hash is
   refused unless forced with a reason. Reports hold counts and metrics only.

`toned` is kept separate: the primary metric counts only `mono` as black-and-white on both sides (an Opus `toned`
row predicted `mono` is a false positive); a lenient variant (`toned` counted as mono on both sides) is reported.
"""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, fields, replace
from pathlib import Path

from shoebox import privacy, store
from shoebox.config import REPO_ROOT
from shoebox.evals import queries
from shoebox.runners import rules
from shoebox.runners.rules import (
    DEFAULT_THRESHOLDS,
    ColourThresholds,
    Header,
    classify_colour,
)

STRATA = ("scan", "phone", "camera", "received")
CLASSES = ("mono", "toned", "colour", "unknown")
TRUSTED = ("high", "medium")
TRUSTED_MIN = 0.5  # label-v2 numeric confidence: >= 0.9 clear, 0.5-0.8 leaning (high/medium), < 0.5 a guess
SET_NAME = "colour-mono"
SAMPLE_META = "sample.json"
FROZEN_PATH = "colour-thresholds.json"
REPORT_SUFFIX = "colour-heuristic"
# Fixed record of every test-half scoring (not under --out-dir, so another out dir cannot skip the run-once rule).
LEDGER_PATH = REPO_ROOT / "evals" / "colour-test-ledger.jsonl"
NEAR_COLOUR_C95 = 45.0
DEFAULT_SEED = 20261011
N_MONO = N_NEAR = 150
FLOOR = 25
MIN_PRED = 10
REFERENCE_CANDIDATE = "opus-v2"
GEMINI_CANDIDATE = "gemini-medium-v2"
MONO_C95_GRID = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0)
FRAME_COLUMNS = ("source", "relpath", "asset_id", "kind", "ext", "bytes", "mtime", "make", "model", "software",
                 "chroma_mean", "chroma_median", "chroma_p95", "chromatic", "hue_r", "colour_guess", "group_id",
                 "group_rule")
# Used only when the eval-1k caption audit report json is missing (evals/RECOMMENDATION.md, colour filter gap).
FALLBACK_COMPARISONS = {"gemini-medium-v2": 0.974, "g12b-q4-v2": 1.0}


class AlreadyScored(RuntimeError):
    pass


class NotReady(RuntimeError):
    pass


# ---------- rows ----------


def origin_stratum(row: dict) -> str:
    make, model = str(row.get("make") or "").lower(), str(row.get("model") or "").lower()
    gear = f"{row.get('make') or ''} {row.get('model') or ''} {row.get('software') or ''}"
    ext = str(row.get("ext") or "").lower()
    if make == "seiko epson corp." and "r-d1" in model:
        return "camera"  # before is_scan, whose "epson" would take it
    if queries.is_scan(row) or rules.FILM_SCANNER.search(gear) or rules.FLATBED.search(gear) \
            or rules.GENERIC_SCAN.search(gear):
        return "scan"
    if rules._is_phone(make, model):
        return "phone"
    if make.startswith(rules.MIXED_MAKES) and not rules.MIXED_CAMERA_MODEL.search(model) \
            and ext not in rules.CAMERA_RAW_EXTS:
        return "received"
    if make.startswith(rules.CAMERA_MAKES):
        return "camera"
    if not make and ext in rules.CAMERA_RAW_EXTS:
        return "camera"
    return "received"


def row_stats(row: dict) -> dict | None:
    vals = {"c95": row.get("chroma_p95"), "c50": row.get("chroma_median"), "hue_r": row.get("hue_r")}
    if any(v is None for v in vals.values()):
        return None
    return {**vals, "cmean": row.get("chroma_mean"), "chromatic": row.get("chromatic")}


def guess(row: dict, t: ColourThresholds | None = None) -> str | None:
    s = row_stats(row)
    if t is None or s is None:
        return row.get("colour_guess")
    if row.get("colour_guess") == "mono" and classify_colour(s, Header())[0] != "mono":
        return "mono"  # header-grey (see module doc)
    return classify_colour(s, Header(), t)[0]


def bucket(row: dict) -> str | None:
    g, c95 = row.get("colour_guess"), row.get("chroma_p95")
    if g == "unknown" or (g == "colour" and c95 is not None and c95 < NEAR_COLOUR_C95) \
            or (g == "mono" and c95 is not None and c95 >= DEFAULT_THRESHOLDS.mono_sure_c95):
        return "near"
    return "mono" if g in ("mono", "toned") else None


def read_frame(path: Path) -> list[dict]:
    import pyarrow.parquet as pq

    names = pq.read_schema(path).names
    missing = [c for c in FRAME_COLUMNS if c not in names]
    if missing:
        raise ValueError(f"frame {path} lacks columns {missing}")
    return pq.read_table(path, columns=list(FRAME_COLUMNS)).to_pylist()


def by_asset(rows: list[dict]) -> dict[str, dict]:
    """One image row per asset_id (the first by source, relpath)."""
    out: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: (r.get("source") or "", r.get("relpath") or "")):
        if r.get("asset_id") and r.get("kind", "image") == "image":
            out.setdefault(r["asset_id"], r)
    return out


def rows_for(items: list[dict], frame_rows: list[dict]) -> dict[str, dict]:
    """{asset_id: frame row}: the item's own (source, relpath) row, else the asset's first image row."""
    by_path = {(r.get("source"), r.get("relpath")): r for r in frame_rows}
    ids = by_asset(frame_rows)
    return {it["asset_id"]: by_path.get((it.get("source"), it.get("relpath"))) or ids.get(it["asset_id"]) or {}
            for it in items}


def trusted_label(rec: dict | None) -> str | None:
    """colour_class of an ok label record with confidence >= TRUSTED_MIN (opus-v2 reference, Gemini)."""
    if not rec or rec.get("status") != "ok" or not isinstance(rec.get("output"), dict):
        return None
    out = rec["output"]
    conf = (out.get("confidence") or {}).get("colour_class")
    return out.get("colour_class") if isinstance(conf, int | float) and conf >= TRUSTED_MIN else None


def label_colour(rec: dict | None) -> str | None:
    """colour_class of an ok label record, any confidence (the Gemini side of the combination rule)."""
    if not rec or rec.get("status") != "ok" or not isinstance(rec.get("output"), dict):
        return None
    return rec["output"].get("colour_class")


def load_labels(cfg, candidate: str) -> dict[str, dict]:
    """{asset_id: label record} of a candidate via `runners.label_index`; {} when it has no store yet."""
    from shoebox import runners

    try:
        return runners.label_index(cfg, runners.resolve(candidate))
    except FileNotFoundError:
        return {}


def labelled_rows(items: list[dict], frows: dict[str, dict], refs: dict[str, dict], gem: dict[str, dict]
                  ) -> list[dict]:
    """Rows with a trusted reference: {asset_id, stratum, row, ref, split, gemini}."""
    out = []
    for it in items:
        a = it["asset_id"]
        ref = trusted_label(refs.get(a))
        if ref is None:
            continue
        row = frows.get(a) or {}
        out.append({"asset_id": a, "stratum": origin_stratum(row), "row": row, "ref": ref, "split": it.get("split"),
                    "gemini": label_colour(gem.get(a))})
    return out


def audit_rows(records: list[dict], frows: dict[str, dict], gem: dict[str, dict]) -> list[dict]:
    """The caption-audit sample as tuning rows (split `audit`), high/medium reference only."""
    out = []
    for rec in records:
        ref = rec.get("reference") or {}
        if (ref.get("confidence") or {}).get("colour_class") in TRUSTED:
            row = frows.get(rec["asset_id"]) or {}
            out.append({"asset_id": rec["asset_id"], "stratum": origin_stratum(row), "row": row,
                        "ref": ref.get("colour_class"), "split": "audit", "gemini": label_colour(gem.get(rec["asset_id"]))})
    return out


# ---------- scoring ----------


def _ratio(a: int, b: int):
    return round(a / b, 4) if b else None


def _mono(rows: list[dict], key: str = "guess", lenient: bool = False) -> dict:
    yes = ("mono", "toned") if lenient else ("mono",)
    tp = sum(r[key] in yes and r["ref"] in yes for r in rows)
    fp = sum(r[key] in yes and r["ref"] not in yes for r in rows)
    fn = sum(r[key] not in yes and r["ref"] in yes for r in rows)
    return {"n": len(rows), "tp": tp, "fp": fp, "fn": fn, "predicted": tp + fp, "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn)}


def score(rows: list[dict], key: str = "guess") -> dict:
    """rows: {stratum, guess, ref}. Mono metrics overall and per origin stratum, lenient (toned = mono), and a
    guess x reference confusion table (None guesses as `none`)."""
    conf: dict[str, Counter] = {}
    for r in rows:
        conf.setdefault(str(r[key] or "none"), Counter())[r["ref"]] += 1
    return {"overall": _mono(rows, key), "per_stratum": {s: _mono([r for r in rows if r["stratum"] == s], key)
                                                         for s in STRATA},
            "lenient_toned": _mono(rows, key, lenient=True),
            "confusion": {g: dict(sorted(c.items())) for g, c in sorted(conf.items())}}


def audit(records: list[dict], frows: dict[str, dict], t: ColourThresholds | None = None) -> dict:
    """Frame guess (or `t`) vs the caption-audit reference colour_class at high/medium confidence."""
    rows = []
    for rec in records:
        ref = rec.get("reference") or {}
        if (ref.get("confidence") or {}).get("colour_class") not in TRUSTED:
            continue
        fr = frows.get(rec["asset_id"]) or {}
        rows.append({"stratum": origin_stratum(fr), "guess": guess(fr, t), "ref": ref.get("colour_class")})
    return {"records": len(records), "n_trusted": len(rows), "reference_mono": sum(r["ref"] == "mono" for r in rows),
            "predicted_mono": sum(r["guess"] == "mono" for r in rows),
            "missing_frame_guess": sum(r["guess"] is None for r in rows), "frame": score(rows)}


# ---------- draw ----------


def _h(seed: int, tag: str, key: str) -> str:
    return hashlib.sha256(f"{seed}:{tag}:{key}".encode()).hexdigest()


def split_of(seed: int, group: str) -> str:
    return "dev" if int(_h(seed, "split", group), 16) % 2 == 0 else "test"


def draw(frame_rows: list[dict], derived: set[str], exclude: set[str], cfg, seed: int = DEFAULT_SEED,
         n_mono: int = N_MONO, n_near: int = N_NEAR, floor: int = FLOOR, **privacy_kw) -> tuple[list[dict], dict]:
    """Seeded, privacy-filtered (`provider="claude"`) mono-stratified draw. `privacy_kw` go to
    `privacy.filter_items` (tests); a PrivacyError (e.g. incomplete pixel-hash coverage) propagates."""
    rows = by_asset(frame_rows)
    drop = Counter()
    cands, seen_groups = [], set()
    audit_groups = {queries.group_key(rows[a]) for a in exclude if a in rows}
    for a in sorted(rows, key=lambda a: (_h(seed, "order", a), a)):
        r = rows[a]
        b = bucket(r)
        g = queries.group_key(r)
        if b is None:
            drop["not_in_bucket"] += 1
        elif a not in derived:
            drop["not_derived"] += 1
        elif a in exclude:
            drop["caption_audit_sample"] += 1
        elif g in audit_groups:
            drop["caption_audit_group"] += 1
        elif g in seen_groups:
            drop["same_group"] += 1
        else:
            seen_groups.add(g)
            cands.append(r)
    items = [{"asset_id": r["asset_id"], "source": r.get("source"), "relpath": r.get("relpath"),
              "ext": r.get("ext"), "bytes": r.get("bytes"), "mtime": r.get("mtime"), "group_id": r.get("group_id"),
              "group_rule": r.get("group_rule"), "role": "target"} for r in cands]
    elig = privacy.filter_items(items, cfg, others=privacy.known_items(), provider="claude", **privacy_kw)
    allowed = {it["asset_id"]: it for it in elig.allowed}
    pools: dict[str, dict[str, list[dict]]] = {b: {s: [] for s in STRATA} for b in ("mono", "near")}
    for r in cands:
        if r["asset_id"] in allowed:
            pools[bucket(r)][origin_stratum(r)].append(r)
    out, meta_b = [], {}
    for b, n in (("mono", n_mono), ("near", n_near)):
        sizes = {s: len(v) for s, v in pools[b].items()}
        alloc = (queries._largest_remainder({s: max(v, 1e-9) for s, v in sizes.items()}, min(n, sum(sizes.values())),
                                            floor_min=floor, cap=sizes) if sum(sizes.values()) else
                 dict.fromkeys(STRATA, 0))
        meta_b[b] = {"target": n, "available": sizes, "drawn": alloc,
                     "shortfall": {s: floor - sizes[s] for s in STRATA if sizes[s] < floor}}
        for s in STRATA:
            for r in pools[b][s][:alloc[s]]:
                g = queries.group_key(r)
                out.append({**allowed[r["asset_id"]], "origin": s, "bucket": b, "split": split_of(seed, g),
                            "colour_guess": r.get("colour_guess")})
    out.sort(key=lambda it: (_h(seed, "key", it["asset_id"]), it["asset_id"]))
    out = [{"key": f"m-{i:03d}", **it} for i, it in enumerate(out, 1)]
    meta = {"seed": seed, "n": len(out), "frame_assets": len(rows), "candidates": len(cands),
            "dropped": dict(sorted(drop.items())), "skipped": elig.counts(), "buckets": meta_b,
            "splits": dict(sorted(Counter(it["split"] for it in out).items())),
            "rules": {"mono": "colour_guess in (mono, toned), not near",
                      "near": f"colour_guess unknown, colour with chroma_p95 < {NEAR_COLOUR_C95}, or mono with "
                              f"chroma_p95 >= {DEFAULT_THRESHOLDS.mono_sure_c95}",
                      "allocation": f"largest remainder over available per origin stratum, floor {floor}, capped",
                      "eligibility": "one image row per asset (latest frame), derived infer-v1 ok, "
                                     "privacy.filter_items(provider=claude), not in the caption-audit sample, "
                                     "one per capture group",
                      "split": "dev/test by seeded sha256 of the capture group (group_id or asset_id), 50/50",
                      "reference": f"{REFERENCE_CANDIDATE} label records (label-v2; colour_guess never shown), "
                                   f"colour_class with confidence >= {TRUSTED_MIN}"}}
    return out, meta


# ---------- tune / freeze / test ----------


def _guessed(rows: list[dict], t: ColourThresholds) -> list[dict]:
    return [{"stratum": r["stratum"], "ref": r["ref"], "guess": guess(r["row"], t), "gemini": r.get("gemini")}
            for r in rows]


def strata_status(sc: dict, gate: float, min_pred: int) -> dict:
    out = {}
    for s, m in sc["per_stratum"].items():
        st = "insufficient" if m["predicted"] < min_pred else "pass" if m["precision"] >= gate else "fail"
        out[s] = {**m, "status": st}
    return out


def combination(rows: list[dict]) -> dict:
    """Heuristic mono AND Gemini colour_class != colour, on rows with a Gemini record."""
    have = [r for r in rows if r.get("gemini") is not None]
    comb = [{**r, "combo": "mono" if r["guess"] == "mono" and r["gemini"] != "colour" else "other"} for r in have]
    return {"rows_with_gemini": len(have), **score(comb, "combo")}


def tune(rows: list[dict], gate: float, min_pred: int = MIN_PRED, grid=MONO_C95_GRID,
         base: ColourThresholds = DEFAULT_THRESHOLDS) -> dict:
    """rows: {stratum, row (frame), ref, gemini?}. Only the mono cutoff decides mono, so the grid is over
    `mono_c95` (`mono_sure_c95` follows as min(itself, mono_c95): it sets confidence only)."""
    summary, best = [], None
    for c in grid:
        t = replace(base, mono_c95=c, mono_sure_c95=min(base.mono_sure_c95, c))
        sc = score(_guessed(rows, t))
        st = strata_status(sc, gate, min_pred)
        ok = all(v["status"] != "fail" for v in st.values())
        summary.append({"mono_c95": c, "feasible": ok, "precision": sc["overall"]["precision"],
                        "recall": sc["overall"]["recall"], "predicted": sc["overall"]["predicted"],
                        "strata": {s: v["status"] for s, v in st.items()}})
        if ok and (best is None or (sc["overall"]["recall"] or 0) > (best[1]["overall"]["recall"] or 0)):
            best = (t, sc, st)
    if best is None:
        return {"chosen": None, "grid": summary, "strata": {}, "combination": None, "gate": gate,
                "min_pred": min_pred, "n": len(rows)}
    t, sc, st = best
    return {"chosen": asdict(t), "grid": summary, "dev": sc, "strata": st, "gate": gate, "min_pred": min_pred,
            "n": len(rows), "combination": combination(_guessed(rows, t))}


def thresholds_hash(t: ColourThresholds) -> str:
    return store.params_hash(asdict(t))


def freeze(path: Path, tuned: dict, meta: dict) -> dict:
    if tuned.get("chosen") is None:
        raise ValueError("no feasible thresholds on dev; nothing to freeze (the filter is unavailable)")
    t = ColourThresholds(**tuned["chosen"])
    doc = {"thresholds": asdict(t), "hash": thresholds_hash(t), "gate": tuned["gate"], "min_pred": tuned["min_pred"],
           "dev_strata": {s: v["status"] for s, v in tuned["strata"].items()}, "dev_n": tuned["n"], **meta}
    Path(path).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def load_frozen(path: Path) -> tuple[ColourThresholds, dict]:
    doc = json.loads(Path(path).read_text())
    t = ColourThresholds(**{f.name: doc["thresholds"][f.name] for f in fields(ColourThresholds)})
    if thresholds_hash(t) != doc["hash"]:
        raise ValueError(f"{path}: thresholds do not match their hash {doc['hash']}")
    return t, doc


def prior_test(runs_dir: Path, h: str, ledger: Path) -> str | None:
    """Name of the first test report for frozen hash `h`: from the fixed ledger, else from `runs_dir`."""
    if Path(ledger).is_file():
        for line in Path(ledger).read_text().splitlines():
            if line.strip() and json.loads(line).get("frozen_hash") == h:
                return json.loads(line)["report"] + ".json"
    for p in sorted(Path(runs_dir).glob(f"*_{REPORT_SUFFIX}*.json")):
        if json.loads(p.read_text()).get("frozen_hash") == h:
            return p.name
    return None


def comparisons(audit_json: Path) -> dict:
    p = Path(audit_json)
    if p.is_file():
        st = json.loads(p.read_text()).get("structured") or {}
        out = {a: {"precision": st[a]["overall"]["mono"]["precision"], "source": str(p)} for a in FALLBACK_COMPARISONS
               if a in st}
        if len(out) == len(FALLBACK_COMPARISONS):
            return out
    return {a: {"precision": v, "source": "evals/RECOMMENDATION.md (colour filter gap; constant)"}
            for a, v in FALLBACK_COMPARISONS.items()}


def test_report(rows: list[dict], frozen: Path, runs_dir: Path, day: str, gate: float, min_pred: int = MIN_PRED,
                force_reason: str | None = None, audit: dict | None = None, dev: dict | None = None,
                comparisons: dict | None = None, coverage: tuple[int, int] | None = None,
                ledger: Path | None = None) -> dict:
    """`coverage`: (drawn test items with a reference label record, drawn test items). Refuses, writing
    nothing, unless there are trusted rows and every drawn test item is labelled: the one score is not spent
    on missing labels."""
    ledger = Path(ledger or LEDGER_PATH)
    t, doc = load_frozen(frozen)
    labelled, total = coverage or (len(rows), len(rows))
    if not rows or labelled < total:
        raise NotReady(f"test half not ready: {labelled} of {total} drawn test items labelled, {len(rows)} with a "
                       "trusted reference; nothing scored")
    prior = prior_test(runs_dir, doc["hash"], ledger)
    if force_reason is not None and not force_reason.strip():
        raise ValueError("--force-rescore needs a non-empty reason")
    if prior and force_reason is None:
        raise AlreadyScored(f"the test half was already scored for frozen hash {doc['hash']} ({prior}); "
                            "pass --force-rescore REASON to score it again")
    g = _guessed(rows, t)
    sc = score(g)
    st = strata_status(sc, gate, min_pred)
    rep = {"date": day, "frozen_hash": doc["hash"], "thresholds": doc["thresholds"], "gate": gate,
           "min_pred": min_pred, "test": sc, "strata": st, "combination": combination(g),
           "filter": {s: ("heuristic" if v["status"] == "pass" else "unavailable") for s, v in st.items()},
           "toned": {"default": "separate: toned does not match black-and-white searches",
                     "lenient_toned_test": sc["lenient_toned"]},
           "audit_sample": audit, "dev": dev or {k: doc.get(k) for k in ("dev_strata", "dev_n")},
           "comparisons": comparisons or {}}
    rep["coverage"] = {"labelled": labelled, "total": total}
    if prior:
        rep["force_rescore_reason"], rep["previous"] = force_reason, prior
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    name, k = f"{day}_{REPORT_SUFFIX}", 1
    while (runs_dir / f"{name}.json").exists() or (runs_dir / f"{name}.md").exists():
        k += 1
        name = f"{day}_{REPORT_SUFFIX}-r{k}"
    rep["report"] = name
    stem = runs_dir / name
    stem.with_suffix(".json").write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    stem.with_suffix(".md").write_text(render_md(rep))
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps({"frozen_hash": doc["hash"], "date": day, "report": name, "runs_dir": runs_dir.name,
                            "force_rescore_reason": force_reason if prior else None}, sort_keys=True) + "\n")
    return rep


def _f(v) -> str:
    return "-" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)


def render_md(rep: dict) -> str:
    out = [f"# Colour heuristic: mono filter source ({rep['date']})", "",
           f"Frozen thresholds hash `{rep['frozen_hash']}`: " +
           ", ".join(f"{k} {v}" for k, v in sorted(rep["thresholds"].items())) + ".",
           (f"Gate: mono precision >= {rep['gate']} in every origin stratum; a stratum with fewer than "
           f"{rep['min_pred']} predicted-mono rows is `insufficient` (filter unavailable there). Counts only."), ""]
    if rep.get("force_rescore_reason"):
        out += [f"**Re-scored** (previous: {rep['previous']}): {rep['force_rescore_reason']}", ""]
    out += ["## Test half", "", "| stratum | n | tp | fp | fn | precision | recall | status | filter |",
            "|---|---|---|---|---|---|---|---|---|"]
    for s in STRATA:
        m = rep["strata"][s]
        out.append(f"| {s} | {m['n']} | {m['tp']} | {m['fp']} | {m['fn']} | {_f(m['precision'])} | "
                   f"{_f(m['recall'])} | {m['status']} | {rep['filter'][s]} |")
    o = rep["test"]["overall"]
    out += [(f"| overall | {o['n']} | {o['tp']} | {o['fp']} | {o['fn']} | {_f(o['precision'])} | {_f(o['recall'])} "
            "| | |"), ""]
    c = rep["combination"]["overall"]
    out += [(f"Combination (heuristic mono AND Gemini colour_class != colour; {rep['combination']['rows_with_gemini']}"
            f" rows with a Gemini record): tp {c['tp']}, fp {c['fp']}, fn {c['fn']}, precision {_f(c['precision'])}, "
            f"recall {_f(c['recall'])}."), ""]
    lt = rep["toned"]["lenient_toned_test"]
    out += ["## toned", "", (f"Default: {rep['toned']['default']}. Lenient variant (toned counted as mono on both "
            f"sides): precision {_f(lt['precision'])}, recall {_f(lt['recall'])}."), "", "## Comparison", ""]
    for arm, v in sorted(rep["comparisons"].items()):
        out.append(f"- {arm}: mono precision {_f(v['precision'])} (eval-1k caption audit; source {v['source']})")
    a = rep.get("audit_sample")
    if a and a.get("frame"):
        fo = a["frame"]["overall"]
        out += ["", "## eval-1k audit sample (frame colour_guess)", "",
                (f"tp {fo.get('tp')}, fp {fo.get('fp')}, fn {fo.get('fn')}, precision {_f(fo.get('precision'))}, "
                f"recall {_f(fo.get('recall'))}.")]
    d = rep.get("dev") or {}
    if d.get("grid"):
        out += ["", "## Dev tuning grid", "", "| mono_c95 | feasible | precision | recall | predicted |",
                "|---|---|---|---|---|"]
        out += [f"| {g['mono_c95']} | {g['feasible']} | {_f(g['precision'])} | {_f(g['recall'])} | "
                f"{g['predicted']} |" for g in d["grid"]]
    elif d.get("dev_strata"):
        out += ["", "## Dev", "", f"{d.get('dev_n')} rows; strata " +
                ", ".join(f"{s} {v}" for s, v in sorted(d["dev_strata"].items()))]
    return "\n".join(out) + "\n"
