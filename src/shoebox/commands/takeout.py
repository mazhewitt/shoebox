"""`shoebox takeout verify`: test, extract and hash a Google Takeout export (add-takeout-landing D1-D5)."""

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
    v.set_defaults(func=cmd_verify)


def cmd_verify(args) -> int:
    cfg = config.load(config.CONFIG_PATH)
    try:
        return takeout.verify(cfg, args.zips, args.account, dest_root=args.dest, export=args.export)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
