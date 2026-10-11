"""One canonical member per capture, in design D7's order, with the deciding step recorded.

Candidates are the capture's members, minus `motion_clip`s (unless nothing else is left); linked
edits are never candidates. Each step keeps the best candidates; the reason is the step that left one:
1. `consent`: a copy in a consented location (`consented(source, relpath)`, any location counts);
2. `metadata`: in-file date plus GPS, counted 0-2;
3. `resolution`: most pixels (pixel-hash dimensions, else the frame's);
4. `processed`: not a RAW;
5. `original`: not named as an edited copy (`-edited`, `_edited`, `-bearbeitet`) at every location;
6. `asset_id`: the smallest.
A capture with one candidate gets reason `single`.
"""

import json
from collections.abc import Callable, Iterable

from shoebox.dedup.capture import RAW_EXTS, is_edited_name

Consented = Callable[[str, str], bool]
STEPS = ("consent", "metadata", "resolution", "processed", "original")


def _has_gps(exif_json: str | None) -> bool:
    if not exif_json:
        return False
    try:
        exif = json.loads(exif_json)
    except (TypeError, ValueError):
        return False
    return any(k.rsplit(":", 1)[-1] in ("GPSLatitude", "GPSPosition") and v not in (None, "")
               for k, v in (exif or {}).items())


def asset_facts(rows: Iterable[dict], pixels: dict[str, dict], consented: Consented) -> dict[str, dict]:
    """Per asset: the scores the steps compare (higher is better)."""
    by_asset: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("asset_id"):
            by_asset.setdefault(r["asset_id"], []).append(r)
    out = {}
    for a, rs in by_asset.items():
        px = pixels.get(a) or {}
        if px.get("status") == "ok" and px.get("width") and px.get("height"):
            area = px["width"] * px["height"]
        else:
            area = max((r.get("width") or 0) * (r.get("height") or 0) for r in rs)
        out[a] = {
            "consent": int(any(consented(r["source"], r["relpath"]) for r in rs)),
            "metadata": max(int(r.get("date_kind") == "exif") + int(_has_gps(r.get("exif"))) for r in rs),
            "resolution": area,
            "processed": int(not any(r["ext"] in RAW_EXTS for r in rs)),
            "original": int(any(not is_edited_name(r["name"]) for r in rs)),
        }
    return out


def choose(capture: dict, facts: dict[str, dict]) -> tuple[str, str]:
    """`(canonical_asset_id, reason)` for one capture."""
    cands = sorted(m["asset_id"] for m in capture["members"] if m["role"] != "motion_clip")
    if not cands:
        cands = sorted(m["asset_id"] for m in capture["members"])
    if len(cands) == 1:
        return cands[0], "single"
    for step in STEPS:
        best = max(facts[a][step] for a in cands)
        cands = [a for a in cands if facts[a][step] == best]
        if len(cands) == 1:
            return cands[0], step
    return cands[0], "asset_id"
