"""`shoebox takeout verify`: test, extract and hash a Google Takeout export (add-takeout-landing D1-D5);
`shoebox takeout flags`: quarantine partner-shared and Locked Folder photos (D9)."""

import sys
from pathlib import Path

from shoebox import config, takeout


def register(sub) -> None:
    p = sub.add_parser("takeout", help="land a Google Takeout export on the NAS")
    tsub = p.add_subparsers(dest="takeout_command", required=True)
    v = tsub.add_parser("verify", help="CRC-test and extract every zip read-only, write the BLAKE3 manifest, "
                        "reconcile (counts only on stdout)")
    v.add_argument("--zips", type=Path, required=True, help="folder holding the takeout-<TS>-*.zip files")
    v.add_argument("--account", required=True, help="whose export, e.g. owner")
    v.add_argument("--dest", type=Path, default=None, help="default <shoebox_root>/originals/takeout/<account>")
    v.add_argument("--export", default=None, metavar="TS", help="only zips of this export timestamp")
    v.add_argument("--extracted", action="store_true",
                   help="the tree was already extracted (7-Zip on the NAS, one folder per zip): verify it against "
                   "the zips' central directories without reading zip data (design D7); needs --export")
    v.add_argument("--workers", type=int, default=8, help="--extracted: concurrent file reads (default 8)")
    v.set_defaults(func=cmd_verify)
    f = tsub.add_parser("flags", help="quarantine partner-shared (exclude) and Locked Folder (private) photos of a "
                        "verified export (design D9; counts only on stdout)")
    f.add_argument("--account", required=True, help="whose export, e.g. owner")
    f.add_argument("--export", required=True, metavar="TS", help="the export timestamp")
    f.add_argument("--dest", type=Path, default=None, help="default <shoebox_root>/originals/takeout/<account>")
    f.add_argument("--workers", type=int, default=8, help="concurrent sidecar reads (default 8)")
    f.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    f.add_argument("--quarantine", type=Path, default=None, help="default evals/quarantine.jsonl")
    f.add_argument("--refresh-sidecars", action="store_true",
                   help="ignore the sidecar cache (<export>.manifest/sidecars.parquet) and re-read every sidecar")
    f.set_defaults(func=cmd_flags)


def cmd_verify(args) -> int:
    cfg = config.load(config.CONFIG_PATH)
    try:
        if args.extracted:
            if not args.export:
                raise ValueError("--extracted needs --export TS")
            return takeout.verify_extracted(cfg, args.zips, args.account, args.export, dest_root=args.dest,
                                            workers=args.workers)
        return takeout.verify(cfg, args.zips, args.account, dest_root=args.dest, export=args.export)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


def cmd_flags(args) -> int:
    cfg = config.load(config.CONFIG_PATH)
    try:
        return takeout.flags(cfg, args.account, args.export, dest_root=args.dest, workers=args.workers,
                             dry_run=args.dry_run, quarantine_path=args.quarantine,
                             refresh_sidecars=args.refresh_sidecars)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
