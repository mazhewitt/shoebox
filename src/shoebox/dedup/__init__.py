"""Dedup: exact/same_pixels groups, capture rules and canonical members (design D6/D7).

Entry points:
- `load_frames([...])` -> rows of every source's frame;
- `build_groups(rows, pixels)` / `write_groups(cfg, groups, groups_params(...))` (kind `dedup-groups`);
- `captures(rows, pixels, consented)` -> one dict per capture, with `capture_id`, `rule`, `rule_version`,
  `members` (roles, locations), `linked`, `canonical_asset_id`, `canonical_reason`, `group_size`;
- `write_captures(cfg, captures, capture_params(...))` (kind `capture`, model dir `capture-v1`).

`pixels` is `{asset_id: pixel-hash record}`, e.g. `derive.pixelhash_index(cfg, settings)`.
`consented(source, relpath)` is supplied by the caller, so this package holds no privacy logic.
"""

from collections.abc import Iterable
from pathlib import Path

from shoebox.config import Config
from shoebox.dedup.canonical import Consented, asset_facts, choose
from shoebox.dedup.capture import (
    CAPTURE_KIND,
    CAPTURE_VERSION,
    RULE_VERSIONS,
    build_captures,
    capture_id,
)
from shoebox.dedup.groups import (
    GROUPS_KIND,
    GROUPS_VERSION,
    build_groups,
    groups_params,
    inputs_digest,
    load_frames,
    write_groups,
    write_records,
)

__all__ = [
    "CAPTURE_KIND", "CAPTURE_VERSION", "GROUPS_KIND", "GROUPS_VERSION", "RULE_VERSIONS", "build_captures",
    "build_groups", "capture_id", "capture_params", "captures", "groups_params", "load_frames",
    "write_captures", "write_groups",
]


def captures(rows: Iterable[dict], pixels: dict[str, dict], consented: Consented) -> list[dict]:
    """`build_captures` plus `canonical_asset_id` and `canonical_reason` on each capture."""
    rows = list(rows)
    facts = asset_facts(rows, pixels, consented)
    out = []
    for c in build_captures(rows, pixels):
        aid, reason = choose(c, facts)
        out.append({**c, "canonical_asset_id": aid, "canonical_reason": reason})
    return out


def capture_params(rows: Iterable[dict], pixels: dict[str, dict], pixel_key: tuple[str, str]) -> dict:
    """What a capture set depends on: versions and inputs (new inputs give a new store dir)."""
    rows = list(rows)
    return {"version": CAPTURE_VERSION, "rules": RULE_VERSIONS, "pixelhash": list(pixel_key),
            "inputs": inputs_digest(rows, pixels)}


def write_captures(cfg: Config, caps: list[dict], params: dict) -> Path:
    """Store captures under `results/capture/capture-v1/<params_hash>/`, keyed by capture id."""
    return write_records(cfg, CAPTURE_KIND, CAPTURE_VERSION, params, [(c["capture_id"], c) for c in caps])
