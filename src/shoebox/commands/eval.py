"""`shoebox eval`: generate Vally specs from a set (`gen`) and report on Vally results (`report`).

Known-item search: `search-queries`, `search-run`, `gen-search` and `search-report` (docs/evals.md).
"""

import json
import os
import sys
from pathlib import Path

from shoebox import config
from shoebox.config import REPO_ROOT


def register(sub) -> None:
    p = sub.add_parser(
        "eval", help="generate Vally eval specs and report on Vally results"
    )
    esub = p.add_subparsers(dest="eval_command", required=True)

    g = esub.add_parser(
        "gen", help="write evals/vally/generated/<set>/eval.yaml and candidates.json"
    )
    g.add_argument(
        "--set",
        required=True,
        dest="set_name",
        help="set name under evals/sets/, e.g. pilot-v0",
    )
    g.add_argument(
        "--candidates",
        required=True,
        help="comma-separated candidate names (config/candidates.toml)",
    )
    g.add_argument(
        "--mode",
        choices=["replay", "live"],
        default="replay",
        help="executor mode (gate runs: replay)",
    )
    g.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    g.add_argument(
        "--out-dir", type=Path, default=None, help="default evals/vally/generated/<set>"
    )
    g.add_argument(
        "--resolutions",
        type=Path,
        default=None,
        help="JSON {candidate: {model_id, params_hash, store_dir}} used instead of `shoebox label resolve` "
        "(offline/tests)",
    )
    g.set_defaults(func=_gen)

    r = esub.add_parser(
        "report",
        help="write evals/runs/<date>_<set>.json and .md from a Vally results.jsonl",
    )
    r.add_argument(
        "--results",
        required=True,
        type=Path,
        help="results.jsonl from `vally eval` or `vally grade`",
    )
    r.add_argument(
        "--set",
        dest="set_name",
        default=None,
        help="expected set (default: the set recorded in the trials; must agree with it)",
    )
    r.add_argument(
        "--candidates-file",
        type=Path,
        default=None,
        help="candidates.json listing the set's items (default: the one recorded in the trials)",
    )
    r.add_argument("--date", default=None, help="run date YYYY-MM-DD (default today)")
    r.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evals" / "runs")
    r.set_defaults(func=_report)
    _register_search(esub)
    _register_judge(esub)
    _register_audit(esub)
    _register_caption_audit(esub)
    _register_opus_pool(esub)
    _register_graded(esub)
    _register_colour(esub)


def _register_search(esub) -> None:
    from shoebox.evals import search

    q = esub.add_parser("search-queries",
                        help="write evals/sets/<set>/search-known-item.jsonl from the owner's label notes")
    q.add_argument("--set", required=True, dest="set_name")
    q.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    q.set_defaults(func=_search_queries)

    s = esub.add_parser("search-run",
                        help="rank the set for every query along each route; write results/search/<set>/")
    s.add_argument("--set", required=True, dest="set_name")
    s.add_argument("--routes", default=None,
                   help="comma-separated routes (default: eg2-image@768,256 and every route of --candidates)")
    s.add_argument("--candidates", default=",".join(search.VLM_CANDIDATES),
                   help="VLM label candidates for the default routes")
    s.add_argument("--no-sensitivity", action="store_true", help="omit the eg2-blend@w0.3/0.7 routes")
    s.add_argument("--embed-candidate", default=search.EMBED_CANDIDATE)
    s.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    s.add_argument("--queries", default=search.QUERIES_FILE, help="query file in the set dir")
    s.add_argument("--grid", choices=("default", "eval1k"), default="default",
                   help="route grid when --routes is not given (eval1k: meta, fusion, +exp/+filt variants)")
    s.add_argument("--frame", type=Path, default=None,
                   help="frame parquet for meta/+filt routes (default: newest <shoebox_root>/frame/frame-*.parquet)")
    s.set_defaults(func=_search_run)

    x = esub.add_parser("expand", help="expand queries with the local E4B (cached in results/expansions/)")
    x.add_argument("--set", required=True, dest="set_name")
    x.add_argument("--queries", default=search.QUERIES_FILE, help="query file in the set dir")
    x.add_argument("--split", default=None, help="only queries of this split (dev|test|owner)")
    x.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    x.set_defaults(func=_expand)

    g = esub.add_parser("gen-search",
                        help="write evals/vally/generated/<set>-search/eval.yaml and routes.json")
    g.add_argument("--set", required=True, dest="set_name")
    g.add_argument("--routes", default=None, help="comma-separated routes (default: every indexed route)")
    g.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    g.add_argument("--out-dir", type=Path, default=None, help="default evals/vally/generated/<set>-search")
    g.add_argument("--index", type=Path, default=None,
                   help="rankings index (default <shoebox_root>/results/search/<set>/index.json)")
    g.set_defaults(func=_gen_search)

    pl = esub.add_parser("search-pool",
                         help="new set: a base set's items and queries plus random distractors from the frame")
    pl.add_argument("--set", required=True, dest="set_name", help="base set (its queries are copied)")
    pl.add_argument("--frame", required=True, type=Path, help="<shoebox_root>/frame/frame-<date>.parquet")
    pl.add_argument("--n", type=int, default=5000, help="distractors to draw")
    pl.add_argument("--seed", type=int, default=20261010)
    pl.add_argument("--out-set", required=True, help="new set name, e.g. pilot-v0-pool5k")
    pl.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    pl.add_argument("--never-send", type=Path, default=None, help="default evals/never-send.txt")
    pl.add_argument("--force", action="store_true", help="overwrite an existing pool set's files")
    pl.set_defaults(func=_search_pool)

    nd = esub.add_parser("search-neardups",
                         help="write evals/sets/<set>/near-dups.jsonl: distractors closest to a target (EG2 image)")
    nd.add_argument("--set", required=True, dest="set_name")
    nd.add_argument("--threshold", type=float, default=0.90, help="list every distractor at or above this cosine")
    nd.add_argument("--top", type=int, default=200, help="also list the N closest regardless of the threshold")
    nd.add_argument("--embed-candidate", default=search.EMBED_CANDIDATE)
    nd.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    nd.set_defaults(func=_search_neardups)

    cd = esub.add_parser("corpus-draw",
                         help="draw a screened eval corpus into evals/sets/<name>/ (exit 3: screening needed)")
    cd.add_argument("--name", required=True, help="new set name, e.g. eval-1k")
    cd.add_argument("--base", required=True, help="base set, e.g. pilot-v0-pool5k")
    cd.add_argument("--labelled-by", required=True, help="label candidate whose final records make the candidates")
    cd.add_argument("--n", type=int, required=True)
    cd.add_argument("--seed", type=int, required=True)
    cd.add_argument("--buffer", type=int, default=80, help="extra candidates queued for screening")
    cd.add_argument("--screen", type=Path, default=None, help="default evals/sets/<name>/screen.jsonl")
    cd.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    cd.set_defaults(func=_corpus_draw)

    qs = esub.add_parser("query-sources", help="draw known-item query sources into evals/sets/<set>/query-sources.jsonl")
    qs.add_argument("--set", required=True, dest="set_name")
    qs.add_argument("--n", type=int, default=200)
    qs.add_argument("--seed", type=int, default=20261012)
    qs.add_argument("--frame", type=Path, default=None, help="default: newest <shoebox_root>/frame/frame-*.parquet")
    qs.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    qs.set_defaults(func=_query_sources)

    qp = esub.add_parser("query-packets", help="write batch packets with 768px thumbnails for the query writers")
    qp.add_argument("--set", required=True, dest="set_name")
    qp.add_argument("--out", required=True, type=Path, help="scratch dir (not in git)")
    qp.add_argument("--batch", type=int, default=25)
    qp.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    qp.set_defaults(func=_query_packets)

    cs = esub.add_parser("corpus-summary", help="print a markdown summary of a set for the category writer")
    cs.add_argument("--set", required=True, dest="set_name")
    cs.add_argument("--frame", type=Path, default=None, help="default: newest <shoebox_root>/frame/frame-*.parquet")
    cs.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    cs.set_defaults(func=_corpus_summary)

    qi = esub.add_parser("query-ingest", help="validate, store and split the written queries into queries-v1.jsonl")
    qi.add_argument("--set", required=True, dest="set_name")
    qi.add_argument("--known", required=True, help="glob of known-item JSONL files")
    qi.add_argument("--category", required=True, type=Path)
    qi.add_argument("--model-id", required=True)
    qi.add_argument("--dev-pin", type=Path, default=None, help="items JSONL whose capture groups go to dev")
    qi.add_argument("--seed", type=int, default=20261012)
    qi.add_argument("--notes", type=Path, default=None, help="owner notes (default pilot-v0-pool5k known-item)")
    qi.add_argument("--prompts-dir", type=Path, default=None)
    qi.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    qi.set_defaults(func=_query_ingest)

    r = esub.add_parser("search-report",
                        help="write evals/runs/<date>_<set>-search.json and .md from a Vally results.jsonl")
    r.add_argument("--results", required=True, type=Path)
    r.add_argument("--routes-file", type=Path, default=None,
                   help="routes.json (default: the one recorded in the trials)")
    r.add_argument("--date", default=None, help="run date YYYY-MM-DD (default today)")
    r.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evals" / "runs")
    r.set_defaults(func=_search_report)


def _register_judge(esub) -> None:
    from shoebox.evals import judge

    jp = esub.add_parser("judge-pool",
                         help="pool every route's top-k per query plus its targets into evals/sets/<set>/pool-v1.jsonl")
    jp.add_argument("--set", required=True, dest="set_name")
    jp.add_argument("--queries", default="queries-v1.jsonl", help="query file in the set dir")
    jp.add_argument("--k", type=int, default=10)
    jp.add_argument("--split", choices=("dev", "test"), default=None)
    jp.add_argument("--out", default=judge.POOL_FILE)
    jp.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    jp.set_defaults(func=_judge_pool)

    j = esub.add_parser("judge", help="grade pooled (query, photo) pairs 0/1/2 with the Gemini grid judge")
    j.add_argument("--set", required=True, dest="set_name")
    j.add_argument("--pool", default=judge.POOL_FILE, help="pool file in the set dir")
    j.add_argument("--split", choices=("dev", "test"), default=None)
    j.add_argument("--limit-queries", type=int, default=None)
    j.add_argument("--max-usd", type=float, default=judge.DEFAULT_MAX_USD)
    j.add_argument("--dry-run", action="store_true", help="print grids, images and estimated cost; call nothing")
    j.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    j.set_defaults(func=_judge)

    jq = esub.add_parser("judgments", help="export the judge's ok grades to evals/sets/<set>/judgments-v1.jsonl")
    jq.add_argument("--set", required=True, dest="set_name")
    jq.add_argument("--out", default=judge.JUDGMENTS_FILE)
    jq.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    jq.set_defaults(func=_judgments)


def _register_audit(esub) -> None:
    from shoebox.evals import audit

    sets = REPO_ROOT / "evals" / "sets"
    s = esub.add_parser("audit-sample", help="stratified sample of Gemini-judged pairs for the Opus audit (D10)")
    s.add_argument("--set", required=True, dest="set_name")
    s.add_argument("--pool", default="pool-v2.jsonl")
    s.add_argument("--judgments", default="judgments-v1.jsonl", help="`eval judgments` export in the set dir")
    s.add_argument("--frac", type=float, default=audit.DEFAULT_FRAC)
    s.add_argument("--seed", type=int, default=audit.DEFAULT_SEED)
    s.add_argument("--sets-dir", type=Path, default=sets)
    s.set_defaults(func=_audit_sample)

    p = esub.add_parser("audit-packets", help="numbered contact sheets and batch files for Opus subagents")
    p.add_argument("--set", required=True, dest="set_name")
    p.add_argument("--out", required=True, type=Path, help="scratch dir (never under evals/)")
    p.add_argument("--pool", default="pool-v2.jsonl")
    p.add_argument("--grid", type=int, default=10)
    p.add_argument("--per-batch", type=int, default=30, help="grids per batch file")
    p.add_argument("--seed", type=int, default=audit.DEFAULT_SEED)
    p.add_argument("--frame", type=Path, default=None)
    p.add_argument("--sets-dir", type=Path, default=sets)
    p.set_defaults(func=_audit_packets)

    i = esub.add_parser("audit-ingest", help="validate and store the subagents' grades-*.jsonl")
    i.add_argument("--set", required=True, dest="set_name")
    i.add_argument("--dir", required=True, type=Path)
    i.add_argument("--model-id", default=audit.MODEL_ID)
    i.set_defaults(func=_audit_ingest)

    r = esub.add_parser("audit-report", help="Gemini vs Opus weighted kappa -> evals/runs/<date>_<set>-judge-audit")
    r.add_argument("--set", required=True, dest="set_name")
    r.add_argument("--judgments", default="judgments-v1.jsonl")
    r.add_argument("--model-id", default=audit.MODEL_ID)
    r.add_argument("--params-hash", default=None)
    r.add_argument("--date", default=None)
    r.add_argument("--bootstrap", type=int, default=audit.BOOTSTRAP)
    r.add_argument("--sets-dir", type=Path, default=sets)
    r.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evals" / "runs")
    r.set_defaults(func=_audit_report)


def _register_caption_audit(esub) -> None:
    from shoebox.evals import caption_audit as ca

    sets = REPO_ROOT / "evals" / "sets"
    s = esub.add_parser("caption-sample", help="stratified photos for the Opus caption audit (task 6.5)")
    s.add_argument("--set", required=True, dest="set_name")
    s.add_argument("--n", type=int, default=ca.DEFAULT_N)
    s.add_argument("--seed", type=int, default=ca.DEFAULT_SEED)
    s.add_argument("--queries", default="queries-v1.jsonl", help="query file whose dev groups are excluded")
    s.add_argument("--frame", type=Path, default=None)
    s.add_argument("--sets-dir", type=Path, default=sets)
    s.set_defaults(func=_caption_sample)

    p = esub.add_parser("caption-packets", help="thumbnails and blind caption sets A-E for Opus subagents")
    p.add_argument("--set", required=True, dest="set_name")
    p.add_argument("--out", required=True, type=Path, help="scratch dir (never under evals/)")
    p.add_argument("--per-batch", type=int, default=20)
    p.add_argument("--seed", type=int, default=ca.DEFAULT_SEED)
    p.add_argument("--arms", default=",".join(ca.ARMS),
                   help=f"comma-separated arms, up to {len(ca.LABELS)} (options: {','.join(ca.ARM_OPTIONS)})")
    p.add_argument("--sets-dir", type=Path, default=sets)
    p.set_defaults(func=_caption_packets)

    i = esub.add_parser("caption-ingest", help="validate, un-blind and store the subagents' audit-*.jsonl")
    i.add_argument("--set", required=True, dest="set_name")
    i.add_argument("--dir", required=True, type=Path)
    i.add_argument("--model-id", default=ca.MODEL_ID)
    i.add_argument("--sets-dir", type=Path, default=sets)
    i.set_defaults(func=_caption_ingest)

    r = esub.add_parser("caption-report", help="per-arm caption audit -> evals/runs/<date>_<set>-caption-audit")
    r.add_argument("--set", required=True, dest="set_name")
    r.add_argument("--model-id", default=ca.MODEL_ID)
    r.add_argument("--params-hash", default=None)
    r.add_argument("--date", default=None)
    r.add_argument("--bootstrap", type=int, default=ca.BOOTSTRAP)
    r.add_argument("--rescore-params-hash", default=None, help="caption_usefulness store dir (default: the only one)")
    r.add_argument("--owner-params-hash", default=None, help="owner_reviews store dir (default: the only one)")
    r.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evals" / "runs")
    r.add_argument("--auditor", choices=("opus", "gemini", "both"), default="opus")
    r.add_argument("--gemini-model-id", default="gemini/gemini-3.8-flash")
    r.add_argument("--gemini-params-hash", default=None, help="Gemini caption_audits store dir (default: the only one)")
    r.set_defaults(func=_caption_report)

    from shoebox.evals import caption_audit_gemini as cg

    g = esub.add_parser("caption-audit-gemini", help="audit a caption-packets dir with Gemini (second auditor)")
    g.add_argument("--set", required=True, dest="set_name")
    g.add_argument("--packets", required=True, type=Path, help="caption-packets dir (batch-*.json, map.json)")
    g.add_argument("--max-usd", type=float, default=cg.DEFAULT_MAX_USD)
    g.add_argument("--limit", type=int, default=None, help="at most N photos this run")
    g.add_argument("--dry-run", action="store_true", help="plan and estimate only: no HTTP, no spend")
    g.add_argument("--sets-dir", type=Path, default=sets)
    g.set_defaults(func=_caption_audit_gemini)
    _register_review(esub, sets)


def _register_colour(esub) -> None:
    from shoebox.evals import colour

    sets, evals = REPO_ROOT / "evals" / "sets", REPO_ROOT / "evals"

    def common(p):
        p.add_argument("--frame", type=Path, default=None, help="default: newest frame-*.parquet")
        p.add_argument("--sets-dir", type=Path, default=sets)
        p.add_argument("--audit-set", default="eval-1k", help="set holding the Opus caption-audit sample")
        p.add_argument("--params-hash", default=None, help="caption_audits store dir (default: the only one)")

    a = esub.add_parser("colour-audit", help="frame colour_guess vs the Opus caption-audit reference (counts only)")
    common(a)
    a.set_defaults(func=_colour_audit)

    d = esub.add_parser("colour-draw", help=f"mono-stratified sample -> evals/sets/{colour.SET_NAME}/items.jsonl")
    common(d)
    d.add_argument("--seed", type=int, default=colour.DEFAULT_SEED)
    d.add_argument("--n-mono", type=int, default=colour.N_MONO)
    d.add_argument("--n-near", type=int, default=colour.N_NEAR)
    d.add_argument("--floor", type=int, default=colour.FLOOR)
    d.add_argument("--profile", default="infer-v1")
    d.set_defaults(func=_colour_draw)

    t = esub.add_parser("colour-tune", help="grid-search ColourThresholds on the dev half and freeze them")
    common(t)
    t.add_argument("--with-audit", action="store_true", help="also tune on the caption-audit sample (flagged)")
    t.add_argument("--min-pred", type=int, default=colour.MIN_PRED)
    t.add_argument("--gates", type=Path, default=evals / "gates.toml")
    t.add_argument("--frozen", type=Path, default=evals / colour.FROZEN_PATH)
    t.set_defaults(func=_colour_tune)

    s = esub.add_parser("colour-test", help="score the frozen thresholds once on the test half")
    common(s)
    s.add_argument("--min-pred", type=int, default=colour.MIN_PRED)
    s.add_argument("--gates", type=Path, default=evals / "gates.toml")
    s.add_argument("--frozen", type=Path, default=evals / colour.FROZEN_PATH)
    s.add_argument("--caption-audit-json", type=Path, default=None,
                   help="eval-1k caption audit report (default: newest <out-dir>/*_eval-1k-caption-audit.json)")
    s.add_argument("--force-rescore", default=None, metavar="REASON", help="score again; the reason is recorded")
    s.add_argument("--date", default=None)
    s.add_argument("--out-dir", type=Path, default=evals / "runs")
    s.set_defaults(func=_colour_test)


def _mono_gate(path: Path) -> float:
    import tomllib

    return float(tomllib.loads(Path(path).read_text())["captions"]["mono_precision_min"])


def _colour_audit_inputs(args, cfg, frame_rows=None):
    from shoebox.evals import caption_audit as ca
    from shoebox.evals import colour, search

    _ph, recs = ca.audit_records(cfg, ca.MODEL_ID, args.params_hash)
    items = search.read_jsonl(args.sets_dir / args.audit_set / "items.jsonl")
    sample = {r["asset_id"] for r in search.read_jsonl(args.sets_dir / args.audit_set / ca.SAMPLE_FILE)}
    frame_rows = frame_rows if frame_rows is not None else colour.read_frame(_frame(cfg, args.frame))
    recs = [r for r in recs if r["asset_id"] in sample]
    return recs, colour.rows_for([it for it in items if it["asset_id"] in sample], frame_rows)


def _fmt_mono(m: dict) -> str:
    return f"n {m['n']}, tp {m['tp']}, fp {m['fp']}, fn {m['fn']}, precision {m['precision']}, recall {m['recall']}"


def _colour_audit(args) -> int:
    from shoebox.evals import colour

    try:
        recs, frows = _colour_audit_inputs(args, _cfg())
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval colour-audit: {e}", file=sys.stderr)
        return 2
    rep = colour.audit(recs, frows)
    sc = rep["frame"]
    print(f"{rep['records']} audit records, {rep['n_trusted']} with a high/medium colour reference: "
          f"{rep['predicted_mono']} frame-mono, {rep['reference_mono']} reference mono")
    print(f"mono overall: {_fmt_mono(sc['overall'])}")
    for s in colour.STRATA:
        print(f"mono {s}: {_fmt_mono(sc['per_stratum'][s])}")
    print(f"mono lenient (toned = mono): {_fmt_mono(sc['lenient_toned'])}")
    print("confusion (guess -> reference counts): " + json.dumps(sc["confusion"], sort_keys=True))
    return 0


def _colour_draw(args) -> int:
    from shoebox import derive, privacy
    from shoebox.evals import caption_audit as ca
    from shoebox.evals import colour, judge, search

    cfg = _cfg()
    try:
        frame_rows = colour.read_frame(_frame(cfg, args.frame))
        derived = {a for a, r in derive.load_index(cfg, args.profile).items() if r.get("status") == "ok"}
        exclude = {r["asset_id"] for r in search.read_jsonl(args.sets_dir / args.audit_set / ca.SAMPLE_FILE)}
        items, meta = colour.draw(frame_rows, derived, exclude, cfg, args.seed, args.n_mono, args.n_near, args.floor)
    except privacy.PrivacyError as e:
        print(f"shoebox eval colour-draw: privacy check refused the draw: {e}", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval colour-draw: {e}", file=sys.stderr)
        return 2
    out = args.sets_dir / colour.SET_NAME
    out.mkdir(parents=True, exist_ok=True)
    judge.write_jsonl(out / "items.jsonl", items)
    (out / colour.SAMPLE_META).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    for b, m in meta["buckets"].items():
        print(f"{b}: drawn {m['drawn']} of available {m['available']}"
              + (f"; shortfall below floor {args.floor}: {m['shortfall']}" if m["shortfall"] else ""))
    print(f"wrote {out / 'items.jsonl'}: {meta['n']} items ({meta['splits']}); skipped {meta['skipped']}")
    return 0


def _colour_rows(args, cfg, split: str):
    from shoebox.evals import colour, search

    frame_rows = colour.read_frame(_frame(cfg, args.frame))
    items = [it for it in search.read_jsonl(args.sets_dir / colour.SET_NAME / "items.jsonl")
             if it.get("split") == split]
    refs = colour.load_labels(cfg, colour.REFERENCE_CANDIDATE)
    gem = colour.load_labels(cfg, colour.GEMINI_CANDIDATE)
    labelled = sum((refs.get(it["asset_id"]) or {}).get("status") == "ok" for it in items)
    return (colour.labelled_rows(items, colour.rows_for(items, frame_rows), refs, gem), (labelled, len(items)),
            frame_rows, gem)


def _colour_tune(args) -> int:
    from shoebox.evals import colour

    cfg = _cfg()
    try:
        gate = _mono_gate(args.gates)
        rows, (_lab, n_items), frame_rows, gem = _colour_rows(args, cfg, "dev")
        n_audit = 0
        if args.with_audit:
            recs, frows = _colour_audit_inputs(args, cfg, frame_rows)
            extra = colour.audit_rows(recs, frows, gem)
            n_audit, rows = len(extra), rows + extra
        if not rows:
            raise ValueError(f"no dev rows with a trusted {colour.REFERENCE_CANDIDATE} colour_class yet")
        tuned = colour.tune(rows, gate, args.min_pred)
        doc = colour.freeze(args.frozen, tuned, {"dev_items": n_items, "with_audit_sample": args.with_audit,
                                                 "audit_rows": n_audit, "grid": tuned["grid"],
                                                 "combination": tuned["combination"]})
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"shoebox eval colour-tune: {e}", file=sys.stderr)
        return 2
    for g in tuned["grid"]:
        print(f"mono_c95 {g['mono_c95']}: feasible {g['feasible']}, precision {g['precision']}, "
              f"recall {g['recall']}, predicted {g['predicted']}, strata {g['strata']}")
    print(f"froze {args.frozen}: mono_c95 {doc['thresholds']['mono_c95']}, hash {doc['hash']}, {len(rows)} dev rows"
          f"{f' incl. {n_audit} audit-sample rows' if args.with_audit else ''}; strata {doc['dev_strata']}")
    return 0


def _colour_test(args) -> int:
    from datetime import date

    from shoebox.evals import colour

    cfg = _cfg()
    day = args.date or date.today().isoformat()
    try:
        gate = _mono_gate(args.gates)
        _t, doc = colour.load_frozen(args.frozen)
        rows, cov, frame_rows, _gem = _colour_rows(args, cfg, "test")
        try:
            recs, frows = _colour_audit_inputs(args, cfg, frame_rows)
            audit = colour.audit(recs, frows)
        except (ValueError, FileNotFoundError) as e:
            audit = {"unavailable": str(e)[:200]}
        cj = args.caption_audit_json or max(args.out_dir.glob("*_eval-1k-caption-audit.json"),
                                            default=args.out_dir / "missing.json")
        dev = {k: doc.get(k) for k in ("grid", "dev_strata", "dev_n", "with_audit_sample", "audit_rows",
                                        "combination")}
        rep = colour.test_report(rows, args.frozen, args.out_dir, day, gate, args.min_pred, args.force_rescore,
                                 audit, dev, colour.comparisons(cj), cov)
    except (colour.AlreadyScored, colour.NotReady) as e:
        print(f"shoebox eval colour-test: {e}", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"shoebox eval colour-test: {e}", file=sys.stderr)
        return 2
    for s in colour.STRATA:
        m = rep["strata"][s]
        print(f"test {s}: {_fmt_mono(m)}, {m['status']}, filter {rep['filter'][s]}")
    print(f"wrote {args.out_dir / rep['report']}.md/.json (frozen hash {rep['frozen_hash']})")
    return 0


def _register_review(esub, sets: Path) -> None:
    from shoebox.evals import review as rv

    p = esub.add_parser("review-page", help="offline owner review page: blind caption usefulness + query realism")
    p.add_argument("--set", required=True, dest="set_name")
    p.add_argument("--packets", required=True, type=Path, help="caption-packets dir (batch-*.json, map.json)")
    p.add_argument("--n", type=int, default=rv.DEFAULT_N)
    p.add_argument("--seed", type=int, default=rv.DEFAULT_SEED)
    p.add_argument("--realism", required=True, type=Path, help="JSON list of query_ids")
    p.add_argument("--queries", default="queries-v1.jsonl")
    p.add_argument("--out", required=True, type=Path, help="scratch dir (never under evals/)")
    p.add_argument("--sets-dir", type=Path, default=sets)
    p.set_defaults(func=_review_page)

    i = esub.add_parser("review-ingest", help="validate, un-blind and store the owner's downloaded answers")
    i.add_argument("--set", required=True, dest="set_name")
    i.add_argument("--answers", required=True, type=Path)
    i.add_argument("--page", required=True, type=Path, help="the review-page --out dir (page-map.json)")
    i.add_argument("--queries", default="queries-v1.jsonl")
    i.add_argument("--sets-dir", type=Path, default=sets)
    i.set_defaults(func=_review_ingest)

    r = esub.add_parser("rescore-ingest", help="validate, un-blind and store Opus rubric usefulness rescore-*.jsonl")
    r.add_argument("--set", required=True, dest="set_name")
    r.add_argument("--dir", required=True, type=Path)
    r.add_argument("--model-id", default=None)
    r.add_argument("--sets-dir", type=Path, default=sets)
    r.set_defaults(func=_rescore_ingest)


def _review_page(args) -> int:
    from shoebox.evals import caption_audit as ca
    from shoebox.evals import review as rv
    from shoebox.evals import search

    set_dir = args.sets_dir / args.set_name
    try:
        rows = search.read_jsonl(set_dir / ca.SAMPLE_FILE)
        qs = search.read_jsonl(set_dir / args.queries)
        ids = json.loads(args.realism.read_text())
        pm = rv.build_page(args.packets, rows, qs, ids, args.n, args.seed, args.out, rv.rubric_text())
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"shoebox eval review-page: {e}", file=sys.stderr)
        return 2
    print(f"wrote {args.out / 'index.html'}: {len(pm['photos'])} photos, {len(pm['queries'])} queries "
          f"(page {pm['page_id']})")
    return 0


def _review_ingest(args) -> int:
    from shoebox.evals import review as rv
    from shoebox.evals import search

    set_dir = args.sets_dir / args.set_name
    try:
        ans = json.loads(args.answers.read_text())
        qs = search.read_jsonl(set_dir / args.queries)
        res = rv.review_ingest(_cfg(), ans, args.page, rv.rubric_text(), qs, set_dir / "QUERIES.md")
    except (rv.IngestError, ValueError, FileNotFoundError) as e:
        print(f"shoebox eval review-ingest: {e}", file=sys.stderr)
        return 1
    print(f"stored {res.stored} owner reviews ({res.already} already stored, {res.incomplete} incomplete skipped) "
          f"as {rv.OWNER_MODEL_ID} {res.params_hash}; updated {set_dir / 'QUERIES.md'}")
    if res.conflicts:
        print(f"shoebox eval review-ingest: {len(res.conflicts)} already stored differently "
              f"(kept the stored one): {res.conflicts[:10]}", file=sys.stderr)
        return 1
    return 0


def _rescore_ingest(args) -> int:
    from shoebox.evals import caption_audit as ca
    from shoebox.evals import review as rv
    from shoebox.evals import search

    try:
        rows = search.read_jsonl(args.sets_dir / args.set_name / ca.SAMPLE_FILE)
        res = rv.rescore_ingest(_cfg(), args.dir, rows, rv.rubric_text(), args.model_id or ca.MODEL_ID)
    except (rv.IngestError, FileNotFoundError) as e:
        print(f"shoebox eval rescore-ingest: {e}", file=sys.stderr)
        return 1
    print(f"stored {res.stored} usefulness rescores ({res.already} already stored) as "
          f"{args.model_id or ca.MODEL_ID} {res.params_hash}")
    if res.conflicts:
        print(f"shoebox eval rescore-ingest: {len(res.conflicts)} photos already stored differently "
              f"(kept the stored one): {res.conflicts[:10]}", file=sys.stderr)
        return 1
    return 0


def _caption_sample(args) -> int:
    from shoebox.evals import caption_audit as ca
    from shoebox.evals import judge, queries, search

    set_dir = args.sets_dir / args.set_name
    try:
        items = search.read_jsonl(set_dir / "items.jsonl")
        qs = search.read_jsonl(set_dir / args.queries)
        frows = queries.frame_rows_for(items, _frame(_cfg(), args.frame))
        rows, meta = ca.draw(items, frows, qs, args.n, args.seed)
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval caption-sample: {e}", file=sys.stderr)
        return 2
    judge.write_jsonl(set_dir / ca.SAMPLE_FILE, rows)
    (set_dir / ca.SAMPLE_META).write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {set_dir / ca.SAMPLE_FILE}: {meta['n']} photos from {meta['candidates']} capture groups "
          f"({meta['excluded_dev_groups']} dev groups excluded) over {len(meta['strata'])} strata")
    return 0


def _caption_packets(args) -> int:
    from shoebox import privacy
    from shoebox.evals import caption_audit as ca
    from shoebox.evals import search

    set_dir = args.sets_dir / args.set_name
    cfg = _cfg()
    try:
        ca.check_out_dir(args.out)
        rows = search.read_jsonl(set_dir / ca.SAMPLE_FILE)
        items = search.read_jsonl(set_dir / "items.jsonl")
        labels, sources = ca.load_arms(cfg, _split(args.arms))
        res = ca.packets(cfg, rows, items, labels, args.out, args.per_batch, args.seed, privacy.known_items(),
                         sources)
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"shoebox eval caption-packets: {e}", file=sys.stderr)
        return 2
    for s in res.skipped:
        print(f"  skipped {s['key']}: {s['reason']}", file=sys.stderr)
    for m in res.missing:
        print(f"  {m['key']}: no caption from {m['arm']}", file=sys.stderr)
    print(f"wrote {len(res.photos)} photos in {len(res.batches)} batches to {args.out}; {len(res.skipped)} skipped, "
          f"{len(res.missing)} missing caption sets")
    return 0


def _caption_ingest(args) -> int:
    from shoebox.evals import caption_audit as ca
    from shoebox.evals import search

    try:
        rows = search.read_jsonl(args.sets_dir / args.set_name / ca.SAMPLE_FILE)
        res = ca.ingest(_cfg(), args.dir, rows, ca.instructions_text(), args.model_id)
    except (ca.IngestError, FileNotFoundError) as e:
        print(f"shoebox eval caption-ingest: {e}", file=sys.stderr)
        return 1
    print(f"stored {res.stored} caption audits ({res.already} already stored) as {args.model_id} {res.params_hash}")
    if res.conflicts:
        print(f"shoebox eval caption-ingest: {len(res.conflicts)} photos already stored differently "
              f"(kept the stored one): {res.conflicts[:10]}", file=sys.stderr)
        return 1
    return 0


def _caption_report(args) -> int:
    from datetime import date

    from shoebox.evals import caption_audit as ca
    from shoebox.evals import caption_audit_gemini as cg
    from shoebox.evals import review as rv
    from shoebox.evals import search

    cfg = _cfg()
    day = args.date or date.today().isoformat()
    try:
        if args.auditor in ("opus", "both"):
            ph, recs = ca.audit_records(cfg, args.model_id, args.params_hash)
            rph, rescore = rv.records(cfg, rv.RESCORE_KIND, args.model_id, args.rescore_params_hash)
            oph, owner = rv.records(cfg, rv.OWNER_KIND, rv.OWNER_MODEL_ID, args.owner_params_hash)
        if args.auditor in ("gemini", "both"):
            gph, grecs = ca.audit_records(cfg, args.gemini_model_id, args.gemini_params_hash)
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval caption-report: {e}", file=sys.stderr)
        return 2
    if args.auditor in ("opus", "both"):
        meta = {"set": args.set_name, "date": day, "opus_model_id": args.model_id, "opus_params_hash": ph}
        if rescore:
            meta["rescore_params_hash"] = rph
            if owner:
                meta["owner_params_hash"] = oph
        rep = ca.build_report(recs, meta, args.bootstrap, rescore=rescore or None, owner=owner or None,
                              self_audit_arm=cg.SELF_AUDIT.get(args.model_id))
    if args.auditor in ("gemini", "both"):
        gmeta = {"set": args.set_name, "date": day, "auditor": "Gemini", "auditor_model_id": args.gemini_model_id,
                 "auditor_params_hash": gph}
        grep_ = ca.build_report(grecs, gmeta, args.bootstrap, self_audit_arm=cg.SELF_AUDIT.get(args.gemini_model_id))
    if args.auditor == "gemini":
        rep, suffix, md = grep_, "caption-audit-gemini", ca.render_md(grep_)
    elif args.auditor == "both":
        rep = cg.compare(rep, grep_, recs, grecs, {"set": args.set_name, "date": day})
        suffix, md = "caption-audit-both", cg.render_compare_md(rep)
    else:
        suffix, md = "caption-audit", ca.render_md(rep)
    stem = args.out_dir / f"{day}_{args.set_name}-{suffix}"
    search._atomic_write(stem.with_suffix(".json"), json.dumps(rep, indent=1, sort_keys=True) + "\n")
    search._atomic_write(stem.with_suffix(".md"), md)
    if args.auditor == "both":
        print(f"wrote {stem}.md/.json: Opus and Gemini auditors, {len(rep['side_by_side'])} arms")
    else:
        print(f"wrote {stem}.md/.json: {rep['photos']} photos, {len(rep['arms'])} arms")
    return 0


def _caption_audit_gemini(args) -> int:
    from shoebox import privacy, runners
    from shoebox.evals import caption_audit as ca
    from shoebox.evals import caption_audit_gemini as cg
    from shoebox.evals import search
    from shoebox.runners import gemini

    set_dir = args.sets_dir / args.set_name
    cfg = _cfg()
    try:
        rows = search.read_jsonl(set_dir / ca.SAMPLE_FILE)
        items = search.read_jsonl(set_dir / "items.jsonl")
        cand = cg.candidate(runners.load_candidates()[cg.BASE_CANDIDATE], args.max_usd)
        if args.dry_run:
            p, meta, _ = cg.plan(cfg, args.packets, rows, items, cand, privacy.known_items(), args.limit)
            est = cg.estimate(cand, p)
            print(f"dry run [{cg.NAME} {cg.params_hash(cand, meta)}]: {len(p.tasks)} photos, {p.cached} cached, "
                  f"{len(p.skipped)} skipped; arms {','.join(meta['arms'])}; estimated {est.input_tokens} input + "
                  f"{est.output_tokens} output tokens = ${est.usd:.4f} (cap ${args.max_usd:.2f})")
            for s in p.skipped:
                print(f"  skipped {s['key']}: {s['reason']}")
            return 0
        res = cg.run(cfg, args.packets, rows, items, cand, privacy.known_items(), args.limit)
    except (FileNotFoundError, KeyError, ValueError, privacy.PrivacyError, gemini.GeminiError) as e:
        print(f"shoebox eval caption-audit-gemini: {e}", file=sys.stderr)
        return 2
    print(f"audited {sum(res.status.values())} photos in {res.calls} calls (${res.usd:.4f}): {res.status}; "
          f"{res.plan.cached} cached, {len(res.plan.skipped)} skipped; {cg.NAME} {res.params_hash}")
    if res.stopped:
        print(f"shoebox eval caption-audit-gemini: stopped: {res.stopped}", file=sys.stderr)
        return 1
    return 0


def _register_opus_pool(esub) -> None:
    """Opus judging of a whole split's pool (D10 re-judge): `audit-sample --all --split S` writes every pool pair
    to opus-pool-S.jsonl; `audit-packets`/`audit-report --sample FILE` read it; `judgments --source opus`
    exports the Opus grades as qrels to judgments-opus.jsonl. Added to the existing parsers."""
    from shoebox.evals import audit

    s = esub.choices["audit-sample"]
    s.add_argument("--all", action="store_true", help="every pool pair of --split (Opus judges the whole pool)")
    s.add_argument("--split", choices=("dev", "test"), default=None)
    s.add_argument("--out", default=None, help=f"with --all (default {audit.POOL_SAMPLE_FILE})")
    s.set_defaults(func=_audit_sample_any)
    for name in ("audit-packets", "audit-report"):
        esub.choices[name].add_argument("--sample", default=audit.SAMPLE_FILE, help="sample file in the set dir")
    j = esub.choices["judgments"]
    j.add_argument("--source", choices=("gemini", "opus"), default="gemini")
    j.add_argument("--model-id", default=audit.MODEL_ID, help="with --source opus")
    j.add_argument("--params-hash", default=None, help="with --source opus (default: the only one)")
    j.set_defaults(func=_judgments_any, out=None)


def _audit_sample_any(args) -> int:
    from shoebox.evals import audit, judge, search

    if not args.all:
        return _audit_sample(args)
    if not args.split:
        print("shoebox eval audit-sample: --all needs --split", file=sys.stderr)
        return 2
    set_dir = args.sets_dir / args.set_name
    try:
        pool = search.read_jsonl(set_dir / args.pool)
        jfile = set_dir / args.judgments
        gem = audit.gemini_grades(search.read_jsonl(jfile)) if jfile.exists() else {}
    except FileNotFoundError as e:
        print(f"shoebox eval audit-sample: {e}", file=sys.stderr)
        return 2
    rows = audit.pool_sample(pool, gem, args.split)
    out = set_dir / (args.out or audit.POOL_SAMPLE_FILE.format(split=args.split))
    judge.write_jsonl(out, rows)
    s = audit.pool_summary(rows)
    print(f"wrote {out}: {s['pairs']} pairs over {s['queries']} {args.split} queries; "
          f"{s['no_gemini']} without a Gemini grade")
    return 0


def _judgments_any(args) -> int:
    from shoebox.evals import audit, judge

    if args.source == "gemini":
        args.out = args.out or judge.JUDGMENTS_FILE
        return _judgments(args)
    try:
        rows = audit.opus_qrels(_cfg(), args.model_id, args.params_hash)
    except ValueError as e:
        print(f"shoebox eval judgments: {e}", file=sys.stderr)
        return 2
    out = args.sets_dir / args.set_name / (args.out or audit.OPUS_JUDGMENTS_FILE)
    judge.write_jsonl(out, rows)
    print(f"wrote {out}: {len(rows)} Opus judgments ({len({r['query_id'] for r in rows})} queries), "
          f"params {rows[0]['params_hash'] if rows else None}")
    return 0


def _audit_sample(args) -> int:
    from shoebox.evals import audit, judge, search

    set_dir = args.sets_dir / args.set_name
    try:
        pool = search.read_jsonl(set_dir / args.pool)
        gem = audit.gemini_grades(search.read_jsonl(set_dir / args.judgments))
    except FileNotFoundError as e:
        print(f"shoebox eval audit-sample: {e}", file=sys.stderr)
        return 2
    rows = audit.sample(pool, gem, args.frac, args.seed)
    judge.write_jsonl(set_dir / audit.SAMPLE_FILE, rows)
    s = audit.sample_summary(rows, pool, gem)
    print(f"wrote {set_dir / audit.SAMPLE_FILE}: {s['pairs']} of {s['judged_pairs']} judged pairs "
          f"({s['fraction']:.1%}) over {s['queries']} queries; {s['targets_lt2']} targets graded < 2")
    return 0


def _audit_packets(args) -> int:
    from shoebox import privacy
    from shoebox.evals import audit, queries, search

    set_dir = args.sets_dir / args.set_name
    cfg = _cfg()
    try:
        audit.check_out_dir(args.out)
        rows = search.read_jsonl(set_dir / getattr(args, "sample", audit.SAMPLE_FILE))
        pool = search.read_jsonl(set_dir / args.pool)
        items = search.read_jsonl(set_dir / "items.jsonl")
        frows = queries.frame_rows_for(items, _frame(cfg, args.frame))
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval audit-packets: {e}", file=sys.stderr)
        return 2
    res = audit.packets(cfg, rows, pool, items, frows, args.out, args.grid, args.per_batch, args.seed,
                        privacy.known_items())
    for s in res.skipped:
        print(f"  skipped {s['asset_id']} for {s['query_id']}: {s['reason']}", file=sys.stderr)
    print(f"wrote {len(res.grids)} grids in {len(res.batches)} batches to {args.out}; {len(res.skipped)} skipped")
    return 0


def _audit_ingest(args) -> int:
    from shoebox.evals import audit, judge
    from shoebox.runners import gemini

    try:
        res = audit.ingest(_cfg(), args.dir, (gemini.PROMPTS_DIR / judge.PROMPT_FILE).read_text(), args.model_id)
    except (audit.IngestError, FileNotFoundError) as e:
        print(f"shoebox eval audit-ingest: {e}", file=sys.stderr)
        return 1
    print(f"stored {res.stored} judgments ({res.already} already stored) as {args.model_id} {res.params_hash}; "
          f"{len(res.missing_grids)} grids not yet graded")
    if res.conflicts:
        print(f"shoebox eval audit-ingest: {len(res.conflicts)} pairs already stored with another grade "
              f"(kept the stored one): {res.conflicts[:10]}", file=sys.stderr)
        return 1
    return 0


def _audit_report(args) -> int:
    from datetime import date

    from shoebox.evals import audit, search

    set_dir = args.sets_dir / args.set_name
    try:
        rows = search.read_jsonl(set_dir / getattr(args, "sample", audit.SAMPLE_FILE))
        exported = search.read_jsonl(set_dir / args.judgments)
        ph, opus = audit.opus_grades(_cfg(), args.model_id, args.params_hash)
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval audit-report: {e}", file=sys.stderr)
        return 2
    first = exported[0] if exported else {}
    meta = {"set": args.set_name, "date": args.date or date.today().isoformat(),
            "gemini_model_id": first.get("model_id"), "gemini_params_hash": first.get("params_hash"),
            "opus_model_id": args.model_id, "opus_params_hash": ph}
    rep = audit.build_report(rows, opus, meta, args.bootstrap)
    stem = args.out_dir / f"{meta['date']}_{args.set_name}-judge-audit"
    search._atomic_write(stem.with_suffix(".json"), json.dumps(rep, indent=1, sort_keys=True) + "\n")
    search._atomic_write(stem.with_suffix(".md"), audit.render_md(rep))
    o = rep["overall"]
    print(f"wrote {stem}.md/.json: {o['n']} pairs, kappa linear {o['kappa_linear']} quadratic "
          f"{o['kappa_quadratic']}; re-judge test pool: {rep['rejudge_test_pool']}")
    return 0


def _judge_cand(max_usd: float):
    from shoebox import runners
    from shoebox.evals import judge

    return judge.judge_candidate(runners.load_candidates()[judge.BASE_CANDIDATE], max_usd)


def _judge_pool(args) -> int:
    from shoebox.evals import judge, search

    set_dir = args.sets_dir / args.set_name
    try:
        queries = search.read_jsonl(set_dir / args.queries)
        rankings = judge.load_rankings(_cfg(), args.set_name, args.queries)
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval judge-pool: {e}", file=sys.stderr)
        return 2
    rows = judge.build_pool(queries, rankings, args.k, args.split)
    judge.write_jsonl(set_dir / args.out, rows)
    s = judge.pool_summary(rows)
    print(f"wrote {set_dir / args.out}: {s['queries']} queries, {s['pairs']} pairs from {len(rankings)} routes; "
          f"pool size min {s['min']} median {s['median']} mean {s['mean']} max {s['max']}")
    if s["queries_with_missing_routes"]:
        print(f"  {s['queries_with_missing_routes']} queries lack rankings from some route", file=sys.stderr)
    return 0


def _judge(args) -> int:
    from shoebox import privacy
    from shoebox.evals import judge, search
    from shoebox.runners import gemini

    set_dir = args.sets_dir / args.set_name
    try:
        pool = search.read_jsonl(set_dir / args.pool)
        items = search.read_jsonl(set_dir / "items.jsonl")
        cand = _judge_cand(args.max_usd)
    except (FileNotFoundError, KeyError) as e:
        print(f"shoebox eval judge: {e}", file=sys.stderr)
        return 2
    if args.split:
        pool = [r for r in pool if r.get("split") == args.split]
    if args.limit_queries is not None:
        pool = pool[: args.limit_queries]
    cfg = _cfg()
    if args.dry_run:
        p = judge.plan(cfg, pool, cand, items, privacy.known_items())
        est = judge.estimate(cand, p)
        print(f"dry run [{judge.JUDGE_NAME} {judge.params_hash(cand)}]: {len(pool)} queries, {len(p.grids)} grids, "
              f"{p.images} images, {p.cached} cached, {len(p.skipped)} skipped; estimated "
              f"{est.input_tokens} input + {est.output_tokens} output tokens = ${est.usd:.4f}")
        for s in p.skipped:
            print(f"  skipped {s['asset_id']} for {s['query_id']}: {s['reason']}")
        return 0
    from shoebox.evals import queries

    frows = queries.frame_rows_for(items, _frame(cfg, None))
    notes = {it["asset_id"]: judge.photo_note(it, frows.get(it["asset_id"])) for it in items}
    try:
        res = judge.run(cfg, pool, cand, items, privacy.known_items(), notes=notes)
    except (privacy.PrivacyError, gemini.GeminiError) as e:
        print(f"shoebox eval judge: {e}", file=sys.stderr)
        return 2
    print(f"judged {sum(res.status.values())} pairs in {res.calls} calls (${res.usd:.4f}): {res.status}; "
          f"{res.plan.cached} cached, {len(res.plan.skipped)} skipped")
    if res.stopped:
        print(f"shoebox eval judge: stopped: {res.stopped}", file=sys.stderr)
        return 1
    return 0


def _judgments(args) -> int:
    from shoebox.evals import judge

    cand = _judge_cand(judge.DEFAULT_MAX_USD)
    rows = judge.qrels(_cfg(), cand)
    out = args.sets_dir / args.set_name / args.out
    judge.write_jsonl(out, rows)
    print(f"wrote {out}: {len(rows)} judgments ({len({r['query_id'] for r in rows})} queries), "
          f"params {judge.params_hash(cand)}")
    return 0


def _register_graded(esub) -> None:
    from shoebox.evals import graded

    sets = REPO_ROOT / "evals" / "sets"
    for name, helptext in (("graded-dev", "dev nDCG@20 per route; freeze one route per captioner (task 7.1)"),
                           ("graded-test", "score the frozen routes on test and apply gates.toml (task 7.3)")):
        p = esub.add_parser(name, help=helptext)
        p.add_argument("--set", required=True, dest="set_name")
        p.add_argument("--qrels", required=True, help="qrels file in the set dir ({query_id, asset_id, grade})")
        p.add_argument("--queries", default="queries-v1.jsonl", help="query file in the set dir")
        p.add_argument("--index", type=Path, default=None,
                       help="rankings index (default <shoebox_root>/results/search/<set>/index.json)")
        p.add_argument("--date", default=None, help="run date YYYY-MM-DD (default today)")
        p.add_argument("--sets-dir", type=Path, default=sets)
        p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evals" / "runs")
        p.add_argument("--gates", type=Path, default=graded.GATES_PATH)
        if name == "graded-dev":
            p.add_argument("--force", action="store_true", help="overwrite an existing frozen-routes.json")
            p.set_defaults(func=_graded_dev)
        else:
            p.add_argument("--secondary", default="judgments-v1.jsonl", help="secondary qrels in the set dir")
            p.add_argument("--audit-dir", type=Path, default=REPO_ROOT / "evals" / "runs",
                           help="where the caption audit JSONs are")
            p.add_argument("--opus-tokens-per-1000", type=float, default=graded.OPUS_TOKENS_PER_1000)
            p.add_argument("--spend", type=Path, default=None, help="spend log (default evals/spend.jsonl)")
            p.set_defaults(func=_graded_test)


def _graded_inputs(args):
    from datetime import date

    from shoebox.evals import graded, search

    set_dir = args.sets_dir / args.set_name
    qpath, rpath = set_dir / args.queries, set_dir / args.qrels
    for f in (qpath, rpath):
        if not f.exists():
            raise FileNotFoundError(f"{f} not found")
    index = args.index or search.search_dir(_cfg(), args.set_name) / "index.json"
    routes, hashes = graded.load_routes(index, args.queries)
    meta = {"set": args.set_name, "date": args.date or date.today().isoformat(), "queries_file": args.queries,
            "qrels": args.qrels, "qrels_sha256": search.file_sha256(rpath)}
    return set_dir, search.read_jsonl(qpath), graded.load_qrels(rpath), routes, hashes, meta


def _graded_dev(args) -> int:
    from shoebox.evals import graded, search

    try:
        set_dir, queries, qrels, routes, hashes, meta = _graded_inputs(args)
        rep = graded.dev_report(queries, routes, qrels, meta)
        graded.write_freeze(set_dir / graded.FREEZE_FILE, rep, hashes, args.force)
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        print(f"shoebox eval graded-dev: {e}", file=sys.stderr)
        return 2
    stem = args.out_dir / f"{meta['date']}_{args.set_name}-dev"
    search._atomic_write(stem.with_suffix(".json"), json.dumps(rep, indent=1, sort_keys=True) + "\n")
    search._atomic_write(stem.with_suffix(".md"), graded.render_dev_md(rep))
    print(f"wrote {stem}.md/.json and {set_dir / graded.FREEZE_FILE}: {len(rep['routes'])} routes, "
          f"{rep['queries']} dev queries ({rep['queries_without_qrels']} without qrels, "
          f"{len(rep['excluded_no_positive_grade'])} excluded)")
    for c, f in rep["frozen"].items():
        print(f"  {c}: {f['route']} {f['dev_ndcg']:.4f}")
    return 0


def _graded_test(args) -> int:
    from shoebox import privacy
    from shoebox.evals import graded, search

    try:
        set_dir = args.sets_dir / args.set_name
        fpath = set_dir / graded.FREEZE_FILE
        if not fpath.exists():
            raise FileNotFoundError(f"{fpath} not found: run `shoebox eval graded-dev` to freeze the dev choice first")
        frozen = json.loads(fpath.read_text())
        set_dir, queries, primary, routes, _, meta = _graded_inputs(args)
        secondary = None
        if args.secondary:
            spath = set_dir / args.secondary
            if not spath.exists():
                raise FileNotFoundError(f"{spath} not found")
            secondary = graded.load_qrels(spath)
            meta["secondary_qrels"] = args.secondary
        gates = graded.load_gates(args.gates)
        meta |= {"frozen_file": graded.FREEZE_FILE, "frozen_qrels": frozen.get("qrels"),
                 "gates_version": gates.get("version"), "margin": float(gates["search"]["non_inferiority_margin"])}
        rep = graded.score_test(queries, routes, frozen, primary, secondary, meta, gates)
        caps = sorted(frozen["routes"])
        rules = graded.caption_rules(args.audit_dir, args.set_name, caps, gates)
        costs = graded.measure_costs(graded.load_config(), caps)
        proj = graded.projections(costs, caps, int(gates["projection"]["library_images"]), args.opus_tokens_per_1000)
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"shoebox eval graded-test: {e}", file=sys.stderr)
        return 2
    spent = privacy.spent_total(args.spend)
    budget = privacy.limits().max_total_usd
    cands = {c: proj[c] | {"search_ni": True if v["paired"] is None else v["paired"]["non_inferior"],
                           "caption_pass": rules[c]["pass"], "mono_ok": rules[c]["mono_ok"], "caption": rules[c]}
             for c, v in rep["frozen"].items()}
    dec = graded.decide(cands, budget - spent, gates["reference"]["captioner"])
    rep["decision"] = dec | {"candidates": cands, "spent_usd": spent, "budget_usd": budget}
    stem = args.out_dir / f"{meta['date']}_{args.set_name}-test"
    search._atomic_write(stem.with_suffix(".json"), json.dumps(rep, indent=1, sort_keys=True) + "\n")
    search._atomic_write(stem.with_suffix(".md"), graded.render_test_md(rep))
    print(f"wrote {stem}.md/.json: recommendation {dec['recommendation']['captioner']} "
          f"(rule {dec['recommendation']['rule']})")
    return 0


def _cfg():
    return config.load(Path(os.environ.get("SHOEBOX_CONFIG", config.CONFIG_PATH)))


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _search_queries(args) -> int:
    from shoebox.evals import search

    try:
        path, qb = search.write_queries(args.sets_dir / args.set_name)
    except FileNotFoundError as e:
        print(f"shoebox eval search-queries: {e}", file=sys.stderr)
        return 2
    rel = sorted(len(q["relevant"]) for q in qb.queries)
    print(f"wrote {path}: {len(qb.queries)} queries (relevant sizes { {n: rel.count(n) for n in sorted(set(rel))} }), "
          f"{len(qb.items_without_note)} items without a note")
    for qid, groups in qb.merged_groups.items():
        print(f"  {qid}: identical note on {len(groups)} groups/items; relevant set is their union")
    return 0


def _search_run(args) -> int:
    from shoebox.evals import search

    if args.grid == "eval1k":
        names = _split(args.routes) or search.eval1k_routes(_split(args.candidates))
    else:
        names = _split(args.routes) or search.default_routes(_split(args.candidates), not args.no_sensitivity)
    try:
        routes = [search.parse_route(n) for n in names]
        cfg = _cfg()
        need_frame = any(r.kind in ("meta", "image-meta", "hybrid-meta") or r.filt for r in routes)
        frame = _frame(cfg, args.frame) if need_frame else None
        expander = search.ollama_expander() if any(r.exp for r in routes) else None
        src = search.load_sources(cfg, args.set_name, routes, args.sets_dir, args.embed_candidate,
                                  queries_file=args.queries, frame_path=frame, expander=expander)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"shoebox eval search-run: {e}", file=sys.stderr)
        return 2
    res = search.run_routes(_cfg(), src, routes)
    for name, e in res.written.items():
        print(f"{name:32} {e['params_hash']}  {e['n_queries']} queries  {e['path']}")
    for name, why in res.skipped.items():
        print(f"skipped {name}: {why}", file=sys.stderr)
    if res.index_path:
        print(f"updated {res.index_path}")
    return 1 if res.skipped or not res.written else 0


def _expand(args) -> int:
    import httpx

    from shoebox.evals import search

    qpath = args.sets_dir / args.set_name / args.queries
    try:
        queries = search.read_jsonl(qpath)
        if args.split:
            queries = [q for q in queries if q.get("split") == args.split]
        expander = search.ollama_expander()
    except (FileNotFoundError, RuntimeError, httpx.HTTPError) as e:
        print(f"shoebox eval expand: {e}", file=sys.stderr)
        return 2
    out = search.expand_queries(_cfg(), queries, expander)
    d = search.store.store_dir(_cfg(), expander.model_id, expander.params_hash, "expansions")
    print(f"{len(out)} of {len(queries)} queries expanded ({expander.params_hash}) in {d}")
    return 0 if len(out) == len(queries) else 1


def _search_pool(args) -> int:
    from shoebox.evals import pool

    try:
        res = pool.build_pool(_cfg(), args.sets_dir / args.set_name, args.frame, args.n, args.seed,
                              args.sets_dir / args.out_set, never_send_path=args.never_send, force=args.force)
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        print(f"shoebox eval search-pool: {e}", file=sys.stderr)
        return 2
    print(f"wrote {res.out_dir}: {res.n_drawn} distractors (requested {res.n_requested}) from "
          f"{res.eligible_assets} eligible photos")
    for reason, n in res.exclusions.items():
        print(f"  excluded {n:7} paths: {reason}")
    return 0 if res.n_drawn == res.n_requested else 1


def _search_neardups(args) -> int:
    from shoebox.evals import pool, search

    set_dir = args.sets_dir / args.set_name
    try:
        if not (set_dir / "items.jsonl").exists():
            raise FileNotFoundError(f"no items at {set_dir / 'items.jsonl'}")
        cfg = _cfg()
        config.check_write_location(cfg, set_dir)
        _, vectors = search.load_image_store(cfg, args.embed_candidate)
        res = pool.near_dups(set_dir, vectors, args.threshold, args.top)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"shoebox eval search-neardups: {e}", file=sys.stderr)
        return 2
    print(f"{res.above_threshold} of {res.distractors} distractors at cosine >= {args.threshold} to a target "
          f"({res.with_vectors} have an image vector; max {res.max_cosine})")
    print(f"wrote {res.path} ({res.written} rows); review them, then list decided exclusions in "
          f"{set_dir / search.EXCLUDE_FILE}")
    return 0


def _corpus_draw(args) -> int:
    from shoebox import privacy
    from shoebox.evals import corpus

    try:
        cfg = _cfg()
        labelled_by, labels = corpus.resolve_labels(cfg, args.labelled_by)
        res = corpus.draw_corpus(cfg, args.sets_dir / args.base, args.sets_dir / args.name, labels, labelled_by,
                                 args.n, args.seed, args.buffer, args.screen, others=privacy.known_items(),
                                 never=privacy.load_never_send())
    except (ValueError, KeyError, FileNotFoundError) as e:
        print(f"shoebox eval corpus-draw: {e}", file=sys.stderr)
        return 2
    c = res.counts
    print(f"base {c['base_items']} items ({c['base_excluded']} excluded); targets {c['targets_kept']} kept of "
          f"{c['targets']}; candidates {c['candidates']} labelled by {labelled_by['candidate']}; "
          f"rule-eligible {c['rule_eligible']}")
    for reason, k in c["rule_skips"].items():
        print(f"  rule skip {k:6} {reason}")
    for d in c["targets_dropped"]:
        print(f"  target dropped {d['asset_id'][:12]}: {d['reason']}")
    print(f"screened {c['screened']}; excludes {c['screen_excludes']}; group-mates skipped {c['screen_group']}; "
          f"drawn {c['drawn']}; substitutions {len(c['substitutions'])}")
    if not res.written:
        print(f"{res.to_screen} more item(s) need a screen verdict; wrote {res.queue_path} ({res.queue_len} rows). "
              f"Screen them into {args.screen or res.out_dir / corpus.SCREEN_FILE} and rerun.")
        return 3
    print(f"wrote {res.out_dir}/items.jsonl, draw.json, README.md")
    return 0


def _frame(cfg, frame):
    if frame:
        return frame
    frames = sorted((cfg.shoebox_root / "frame").glob("frame-*.parquet"))
    if not frames:
        raise FileNotFoundError(f"no frame-*.parquet under {cfg.shoebox_root / 'frame'}")
    return frames[-1]


def _query_sources(args) -> int:
    from shoebox.evals import queries

    try:
        res = queries.write_sources(args.sets_dir / args.set_name, _frame(_cfg(), args.frame), args.n, args.seed)
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval query-sources: {e}", file=sys.stderr)
        return 2
    print(f"wrote {res.path} ({len(res.rows)} sources from {res.meta['candidates']} capture groups) and {res.sidecar}")
    for s, c in res.meta["strata"].items():
        print(f"  {s:28} {c['drawn']:4} of {c['candidates']}")
    print("  styles: " + ", ".join(f"{k} {v}" for k, v in res.meta["styles"].items()))
    return 0


def _query_packets(args) -> int:
    from shoebox import privacy
    from shoebox.evals import queries

    try:
        res = queries.write_packets(_cfg(), args.sets_dir / args.set_name, args.out, args.batch,
                                    others=privacy.known_items(), never=privacy.load_never_send())
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval query-packets: {e}", file=sys.stderr)
        return 2
    print(f"wrote {len(res.paths)} packet(s) to {args.out}")
    for s in res.skipped:
        print(f"  refused {s['key']}: {s['reason']}", file=sys.stderr)
    return 1 if res.skipped else 0


def _corpus_summary(args) -> int:
    from shoebox.evals import queries

    try:
        print(queries.corpus_summary(args.sets_dir / args.set_name, _frame(_cfg(), args.frame)))
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval corpus-summary: {e}", file=sys.stderr)
        return 2
    return 0


def _query_ingest(args) -> int:
    import glob

    from shoebox.evals import queries

    known = [Path(p) for p in sorted(glob.glob(args.known))]
    kwargs = {"notes_path": args.notes or queries.NOTES_PATH}
    if args.prompts_dir:
        kwargs["prompts_dir"] = args.prompts_dir
    try:
        if not known:
            raise FileNotFoundError(f"no files match {args.known}")
        res = queries.ingest(_cfg(), args.sets_dir / args.set_name, known, args.category, args.model_id,
                             args.seed, dev_pin=args.dev_pin, **kwargs)
    except queries.IngestError as e:
        for err in e.errors:
            print(err, file=sys.stderr)
        print(f"shoebox eval query-ingest: {e}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval query-ingest: {e}", file=sys.stderr)
        return 2
    print(f"params_hash {res.params_hash}: appended {res.appended} record(s)")
    print(f"wrote {res.path} ({len(res.rows)} queries) and {res.md_path}")
    return 0


def _gen_search(args) -> int:
    from shoebox.evals import search

    cfg = _cfg()
    names = _split(args.routes)
    if not names:
        index = args.index or search.search_dir(cfg, args.set_name) / "index.json"
        names = sorted(json.loads(index.read_text())["routes"]) if index.exists() else []
    try:
        res = search.generate_spec(cfg, args.set_name, names, args.sets_dir, args.out_dir, args.index)
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox eval gen-search: {e}", file=sys.stderr)
        return 2
    print(f"wrote {res.spec_path} ({res.stimuli} stimuli)")
    print(f"wrote {res.routes_path} ({len(res.routes)} routes); run with --model {','.join(res.routes)}")
    return 0 if res.stimuli and res.routes else 1


def _search_report(args) -> int:
    from shoebox.evals import search

    if not args.results.exists():
        print(f"no such results file: {args.results}", file=sys.stderr)
        return 2
    try:
        jpath, mpath, _ = search.write_report(args.results, args.out_dir, args.date, args.routes_file)
    except ValueError as e:
        print(f"shoebox eval search-report: {e}", file=sys.stderr)
        return 2
    print(f"wrote {jpath}")
    print(f"wrote {mpath}")
    return 0


def _gen(args) -> int:
    from shoebox.evals import gen

    kwargs = {}
    if args.resolutions:
        table = json.loads(args.resolutions.read_text())

        def resolver(name: str) -> dict:
            if name not in table:
                raise KeyError(f"candidate {name} not in {args.resolutions}")
            return table[name]

        kwargs["resolver"] = resolver
    names = [c.strip() for c in args.candidates.split(",") if c.strip()]
    res = gen.generate(
        config.load(Path(os.environ.get("SHOEBOX_CONFIG", config.CONFIG_PATH))),
        args.set_name,
        names,
        mode=args.mode,
        sets_dir=args.sets_dir,
        out_dir=args.out_dir,
        **kwargs,
    )
    print(
        f"wrote {res.spec_path} ({res.stimuli} stimuli, {len(res.skipped_items)} items skipped)"
    )
    print(f"wrote {res.candidates_path}")
    return 0 if res.stimuli else 1


def _report(args) -> int:
    from shoebox.evals import report

    if not args.results.exists():
        print(f"no such results file: {args.results}", file=sys.stderr)
        return 2
    try:
        jpath, mpath, _ = report.write_report(
            args.results, args.set_name, args.out_dir, args.date, args.candidates_file
        )
    except ValueError as e:
        print(f"shoebox eval report: {e}", file=sys.stderr)
        return 2
    print(f"wrote {jpath}")
    print(f"wrote {mpath}")
    return 0
