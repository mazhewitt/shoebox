"""Contact sheets for the capture spot check (task 6.5): a seeded, rule-stratified sample of multi-member
captures, one sheet per capture (its members' derived images side by side, labelled A, B, ... and role).

Only captures whose every member and linked asset passes `privacy.filter_items(provider="claude")` are
drawn: never-send, held-back, consent, copy-restricted, quarantined (incl. pending) and unscreened assets
keep the whole capture off the sheets. Sheets carry keys and roles only; `map.json` (key -> capture id
and rule) is written next to, not inside, the `sheets/` folder. Prints counts only.

    uv run python tools/capture_spotcheck.py [--n 50] [--seed 20261012] [--out ~/shoebox-work/spotcheck-6.5]
"""

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pyvips

from shoebox import config, privacy, store
from shoebox.runners import derived_image

CAPTURE_KIND = "capture"
PROFILE = "infer-v1"
SHEET_H = 480
FORBIDDEN = ("Desktop", "Documents", "Library/Mobile Documents")


def check_out(out: Path) -> None:
    home, repo = Path.home(), Path(__file__).resolve().parents[1]
    r = out.expanduser().resolve()
    if r.parts[:2] == ("/", "Volumes") or r.is_relative_to(repo) or any(r.is_relative_to(home / f) for f in FORBIDDEN):
        raise SystemExit(f"refusing to write sheets under {r}")


def latest_capture_dir(cfg) -> Path:
    dirs = store.store_dirs(cfg, CAPTURE_KIND)
    if len(dirs) != 1:
        raise SystemExit(f"{len(dirs)} capture store dirs; expected 1")
    return dirs[0]


def assets_of(cap: dict) -> list[dict]:
    return [*cap.get("members", []), *cap.get("linked", [])]


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Largest remainder over sqrt(stratum size), at least 1 per stratum, capped at its size."""
    w = {k: math.sqrt(v) for k, v in sizes.items()}
    total = sum(w.values())
    quota = {k: n * v / total for k, v in w.items()}
    out = {k: min(sizes[k], max(1, int(q))) for k, q in quota.items()}
    for k in sorted(quota, key=lambda k: quota[k] - int(quota[k]), reverse=True):
        if sum(out.values()) >= n:
            break
        if out[k] < sizes[k]:
            out[k] += 1
    while sum(out.values()) < min(n, sum(sizes.values())):
        k = max((k for k in sizes if out[k] < sizes[k]), key=lambda k: sizes[k] - out[k])
        out[k] += 1
    return out


def sheet(cfg, cap: dict) -> pyvips.Image:
    tiles = []
    for i, m in enumerate(assets_of(cap)):
        img = pyvips.Image.thumbnail(str(derived_image(cfg, PROFILE, m["asset_id"])), 2000, height=SHEET_H)
        label = pyvips.Image.text(f"{chr(65 + i)}  {m.get('role') or 'linked'}", dpi=150).ifthenelse(
            [0, 0, 0], [255, 255, 255], blend=True)
        tiles.append(img.flatten() if img.hasalpha() else img)
        tiles[-1] = tiles[-1].colourspace("srgb").insert(label, 8, 8)
    return pyvips.Image.arrayjoin(tiles, shim=12, background=[255, 255, 255])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20261012)
    ap.add_argument("--out", type=Path, default=Path("~/shoebox-work/spotcheck-6.5"))
    args = ap.parse_args(argv)
    out = args.out.expanduser()
    check_out(out)
    if (out / "sheets").exists():
        raise SystemExit(f"{out / 'sheets'} exists")
    cfg = config.load()
    caps = [c for c in store.index_records(store.iter_dir(latest_capture_dir(cfg))).values()
            if c.get("status") == "ok" and len(assets_of(c)) > 1]
    copies, screened = privacy.default_copy_index(cfg), privacy.default_screen_index(cfg)
    eligible, dropped = {}, Counter()
    for c in caps:
        items = [{"asset_id": m["asset_id"], "source": s, "relpath": r, "group_id": c["capture_id"]}
                 for m in assets_of(c) for s, r in (m.get("locations") or [loc[:2] for loc in copies.locations(m["asset_id"])])]
        if {i["asset_id"] for i in items} != {m["asset_id"] for m in assets_of(c)}:
            dropped["unlocated"] += 1
            continue
        el = privacy.filter_items(items, cfg, provider="claude", copies=copies, screened=screened)
        if el.skipped:
            dropped.update(s.reason for s in el.skipped[:1])
        elif all(derived_image(cfg, PROFILE, m["asset_id"]).exists() for m in assets_of(c)):
            eligible.setdefault(c["rule"] if c["rule"] != "single" else "linked", []).append(c)
        else:
            dropped["not_derived"] += 1
    sizes = {k: len(v) for k, v in eligible.items()}
    take = allocate(sizes, args.n)
    order = lambda c: hashlib.sha256(f"{args.seed}:{c['capture_id']}".encode()).hexdigest()
    chosen = [c for k in sorted(take) for c in sorted(eligible[k], key=order)[:take[k]]]
    chosen.sort(key=order)
    (out / "sheets").mkdir(parents=True)
    mapping = {}
    for i, c in enumerate(chosen, 1):
        key = f"s-{i:02d}"
        sheet(cfg, c).jpegsave(str(out / "sheets" / f"{key}.jpg"), Q=85)
        mapping[key] = {"capture_id": c["capture_id"], "rule": c["rule"], "n": len(assets_of(c))}
    (out / "map.json").write_text(json.dumps({"seed": args.seed, "captures": mapping}, indent=1))
    print(f"multi-member captures {len(caps)}; eligible {sum(sizes.values())} {dict(sorted(sizes.items()))}")
    print(f"dropped (first reason per capture): {dict(dropped)}")
    print(f"drawn {len(chosen)}: {dict(sorted(take.items()))} -> {out / 'sheets'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
