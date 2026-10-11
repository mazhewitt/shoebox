"""Capture grouping: conservative, versioned rules (design D6, spec asset-dedup, issue dedup/02).

Every rule needs the members in the same `(source, folder)`. Where every member has an in-file
(`date_kind == "exif"`) date, the dates must also be close. When a rule is unsure, files stay apart.

Rules, tried per folder in this order; a file taken by one rule is not offered to the next:
- `burst`: names carrying the same `BURST<14+ digits>` token, exactly one of them `_COVER`. Cover is
  `primary`, the rest `burst_member`. A burst without a cover stays apart.
- `raw_jpeg_basename`: one JPG plus RAW(s) with the same stem. JPG `primary`, RAW `raw`.
- `scan_export_basename`: one JPG plus TIFF/PSD(s) with the same stem. TIFF/PSD `primary`, JPG `scan_export`.
- `live_photo`: one HEIC/HEIF/JPG still plus one MOV/MP4 with the same stem. Still `primary`, video
  `motion_clip`. Date check tolerates a whole quarter-hour offset (QuickTime dates are UTC).
- `edited` (a link, not a member): `<stem>-edited`, `_edited` or `-bearbeitet`, linked to the capture
  holding the same-folder image(s) named `<stem>`. With no such original, it is its own capture.
- `motion_photo`: a single still with Google Motion Photo XMP (`MotionPhoto`/`MicroVideo`) in the
  frame's `exif`, else `MVIMG_*` / `*.MP.jpg` names, marked `filename_only`.

Rule groups found at several locations with the same members are one group. Two different rule
groups sharing an asset are both dropped. `same_pixels` copies then join a capture, unless that
would join two captures made by different rules.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import PurePosixPath

from shoebox import inventory
from shoebox.dedup.groups import locations, media_rows, pixel_links

CAPTURE_KIND = "capture"
CAPTURE_VERSION = "capture-v1"
RULE_VERSIONS = {
    "burst": "burst-v1",
    "raw_jpeg_basename": "raw_jpeg_basename-v1",
    "scan_export_basename": "scan_export_basename-v1",
    "live_photo": "live_photo-v1",
    "edited": "edited-v1",
    "motion_photo": "motion_photo-v1",
    "same_pixels": "same_pixels-v1",
    "single": "single-v1",
}

RAW_EXTS = inventory.RAW_EXTS
JPG_EXTS = frozenset({"jpg", "jpeg"})
SCAN_EXTS = frozenset({"tif", "tiff", "psd"})
STILL_EXTS = frozenset({"heic", "heif"}) | JPG_EXTS
CLIP_EXTS = frozenset({"mov", "mp4"})

MAX_GAP_S = 120  # in-file dates of one capture's members
LIVE_TZ_MAX_S = 14 * 3600

BURST = re.compile(r"BURST(\d{14,})", re.IGNORECASE)
COVER = re.compile(r"_COVER(?:\.|_|$)", re.IGNORECASE)
EDITED = re.compile(r"^(?P<orig>.+?)[-_](?:edited|bearbeitet)$", re.IGNORECASE)
MOTION_XMP = ("MotionPhoto", "MicroVideo")
MOTION_NAME = re.compile(r"^MVIMG_|\.MP$", re.IGNORECASE)


def stem(r: dict) -> str:
    return PurePosixPath(r["name"]).stem


def is_edited_name(name: str) -> bool:
    return bool(EDITED.match(PurePosixPath(name).stem))


def _when(r: dict) -> datetime | None:
    if r.get("date_kind") != "exif" or not r.get("date"):
        return None
    try:
        return datetime.fromisoformat(r["date"]).replace(tzinfo=None)
    except ValueError:
        return None


def _dates_close(rs: list[dict]) -> bool:
    ws = [_when(r) for r in rs]
    if any(w is None for w in ws):
        return True  # nothing to compare: same folder + name is the whole test
    return (max(ws) - min(ws)).total_seconds() <= MAX_GAP_S


def _live_dates_close(still: dict, clip: dict) -> bool:
    a, b = _when(still), _when(clip)
    if a is None or b is None:
        return True
    gap = abs((a - b).total_seconds())
    if gap > LIVE_TZ_MAX_S + MAX_GAP_S:
        return False
    return abs(gap - round(gap / 900) * 900) <= MAX_GAP_S


def motion_source(r: dict) -> str | None:
    """`xmp` or `filename_only` for a Google Motion Photo still, else None."""
    if r.get("kind") != "image" or r.get("ext") not in JPG_EXTS | {"heic", "heif"}:
        return None
    try:
        exif = json.loads(r["exif"]) if r.get("exif") else {}
    except (TypeError, ValueError):
        exif = {}
    for k, v in (exif or {}).items():
        if k.rsplit(":", 1)[-1] in MOTION_XMP and v not in (0, "0", None, "", False):
            return "xmp"
    return "filename_only" if MOTION_NAME.search(stem(r)) else None


def _folder_groups(rows: list[dict]) -> tuple[list[tuple[str, dict[str, str]]], list[dict]]:
    """Rule groups `(rule, {asset_id: role})` in one folder, and the rows no rule took."""
    free = list(rows)
    found: list[tuple[str, dict[str, str]]] = []

    def take(rule: str, members: list[tuple[dict, str]]) -> None:
        roles = {r["asset_id"]: role for r, role in members}
        if len(roles) < 2:
            return
        found.append((rule, roles))
        ids = {id(r) for r, _ in members}
        free[:] = [r for r in free if id(r) not in ids]

    bursts: dict[str, list[dict]] = defaultdict(list)
    for r in free:
        if r["kind"] == "image" and (m := BURST.search(r["name"])):
            bursts[m.group(1)].append(r)
    for _, rs in sorted(bursts.items()):
        covers = [r for r in rs if COVER.search(stem(r) + ".")]
        if len(covers) == 1 and len(rs) > 1 and _dates_close(rs):
            take("burst", [(r, "primary" if r is covers[0] else "burst_member") for r in rs])

    def by_stem() -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = defaultdict(list)
        for r in free:
            out[stem(r).lower()].append(r)
        return dict(sorted(out.items()))

    for rs in by_stem().values():
        jpgs = [r for r in rs if r["ext"] in JPG_EXTS and r["kind"] == "image"]
        raws = [r for r in rs if r["ext"] in RAW_EXTS and r["kind"] == "image"]
        if len(jpgs) == 1 and raws and _dates_close(jpgs + raws):
            take("raw_jpeg_basename", [(jpgs[0], "primary")] + [(r, "raw") for r in raws])
    for rs in by_stem().values():
        jpgs = [r for r in rs if r["ext"] in JPG_EXTS and r["kind"] == "image"]
        scans = [r for r in rs if r["ext"] in SCAN_EXTS and r["kind"] == "image"]
        if len(jpgs) == 1 and scans:
            take("scan_export_basename", [(r, "primary") for r in scans] + [(jpgs[0], "scan_export")])
    for rs in by_stem().values():
        stills = [r for r in rs if r["ext"] in STILL_EXTS and r["kind"] == "image"]
        clips = [r for r in rs if r["ext"] in CLIP_EXTS and r["kind"] == "video"]
        if len(stills) == 1 and len(clips) == 1 and _live_dates_close(stills[0], clips[0]):
            take("live_photo", [(stills[0], "primary"), (clips[0], "motion_clip")])
    return found, free


class _Union:
    def __init__(self):
        self.parent: dict[str, str] = {}
        self.rule: dict[str, str | None] = {}

    def add(self, a: str, rule: str | None = None) -> None:
        if a not in self.parent:
            self.parent[a], self.rule[a] = a, rule

    def find(self, a: str) -> str:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def join(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        x, y = self.rule[ra], self.rule[rb]
        if x and y and x != y:
            return False
        lo, hi = sorted((ra, rb))
        self.parent[hi] = lo
        self.rule[lo] = x or y
        return True


def capture_id(asset_ids: Iterable[str]) -> str:
    """`c-` + 12 hex of sha256 over the sorted asset ids (members and linked)."""
    return "c-" + hashlib.sha256("\n".join(sorted(set(asset_ids))).encode()).hexdigest()[:12]


def build_captures(rows: Iterable[dict], pixels: dict[str, dict]) -> list[dict]:
    """Every media asset in exactly one capture (as a member or a linked edit), sorted by capture id.

    A capture: `capture_id`, `rule`, `rule_version`, `members` (`asset_id`, `role`, `locations`),
    `linked` (edits: `asset_id`, `role`, `of`, `rule`, `rule_version`), `motion_source`, `group_size`.
    """
    rows = media_rows(rows)
    locs = locations(rows)
    folders: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        folders[(r["source"], r["folder"] or "")].append(r)

    # 1. rule groups per folder; identical member sets merge, conflicting ones are dropped
    proposals: dict[frozenset, tuple[str, dict[str, str]]] = {}
    for key in sorted(folders):
        for rule, roles in _folder_groups(folders[key])[0]:
            proposals.setdefault(frozenset(roles), (rule, roles))
    claims: dict[str, list[frozenset]] = defaultdict(list)
    for ms in proposals:
        for a in ms:
            claims[a].append(ms)
    accepted = {ms: v for ms, v in proposals.items() if all(len(claims[a]) == 1 for a in ms)}
    role_of: dict[str, str] = {}
    uf = _Union()
    for ms, (rule, roles) in sorted(accepted.items(), key=lambda kv: sorted(kv[0])):
        role_of.update(roles)
        first = min(ms)
        uf.add(first, rule)
        for a in sorted(ms):
            uf.add(a)
            uf.join(first, a)

    # 2. edited copies: link to the one capture holding their same-folder original(s)
    links: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for _, rs in sorted(folders.items()):
        by_stem: dict[str, list[dict]] = defaultdict(list)
        for r in rs:
            if r["kind"] == "image":
                by_stem[stem(r).lower()].append(r)
        for r in rs:
            m = EDITED.match(stem(r)) if r["kind"] == "image" else None
            if not m:
                continue
            origs = sorted({o["asset_id"] for o in by_stem.get(m.group("orig").lower(), [])
                            if o["asset_id"] != r["asset_id"]})
            links[r["asset_id"]].add(tuple(origs))

    # 3. same_pixels copies join (never across two different rules)
    for a in locs:
        uf.add(a)
    for m in pixel_links(locs, pixels):
        for a in m[1:]:
            uf.join(m[0], a)

    comps: dict[str, list[str]] = defaultdict(list)
    for a in sorted(locs):
        comps[uf.find(a)].append(a)

    edit_of: dict[str, tuple[str, str]] = {}  # edited asset -> (root, original)
    for e, origsets in links.items():
        if len(comps[uf.find(e)]) > 1 or len(origsets) != 1:
            continue
        origs = next(iter(origsets))
        roots = {uf.find(o) for o in origs}
        if not origs or len(roots) != 1 or e in origs:
            continue
        exts = {o: next(r["ext"] for r in rows if r["asset_id"] == o) for o in origs}
        of = min(origs, key=lambda o: (exts[o] in RAW_EXTS, o))
        edit_of[e] = (roots.pop(), of)
    for e in edit_of:
        comps.pop(uf.find(e), None)

    first_row = {}
    for r in rows:
        first_row.setdefault(r["asset_id"], r)
    out = []
    for root, ms in comps.items():
        rule = uf.rule[root]
        if rule is None:
            if len(ms) > 1:
                rule = "same_pixels"
            elif motion_source(first_row[ms[0]]):
                rule = "motion_photo"
            else:
                rule = "single"
        primary = min(ms)
        members = [{"asset_id": a,
                    "role": role_of.get(a) or ("primary" if rule in ("same_pixels", "motion_photo", "single")
                                               and a == primary else "copy"),
                    "locations": locs[a]} for a in ms]
        linked = [{"asset_id": e, "role": "edited", "of": of, "rule": "edited",
                   "rule_version": RULE_VERSIONS["edited"]}
                  for e, (r, of) in sorted(edit_of.items()) if r == root]
        motion = sorted({s for a in ms if (s := motion_source(first_row[a]))})
        out.append({
            "capture_id": capture_id(ms + [x["asset_id"] for x in linked]),
            "rule": rule,
            "rule_version": RULE_VERSIONS[rule],
            "members": members,
            "linked": linked,
            "motion_source": motion[-1] if motion else None,  # "xmp" sorts after "filename_only"
            "group_size": len(ms) + len(linked),
        })
    return sorted(out, key=lambda c: c["capture_id"])
