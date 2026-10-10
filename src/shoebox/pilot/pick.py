"""`pilot pick`: run the pilot-v0 recipe and write `items.jsonl` and `README.md` (docs/pilot.md section 1)."""

import json
import time
from collections import Counter
from datetime import date
from pathlib import Path

from shoebox.config import Source
from shoebox.ids import asset_id
from shoebox.pilot.select import (
    MAX_BYTES,
    MAX_TOTAL,
    Bucket,
    Log,
    assign_groups,
    select,
)
from shoebox.pilot.untouched import check_read_only

ITEM_KEYS = ["asset_id", "source", "relpath", "ext", "bytes", "mtime", "bucket", "group_id", "group_rule"]

_GP = "Pictures/Google Photos"

RECIPE: tuple[Bucket, ...] = (
    Bucket("leica-dng-jpg-pair", ("Pictures/Greece",), 4, mode="pairs"),
    Bucket("nikon-nef-jpg-pair", ("Pictures/100D5600",), 2, mode="pairs"),
    Bucket("nikon-d70-nef", ("Pictures/D70",), 3, exts=frozenset({"nef"})),
    Bucket("film-scan-tiff", ("Pictures/Saved Pictures/Scanned Photos",), 5),
    Bucket("nikon-film-scan", ("Pictures/New Nikon Scans",), 3),
    Bucket("processed-scan", ("Pictures/Processed",), 3),
    Bucket("to-work-scan", ("Pictures/To Work",), 2),
    Bucket("google-photos-old", tuple(f"{_GP}/{y}" for y in (1969, 1970, 1998, 1999)), 1, per_folder=True),
    Bucket("google-photos-2010s", tuple(f"{_GP}/{y}" for y in range(2010, 2020)), 3, spread_folders=True),
    Bucket("phone", ("Pictures/Phone Photos",), 4, want_exts=frozenset({"heic", "heif"})),
    Bucket("screenshot", ("Pictures/Screenshots",), 2),
    Bucket("camera-roll", ("Pictures/Camera Roll",), 2),
    Bucket(
        "psd",
        ("Pictures/Processed", "Pictures/photos", "Pictures/Ligtroom-Exports"),
        1,
        exts=frozenset({"psd"}),
        extra_files=("Pictures/L1002656.psd",),
        exclude_words=("voucher", "v-bucks"),
    ),
    Bucket("old-prints", ("Pictures/old photos from dad",), 2),
    Bucket("pinhole", ("Pictures/pinhole",), 1),
    Bucket("low-jpg-scan", ("Pictures/11w-scan1-low_jpg_files",), 1),
)


def _describe(b: Bucket) -> str:
    how = {"pairs": f"{b.n} raw+jpg pairs"}.get(b.mode, f"{b.n} files")
    if b.per_folder:
        how = f"{b.n} per folder"
    if b.spread_folders:
        how = f"{b.n} total, one each from evenly spaced non-empty folders"
    bits = [how]
    if b.exts:
        bits.append("exts " + "/".join(sorted(b.exts)))
    if b.want_exts:
        bits.append("include a " + "/".join(sorted(b.want_exts)) + " if any")
    if b.extra_files:
        bits.append("plus " + ", ".join(f"`{f}`" for f in b.extra_files))
    if b.exclude_words:
        bits.append("skip names containing " + ", ".join(b.exclude_words))
    return f"| {b.name} | {', '.join(f'`{f}`' for f in b.folders)} | {'; '.join(bits)} |"


def render_readme(
    items: list[dict], skipped: list[tuple[str, str]], recipe: tuple[Bucket, ...], source: str, notes: list[str]
) -> str:
    total = sum(i["bytes"] for i in items)
    by_bucket = Counter(i["bucket"] for i in items)
    by_ext = Counter(i["ext"] for i in items)
    groups = Counter(i["group_rule"] for i in items if i["group_rule"])
    lines = [
        "# pilot-v0",
        "",
        (
            f"Unscored pilot set (docs/pilot.md). Written by `shoebox pilot pick` on {date.today().isoformat()} "
            f"from source `{source}`. No image bytes are stored here; `items.jsonl` holds asset IDs and paths."
        ),
        "",
        f"- Files: {len(items)}",
        f"- Total bytes: {total:,} ({total / 1e9:.2f} GB)",
        "- Sibling-group members: "
        + (", ".join(f"{k} {v}" for k, v in sorted(groups.items())) or "none"),
        "",
        "## Files per bucket",
        "",
        "| bucket | files |",
        "|---|---|",
        *(f"| {b.name} | {by_bucket[b.name]} |" for b in recipe if by_bucket[b.name]),
        "",
        "## Files per extension",
        "",
        "| ext | files |",
        "|---|---|",
        *(f"| {e} | {n} |" for e, n in sorted(by_ext.items())),
        "",
        "## Skipped buckets",
        "",
        *([f"- {b}: {why}" for b, why in skipped] or ["- none"]),
        "",
        "## Selection log",
        "",
        *([f"- {n}" for n in notes] or ["- nothing to report"]),
        "",
        "## Recipe",
        "",
        (
            "Each bucket folder is listed recursively to depth 2, skipping hidden files, `#recycle`, "
            "`$RECYCLE.BIN`, `.tmp.drivedownload`, `*.lrdata` and `Thumbs.db`. Image extensions: jpg jpeg png "
            f"heic heif webp tif tiff nef dng psd (any case). Files over {MAX_BYTES // 2**20} MiB are skipped. "
            "Candidates are sorted by relpath and picked evenly spaced at index "
            "`floor(i*(len-1)/(n-1) + 0.5)` (one pick takes the middle). Pair buckets pick among base names "
            "with both a raw (nef/dng) and a jpg in the same folder and take both files. A file is picked at "
            f"most once (earlier buckets win). The total is capped at {MAX_TOTAL}. Groups: same-folder "
            "raw+jpg base names (`raw_jpeg_basename`) and tif/psd+jpg base names (`scan_export_basename`) "
            "among the picked files; `group_id` is `g-` plus the first 12 hex of the smallest member asset_id."
        ),
        "",
        "| bucket | folders | rule |",
        "|---|---|---|",
        *(_describe(b) for b in recipe),
        "",
    ]
    return "\n".join(lines)


def run_pick(source: Source, out_dir: Path, recipe: tuple[Bucket, ...] = RECIPE, log: Log = print) -> list[dict]:
    """Select, hash and group the pilot files; write `items.jsonl` and `README.md` into `out_dir`."""
    check_read_only(source)
    notes: list[str] = []

    def note(msg: str) -> None:
        notes.append(msg)
        log(msg)

    t0 = time.monotonic()
    picks, skipped = select(source.root, recipe, log=note)
    log(f"selected {len(picks)} files in {time.monotonic() - t0:.1f}s")
    t1 = time.monotonic()
    items = []
    for bucket, f in picks:
        items.append({
            "asset_id": asset_id(source.root / f.relpath),
            "source": source.name,
            "relpath": f.relpath,
            "ext": f.ext,
            "bytes": f.bytes,
            "mtime": float(f.mtime),
            "bucket": bucket,
            "group_id": None,
            "group_rule": None,
        })
    log(f"hashed {sum(i['bytes'] for i in items):,} bytes in {time.monotonic() - t1:.1f}s")
    assign_groups(items)
    items = [{k: i[k] for k in ITEM_KEYS} for i in items]
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "items.jsonl", "w") as fh:
        fh.writelines(json.dumps(i) + "\n" for i in items)
    (out_dir / "README.md").write_text(render_readme(items, skipped, recipe, source.name, notes))
    return items
