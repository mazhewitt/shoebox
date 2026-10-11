"""`shoebox screen <sub>`: the local nudity screen and the owner's review of what it flags
(spec content-screen, docs/screen.md). `run` screens a derived set; `review-page` and `ingest`
are the owner's review. Output is counts only: never paths, asset ids or scores of flagged items.
"""

import json
import sys
from pathlib import Path

from shoebox import config

DEFAULT_CANDIDATE = "nsfw-coreml-v1"


def _review_page(args) -> int:
    from shoebox import quarantine
    from shoebox.screenreview import page as pg

    cfg = config.load()
    try:
        unflagged = pg.unflagged_from_store(cfg) if args.sample else []
        res = pg.build_page(cfg, args.out or pg.default_out(), quarantine.load(), unflagged=unflagged,
                            sample=args.sample, seed=args.seed, page_map=args.page_map, profile=args.profile)
    except ValueError as e:
        print(f"shoebox screen review-page: {e}", file=sys.stderr)
        return 2
    print(f"review page {res.page_id}: {res.flagged} flagged, {res.sampled} sampled unflagged, "
          f"{res.no_derived} pending without a derived image (not on the page)")
    print("open index.html in the page folder yourself; the page-map is kept outside it")
    return 0


def _ingest(args) -> int:
    from shoebox import privacy
    from shoebox.screenreview import ingest as ing
    from shoebox.screenreview import page as pg

    cfg = config.load()
    try:
        ans = json.loads(Path(args.answers).expanduser().read_text())
        index = privacy.default_copy_index(cfg)
        res = ing.ingest(ans, args.page_map or pg.default_page_map(), groups=index.pixel_group,
                         delete_thumbnails=args.delete_thumbnails)
    except (ValueError, FileNotFoundError) as e:
        print(f"shoebox screen ingest: {e}", file=sys.stderr)
        return 2
    parts = ", ".join(f"{n} {v}" for v, n in sorted(res.counts.items()))
    print(f"recorded owner verdicts: {parts or 'none'}; {res.group_members} further group members ruled; "
          f"{res.kept_stricter} kept their more restrictive verdict")
    if res.thumbnails_deleted:
        print("review thumbnails deleted")
    else:
        print("review thumbnails kept; re-run with --delete-thumbnails to delete them")
    return 0


def cmd_run(args) -> int:
    from shoebox import derive
    from shoebox.runners import resolve
    from shoebox.runners import screen as runner

    cfg = config.load()
    res = resolve(args.candidate)
    if res.cand.get("runtime") != "screen":
        raise SystemExit(f"candidate {args.candidate!r} has runtime {res.cand.get('runtime')!r}, not 'screen'")
    if args.all_derived:
        index = derive.load_index(cfg, res.cand.get("profile", "infer-v1"))
        items = [{"asset_id": a} for a, r in index.items() if r.get("status") == "ok"]
    elif args.retro:
        from shoebox import retro

        items = [{"asset_id": a} for a in sorted(retro.retro_ids(cfg))]
    else:
        items = [json.loads(line) for line in args.items.read_text().splitlines() if line.strip()]
    print(f"{res.name}: params_hash={res.params_hash} runtime={res.runtime} items={len(items)}", file=sys.stderr)
    tally = runner.screen_items(res, items, cfg, workers=args.workers)
    out = {"candidate": res.name, "screened": tally.screened, "flagged": tally.flagged,
           "cached": tally.cached, "errors": tally.errors}
    if args.retro:  # counts over the whole retro set, cached results included
        from shoebox import store

        done = store.index(cfg, res.model_id, res.params_hash, res.kind)
        ids = {it["asset_id"] for it in items}
        out.update(retro_union=len(ids),
                   retro_flagged=sum(1 for a in ids if (done.get(a) or {}).get("sensitive") is True),
                   retro_unscreened=sum(1 for a in ids if not store.is_final(done.get(a))))
    print(json.dumps(out))
    return 1 if tally.errors else 0


def register(sub) -> None:
    from shoebox.screenreview import page as pg

    p = sub.add_parser("screen", help="sensitive-content screen and owner review of flagged assets")
    ssub = p.add_subparsers(dest="screen_command", required=True)

    r = ssub.add_parser("run", help="screen derived images; flagged assets are queued in the quarantine")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--items", type=Path, help="items file (JSONL with asset_id)")
    src.add_argument("--all-derived", action="store_true", help="every ok asset in the profile's derive index")
    src.add_argument("--retro", action="store_true",
                     help="every asset already sent to a hosted model or shown to Claude (retro-check)")
    r.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    r.add_argument("--workers", type=int, default=4, help="threads reading and preprocessing images")
    r.set_defaults(func=cmd_run)

    r = ssub.add_parser("review-page", help="offline owner review page for pending flags (prints counts only)")
    r.add_argument("--out", type=Path, default=None, help="page folder (default ~/shoebox-review/screen/)")
    r.add_argument("--page-map", type=Path, default=None,
                   help="page-map path, outside the page folder (default ~/shoebox-review/screen-page-map.json)")
    r.add_argument("--sample", type=int, default=0, help="add N seeded random screened, unflagged assets")
    r.add_argument("--seed", type=int, default=pg.DEFAULT_SEED)
    r.add_argument("--profile", default="infer-v1")
    r.set_defaults(func=_review_page)

    i = ssub.add_parser("ingest", help="record the owner's downloaded verdicts in the quarantine file")
    i.add_argument("answers", type=Path)
    i.add_argument("--page-map", type=Path, default=None, help="default ~/shoebox-review/screen-page-map.json")
    i.add_argument("--delete-thumbnails", action="store_true", help="delete the page's img/ folder afterwards")
    i.set_defaults(func=_ingest)
