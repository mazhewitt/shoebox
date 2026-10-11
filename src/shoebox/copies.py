"""Cross-source copy index (design D5): every place an asset's bytes or pixels sit, across all sources.

Built from the latest frame of every configured source (`<shoebox_root>/frame/frame-YYYY-MM-DD.parquet`;
one frame may hold several sources), not from the items about to be sent, plus same-pixel links from the
pixel-hash store (`results/pixelhash/...`, written by derive). A configured source with no frame dated on
or after its `added` config date is listed in `missing`; `privacy.filter_items` then refuses to run.
Image rows with no pixel hash are listed in `unhashed` (their same-pixel copies can't be seen); if any sits at a
restricted location, `privacy.filter_items` refuses too (`privacy.uncovered`).
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shoebox import store
from shoebox.config import Config

PIXELHASH_KIND = "pixelhash"  # derive.PIXELHASH_KIND; not imported so this module needs no pyvips
_FRAME = re.compile(r"^frame-(\d{4}-\d{2}-\d{2})\.parquet$")

Location = tuple[str, str, str]  # (source, relpath, asset_id)


@dataclass(frozen=True)
class CopyIndex:
    by_asset: dict[str, tuple[Location, ...]]
    pixel_keys: dict[str, frozenset[str]]  # asset_id -> pixel-hash keys (scoped to their store dir)
    missing: tuple[str, ...] = ()  # configured sources without a current frame
    _by_pixel: dict[str, frozenset[str]] = field(default_factory=dict, repr=False, compare=False)
    # Image rows with no pixel-hash record: their same-pixel copies are invisible (`privacy.uncovered`).
    unhashed: tuple[Location, ...] = ()
    # Of the image rows without a pixel hash, those whose latest derive record is `error`: undecodable, so
    # they can't be compared at all. Reported, never blocking.
    undecodable: tuple[Location, ...] = ()

    @classmethod
    def build(cls, locations: Iterable[Location], pixels: Mapping[str, str | Iterable[str]],
              missing: Iterable[str] = (), unhashed: Iterable[Location] = (),
              undecodable: Iterable[Location] = ()) -> "CopyIndex":
        """From `(source, relpath, asset_id)` rows and `{asset_id: pixel_hash or [keys]}`."""
        by_asset: dict[str, list[Location]] = defaultdict(list)
        for loc in locations:
            by_asset[loc[2]].append(tuple(loc))
        keys = {a: frozenset([p] if isinstance(p, str) else p) for a, p in pixels.items()}
        by_pixel: dict[str, set[str]] = defaultdict(set)
        for a, ks in keys.items():
            for k in ks:
                by_pixel[k].add(a)
        return cls({a: tuple(v) for a, v in by_asset.items()}, keys, tuple(sorted(missing)),
                   {k: frozenset(v) for k, v in by_pixel.items()},
                   tuple(sorted(tuple(x) for x in unhashed)), tuple(sorted(tuple(x) for x in undecodable)))

    def locations(self, asset_id: str) -> list[Location]:
        """Byte copies: every frame row with this asset_id."""
        return list(self.by_asset.get(asset_id, ()))

    def pixel_group(self, asset_id: str) -> set[str]:
        """This asset and every asset with the same pixel hash."""
        out = {asset_id}
        for k in self.pixel_keys.get(asset_id, ()):
            out |= self._by_pixel.get(k, frozenset())
        return out

    def copies(self, asset_id: str) -> list[Location]:
        """Every location of this asset's bytes or pixels."""
        return [loc for a in sorted(self.pixel_group(asset_id)) for loc in self.locations(a)]


def latest_frames(cfg: Config) -> tuple[dict[str, tuple[date, Path]], tuple[str, ...]]:
    """`({source: (frame date, frame path)}, missing)` for the configured sources."""
    import pyarrow.parquet as pq

    frame_dir = Path(cfg.shoebox_root).expanduser() / "frame"
    frames = []
    if frame_dir.is_dir():
        for p in frame_dir.iterdir():
            m = _FRAME.match(p.name)
            if m:
                frames.append((date.fromisoformat(m.group(1)), p))
    latest: dict[str, tuple[date, Path]] = {}
    for day, p in sorted(frames, reverse=True):
        if len(latest) == len(cfg.sources):
            break
        present = set(pq.read_table(p, columns=["source"]).column("source").unique().to_pylist())
        for name in cfg.sources:
            if name in present and name not in latest:
                latest[name] = (day, p)
    missing = tuple(sorted(
        n for n, s in cfg.sources.items()
        if n not in latest or (s.added is not None and latest[n][0] < s.added)
    ))
    return latest, missing


def load_pixel_keys(cfg: Config) -> dict[str, set[str]]:
    """`{asset_id: {"<store dir>:<pixel_hash>"}}` from every pixel-hash store dir. Keys are scoped to their
    store dir, so hashes made under different versions or settings never link."""
    out: dict[str, set[str]] = defaultdict(set)
    for d in store.store_dirs(cfg, PIXELHASH_KIND):
        for r in store.iter_dir(d):
            if r.get("status") == "ok" and r.get("pixel_hash"):
                out[r["asset_id"]].add(f"{d.parent.name}/{d.name}:{r['pixel_hash']}")
    return out


def derive_errors(cfg: Config, profile: str = "infer-v1") -> set[str]:
    """Assets whose latest record in the profile's derive index is `error` (as `derive.load_index` reads it;
    read here directly so this module needs no pyvips). Unparseable lines are skipped."""
    path = cfg.derived_dir / profile / "index.jsonl"
    latest: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("asset_id"), str):
                latest[rec["asset_id"]] = rec.get("status")
    return {a for a, s in latest.items() if s == "error"}


def load(cfg: Config, profile: str = "infer-v1") -> CopyIndex:
    """The index over the latest frame of every configured source plus the pixel-hash store. Image rows (a
    frame without a `kind` column counts every row as an image) with no pixel hash are listed in `unhashed`,
    or in `undecodable` if the profile's derive record for them is `error`."""
    import pyarrow.parquet as pq

    latest, missing = latest_frames(cfg)
    by_path: dict[Path, list[str]] = defaultdict(list)
    for name, (_, p) in latest.items():
        by_path[p].append(name)
    locs: list[Location] = []
    images: list[Location] = []
    for p, names in by_path.items():
        cols = ["source", "relpath", "asset_id"]
        has_kind = "kind" in pq.read_schema(p).names
        t = pq.read_table(p, columns=cols + ["kind"] * has_kind).to_pydict()
        kinds = t["kind"] if has_kind else ["image"] * len(t["source"])
        wanted = set(names)
        for s, r, a, k in zip(t["source"], t["relpath"], t["asset_id"], kinds):
            if a and s in wanted:
                locs.append((s, r, a))
                if k in ("image", None):
                    images.append((s, r, a))
    pixels = load_pixel_keys(cfg)
    errors = derive_errors(cfg, profile) if images else set()
    unhashed = [loc for loc in images if loc[2] not in pixels]
    return CopyIndex.build(locs, pixels, missing, [x for x in unhashed if x[2] not in errors],
                           [x for x in unhashed if x[2] in errors])
