"""Diff two inventory frames by (source, relpath) (task 2.2). Counts only; exit 1 on any change that is not a
path newly skipped by the 2.1 rules, or when an eval item sits under a newly skipped path.

    uv run python tools/frame_diff.py OLD.parquet NEW.parquet [--eval-items evals/sets/eval-1k/items.jsonl]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

import pyarrow.parquet as pq

from shoebox.inventory import skip_reason

# The skip rules before task 2.1 (bb0c538): exact dir names only.
OLD_SKIP_DIRS = frozenset({"#recycle", "$RECYCLE.BIN", ".tmp.drivedownload", "@eaDir", ".Spotlight-V100",
                           ".Trashes", ".fseventsd"})
IGNORE = {"exif"}  # compared separately: JSON text whose key order may differ


def newly_skipped(relpath: str) -> str | None:
    for part in PurePosixPath(relpath).parts[:-1]:
        if part not in OLD_SKIP_DIRS and (reason := skip_reason(part, True)):
            return reason
    return None


def rows(path: str) -> dict[tuple[str, str], dict]:
    return {(r["source"], r["relpath"]): r for r in pq.read_table(path).to_pylist()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--eval-items", default="evals/sets/eval-1k/items.jsonl")
    args = ap.parse_args(argv)
    old, new = rows(args.old), rows(args.new)
    gone, added = old.keys() - new.keys(), new.keys() - old.keys()
    skipped = Counter(newly_skipped(k[1]) for k in gone)
    other_gone = skipped.pop(None, 0)
    changed = Counter()
    for k in old.keys() & new.keys():
        a, b = old[k], new[k]
        for col in (a.keys() | b.keys()) - IGNORE:
            if a.get(col) != b.get(col):
                changed[col] += 1
        if json.loads(a.get("exif") or "null") != json.loads(b.get("exif") or "null"):
            changed["exif"] += 1
    still = Counter(newly_skipped(k[1]) for k in new)
    still.pop(None, 0)
    evals = [json.loads(line) for line in Path(args.eval_items).read_text().splitlines() if line]
    eval_hit = sum(newly_skipped(it["relpath"]) is not None for it in evals)
    print(f"old {len(old):,} rows, new {len(new):,} rows")
    print(f"removed, newly skipped: {sum(skipped.values()):,} {dict(skipped)}")
    print(f"removed, other: {other_gone:,}")
    print(f"added: {len(added):,}")
    print(f"changed rows by column: {dict(changed)}")
    print(f"new frame rows still under a newly skipped dir: {sum(still.values())}")
    print(f"eval items under a newly skipped path: {eval_hit} of {len(evals)}")
    bad = other_gone or added or changed or still or eval_hit
    print("OK: differs only by newly skipped paths" if not bad else "DIFFERS beyond newly skipped paths")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
