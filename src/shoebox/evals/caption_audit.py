"""Opus caption audit (task 6.5; the caption part of D11).

Opus runs as Claude Code subagents (no API key): this module builds what they read and stores what they write.

1. `draw`: 200 photos of the set, stratified by `queries.stratum` (era|mono|scan). Photos whose capture group
   holds a dev-split query of queries-v1 are excluded (the dev photos were used to tune label-v2). One candidate
   per capture group (the group's first member in a seeded order, as `queries.draw_sources`). Allocation is
   largest remainder over *weighted* stratum sizes, at least one per stratum, capped at the stratum size: a
   stratum that is mono or scan weighs `OVERWEIGHT` (x2; a mono scan also x2, not x4), so those photos are
   over-represented relative to the corpus. Per-stratum numbers in the report are unweighted within a stratum.
2. `packets`: per photo a 1024 px thumbnail of the derived infer-v1 image and its five caption sets, labelled
   A..E in a seeded per-photo shuffle (blind: no arm names, no asset_ids in batch files); `map.json` keeps
   key -> {label: arm}. An arm with no ok record shows `caption: null` and is listed in `packets.json`.
3. `ingest`: validates every `audit-*.jsonl` line, un-blinds via `map.json`, stores one keyed record per photo
   (kind `caption_audits`), idempotent. The structured fields each arm showed are copied from the batch files
   (never the caption text), so the report scores exactly what the auditor saw.
4. `build_report`: per-arm error counts with photo-bootstrap CIs, paired differences vs the reference arm,
   structured-field accuracy against the Opus reference (high/medium confidence only), mono precision/recall,
   rotate_cw on rotated vs upright photos. Ids and counts only: no caption, description or note text.
"""

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from shoebox import derive, privacy, store
from shoebox.config import REPO_ROOT, Config
from shoebox.evals import queries
from shoebox.evals.audit import check_out_dir

ARMS = ("e4b-bf16", "g12b-q4", "e4b-bf16-v2", "g12b-q4-v2", "gemini-medium-v2")
REFERENCE_ARM = "gemini-medium-v2"
OPUS_ARM = "opus-v2"  # Claude Opus captions: a sixth arm option (`--arms`), not in the default five
ARM_OPTIONS = (*ARMS, OPUS_ARM)
LABELS = "ABCDEF"  # five arms use A-E exactly as before; a sixth gets F
SELF_AUDIT_NOTE = "self-audit — not used for the rule"
SAMPLE_FILE = "caption-audit-sample.jsonl"
SAMPLE_META = "caption-audit-sample.json"
DEFAULT_N = 200
DEFAULT_SEED = 20261014
OVERWEIGHT = 2
KIND = "caption_audits"
MODEL_ID = "claude/opus-5.5"
WRITER = "Claude Code subagent"
PRESENTATION = "1024px thumbnail"
AUDIT_VERSION = "caption-audit-1"
PROMPT_FILE = "caption-audit-v1.md"
PROMPTS_DIR = REPO_ROOT / "config" / "prompts"
SCHEMA = REPO_ROOT / "config" / "schemas" / "label-v1.json"
THUMB_PX = 1024
PROVIDER = "claude"
BOOTSTRAP = 2000
FIELDS = ("medium", "digitiser", "colour_class", "rotate_cw")
CONFIDENCE = ("high", "medium", "low")
TRUSTED = ("high", "medium")
ROTATIONS = (0, 90, 180, 270)
COUNTS = ("wrong", "invented", "missing")
MONO_PRECISION_GATE = 0.98
ERROR_RATE_MARGIN = 0.02


def instructions_text() -> str:
    return (PROMPTS_DIR / PROMPT_FILE).read_text()


def enums() -> dict[str, list[str]]:
    props = json.loads(SCHEMA.read_text())["properties"]
    return {f: list(props[f]["enum"]) for f in ("medium", "digitiser", "colour_class")}


# ---------- sample ----------


def dev_groups(qs: list[dict], items: list[dict]) -> set[str]:
    """Capture groups of the dev split's source and relevant photos."""
    by_id = {it["asset_id"]: it for it in items}
    out = set()
    for q in qs:
        if q.get("split") != "dev":
            continue
        for a in [q.get("source_asset_id"), *(q.get("relevant") or [])]:
            if a:
                out.add(queries.group_key(by_id[a]) if a in by_id else a)
    return out


def _weight(s: str) -> int:
    return OVERWEIGHT if "|mono|" in s or s.endswith("|scan") else 1


def draw(items: list[dict], frows: dict[str, dict], qs: list[dict], n: int = DEFAULT_N,
         seed: int = DEFAULT_SEED) -> tuple[list[dict], dict]:
    dev = dev_groups(qs, items)
    by_id = {it["asset_id"]: it for it in items}
    seen, cands = set(), []
    for a in queries._order(by_id, seed):
        g = queries.group_key(by_id[a])
        if g in dev or g in seen:
            continue
        seen.add(g)
        cands.append(a)
    strata: dict[str, list[str]] = {}
    for a in cands:
        strata.setdefault(queries.stratum(frows.get(a, {})), []).append(a)
    sizes = {k: len(v) for k, v in strata.items()}
    queries.allocate(sizes, n)  # the same feasibility checks (n >= strata, enough candidates)
    alloc = queries._largest_remainder({k: v * _weight(k) for k, v in sizes.items()}, n, floor_min=1, cap=sizes)
    chosen = [(a, s) for s, ids in sorted(strata.items()) for a in ids[:alloc[s]]]
    chosen.sort(key=lambda t: (hashlib.sha256(f"{seed}:caption:{t[0]}".encode()).hexdigest(), t[0]))
    rows = [{"key": f"c-{i:03d}", "asset_id": a, "stratum": s} for i, (a, s) in enumerate(chosen, 1)]
    meta = {"seed": seed, "n": len(rows), "items": len(items), "candidates": len(cands),
            "excluded_dev_groups": len(dev),
            "strata": {s: {"candidates": sizes[s], "weight": _weight(s), "drawn": alloc[s]} for s in sorted(sizes)},
            "rules": {"stratum": "queries.stratum: era|colour|scan", "groups": "one photo per capture group",
                      "dev": "photos in a capture group of a dev-split query (queries-v1) are excluded",
                      "weights": f"largest remainder over stratum size x {OVERWEIGHT} when the stratum is mono "
                                 "or scan (x2 once, not per flag), at least 1 per stratum, capped at its size"}}
    return rows, meta


# ---------- packets ----------


def load_arms(cfg: Config, arms=ARMS, resolve=None) -> tuple[dict[str, dict[str, dict]], dict[str, dict]]:
    """({arm: {asset_id: label record}}, {arm: label source}) via `runners.resolve` and `label_index`."""
    from shoebox import runners

    resolve = resolve or runners.resolve
    labels, sources = {}, {}
    for arm in arms:
        res = resolve(arm)
        labels[arm] = runners.label_index(cfg, res)
        sources[arm] = runners.label_source(res)
    return labels, sources


def blind_order(key: str, arms, seed: int) -> list[str]:
    order = sorted(arms)
    random.Random(f"{seed}:{key}").shuffle(order)
    return order


def _shown(rec: dict | None, arm: str) -> dict:
    out = (rec or {}).get("output") if (rec or {}).get("status") == "ok" else None
    if not out or out.get("caption") is None:
        return {"caption": None, "tags": [], "medium": None, "digitiser": None, "colour_class": None,
                "rotate_cw": None, "note": "no caption from this set"}
    return {"caption": out["caption"], "tags": list(out.get("tags") or []), "medium": out.get("medium"),
            "digitiser": out.get("digitiser"), "colour_class": out.get("colour_class"),
            "rotate_cw": out.get("rotate_cw") if "rotate_cw" in out else None}


@dataclass
class Packets:
    photos: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {key, reason}
    missing: list[dict] = field(default_factory=list)  # {key, arm}
    batches: list[Path] = field(default_factory=list)


def packets(cfg: Config, rows: list[dict], items: list[dict], labels: dict[str, dict[str, dict]], out: Path,
            per_batch: int = 20, seed: int = DEFAULT_SEED, others: list[dict] = (), sources: dict | None = None,
            profile: str = "infer-v1") -> Packets:
    out = Path(out)
    check_out_dir(out)
    arms = sorted(labels)
    if len(arms) > len(LABELS):
        raise ValueError(f"{len(arms)} arms but only {len(LABELS)} labels")
    by_id = {it["asset_id"]: it for it in items}
    elig = privacy.filter_items([by_id[r["asset_id"]] for r in rows if r["asset_id"] in by_id], cfg,
                                others=list(others), provider=PROVIDER)
    allowed = {it["asset_id"] for it in elig.allowed}
    reasons = {s.asset_id: s.reason for s in elig.skipped}
    res = Packets()
    (out / "images").mkdir(parents=True, exist_ok=True)
    import pyvips

    listed, mapping = [], {}
    for r in rows:
        k, a = r["key"], r["asset_id"]
        src = derive.derived_path(cfg, profile, a)
        if a not in allowed:
            res.skipped.append({"key": k, "reason": reasons.get(a, "not_in_set")})
            continue
        if not src.is_file():
            res.skipped.append({"key": k, "reason": "no_derived"})
            continue
        img = out / "images" / f"{k}.jpg"
        pyvips.Image.thumbnail(str(src), THUMB_PX).write_to_file(str(img), Q=85, strip=True)
        caps = []
        mapping[k] = {}
        for lab, arm in zip(LABELS, blind_order(k, arms, seed), strict=False):
            mapping[k][lab] = arm
            shown = _shown(labels[arm].get(a), arm)
            if shown["caption"] is None:
                res.missing.append({"key": k, "arm": arm})
            caps.append({"label": lab, **shown})
        listed.append({"key": k, "image": str(img.resolve()), "captions": caps})
        res.photos.append(k)
    (out / "INSTRUCTIONS.md").write_text(instructions_text())
    for b in range(0, len(listed), per_batch):
        n = b // per_batch + 1
        path = out / f"batch-{n:03d}.json"
        path.write_text(json.dumps({"batch": n, "instructions": "INSTRUCTIONS.md", "answer_file": f"audit-{n:03d}.jsonl",
                                    "photos": listed[b:b + per_batch]}, indent=1, ensure_ascii=False))
        res.batches.append(path)
    (out / "map.json").write_text(json.dumps(mapping, indent=1, sort_keys=True))
    (out / "packets.json").write_text(json.dumps({"seed": seed, "profile": profile, "thumb_px": THUMB_PX,
                                                  "arms": arms, "sources": sources or {}, "photos": len(listed),
                                                  "skipped": res.skipped, "missing": res.missing}, indent=1))
    return res


# ---------- ingest ----------


class IngestError(ValueError):
    pass


def audit_params(text: str, seed: int) -> dict:
    return {"instructions_sha256": hashlib.sha256(text.encode()).hexdigest(), "instructions": PROMPT_FILE,
            "presentation": PRESENTATION, "seed": seed, "writer": WRITER, "audit_version": AUDIT_VERSION}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_line(row, labels: list[str], en: dict[str, list[str]]) -> list[str]:
    errs = []
    ref = row.get("reference")
    if not isinstance(ref, dict):
        return ["reference missing"]
    if not isinstance(ref.get("description"), str) or not ref["description"].strip():
        errs.append("reference.description empty")
    for f, allowed in en.items():
        if ref.get(f) not in allowed:
            errs.append(f"reference.{f} {ref.get(f)!r} not in {allowed}")
    if not _is_int(ref.get("rotate_cw")) or ref["rotate_cw"] not in ROTATIONS:
        errs.append(f"reference.rotate_cw {ref.get('rotate_cw')!r} not in {list(ROTATIONS)}")
    conf = ref.get("confidence")
    for f in FIELDS:
        if not isinstance(conf, dict) or conf.get(f) not in CONFIDENCE:
            errs.append(f"reference.confidence.{f} not in {list(CONFIDENCE)}")
    sets = row.get("sets")
    if not isinstance(sets, dict) or sorted(sets) != sorted(labels):
        return [*errs, f"sets {sorted(sets) if isinstance(sets, dict) else sets!r} are not {sorted(labels)}"]
    for lab in labels:
        s = sets[lab]
        if not isinstance(s, dict):
            errs.append(f"set {lab} not an object")
            continue
        for c in COUNTS:
            if not _is_int(s.get(c)) or s[c] < 0:
                errs.append(f"set {lab}.{c} {s.get(c)!r} not an int >= 0")
        if not _is_int(s.get("useful")) or not 1 <= s["useful"] <= 5:
            errs.append(f"set {lab}.useful {s.get('useful')!r} not an int 1-5")
        if "notes" in s and not isinstance(s["notes"], str):
            errs.append(f"set {lab}.notes not a string")
    return errs


def read_audits(d: Path, mapping: dict[str, dict]) -> dict[str, dict]:
    """`{key: line}` from `d/audit-*.jsonl`. Every mapped key exactly once; raises IngestError listing every
    problem."""
    en = enums()
    out, errors, seen = {}, [], {}
    for path in sorted(Path(d).glob("audit-*.jsonl")):
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
            errors += [f"{where}: {k}: {e}" for e in validate_line(row, sorted(mapping[k]), en)]
            out[k] = row
    errors += [f"{k}: no audit line" for k in sorted(set(mapping) - set(seen))]
    if errors:
        raise IngestError("; ".join(errors))
    return out


@dataclass
class IngestResult:
    params_hash: str
    stored: int = 0
    already: int = 0
    conflicts: list[str] = field(default_factory=list)


def _shown_fields(d: Path) -> dict[tuple[str, str], dict | None]:
    """{(key, label): the structured fields shown (None for a missing set)} from the batch files."""
    out = {}
    for path in sorted(Path(d).glob("batch-*.json")):
        for p in json.loads(path.read_text())["photos"]:
            for c in p["captions"]:
                out[(p["key"], c["label"])] = None if c["caption"] is None else {f: c.get(f) for f in FIELDS}
    return out


def ingest(cfg: Config, d: Path, sample_rows: list[dict], text: str, model_id: str = MODEL_ID) -> IngestResult:
    """Validate everything first (nothing is stored if any line is bad), then append one ok record per photo
    not already final in the store."""
    d = Path(d)
    mapping = json.loads((d / "map.json").read_text())
    meta = json.loads((d / "packets.json").read_text())
    lines = read_audits(d, mapping)
    shown = _shown_fields(d)
    by_key = {r["key"]: r for r in sample_rows}
    unknown = sorted(k for k in mapping if k not in by_key)
    if unknown:
        raise IngestError(f"keys not in the sample: {unknown[:10]}")
    params = audit_params(text, int(meta["seed"]))
    ph = store.params_hash(params)
    res = IngestResult(ph)
    have = store.index(cfg, model_id, ph, KIND)
    store.write_params(cfg, model_id, ph, {"candidate": "opus-caption-audit", "model_id": model_id,
                                           "params_hash": ph, "params": params}, KIND)
    for k in sorted(lines):
        row, a = lines[k], by_key[k]["asset_id"]
        sets = {arm: {**{f: row["sets"][lab][f] for f in (*COUNTS, "useful")},
                      "notes": row["sets"][lab].get("notes", ""), "label": lab, "shown": shown.get((k, lab))}
                for lab, arm in mapping[k].items()}
        rec = {"asset_id": a, "model_id": model_id, "params_hash": ph, "status": "ok", "kind": KIND, "key": k,
               "stratum": by_key[k].get("stratum"), "reference": row["reference"], "sets": sets,
               "arm_sources": meta.get("sources") or {}, "writer": WRITER}
        old = have.get(a)
        if store.is_final(old):
            res.already += 1
            if old.get("sets") != sets or old.get("reference") != row["reference"]:
                res.conflicts.append(k)
            continue
        store.append(cfg, rec, KIND)
        have[a] = rec
        res.stored += 1
    return res


def audit_records(cfg: Config, model_id: str = MODEL_ID, params_hash: str | None = None
                  ) -> tuple[str, list[dict]]:
    if params_hash is None:
        dirs = [p.name for p in (cfg.results_dir / KIND / store.model_dir(model_id)).glob("*") if p.is_dir()]
        if len(dirs) != 1:
            raise ValueError(f"{len(dirs)} params dirs for {model_id}; pass --params-hash ({sorted(dirs)})")
        params_hash = dirs[0]
    idx = store.index(cfg, model_id, params_hash, KIND)
    return params_hash, sorted((r for r in idx.values() if r.get("status") == "ok"), key=lambda r: r["key"])


# ---------- report ----------


def _r(v):
    return None if v is None else round(v, 4)


def _pct(vals: list[float]) -> list[float] | None:
    if not vals:
        return None
    vals = sorted(vals)
    return [_r(vals[int(0.025 * (len(vals) - 1))]), _r(vals[int(0.975 * (len(vals) - 1))])]


def _mean_ci(xs: list[float], b: int, rng: random.Random) -> dict:
    if not xs:
        return {"mean": None, "ci95": None}
    n = len(xs)
    boots = [sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(b)]
    return {"mean": _r(sum(xs) / n), "ci95": _pct(boots)}


def _metrics(s: dict) -> dict[str, float]:
    return {"wrong": s["wrong"], "invented": s["invented"], "missing": s["missing"], "useful": s["useful"],
            "error_rate": 1.0 if s["wrong"] + s["invented"] > 0 else 0.0}


NO_CAPTION = {"wrong": None, "invented": None, "missing": None, "useful": 1, "error_rate": 1.0}


def _ratio(num: int, den: int):
    return _r(num / den) if den else None


def _structured(rows: list[tuple[dict, dict]]) -> dict:
    """rows: (reference, shown fields) for photos where the arm had a caption."""
    out = {}
    for f in ("medium", "digitiser", "colour_class"):
        pairs = [(ref[f], sh.get(f)) for ref, sh in rows
                 if ref["confidence"][f] in TRUSTED and sh.get(f) is not None]
        ok = sum(a == b for a, b in pairs)
        out[f] = {"n": len(pairs), "correct": ok, "accuracy": _ratio(ok, len(pairs))}
    pairs = [(ref["colour_class"], sh.get("colour_class")) for ref, sh in rows
             if ref["confidence"]["colour_class"] in TRUSTED and sh.get("colour_class") is not None]
    tp = sum(a == "mono" and b == "mono" for a, b in pairs)
    fp = sum(a != "mono" and b == "mono" for a, b in pairs)
    fn = sum(a == "mono" and b != "mono" for a, b in pairs)
    prec = _ratio(tp, tp + fp)
    out["mono"] = {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": _ratio(tp, tp + fn),
                   "d11_precision_pass": None if prec is None else prec >= MONO_PRECISION_GATE}
    rot = [(ref["rotate_cw"], sh.get("rotate_cw")) for ref, sh in rows
           if ref["confidence"]["rotate_cw"] in TRUSTED and sh.get("rotate_cw") is not None]
    rotated = [(a, b) for a, b in rot if a != 0]
    upright = [(a, b) for a, b in rot if a == 0]
    out["rotate_cw"] = {name: {"n": len(ps), "correct": sum(a == b for a, b in ps),
                               "accuracy": _ratio(sum(a == b for a, b in ps), len(ps))}
                        for name, ps in (("rotated", rotated), ("upright", upright))}
    return out


def build_report(records: list[dict], meta: dict, b: int = BOOTSTRAP, seed: int = DEFAULT_SEED,
                 reference: str = REFERENCE_ARM, rescore: list[dict] | None = None,
                 owner: list[dict] | None = None, self_audit_arm: str | None = None) -> dict:
    """Per-arm audit numbers. Reads counts and enums only; no caption, description or note text is copied.
    With `rescore` (caption_usefulness records) adds `usefulness` (rubric scores, owner review if `owner`).
    `self_audit_arm`: the arm written by the auditor's own model; its cells are marked and its D11 rule
    result is not used (`self_audit` in the report, `self_audit: true` on its paired row)."""
    arms = sorted({arm for r in records for arm in r["sets"]})
    per_arm, structured, by_arm = {}, {}, {}
    for arm in arms:
        rows = [(r, r["sets"][arm]) for r in records if arm in r["sets"]]
        have = [(r, s) for r, s in rows if s.get("shown") is not None]
        # A photo the arm left uncaptioned (blocked, invalid) can't be found by caption: it counts as an error
        # (error_rate 1, useful 1) but adds nothing to the wrong/invented/missing counts.
        by_arm[arm] = {r["asset_id"]: _metrics(s) if s.get("shown") is not None else NO_CAPTION for r, s in rows}
        rng = random.Random(f"{seed}:{arm}")
        ms = list(by_arm[arm].values())
        per_arm[arm] = {"n": len(have), "no_caption": len(rows) - len(have),
                        **{m: _mean_ci([x[m] for x in ms if x[m] is not None], b, rng)
                           for m in ("wrong", "invented", "missing", "error_rate", "useful")}}
        strata = sorted({r.get("stratum") or "unknown" for r, _ in have})
        structured[arm] = {
            "overall": _structured([(r["reference"], s["shown"]) for r, s in have]),
            "per_stratum": {st: _structured([(r["reference"], s["shown"]) for r, s in have
                                             if (r.get("stratum") or "unknown") == st]) for st in strata},
            "scans": _structured([(r["reference"], s["shown"]) for r, s in have
                                  if str(r.get("stratum") or "").endswith("|scan")])}
    paired = {}
    ref = by_arm.get(reference, {})
    for arm in arms:
        if arm == reference or not ref:
            continue
        rng = random.Random(f"{seed}:paired:{arm}")
        out = {"n": len(set(by_arm[arm]) & set(ref))}
        for m in ("wrong", "invented", "missing", "error_rate", "useful"):
            # photos where both arms have a value for this metric (counts need a caption on both sides)
            common = sorted(a for a in set(by_arm[arm]) & set(ref)
                            if by_arm[arm][a][m] is not None and ref[a][m] is not None)
            n = len(common)
            idx = [[rng.randrange(n) for _ in range(n)] for _ in range(b)] if n else []
            d = [by_arm[arm][a][m] - ref[a][m] for a in common]
            out[m] = {"diff": _r(sum(d) / n) if n else None,
                      "ci95": _pct([sum(d[i] for i in ix) / n for ix in idx]) if n else None}
        diff = out["error_rate"]["diff"]
        out["d11_error_rate_pass"] = None if diff is None else diff <= ERROR_RATE_MARGIN + 1e-12
        if arm == self_audit_arm:
            out["self_audit"] = True
        paired[arm] = out
    extra = {}
    if rescore:
        from shoebox.evals import review

        extra["usefulness"] = review.usefulness_report(records, rescore, owner, b, seed, reference)
    if self_audit_arm is not None:
        extra["self_audit"] = {self_audit_arm: SELF_AUDIT_NOTE}
    return {**meta, **extra, "photos": len(records), "reference_arm": reference, "bootstrap": b, "arms": per_arm,
            "paired_vs_reference": paired, "structured": structured,
            "rules": {"error_rate": "share of photos with >= 1 wrong or invented fact, or no caption at all "
                                    "(a blocked or invalid photo counts as an error and as usefulness 1; it adds "
                                    "nothing to the wrong/invented/missing means)",
                      "d11": f"candidate error rate <= reference + {ERROR_RATE_MARGIN:.0%} (point estimate; "
                             f"paired CI shown); mono precision >= {MONO_PRECISION_GATE}",
                      "structured": "scored against the Opus reference where its confidence is high or medium",
                      "ci": "95% percentile bootstrap over photos; paired differences resample photos jointly "
                            "(candidate minus reference)"}}


def _f(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def _ci(c) -> str:
    return "n/a" if not c else f"{_f(c[0])}–{_f(c[1])}"


def _self(rep: dict, arm: str) -> str:
    return f" ({SELF_AUDIT_NOTE})" if arm in (rep.get("self_audit") or {}) else ""


def render_md(rep: dict) -> str:
    who = rep.get("auditor", "Opus")
    mid = rep.get("auditor_model_id", rep.get("opus_model_id"))
    ph = rep.get("auditor_params_hash", rep.get("opus_params_hash"))
    lines = [f"# Caption audit: {rep.get('set', '')} ({who} vs every arm, blind)", "",
             f"- {who}: `{mid}` `{ph}`",
             (f"- Photos audited: {rep['photos']}; reference arm `{rep['reference_arm']}`; "
              f"bootstrap {rep['bootstrap']} resamples over photos"), "",
             "## Per arm (mean per photo, 95% CI)", "",
             "| arm | n | no caption | wrong | invented | missing | error rate | useful |",
             "|---|---|---|---|---|---|---|---|"]
    for arm, a in rep["arms"].items():
        cells = [f"{_f(a[m]['mean'])} ({_ci(a[m]['ci95'])})"
                 for m in ("wrong", "invented", "missing", "error_rate", "useful")]
        lines.append(f"| {arm}{_self(rep, arm)} | {a['n']} | {a['no_caption']} | {' | '.join(cells)} |")
    lines += ["", f"## Paired differences vs `{rep['reference_arm']}` (candidate minus reference)", "",
              "| arm | n | error rate | wrong | invented | missing | useful | D11 error rule |",
              "|---|---|---|---|---|---|---|---|"]
    for arm, p in rep["paired_vs_reference"].items():
        cells = [f"{_f(p[m]['diff'])} ({_ci(p[m]['ci95'])})"
                 for m in ("error_rate", "wrong", "invented", "missing", "useful")]
        rule = SELF_AUDIT_NOTE if p.get("self_audit") else p["d11_error_rate_pass"]
        lines.append(f"| {arm} | {p['n']} | {' | '.join(cells)} | {rule} |")
    head = [("| {} | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | "
             "rotate upright |"), "|---|---|---|---|---|---|---|---|---|"]
    lines += ["", f"## Structured fields vs the {who} reference (high/medium confidence)", "", head[0].format("arm"),
              head[1]]

    def row(name, s):
        m, r = s["mono"], s["rotate_cw"]
        acc = [f"{_f(s[f]['accuracy'])} (n={s[f]['n']})" for f in ("medium", "digitiser", "colour_class")]
        return (f"| {name} | {' | '.join(acc)} | {_f(m['precision'])} | {_f(m['recall'])} | "
                f"{m['d11_precision_pass']} | {_f(r['rotated']['accuracy'])} (n={r['rotated']['n']}) | "
                f"{_f(r['upright']['accuracy'])} (n={r['upright']['n']}) |")

    for arm, s in rep["structured"].items():
        lines.append(row(arm, s["overall"]))
    lines += ["", "### Scans only", "", head[0].format("arm"), head[1]]
    for arm, s in rep["structured"].items():
        lines.append(row(arm, s["scans"]))
    lines += ["", "### Per stratum", ""]
    for arm, s in rep["structured"].items():
        lines += [f"#### {arm}", "", head[0].format("stratum"), head[1]]
        lines += [row(st, v) for st, v in s["per_stratum"].items()]
        lines.append("")
    if rep.get("usefulness"):
        from shoebox.evals import review

        lines += [review.render_md(rep["usefulness"])]
    lines += ["## Rules", "", *(f"- {k}: {v}" for k, v in rep["rules"].items())]
    return "\n".join(lines) + "\n"
