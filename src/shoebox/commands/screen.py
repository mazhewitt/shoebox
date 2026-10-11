"""`shoebox screen`: the sensitive-content screen and the owner's review of what it flags (docs/screen.md)."""

import json
import sys
from pathlib import Path

from shoebox import config


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
    print(f"recorded owner verdicts: {parts or 'none'}; {res.group_members} further group members ruled")
    if res.thumbnails_deleted:
        print("review thumbnails deleted")
    else:
        print("review thumbnails kept; re-run with --delete-thumbnails to delete them")
    return 0


def register(sub) -> None:
    from shoebox.screenreview import page as pg

    p = sub.add_parser("screen", help="sensitive-content screen and owner review of flagged assets")
    ssub = p.add_subparsers(dest="screen_command", required=True)

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
