"""Graded search scoring on eval-1k (tasks 7.1 and 7.3, gates `evals/gates.toml` v2).

`graded-dev` scores every route on the dev split with graded qrels, picks one route per captioner and freezes the
choice (`frozen-routes.json`). `graded-test` scores the frozen routes on the test split, compares each to the
reference captioner's route with a paired bootstrap (non-inferiority), and applies the gates' decision order.

Metrics (per route, per query; `graded_query`):
- nDCG@20 with linear gains (grade 0/1/2) and log2 discount; the ideal is built from every judged grade of the
  query (also items no route ranks). Unjudged items count as grade 0; judged@20 (share of the top 20 that carries
  a judgment) is reported as pool coverage. A query with no grade > 0 anywhere has no defined nDCG: it is excluded
  and counted.
- known_item / owner_note: RR of the first relevant (target) item and hit@10 (`search.query_metrics`).
- category: recall@20 of the grade-2 items and of the grade >= 1 items.
Ties: as in `search.py`, every metric is its expectation over uniformly random orders within tie blocks of equal
score (an item in block (lo, n) sits at each of its n positions with probability 1/n; by linearity of expectation
the expected DCG is exact).

Reports carry ids and numbers only, never query or caption text.
"""

import json
import math
import tomllib
from collections import defaultdict
from pathlib import Path

import numpy as np

from shoebox.config import REPO_ROOT
from shoebox.evals import search

K = 20
HIT_K = 10
PRIMARY_KINDS = ("known_item", "category")
FREEZE_FILE = "frozen-routes.json"
GATES_PATH = REPO_ROOT / "evals" / "gates.toml"
RESAMPLES = 2000  # gates [search].statistic
SEED = 20261010
OPUS_TOKENS_PER_1000 = 3.2e6  # gates [candidates]: measured 2026-10-11
BATCH_FACTOR = 0.5  # gates [projection].price_basis
NONE = "none"


# --- metrics --------------------------------------------------------------------------------------


def _in_top(blocks: list[tuple[int, int]], i: int, k: int) -> float:
    """P(the item at stored position i lands in the top k) over tie orders."""
    lo, n = blocks[i]
    return min(1.0, max(0.0, (k - lo) / n))


def _expected_discount(blocks: list[tuple[int, int]], i: int, k: int, prefix: list[float]) -> float:
    """E[1/log2(rank+1) * 1{rank <= k}] for the item at stored position i."""
    lo, n = blocks[i]
    hi = min(lo + n, k)
    return (prefix[hi] - prefix[lo]) / n if hi > lo else 0.0


def graded_query(ranking: list[str], scores: list[float | None] | None, grades: dict[str, int],
                 k: int = K) -> dict:
    """`{"ndcg": float | None, "judged": float}`; ndcg is None when no judged grade is > 0."""
    blocks = search.tie_blocks(ranking, scores)
    prefix = [0.0]
    for p in range(min(k, len(ranking))):
        prefix.append(prefix[-1] + 1.0 / math.log2(p + 2))
    prefix += [prefix[-1]] * max(0, k + 1 - len(prefix))
    dcg = judged = 0.0
    for i, a in enumerate(ranking):
        if a not in grades:
            continue
        if blocks[i][0] >= k:
            continue
        judged += _in_top(blocks, i, k)
        if grades[a] > 0:
            dcg += grades[a] * _expected_discount(blocks, i, k, prefix)
    ideal = sorted((g for g in grades.values() if g > 0), reverse=True)[:k]
    idcg = sum(g / math.log2(r + 2) for r, g in enumerate(ideal))
    top_n = min(k, len(ranking))
    return {"ndcg": dcg / idcg if idcg else None, "judged": judged / top_n if top_n else 0.0}


def recall_at(ranking: list[str], scores: list[float | None] | None, grades: dict[str, int], min_grade: int,
              k: int = K) -> float | None:
    """Expected share of the items graded >= min_grade that land in the top k (None if there are none)."""
    want = {a for a, g in grades.items() if g >= min_grade}
    if not want:
        return None
    blocks = search.tie_blocks(ranking, scores)
    return sum(_in_top(blocks, i, k) for i, a in enumerate(ranking) if a in want) / len(want)


def score_queries(queries: list[dict], rows: dict[str, tuple], qrels: dict[str, dict[str, int]],
                  splits: set[str], kinds: tuple[str, ...] = PRIMARY_KINDS) -> tuple[dict[str, dict], list[str]]:
    """Per-query metrics of one route over the queries of `splits` and `kinds` that the route ranked.
    Returns (metrics by query_id, excluded query_ids: no grade > 0 in the qrels, or no qrels at all)."""
    per, excluded = {}, []
    for q in sorted(queries, key=lambda q: q["query_id"]):
        qid = q["query_id"]
        if q.get("split") not in splits or q.get("kind") not in kinds or qid not in rows:
            continue
        grades = qrels.get(qid, {})
        ranking, scores = rows[qid]
        m = graded_query(ranking, scores, grades)
        if m["ndcg"] is None:
            excluded.append(qid)
            continue
        out = {"kind": q.get("kind"), "ndcg@20": m["ndcg"], "judged@20": m["judged"]}
        if q.get("kind") in ("known_item", "owner_note") and q.get("relevant"):
            qm = search.query_metrics(ranking, q["relevant"], ks=(HIT_K,), scores=scores)
            out["rr"], out[f"hit@{HIT_K}"] = qm["rr"], qm[f"hit@{HIT_K}"]
        if q.get("kind") == "category":
            out["recall2@20"] = recall_at(ranking, scores, grades, 2)
            out["recall1@20"] = recall_at(ranking, scores, grades, 1)
        per[qid] = out
    return per, excluded


def _mean(values) -> float | None:
    v = [x for x in values if x is not None]
    return sum(v) / len(v) if v else None


def summarise(per: dict[str, dict]) -> dict:
    """Means of one route's per-query metrics, overall and per kind."""
    def block(ms: list[dict]) -> dict:
        return {"n": len(ms), "ndcg@20": _mean(m["ndcg@20"] for m in ms),
                "judged@20": _mean(m["judged@20"] for m in ms),
                "mrr": _mean(m.get("rr") for m in ms), f"hit@{HIT_K}": _mean(m.get(f"hit@{HIT_K}") for m in ms),
                "recall2@20": _mean(m.get("recall2@20") for m in ms),
                "recall1@20": _mean(m.get("recall1@20") for m in ms)}

    kinds = defaultdict(list)
    for m in per.values():
        kinds[m["kind"]].append(m)
    return block(list(per.values())) | {"per_kind": {k: block(v) for k, v in sorted(kinds.items())}}


# --- loading --------------------------------------------------------------------------------------


def load_qrels(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for r in search.read_jsonl(path):
        out[r["query_id"]][r["asset_id"]] = int(r["grade"])
    return dict(out)


def load_routes(index_path: Path, queries_file: str) -> tuple[dict[str, dict[str, tuple]], dict[str, str]]:
    """`({route: {query_id: (ranking, scores)}}, {route: params_hash})` for the routes run on `queries_file`."""
    if not index_path.exists():
        raise FileNotFoundError(f"{index_path} not found: run `shoebox eval search-run` first")
    rows, hashes = {}, {}
    for route, e in sorted(json.loads(index_path.read_text())["routes"].items()):
        if e.get("queries_file", search.QUERIES_FILE) != queries_file:
            continue
        rows[route] = {r["query_id"]: (r["ranking"], r.get("scores")) for r in search.read_jsonl(Path(e["path"]))}
        hashes[route] = e.get("params_hash")
    if not rows:
        raise ValueError(f"no route in {index_path} was run on {queries_file}")
    return rows, hashes


def load_gates(path: Path = GATES_PATH) -> dict:
    return tomllib.loads(Path(path).read_text())


def load_config():
    from shoebox.commands.eval import _cfg

    return _cfg()


# --- dev choice -----------------------------------------------------------------------------------


def captioner_of(route: str) -> str:
    return search.parse_route(route).cand or NONE


def _simplicity(route: str) -> tuple[int, str]:
    r = search.parse_route(route)
    return (int(r.exp) + int(r.filt), route)


def choose_frozen(means: dict[str, float]) -> dict[str, dict]:
    """Best route per captioner by mean dev nDCG@20; ties go to the simpler route (fewer suffixes), then the
    name. Blend-weight sensitivity routes never compete."""
    best: dict[str, str] = {}
    for route in sorted(means, key=lambda r: (-means[r], *_simplicity(r))):
        if means[route] is None or search.parse_route(route).sensitivity:
            continue
        best.setdefault(captioner_of(route), route)
    return {c: {"route": r, "dev_ndcg": means[r]} for c, r in sorted(best.items())}


def dev_report(queries: list[dict], routes: dict[str, dict], qrels: dict, meta: dict) -> dict:
    out_routes, excluded = {}, set()
    for route, rows in routes.items():
        per, exc = score_queries(queries, rows, qrels, {"dev"})
        excluded |= set(exc)
        r = search.parse_route(route)
        out_routes[route] = summarise(per) | {"captioner": r.cand or NONE, "sensitivity": r.sensitivity}
    dev_ids = {q["query_id"] for q in queries if q.get("split") == "dev" and q.get("kind") in PRIMARY_KINDS}
    frozen = choose_frozen({r: v["ndcg@20"] for r, v in out_routes.items()})
    return meta | {"split": "dev", "kinds": list(PRIMARY_KINDS), "queries": len(dev_ids),
                   "queries_without_qrels": len(dev_ids - qrels.keys()),
                   "excluded_no_positive_grade": sorted(excluded), "routes": out_routes, "frozen": frozen}


def write_freeze(path: Path, rep: dict, hashes: dict[str, str], force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists: the dev choice is frozen (use --force to re-freeze)")
    doc = {"set": rep["set"], "date": rep["date"], "chosen_on": "dev", "qrels": rep["qrels"],
           "qrels_sha256": rep["qrels_sha256"],
           "params": {"metric": "ndcg@20", "gains": "linear 0/1/2", "unjudged": "grade 0", "kinds": list(PRIMARY_KINDS),
                      "tie_break": "fewer +exp/+filt suffixes, then route name", "queries_file": rep["queries_file"],
                      "n_dev_queries_scored": {c: rep["routes"][f["route"]]["n"] for c, f in rep["frozen"].items()}},
           "routes": {c: {"route": f["route"], "dev_ndcg": f["dev_ndcg"], "params_hash": hashes.get(f["route"])}
                      for c, f in rep["frozen"].items()}}
    search._atomic_write(path, json.dumps(doc, indent=1, sort_keys=True) + "\n")


# --- test scoring ---------------------------------------------------------------------------------


def non_inferiority(cand: dict[str, float], ref: dict[str, float], margin: float = 0.03,
                    resamples: int = RESAMPLES, seed: int = SEED) -> dict | None:
    p = search.paired_bootstrap(cand, ref, resamples=resamples, seed=seed)
    if p is None:
        return None
    return p | {"delta_ci95": list(p["delta_ci95"]), "margin": margin, "non_inferior": p["delta_ci95"][0] > -margin}


def _group_means(per: dict[str, dict], qmeta: dict[str, dict], key: str) -> dict:
    g = defaultdict(list)
    for qid, m in per.items():
        v = qmeta[qid].get(key)
        if v is not None:
            g[v].append(m["ndcg@20"])
    return {k: {"n": len(v), "ndcg@20": sum(v) / len(v)} for k, v in sorted(g.items())}


def _ndcg_map(per: dict[str, dict]) -> dict[str, float]:
    return {q: m["ndcg@20"] for q, m in per.items()}


def score_test(queries: list[dict], routes: dict[str, dict], frozen: dict, primary: dict, secondary: dict | None,
                meta: dict, gates: dict) -> dict:
    qmeta = {q["query_id"]: q for q in queries}
    ref_cap = gates["reference"]["captioner"]
    margin = float(gates["search"]["non_inferiority_margin"])
    fr = frozen["routes"]
    if ref_cap not in fr:
        raise ValueError(f"the reference captioner {ref_cap} has no frozen route")
    missing = [f["route"] for f in fr.values() if f["route"] not in routes]
    if missing:
        raise ValueError(f"frozen routes missing from the rankings index: {missing}")
    score = {route: score_queries(queries, routes[route], primary, {"test"}) for route in routes}
    ref_map = _ndcg_map(score[fr[ref_cap]["route"]][0])
    out_frozen, excluded = {}, set()
    for cap, f in fr.items():
        per, exc = score[f["route"]]
        excluded |= set(exc)
        vals = [m["ndcg@20"] for m in per.values()]
        ci = search.bootstrap_ci(vals, resamples=RESAMPLES, seed=SEED)
        s = summarise(per)
        out_frozen[cap] = {"route": f["route"], "dev_ndcg": f["dev_ndcg"], "n": s["n"], "mean_ndcg": s["ndcg@20"],
                           "ci95": list(ci) if ci else None, "judged@20": s["judged@20"], "mrr": s["mrr"],
                           f"hit@{HIT_K}": s[f"hit@{HIT_K}"], "recall2@20": s["recall2@20"],
                           "recall1@20": s["recall1@20"], "per_kind": s["per_kind"],
                           "per_style": _group_means(per, qmeta, "style"),
                           "per_facet": _group_means(per, qmeta, "facet"),
                           "paired": (None if cap == ref_cap else non_inferiority(_ndcg_map(per), ref_map, margin))}
    # owner_note subset: primary qrels if they grade any owner query, else the secondary
    owner_ids = {q["query_id"] for q in queries if q.get("split") == "owner"}
    use_primary = bool(owner_ids & {q for q, g in primary.items() if any(v > 0 for v in g.values())})
    oq, oname = (primary, meta["qrels"]) if use_primary else (secondary or {}, meta.get("secondary_qrels"))
    owner = {"qrels": oname, "routes": {}}
    for cap, f in fr.items():
        per, exc = score_queries(queries, routes[f["route"]], oq, {"owner"}, kinds=("owner_note",))
        s = summarise(per)
        owner["routes"][cap] = {"route": f["route"], "n": s["n"], "ndcg@20": s["ndcg@20"], "mrr": s["mrr"],
                                f"hit@{HIT_K}": s[f"hit@{HIT_K}"], "judged@20": s["judged@20"], "excluded": len(exc)}
    sec = None
    if secondary is not None:
        sec = {"qrels": meta.get("secondary_qrels"), "routes": {}}
        sec_map = {}
        for cap, f in fr.items():
            per, exc = score_queries(queries, routes[f["route"]], secondary, {"test"})
            s = summarise(per)
            sec_map[cap] = s["ndcg@20"]
            sec["routes"][cap] = {"route": f["route"], "n": s["n"], "mean_ndcg": s["ndcg@20"],
                                  "judged@20": s["judged@20"], "excluded": len(exc)}
        prim_map = {c: v["mean_ndcg"] for c, v in out_frozen.items()}
        dis = []
        caps = sorted(fr)
        for i, a in enumerate(caps):
            for b in caps[i + 1:]:
                if None in (prim_map[a], prim_map[b], sec_map[a], sec_map[b]):
                    continue
                if np.sign(prim_map[a] - prim_map[b]) != np.sign(sec_map[a] - sec_map[b]):
                    dis.append({"a": a, "b": b, "primary_delta": prim_map[a] - prim_map[b],
                                "secondary_delta": sec_map[a] - sec_map[b]})
        sec["order_disagreements"] = dis
    frozen_routes = {f["route"] for f in fr.values()}
    appendix = {}
    for route in sorted(routes):
        if route in frozen_routes:
            continue
        s = summarise(score[route][0])
        appendix[route] = {"n": s["n"], "mean_ndcg": s["ndcg@20"], "judged@20": s["judged@20"]}
    test_ids = {q["query_id"] for q in queries if q.get("split") == "test" and q.get("kind") in PRIMARY_KINDS}
    return meta | {"split": "test", "kinds": list(PRIMARY_KINDS), "reference": {"captioner": ref_cap,
                   "route": fr[ref_cap]["route"]}, "queries": len(test_ids),
                   "queries_without_qrels": len(test_ids - primary.keys()),
                   "excluded_no_positive_grade": sorted(excluded), "frozen": out_frozen, "owner_note": owner,
                   "secondary": sec, "appendix_not_used": appendix}


# --- caption rules, costs, decision -----------------------------------------------------------------


def _latest(audit_dir: Path, pattern: str) -> Path | None:
    files = sorted(Path(audit_dir).glob(pattern))
    return files[-1] if files else None


def caption_rules(audit_dir: Path, set_name: str, captioners: list[str], gates: dict) -> dict:
    """Per captioner: error rate vs the reference (+ margin), mono precision vs the minimum.
    `pass` is True/False, or None where no rule applies (no captioner; opus-v2, reported not gated)."""
    ref = gates["reference"]["captioner"]
    margin = float(gates["captions"]["error_margin"])
    mono_min = float(gates["captions"]["mono_precision_min"])
    opus_path = _latest(audit_dir, f"*_{set_name}-caption-audit.json")
    both_path = _latest(audit_dir, f"*_{set_name}-caption-audit-both.json")
    opus = json.loads(opus_path.read_text()) if opus_path else {}
    both = json.loads(both_path.read_text()) if both_path else {}
    out = {}
    for cap in captioners:
        if cap == NONE:
            out[cap] = {"pass": None, "note": "no caption rule applies (search-only; does not describe every photo)",
                        "mono_ok": None}
            continue
        if cap == "opus-v2":
            g = both.get("auditors", {}).get("gemini", {}).get("arms", {})
            er, rer = (g.get(cap, {}).get("error_rate", {}).get("mean"), g.get(ref, {}).get("error_rate", {}).get("mean"))
            out[cap] = {"pass": None, "auditor": "gemini (both-auditor report)", "source": both_path.name if both_path else None,
                        "error_rate": er, "reference_error_rate": rer,
                        "within_margin": (er <= rer + margin) if None not in (er, rer) else None,
                        "note": "reported, not gated (Gemini on the reference is a self-audit)",
                        "mono_precision": None, "mono_ok": None}
            continue
        arms = opus.get("arms", {})
        er = arms.get(cap, {}).get("error_rate", {}).get("mean")
        rer = arms.get(ref, {}).get("error_rate", {}).get("mean")
        mono = opus.get("structured", {}).get(cap, {}).get("overall", {}).get("mono", {}).get("precision")
        ok = True if cap == ref and er is not None else ((er <= rer + margin) if None not in (er, rer) else False)
        out[cap] = {"pass": ok, "auditor": "opus", "source": opus_path.name if opus_path else None,
                    "error_rate": er, "reference_error_rate": rer, "margin": margin,
                    "mono_precision": mono, "mono_ok": (mono >= mono_min) if mono is not None else None}
        if er is None:
            out[cap]["note"] = "no audit result: counted as failing"
    return out


def measure_costs(cfg, captioners: list[str]) -> dict:
    """Per captioner from its label store, over the eval-1k items: hosted USD per image (mean `usd`) or local
    img/h (3,600,000 / mean `latency_ms`, sequential). Reads records only."""
    from shoebox.runners import label_source, resolve, source_index

    items = {r["asset_id"] for r in search.read_jsonl(REPO_ROOT / "evals" / "sets" / "eval-1k" / "items.jsonl")}
    out = {}
    for cap in captioners:
        if cap == NONE:
            continue
        res = resolve(cap)
        recs = [r for a, r in source_index(cfg, label_source(res)).items() if a in items]
        local = res.model_id.startswith("ollama/")
        lat = [r["latency_ms"] for r in recs if isinstance(r.get("latency_ms"), (int, float))]
        usd = [float(r.get("usd") or 0.0) for r in recs]
        out[cap] = {"local": local, "n": len(recs), "subscription": res.model_id.startswith("claude/"),
                    "usd_per_image": sum(usd) / len(usd) if usd else None,
                    "img_per_hour": 3.6e6 / (sum(lat) / len(lat)) if lat and local else None}
    return out


def projections(costs: dict, captioners: list[str], library: int, opus_tokens_per_1000: float) -> dict:
    out = {}
    for cap in captioners:
        c = costs.get(cap, {})
        p = {"local": bool(c.get("local")), "usd": 0.0, "batch_usd": 0.0, "hours": None, "subscription_tokens": None}
        if c.get("img_per_hour"):
            p["img_per_hour"] = c["img_per_hour"]
            p["hours"] = library / c["img_per_hour"]
        if c.get("usd_per_image") and not c.get("local"):
            p["usd_per_image"] = c["usd_per_image"]
            p["usd"] = c["usd_per_image"] * library
            p["batch_usd"] = p["usd"] * BATCH_FACTOR
        if cap == "opus-v2":
            p["subscription_tokens"] = opus_tokens_per_1000 * library / 1000
        out[cap] = p
    return out


def decide(cands: dict[str, dict], remaining_usd: float, reference: str = "gemini-medium-v2") -> dict:
    """The gates' decision order. `cands`: {captioner: {search_ni, caption_pass (None = no rule / not gated),
    local, usd (standard, full library), hours, mono_ok}}."""
    dropped = {c: [w for w, bad in (("search non-inferiority", v["search_ni"] is False),
                                    ("caption rule", v["caption_pass"] is False)) if bad]
               for c, v in cands.items()}
    dropped = {c: w for c, w in dropped.items() if w}
    alive = {c: v for c, v in cands.items() if c not in dropped and c != NONE}
    locals_ = {c: v for c, v in alive.items() if v["local"]}
    out = {"dropped": dropped, "surviving": sorted(alive), "fallback": NONE if NONE in cands else None,
           "remaining_usd": remaining_usd}
    if locals_:
        cheapest = min(v["usd"] for v in alive.values())
        tied = {c: v for c, v in alive.items() if v["usd"] == cheapest}
        tied_local = {c: v for c, v in tied.items() if v["local"]}
        if tied_local:
            pick = min(tied_local, key=lambda c: (tied_local[c]["hours"] or math.inf, c))
            rule = "3. cost tie goes to local" if len(tied) > 1 or len(tied_local) > 1 else "2. lowest projected cost"
        else:
            pick, rule = min(tied), "2. lowest projected cost"
        rec = {"captioner": pick, "rule": rule, "usd": alive[pick]["usd"], "hours": alive[pick]["hours"]}
    else:
        ref = cands.get(reference, {})
        batch = (ref.get("usd") or 0.0) * BATCH_FACTOR
        rec = {"captioner": reference, "rule": "4. no local captioner passes: reference at batch price",
               "usd": ref.get("usd"), "batch_usd": batch, "fits_budget": batch <= remaining_usd,
               "other_survivors_not_in_order": sorted(c for c in alive if c != reference)}
    rec["colour_filter"] = cands.get(rec["captioner"], {}).get("mono_ok") is not False
    out["recommendation"] = rec
    return out


# --- rendering ------------------------------------------------------------------------------------


def _f(x, nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _tokens(n: float | None) -> str:
    return "—" if n is None else f"{n / 1e6:.0f}M"


def render_dev_md(rep: dict, top: int | None = None) -> str:
    rows = sorted(rep["routes"].items(), key=lambda kv: (-(kv[1]["ndcg@20"] or -1), kv[0]))
    lines = [f"# Graded dev scoring: {rep['set']} ({rep['date']})", "",
             f"- qrels `{rep['qrels']}` (sha256 {rep['qrels_sha256'][:12]}); queries `{rep['queries_file']}`",
             (f"- dev known_item + category queries: {rep['queries']}; without any qrels: "
             f"{rep['queries_without_qrels']}; excluded (no grade > 0): {len(rep['excluded_no_positive_grade'])}"),
             "- nDCG@20 linear gains 0/1/2, unjudged = 0, expected over tie orders", "",
             "## Frozen route per captioner (best dev nDCG@20; ties to fewer suffixes, then name)", "",
             "| captioner | route | dev nDCG@20 | n |", "|---|---|---|---|"]
    for c, f in rep["frozen"].items():
        lines.append(f"| {c} | `{f['route']}` | {_f(f['dev_ndcg'])} | {rep['routes'][f['route']]['n']} |")
    head = [("| route | captioner | n | nDCG@20 | known_item nDCG | category nDCG | MRR (ki) | hit@10 (ki) | "
            "recall2@20 (cat) | recall1@20 (cat) | judged@20 |"), "|---|---|---|---|---|---|---|---|---|---|---|"]

    def row(r, v):
        pk = v["per_kind"]
        return (f"| `{r}` | {v['captioner']} | {v['n']} | {_f(v['ndcg@20'])} | "
                f"{_f(pk.get('known_item', {}).get('ndcg@20'))} | {_f(pk.get('category', {}).get('ndcg@20'))} | "
                f"{_f(v['mrr'])} | {_f(v['hit@10'])} | {_f(v['recall2@20'])} | {_f(v['recall1@20'])} | "
                f"{_f(v['judged@20'])} |")

    lines += ["", "## All routes (dev)", "", *head]
    lines += [row(r, v) for r, v in rows if not v["sensitivity"]]
    sens = [(r, v) for r, v in rows if v["sensitivity"]]
    lines += ["", "## Blend-weight sensitivity (not contenders)", ""]
    lines += [*head, *(row(r, v) for r, v in sens)] if sens else ["No blend-weight sensitivity routes in the index."]
    if rep["excluded_no_positive_grade"]:
        lines += ["", "Excluded query ids: " + ", ".join(rep["excluded_no_positive_grade"])]
    return "\n".join(lines) + "\n"


def render_test_md(rep: dict) -> str:
    ref = rep["reference"]
    lines = [f"# Graded test scoring: {rep['set']} ({rep['date']})", "",
             (f"- primary qrels `{rep['qrels']}`; secondary `{rep.get('secondary_qrels')}`; frozen routes from "
             f"`{rep['frozen_file']}` (chosen on dev with `{rep['frozen_qrels']}`)"),
             (f"- test known_item + category queries: {rep['queries']}; without qrels {rep['queries_without_qrels']}; "
             f"excluded (no grade > 0): {len(rep['excluded_no_positive_grade'])}"),
             (f"- reference: {ref['captioner']} `{ref['route']}`; paired bootstrap {RESAMPLES} resamples, seed {SEED}; "
             f"non-inferiority: lower bound of (candidate - reference) > -{rep['margin']}"), "",
             "## Frozen routes (primary qrels)", "",
             ("| captioner | route | n | nDCG@20 (95% CI) | delta vs ref (95% CI) | MDD | non-inferior | MRR (ki) | "
             "recall2@20 (cat) | judged@20 |"), "|---|---|---|---|---|---|---|---|---|---|"]
    for c, v in rep["frozen"].items():
        p = v["paired"]
        ci = f"({_f(v['ci95'][0])}, {_f(v['ci95'][1])})" if v["ci95"] else ""
        if p:
            delta = f"{p['mean_delta']:+.3f} ({p['delta_ci95'][0]:+.3f}, {p['delta_ci95'][1]:+.3f})"
            ni, md = str(p["non_inferior"]), _f(p["mdd"])
        else:
            delta, ni, md = "reference", "—", "—"
        lines.append(f"| {c} | `{v['route']}` | {v['n']} | {_f(v['mean_ndcg'])} {ci} | {delta} | {md} | {ni} | "
                     f"{_f(v['mrr'])} | {_f(v['recall2@20'])} | {_f(v['judged@20'])} |")
    for key, title in (("per_kind", "kind"), ("per_style", "style"), ("per_facet", "facet")):
        groups = sorted({g for v in rep["frozen"].values() for g in v[key]})
        lines += ["", f"## nDCG@20 per {title}", "", "| captioner | " + " | ".join(groups) + " |",
                  "|---" * (len(groups) + 1) + "|"]
        for c, v in rep["frozen"].items():
            cells = [f"{_f(v[key][g]['ndcg@20'])} (n={v[key][g]['n']})" if g in v[key] else "—" for g in groups]
            lines.append(f"| {c} | " + " | ".join(cells) + " |")
    o = rep["owner_note"]
    lines += ["", f"## owner_note subset (qrels `{o['qrels']}`)", "",
              "| captioner | route | n | nDCG@20 | MRR | hit@10 | judged@20 | excluded |", "|---|---|---|---|---|---|---|---|"]
    for c, v in o["routes"].items():
        lines.append(f"| {c} | `{v['route']}` | {v['n']} | {_f(v['ndcg@20'])} | {_f(v['mrr'])} | {_f(v['hit@10'])} | "
                     f"{_f(v['judged@20'])} | {v['excluded']} |")
    s = rep["secondary"]
    if s:
        lines += ["", f"## Secondary qrels `{s['qrels']}` (test)", "",
                  "| captioner | route | n | nDCG@20 | judged@20 | excluded |", "|---|---|---|---|---|---|"]
        for c, v in s["routes"].items():
            lines.append(f"| {c} | `{v['route']}` | {v['n']} | {_f(v['mean_ndcg'])} | {_f(v['judged@20'])} | "
                         f"{v['excluded']} |")
        lines += ["", "Route-order disagreements (primary vs secondary): "
                  + ("; ".join(f"{d['a']} vs {d['b']} ({d['primary_delta']:+.3f} / {d['secondary_delta']:+.3f})"
                               for d in s["order_disagreements"]) or "none")]
    dec = rep["decision"]
    lines += ["", "## Decision (gates.toml v{})".format(rep["gates_version"]), "",
              ("| captioner | search NI | caption rule | error rate (ref) | mono precision | colour filter ok | local | "
              "USD std | USD batch | hours | subscription tokens |"), "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, v in dec["candidates"].items():
        cr = v["caption"]
        rule = {True: "pass", False: "fail", None: "n/a"}[cr["pass"]]
        if c == "opus-v2":
            rule = f"reported, not gated (within margin: {cr.get('within_margin')})"
        lines.append(f"| {c} | {v['search_ni']} | {rule} | {_f(cr.get('error_rate'))} ({_f(cr.get('reference_error_rate'))}) "
                     f"| {_f(cr.get('mono_precision'))} | {cr.get('mono_ok')} | {v['local']} | {_f(v['usd'], 2)} | "
                     f"{_f(v['batch_usd'], 2)} | {_f(v['hours'], 1)} | "
                     f"{_tokens(v['subscription_tokens'])} |")
    r = dec["recommendation"]
    lines += ["", f"- dropped: {dec['dropped'] or 'none'}", f"- recommendation: **{r['captioner']}** (rule {r['rule']})"]
    if "batch_usd" in r:
        lines.append(f"- batch quote {_f(r['batch_usd'], 2)} USD vs remaining budget {_f(dec['remaining_usd'], 2)} "
                     f"USD: fits = {r['fits_budget']}")
        if r["other_survivors_not_in_order"]:
            lines.append(f"- also surviving, not covered by the decision order: {r['other_survivors_not_in_order']}")
    lines += [f"- colour_class usable as a filter: {r['colour_filter']}",
              f"- zero-cost fallback (search only): {dec['fallback']}",
              (f"- hosted spend so far {_f(dec['spent_usd'], 2)} of {_f(dec['budget_usd'], 2)} USD; "
              f"remaining {_f(dec['remaining_usd'], 2)}")]
    lines += ["", "## Appendix: non-frozen routes on test — not used for the decision", "",
              "| route | n | nDCG@20 | judged@20 |", "|---|---|---|---|"]
    for route, v in rep["appendix_not_used"].items():
        lines.append(f"| `{route}` | {v['n']} | {_f(v['mean_ndcg'])} | {_f(v['judged@20'])} |")
    return "\n".join(lines) + "\n"
