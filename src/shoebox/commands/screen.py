"""`shoebox screen <sub>`: the local nudity screen (spec content-screen). `run` screens a derived set.

Output is counts only: never paths, asset ids or scores of flagged items.
"""

import json
import sys
from pathlib import Path

from shoebox import config

DEFAULT_CANDIDATE = "nsfw-coreml-v1"


def register(sub) -> None:
    p = sub.add_parser("screen", help="local nudity screen and its quarantine")
    s = p.add_subparsers(dest="screen_command", required=True)

    r = s.add_parser("run", help="screen derived images; flagged assets are queued in the quarantine")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--items", type=Path, help="items file (JSONL with asset_id)")
    src.add_argument("--all-derived", action="store_true", help="every ok asset in the profile's derive index")
    r.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    r.add_argument("--workers", type=int, default=4, help="threads reading and preprocessing images")
    r.set_defaults(func=cmd_run)


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
    else:
        items = [json.loads(line) for line in args.items.read_text().splitlines() if line.strip()]
    print(f"{res.name}: params_hash={res.params_hash} runtime={res.runtime} items={len(items)}", file=sys.stderr)
    tally = runner.screen_items(res, items, cfg, workers=args.workers)
    print(json.dumps({"candidate": res.name, "screened": tally.screened, "flagged": tally.flagged,
                      "cached": tally.cached, "errors": tally.errors}))
    return 1 if tally.errors else 0
