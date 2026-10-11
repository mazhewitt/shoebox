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
