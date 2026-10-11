"""Quarantine file (design D3, spec content-screen): owner verdicts on screen-flagged assets.

`evals/quarantine.jsonl`, append-only, the latest row per asset_id wins. A row is `asset_id`, `screen`
(`nudity` | `document`), `flagged_at`, `verdict` (`pending` when flagged), and once reviewed `verdict_by`
and `verdict_at`. Asset hashes only, never paths or images.

A verdict applies to the asset's group: the same asset_id (byte copies, in any source), assets with the
same pixel hash, and any further grouping a caller supplies. The most restrictive verdict in the group wins.
- hosted and Claude-viewing stages refuse `pending`, `private` and `exclude`;
- local stages refuse `pending` and `exclude`;
- `include`, or no row, is clear.
"""

import json
import os
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from shoebox import config

QUARANTINE_PATH = config.REPO_ROOT / "evals" / "quarantine.jsonl"
SCREENS = ("nudity", "document")
VERDICTS = ("pending", "include", "private", "exclude")
RESTRICTIVENESS = {"include": 0, "private": 1, "pending": 2, "exclude": 3}
REFUSED = {"hosted": frozenset({"pending", "private", "exclude"}), "local": frozenset({"pending", "exclude"})}

Groups = Mapping[str, Iterable[str]] | Callable[[str], Iterable[str]]


def _path(path: Path | None) -> Path:
    return Path(path or QUARANTINE_PATH)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load(path: Path | None = None) -> dict[str, dict]:
    """`{asset_id: latest row}`. A missing file is empty; an unreadable line fails loudly (it may be a verdict)."""
    path = _path(path)
    if not path.exists():
        return {}
    out = {}
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
                out[row["asset_id"]] = row
            except (ValueError, KeyError, TypeError) as e:
                raise ValueError(f"{path}:{i}: unreadable quarantine line ({e})") from e
    return out


def _append(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        f.flush()
        os.fsync(f.fileno())


def flag(asset_ids: Iterable[str], screen: str, path: Path | None = None, now: str | None = None) -> int:
    """Queue assets as `pending`. An asset that already has a row (any verdict) is not re-queued.
    Returns the number of rows appended."""
    if screen not in SCREENS:
        raise ValueError(f"unknown screen {screen!r}; expected one of {SCREENS}")
    path, now = _path(path), now or _now()
    seen = set(load(path))
    rows = []
    for a in asset_ids:
        if a not in seen:
            seen.add(a)
            rows.append({"asset_id": a, "screen": screen, "flagged_at": now, "verdict": "pending"})
    if rows:
        _append(path, rows)
    return len(rows)


def record_verdict(asset_id: str, verdict: str, by: str, path: Path | None = None, now: str | None = None) -> dict:
    """Append the owner's verdict for a flagged asset (KeyError if it was never flagged). Returns the row."""
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}; expected one of {VERDICTS}")
    path = _path(path)
    prev = load(path).get(asset_id)
    if prev is None:
        raise KeyError(f"asset {asset_id} has no quarantine row")
    row = {"asset_id": asset_id, "screen": prev.get("screen"), "flagged_at": prev.get("flagged_at"),
           "verdict": verdict, "verdict_by": by, "verdict_at": now or _now()}
    _append(path, [row])
    return row


def same_pixel_groups(pixels: Mapping[str, str]) -> dict[str, set[str]]:
    """`{asset_id: assets with the same pixel hash}` from `{asset_id: pixel_hash}`."""
    by_hash = defaultdict(set)
    for a, h in pixels.items():
        by_hash[h].add(a)
    return {a: by_hash[h] for a, h in pixels.items()}


@dataclass(frozen=True)
class Checker:
    rows: dict[str, dict]
    groups: Groups | None = None  # asset_id -> other members of its group
    _members: Callable[[str], Iterable[str]] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        g = self.groups
        fn = (lambda a: ()) if g is None else g if callable(g) else (lambda a: g.get(a, ()))
        object.__setattr__(self, "_members", fn)

    @classmethod
    def load(cls, path: Path | None = None, groups: Groups | None = None) -> "Checker":
        return cls(load(path), groups)

    def verdict(self, asset_id: str, group: Iterable[str] = ()) -> str | None:
        """The most restrictive verdict over the asset, its group and `group`; None if none has a row."""
        found = [self.rows[a]["verdict"] for a in {asset_id, *self._members(asset_id), *group} if a in self.rows]
        return max(found, key=lambda v: RESTRICTIVENESS.get(v, len(RESTRICTIVENESS))) if found else None

    def refused(self, asset_id: str, stage: str, group: Iterable[str] = ()) -> bool:
        """`stage` is `hosted` (hosted models, Claude viewing) or `local` (local caption/embed stages)."""
        if stage not in REFUSED:
            raise ValueError(f"unknown stage {stage!r}; expected one of {sorted(REFUSED)}")
        v = self.verdict(asset_id, group)
        return v is not None and (v in REFUSED[stage] or v not in RESTRICTIVENESS)
