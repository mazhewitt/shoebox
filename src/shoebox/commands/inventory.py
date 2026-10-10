"""`shoebox inventory`: build the sampling frame (design D4), and `inventory readme` to summarise it."""

import json
import sys
from pathlib import Path

from shoebox import config, inventory
from shoebox.config import REPO_ROOT


def register(sub) -> None:
    p = sub.add_parser("inventory", help="walk the sources read-only and write frame/frame-<date>.parquet")
    p.add_argument("--sources", nargs="+", default=None, help="source names (default: all configured)")
    p.add_argument("--only", nargs="+", default=[], metavar="RELPATH",
                   help="walk only these folders (relative to each source root)")
    p.add_argument("--workers", type=int, default=8, help="parallel read+hash+decode threads (default 8)")
    p.add_argument("--date", default=None, help="run date YYYY-MM-DD (default today); names the outputs")
    p.add_argument("--out-dir", type=Path, default=None, help="default <shoebox_root>/frame")
    p.add_argument("--resume", action="store_true", help="continue work-<date>/records.jsonl")
    p.set_defaults(func=cmd_run)
    isub = p.add_subparsers(dest="inventory_command", required=False)
    r = isub.add_parser("readme", help="write the frame summary README (no photo file names)")
    r.add_argument("--frame", type=Path, required=True, help="frame-<date>.parquet")
    r.add_argument("--listing", type=Path, default=None, help="default: listing-<date>.json next to the frame")
    r.add_argument("--out", type=Path, default=REPO_ROOT / "frame" / "README.md")
    r.set_defaults(func=cmd_readme)


def cmd_run(args) -> int:
    opts = inventory.Options(sources=args.sources, only=args.only, workers=args.workers, out_dir=args.out_dir,
                             resume=args.resume)
    if args.date:
        opts.date = args.date
    try:
        summary = inventory.run(config.load(config.CONFIG_PATH), opts)
    except inventory.SourcesTouched as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=1))
    return 0


def cmd_readme(args) -> int:
    from shoebox import inventory_readme

    cfg = config.load(config.CONFIG_PATH)
    config.check_write_location(cfg, args.out)
    text = inventory_readme.render(args.frame, args.listing)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out}")
    return 0
