"""`shoebox derive`: write a profile's derived inference images and index for an items file."""

import json
from pathlib import Path

from shoebox import config, derive


def register(sub) -> None:
    p = sub.add_parser(
        "derive",
        help="write derived inference images (profile, e.g. infer-v1) for an items file",
    )
    p.add_argument(
        "--profile", default="infer-v1", help="image profile in config/profiles.toml"
    )
    p.add_argument(
        "--items",
        required=True,
        type=Path,
        help="items JSONL (e.g. evals/sets/pilot-v0/items.jsonl)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="re-derive even when a current ok image exists",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    cfg = config.load(config.CONFIG_PATH)
    items = [
        json.loads(line) for line in args.items.read_text().splitlines() if line.strip()
    ]
    counts = derive.derive_items(cfg, args.profile, items, force=args.force, log=print)
    print(
        f"{args.profile}: ok {counts.ok}, skipped {counts.skipped}, error {counts.error} "
        f"(of {counts.total}); clipped {counts.flagged} of the ok beyond tolerance "
        f"-> {derive.index_path(cfg, args.profile)}"
    )
    return 1 if counts.total and counts.error == counts.total else 0
