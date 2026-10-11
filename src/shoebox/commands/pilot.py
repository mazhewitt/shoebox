"""`shoebox pilot`: pick the pilot-v0 set, check sources are untouched, write the contact sheet."""

import json
import sys
import time
from pathlib import Path

from shoebox import config
from shoebox.pilot.untouched import SourceWritable, check_read_only

SET_DIR = config.REPO_ROOT / "evals" / "sets" / "pilot-v0"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _items(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cmd_pick(args) -> int:
    from shoebox.pilot.pick import run_pick

    cfg = config.load()
    t0 = time.monotonic()
    try:
        items = run_pick(cfg.sources[args.source], Path(args.out_dir), log=_log)
    except (SourceWritable, FileNotFoundError, ValueError) as e:
        _log(f"pick failed: {e}")
        return 2
    _log(f"wrote {len(items)} items to {args.out_dir} in {time.monotonic() - t0:.1f}s")
    return 0


def cmd_snapshot(args) -> int:
    from shoebox.pilot.pick import RECIPE
    from shoebox.pilot.select import recipe_dirs
    from shoebox.pilot.untouched import take_snapshot

    cfg = config.load()
    items_path = Path(args.items)
    items = _items(items_path) if items_path.is_file() else []
    if not items:
        _log(f"no items at {items_path}: snapshot covers the recipe folders only (no hashes)")
    t0 = time.monotonic()
    try:
        for name in sorted({i["source"] for i in items} | {args.source}):
            check_read_only(cfg.sources[name])
        source = cfg.sources[args.source]
        dirs = {source.name: recipe_dirs(source.root, RECIPE)}
    except (SourceWritable, FileNotFoundError) as e:
        _log(f"snapshot refused: {e}")
        return 2
    snap = take_snapshot(cfg, items, dirs)
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n")
    hashed = sum(1 for r in snap["files"].values() if "blake3" in r)
    _log(f"snapshot of {len(snap['files'])} files ({hashed} hashed) and {len(snap['dirs'])} folders -> {out} "
         f"({time.monotonic() - t0:.1f}s)")
    return 0


def cmd_verify(args) -> int:
    from shoebox.pilot.untouched import diff_snapshots, remeasure

    cfg = config.load()
    t0 = time.monotonic()
    before = json.loads(Path(args.snapshot).expanduser().read_text())
    problems = diff_snapshots(before, remeasure(cfg, before))
    for p in problems:
        print(p)
    _log(f"checked {len(before['files'])} files and {len(before['dirs'])} folders "
         f"against snapshot of {before.get('taken')} in {time.monotonic() - t0:.1f}s: "
         + ("CHANGED" if problems else "untouched"))
    return 1 if problems else 0


def cmd_sheet(args) -> int:
    from shoebox.pilot.sheet import write_sheet

    cfg = config.load()
    left_out: dict = {}
    out = write_sheet(cfg, Path(args.items), Path(args.labels), Path(args.out) if args.out else None,
                      left_out=left_out)
    _log(f"wrote {out}; left out {left_out.get('quarantined', 0)} quarantined, "
         f"{left_out.get('unscreened', 0)} unscreened")
    return 0


def register(sub) -> None:
    p = sub.add_parser("pilot", help="pilot-v0 set: pick, sources-untouched check, contact sheet")
    psub = p.add_subparsers(dest="pilot_command", required=True)
    items_default = str(SET_DIR / "items.jsonl")

    pick = psub.add_parser("pick", help="run the pilot recipe; write items.jsonl and README.md")
    pick.add_argument("--source", default="importantfiles")
    pick.add_argument("--out-dir", default=str(SET_DIR))
    pick.set_defaults(func=cmd_pick)

    snap = psub.add_parser(
        "snapshot",
        help="record the listings of every folder the recipe reads, size/mtime of their files and blake3 of "
        "the pilot items (if items.jsonl exists); take it before the first pick",
    )
    snap.add_argument("--out", required=True)
    snap.add_argument("--items", default=items_default, help="hashed if present; optional")
    snap.add_argument("--source", default="importantfiles", help="source whose recipe folders are recorded")
    snap.set_defaults(func=cmd_snapshot)

    ver = psub.add_parser("verify-untouched", help="compare sources against a snapshot; non-zero on change")
    ver.add_argument("--snapshot", required=True)
    ver.set_defaults(func=cmd_verify)

    sheet = psub.add_parser("sheet", help="write the local contact sheet / blind labelling page")
    sheet.add_argument("--items", default=items_default)
    sheet.add_argument("--labels", default=str(SET_DIR / "labels.jsonl"))
    sheet.add_argument("--out", default=None, help="default <shoebox_root>/results/pilot-v0/sheet.html")
    sheet.set_defaults(func=cmd_sheet)
