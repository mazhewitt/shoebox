"""Opus audit of the Gemini relevance judgments (design D10; task 6.3).

Opus runs as Claude Code subagents (no API key): this module builds what they read and stores what they write.

1. `sample`: a stratified ~`frac` of the Gemini-judged pool pairs plus every target pair Gemini graded < 2.
   Method ("per-route quota fill"): each pair is attributed to every route whose top-k contained it (pool
   `routes`; a target no route retrieved is attributed to the bucket `(none)`). For each route in sorted order
   and each (Gemini grade, query kind) stratum of that route's pairs, the quota is ceil(frac * stratum size).
   Pairs already sampled for an earlier route count towards the quota; the rest of the quota is filled from
   the stratum's pairs in a seeded shuffled order (seed, route, grade, kind). So every route's top-k is
   represented in every stratum at >= frac, while overlapping routes share sampled pairs (the union is
   deduplicated; its overall fraction is reported, and is above frac because routes overlap). The random
   fill order doesn't prefer pairs many routes retrieved, which would bias the audit towards consensus.
2. `packets`: per query, the sampled photos in a seeded shuffle, cut into grids of <= 10, each rendered as one
   numbered contact sheet; batch files list grids with the same date/folder note Gemini saw and no asset_ids.
   `map.json` (grid_id -> query_id and asset_ids in cell order) stays beside them for ingest.
3. `ingest`: validates the subagents' `grades-*.jsonl` and stores keyed judgment records idempotently.
4. `report`: linear- and quadratic-weighted Cohen's kappa (Gemini vs Opus), with a query bootstrap CI.
"""

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from shoebox import derive, privacy, store
from shoebox.config import REPO_ROOT, Config
from shoebox.evals import judge

SAMPLE_FILE = "audit-sample.jsonl"
DEFAULT_FRAC = 0.10
DEFAULT_SEED = 20261013
NO_ROUTE = "(none)"
TARGET_REASON = "target_grade_lt2"
POOL_REASON = "pool"
POOL_SAMPLE_FILE = "opus-pool-{split}.jsonl"
OPUS_JUDGMENTS_FILE = "judgments-opus.jsonl"
MODEL_ID = "claude/opus-5.5"
WRITER = "Claude Code subagent"
PRESENTATION = "contact sheet 420px grids of 10"
AUDIT_VERSION = "judge-audit-1"
CELL_PX = 420
COLUMNS = 5
PROVIDER = "claude"
KAPPA_THRESHOLD = 0.6
BOOTSTRAP = 2000
CATEGORIES = (0, 1, 2)


# ---------- sample ----------


def _routes_of(row: dict, a: str) -> list[str]:
    return list((row.get("routes") or {}).get(a) or []) or [NO_ROUTE]


def sample(pool: list[dict], gemini: dict[str, int], frac: float = DEFAULT_FRAC, seed: int = DEFAULT_SEED
           ) -> list[dict]:
    """Rows per query (query_id order): `{query_id, kind, split, pairs: [{asset_id, gemini_grade, routes,
    target, reasons}]}`. `gemini` is `{"query_id:asset_id": grade}`; pool pairs without a grade are ignored."""
    by_q = {r["query_id"]: r for r in pool}
    pairs = {}  # key -> (query_id, asset_id, grade, kind, routes, target)
    for row in pool:
        rel = set(row.get("relevant") or [])
        for a in row["asset_ids"]:
            k = f"{row['query_id']}:{a}"
            if k in gemini:
                pairs[k] = (row["query_id"], a, gemini[k], row.get("kind"), _routes_of(row, a), a in rel)
    strata: dict[tuple, list[str]] = {}
    for k, (_, _, g, kind, routes, _) in pairs.items():
        for route in routes:
            strata.setdefault((route, g, str(kind)), []).append(k)
    reasons: dict[str, list[str]] = {}
    for (route, g, kind), keys in sorted(strata.items()):
        quota = math.ceil(frac * len(keys))
        have = sum(k in reasons for k in keys)
        order = sorted(keys)
        random.Random(f"{seed}:{route}:{g}:{kind}").shuffle(order)
        for k in order:
            if have >= quota:
                break
            if k not in reasons:
                reasons[k] = [f"stratum route={route} grade={g} kind={kind}"]
                have += 1
    for k, (_, _, g, _, _, target) in sorted(pairs.items()):
        if target and g < 2:
            reasons.setdefault(k, []).append(TARGET_REASON)
    out: dict[str, list[dict]] = {}
    for k in sorted(reasons):
        q, a, g, _, routes, target = pairs[k]
        out.setdefault(q, []).append({"asset_id": a, "gemini_grade": g, "routes": routes, "target": target,
                                      "reasons": reasons[k]})
    return [{"query_id": q, "kind": by_q[q].get("kind"), "split": by_q[q].get("split"), "pairs": ps}
            for q, ps in sorted(out.items())]


def sample_summary(rows: list[dict], pool: list[dict], gemini: dict[str, int]) -> dict:
    judged = sum(f"{r['query_id']}:{a}" in gemini for r in pool for a in r["asset_ids"])
    n = sum(len(r["pairs"]) for r in rows)
    return {"queries": len(rows), "pairs": n, "judged_pairs": judged,
            "fraction": round(n / judged, 4) if judged else 0.0,
            "targets_lt2": sum(TARGET_REASON in p["reasons"] for r in rows for p in r["pairs"])}


def pool_sample(pool: list[dict], gemini: dict[str, int], split: str) -> list[dict]:
    """Every pool pair of the split's queries, in `sample`'s row shape, for Opus to judge the whole pool (D10
    re-judge). Pairs Gemini did not grade are included with `gemini_grade: null`; reasons are `["pool"]`."""
    out = []
    for row in sorted((r for r in pool if r.get("split") == split), key=lambda r: r["query_id"]):
        rel = set(row.get("relevant") or [])
        pairs = [{"asset_id": a, "gemini_grade": gemini.get(f"{row['query_id']}:{a}"),
                  "routes": _routes_of(row, a), "target": a in rel, "reasons": [POOL_REASON]}
                 for a in sorted(set(row["asset_ids"]))]
        if pairs:
            out.append({"query_id": row["query_id"], "kind": row.get("kind"), "split": row.get("split"),
                        "pairs": pairs})
    return out


def pool_summary(rows: list[dict]) -> dict:
    n = sum(len(r["pairs"]) for r in rows)
    graded = sum(p["gemini_grade"] is not None for r in rows for p in r["pairs"])
    return {"queries": len(rows), "pairs": n, "gemini_graded": graded, "no_gemini": n - graded}


def gemini_grades(rows: list[dict]) -> dict[str, int]:
    """`{"query_id:asset_id": grade}` from `judge.qrels` rows (the `eval judgments` export)."""
    return {judge._pair(r): int(r["grade"]) for r in rows if r.get("grade") is not None}


# ---------- packets ----------


def contact_sheet(images: list[Path], cell: int = CELL_PX, columns: int = COLUMNS) -> bytes:
    """One JPEG: the images fitted into `cell`-px squares, numbered 1..n top-left in red."""
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    cols = min(columns, len(images))
    rows = -(-len(images) // cols)
    sheet = Image.new("RGB", (cols * cell, rows * cell), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(16, cell // 9))
    for i, path in enumerate(images):
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((cell - 8, cell - 8))
            x, y = (i % cols) * cell, (i // cols) * cell
            sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
        label = str(i + 1)
        box = draw.textbbox((x + 6, y + 4), label, font=font)
        draw.rectangle((box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4), fill=(255, 255, 255))
        draw.text((x + 6, y + 4), label, fill=(220, 0, 0), font=font)
    buf = BytesIO()
    sheet.save(buf, "JPEG", quality=88)
    return buf.getvalue()


@dataclass
class Packets:
    grids: list[dict] = field(default_factory=list)  # {grid_id, query_id, asset_ids}
    skipped: list[dict] = field(default_factory=list)  # {query_id, asset_id, reason}
    batches: list[Path] = field(default_factory=list)


def check_out_dir(out: Path) -> None:
    """Packets hold derived images and their paths: never under the repo's evals/."""
    o, ev = Path(out).resolve(), (REPO_ROOT / "evals").resolve()
    if o == ev or ev in o.parents:
        raise ValueError(f"{out}: audit packets must not be written under {ev}")


def packets(cfg: Config, rows: list[dict], pool: list[dict], items: list[dict], frame_rows: dict[str, dict],
            out: Path, grid: int = judge.GRID_SIZE, per_batch: int = 30, seed: int = DEFAULT_SEED,
            others: list[dict] = (), profile: str = "infer-v1") -> Packets:
    check_out_dir(out)
    text = {r["query_id"]: r["text"] for r in pool}
    by_id = {it["asset_id"]: it for it in items}
    wanted = sorted({p["asset_id"] for r in rows for p in r["pairs"]})
    elig = privacy.filter_items([by_id[a] for a in wanted if a in by_id], cfg, others=list(others),
                                provider=PROVIDER)
    allowed = {it["asset_id"] for it in elig.allowed}
    reasons = {s.asset_id: s.reason for s in elig.skipped}
    res = Packets()
    (out / "images").mkdir(parents=True, exist_ok=True)
    listed = []
    for r in rows:
        q = r["query_id"]
        ok = []
        for p in r["pairs"]:
            a = p["asset_id"]
            path = derive.derived_path(cfg, profile, a)
            if a not in allowed:
                res.skipped.append({"query_id": q, "asset_id": a, "reason": reasons.get(a, "not_in_set")})
            elif not path.is_file():
                res.skipped.append({"query_id": q, "asset_id": a, "reason": "no_derived"})
            else:
                ok.append(a)
        for ids in judge.split_grids(judge.shuffled(q, ok, seed), grid):
            gid = judge.Grid(q, text[q], ids).grid_id
            img = out / "images" / f"{gid}.jpg"
            img.write_bytes(contact_sheet([derive.derived_path(cfg, profile, a) for a in ids]))
            res.grids.append({"grid_id": gid, "query_id": q, "asset_ids": ids})
            listed.append({"grid_id": gid, "query_id": q, "query": text[q], "image": str(img.resolve()),
                           "photos": [{"n": i, "note": judge.photo_note(by_id[a], frame_rows.get(a))}
                                      for i, a in enumerate(ids, 1)]})
    for b in range(0, len(listed), per_batch):
        n = b // per_batch + 1
        path = out / f"batch-{n:03d}.json"
        path.write_text(json.dumps({"batch": n, "prompt_file": str(judge.gemini.PROMPTS_DIR / judge.PROMPT_FILE),
                                    "answer_file": f"grades-{n:03d}.jsonl",
                                    "answer_format": {"grid_id": "...", "grades": [{"n": 1, "grade": 0}]},
                                    "grids": listed[b:b + per_batch]}, indent=1, ensure_ascii=False))
        res.batches.append(path)
    (out / "map.json").write_text(json.dumps(
        {g["grid_id"]: {"query_id": g["query_id"], "asset_ids": g["asset_ids"]} for g in res.grids}, indent=1))
    (out / "packets.json").write_text(json.dumps({"seed": seed, "grid": grid, "cell_px": CELL_PX,
                                                  "profile": profile, "grids": len(res.grids),
                                                  "skipped": res.skipped}, indent=1))
    return res


# ---------- ingest ----------


def audit_params(prompt_text: str, seed: int) -> dict:
    return {"prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(), "prompt": judge.PROMPT_FILE,
            "presentation": PRESENTATION, "seed": seed, "writer": WRITER, "audit_version": AUDIT_VERSION}


class IngestError(ValueError):
    pass


def read_grades(d: Path, grid_map: dict[str, dict]) -> dict[str, dict[int, int]]:
    """`{grid_id: {n: grade}}` from `d/grades-*.jsonl`; raises IngestError listing every problem."""
    out: dict[str, dict[int, int]] = {}
    errors = []
    for path in sorted(Path(d).glob("grades-*.jsonl")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path.name}:{i}"
            try:
                row = json.loads(line)
            except ValueError as e:
                errors.append(f"{where}: not JSON: {e}")
                continue
            gid = row.get("grid_id") if isinstance(row, dict) else None
            if gid not in grid_map:
                errors.append(f"{where}: unknown grid_id {gid!r}")
                continue
            entries = row.get("grades")
            conv = {"grades": [{"photo": e.get("n"), "grade": e.get("grade")} if isinstance(e, dict) else e
                               for e in entries]} if isinstance(entries, list) else None
            grades, err = judge.validate_grades(conv, len(grid_map[gid]["asset_ids"]))
            if err:
                errors.append(f"{where}: grid {gid}: {err}")
            elif gid in out and out[gid] != grades:
                errors.append(f"{where}: grid {gid} graded twice, differently")
            else:
                out[gid] = grades
    if errors:
        raise IngestError("; ".join(errors))
    return out


@dataclass
class IngestResult:
    params_hash: str
    stored: int = 0
    already: int = 0
    conflicts: list[str] = field(default_factory=list)  # pairs already stored with a different grade
    missing_grids: list[str] = field(default_factory=list)


def ingest(cfg: Config, d: Path, prompt_text: str, model_id: str = MODEL_ID) -> IngestResult:
    """Validate everything first (nothing is stored if any line is bad), then append one `ok` record per
    (query_id, asset_id) not already final in the store."""
    d = Path(d)
    grid_map = json.loads((d / "map.json").read_text())
    meta = json.loads((d / "packets.json").read_text())
    grades = read_grades(d, grid_map)
    params = audit_params(prompt_text, int(meta["seed"]))
    ph = store.params_hash(params)
    res = IngestResult(ph, missing_grids=sorted(set(grid_map) - set(grades)))
    have = store.index_records(store.iter_records(cfg, model_id, ph, judge.KIND), key=judge._pair)
    if grades:
        store.write_params(cfg, model_id, ph, {"candidate": "opus-judge-audit", "model_id": model_id,
                                               "params_hash": ph, "params": params}, judge.KIND)
    for gid in sorted(grades):
        g = grid_map[gid]
        ids = g["asset_ids"]
        for n, a in enumerate(ids, 1):
            rec = {"asset_id": a, "query_id": g["query_id"], "model_id": model_id, "params_hash": ph,
                   "status": "ok", "grade": grades[gid][n], "grid": {"id": gid, "photo": n, "size": len(ids)},
                   "writer": WRITER}
            old = have.get(judge._pair(rec))
            if store.is_final(old):
                res.already += 1
                if old.get("grade") != rec["grade"]:
                    res.conflicts.append(judge._pair(rec))
                continue
            store.append(cfg, rec, judge.KIND)
            have[judge._pair(rec)] = rec
            res.stored += 1
    return res


def opus_grades(cfg: Config, model_id: str = MODEL_ID, params_hash: str | None = None
                ) -> tuple[str, dict[str, int]]:
    """(params_hash, `{"query_id:asset_id": grade}`) for the audit store; the only params dir if not given."""
    if params_hash is None:
        dirs = [p.name for p in (cfg.results_dir / judge.KIND / store.model_dir(model_id)).glob("*") if p.is_dir()]
        if len(dirs) != 1:
            raise ValueError(f"{len(dirs)} params dirs for {model_id}; pass --params-hash ({sorted(dirs)})")
        params_hash = dirs[0]
    idx = store.index_records(store.iter_records(cfg, model_id, params_hash, judge.KIND), key=judge._pair)
    return params_hash, {k: int(r["grade"]) for k, r in idx.items() if r.get("status") == "ok"}


def opus_qrels(cfg: Config, model_id: str = MODEL_ID, params_hash: str | None = None) -> list[dict]:
    """The Opus judgments as `{query_id, asset_id, grade, model_id, params_hash}` rows, sorted: the same shape
    as `judge.qrels` (the Gemini `eval judgments` export)."""
    ph, grades = opus_grades(cfg, model_id, params_hash)
    rows = [{"query_id": k.rsplit(":", 1)[0], "asset_id": k.rsplit(":", 1)[1], "grade": g, "model_id": model_id,
             "params_hash": ph} for k, g in grades.items()]
    return sorted(rows, key=lambda r: (r["query_id"], r["asset_id"]))


# ---------- kappa ----------


def confusion(pairs: list[tuple[int, int]], k: int = 3) -> list[list[int]]:
    m = [[0] * k for _ in range(k)]
    for a, b in pairs:
        m[a][b] += 1
    return m


def weighted_kappa(m: list[list[int]], weights: str = "linear") -> float | None:
    """Cohen's weighted kappa for a k x k confusion matrix (rows rater A, columns rater B); disagreement weights
    |i-j|/(k-1) (linear) or its square (quadratic). None when undefined (no pairs, or no expected
    disagreement, i.e. both raters used one and the same category)."""
    k = len(m)
    n = sum(map(sum, m))
    if not n:
        return None
    rows = [sum(m[i]) / n for i in range(k)]
    cols = [sum(m[i][j] for i in range(k)) / n for j in range(k)]
    p = 1 if weights == "linear" else 2
    w = [[(abs(i - j) / (k - 1)) ** p for j in range(k)] for i in range(k)]
    obs = sum(w[i][j] * m[i][j] / n for i in range(k) for j in range(k))
    exp = sum(w[i][j] * rows[i] * cols[j] for i in range(k) for j in range(k))
    if exp == 0:
        return None
    return 1 - obs / exp


def _stats(pairs: list[tuple[int, int]]) -> dict:
    m = confusion(pairs)
    n = len(pairs)
    lin, quad = weighted_kappa(m, "linear"), weighted_kappa(m, "quadratic")
    return {"n": n, "exact_agreement": round(sum(a == b for a, b in pairs) / n, 4) if n else None,
            "kappa_linear": None if lin is None else round(lin, 4),
            "kappa_quadratic": None if quad is None else round(quad, 4), "confusion": m}


def bootstrap_ci(by_query: dict[str, list[tuple[int, int]]], weights: str, b: int = BOOTSTRAP,
                 seed: int = DEFAULT_SEED) -> list[float] | None:
    qs = sorted(by_query)
    if not qs:
        return None
    rng = random.Random(f"{seed}:{weights}")
    vals = []
    for _ in range(b):
        pairs = [p for _ in qs for p in by_query[qs[rng.randrange(len(qs))]]]
        v = weighted_kappa(confusion(pairs), weights)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    return [round(vals[int(0.025 * (len(vals) - 1))], 4), round(vals[int(0.975 * (len(vals) - 1))], 4)]


def build_report(rows: list[dict], opus: dict[str, int], meta: dict, b: int = BOOTSTRAP,
                 seed: int = DEFAULT_SEED) -> dict:
    """Gemini (rows) vs Opus agreement. Ids only: no query text is read or written."""
    all_pairs, by_query, by_kind, by_route, targets = [], {}, {}, {}, []
    no_gemini = 0
    for r in rows:
        for p in r["pairs"]:
            if p.get("gemini_grade") is None:  # a full-pool row Gemini never graded: not comparable
                no_gemini += 1
                continue
            k = f"{r['query_id']}:{p['asset_id']}"
            if k not in opus:
                continue
            pair = (int(p["gemini_grade"]), opus[k])
            all_pairs.append(pair)
            by_query.setdefault(r["query_id"], []).append(pair)
            by_kind.setdefault(str(r.get("kind")), []).append(pair)
            for route in p["routes"]:
                by_route.setdefault(route, []).append(pair)
            if p.get("target"):
                targets.append(pair)
    sampled = sum(len(r["pairs"]) for r in rows)
    overall = _stats(all_pairs)
    overall["ci95_linear"] = bootstrap_ci(by_query, "linear", b, seed)
    overall["ci95_quadratic"] = bootstrap_ci(by_query, "quadratic", b, seed)
    per_grade = {}
    for g in CATEGORIES:
        row = overall["confusion"][g]
        tot = sum(row)
        per_grade[str(g)] = {"n": tot, "opus": row, "agree": round(row[g] / tot, 4) if tot else None}
    lin = overall["kappa_linear"]
    return {**meta, "sampled_pairs": sampled, "no_gemini_grade": no_gemini, "opus_graded_pairs": len(all_pairs),
            "coverage": round(len(all_pairs) / sampled, 4) if sampled else 0.0, "queries": len(by_query),
            "overall": overall, "per_kind": {k: _stats(v) for k, v in sorted(by_kind.items())},
            "per_route": {k: _stats(v) for k, v in sorted(by_route.items())},
            "per_gemini_grade": per_grade, "targets": _stats(targets),
            "rule": f"D10: if Gemini-Opus linear-weighted kappa < {KAPPA_THRESHOLD}, Opus re-judges the test pool",
            "rejudge_test_pool": None if lin is None else lin < KAPPA_THRESHOLD}


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def render_md(rep: dict) -> str:
    o = rep["overall"]
    ci = o["ci95_linear"] or [None, None]
    cq = o["ci95_quadratic"] or [None, None]
    lines = [f"# Judge audit: {rep['set']} (Gemini vs Opus)", "",
             f"- Gemini: `{rep['gemini_model_id']}` `{rep['gemini_params_hash']}`",
             f"- Opus: `{rep['opus_model_id']}` `{rep['opus_params_hash']}`",
             (f"- Sample: {rep['sampled_pairs']} pairs over {rep['queries']} queries graded by Opus; "
             f"coverage {rep['coverage']:.1%}"), "",
             "## Overall", "",
             f"- n = {o['n']}, exact agreement {_fmt(o['exact_agreement'])}",
             (f"- linear-weighted kappa {_fmt(o['kappa_linear'])} (95% CI {_fmt(ci[0])}–{_fmt(ci[1])}, "
             "bootstrap over queries)"),
             f"- quadratic-weighted kappa {_fmt(o['kappa_quadratic'])} (95% CI {_fmt(cq[0])}–{_fmt(cq[1])})", "",
             f"**Rule.** {rep['rule']}. Decision: "
             + ("undefined (no kappa)" if rep["rejudge_test_pool"] is None
                else "re-judge the test pool with Opus" if rep["rejudge_test_pool"] else "keep the Gemini judgments"),
             "", "## Confusion (rows Gemini grade, columns Opus grade)", "", "| Gemini \\ Opus | 0 | 1 | 2 | n | agree |",
             "|---|---|---|---|---|---|"]
    for g, s in rep["per_gemini_grade"].items():
        lines.append(f"| {g} | {' | '.join(map(str, s['opus']))} | {s['n']} | {_fmt(s['agree'])} |")
    for title, block in (("Per query kind", rep["per_kind"]), ("Per route", rep["per_route"]),
                         ("Target pairs", {"targets": rep["targets"]})):
        lines += ["", f"## {title}", "", "| group | n | exact | kappa lin | kappa quad |", "|---|---|---|---|---|"]
        for name, s in block.items():
            lines.append(f"| {name} | {s['n']} | {_fmt(s['exact_agreement'])} | {_fmt(s['kappa_linear'])} | "
                         f"{_fmt(s['kappa_quadratic'])} |")
    return "\n".join(lines) + "\n"
