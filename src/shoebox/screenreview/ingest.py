"""`shoebox screen ingest <answers.json>`: the owner's verdicts from the screen review page into the quarantine.

Answers are `{page_id, verdicts: {"<item number>": "include"|"private"|"exclude"}}`. Refused, with no verdict
written, unless the page id matches the page-map and every item has a valid verdict. Each verdict is written
with `verdict_by = "owner"` for the asset and every member of its group (same-pixel copies at least; the caller
passes the grouping). A group member or sampled asset without a quarantine row is flagged first, then ruled.
A verdict never loosens: page items in one group get the most restrictive verdict given to any of them, and a
member whose existing owner verdict is more restrictive keeps it (counted in `kept_stricter`).
"""

import json
import shutil
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from shoebox import quarantine

OWNER = "owner"
VERDICTS = ("include", "private", "exclude")


class IngestError(ValueError):
    pass


@dataclass
class IngestResult:
    counts: dict[str, int] = field(default_factory=dict)  # verdicts given on the page
    group_members: int = 0  # further assets ruled through their group
    kept_stricter: int = 0  # group members whose existing, more restrictive verdict was kept
    thumbnails_deleted: bool = False


def validate(ans, pm: dict) -> list[str]:
    if not isinstance(ans, dict):
        return ["answers are not a JSON object"]
    errs = []
    if ans.get("page_id") != pm["page_id"]:
        errs.append(f"page_id {ans.get('page_id')!r} is not this page's {pm['page_id']!r}")
        return errs
    v = ans.get("verdicts")
    if not isinstance(v, dict):
        return ["no verdicts object"]
    missing = [k for k in pm["items"] if k not in v]
    bad = [k for k in pm["items"] if k in v and v[k] not in VERDICTS]
    extra = [k for k in v if k not in pm["items"]]
    if missing:
        errs.append(f"{len(missing)} of {len(pm['items'])} items unanswered (first: #{missing[0]})")
    if bad:
        errs.append(f"{len(bad)} items with a verdict not in {VERDICTS} (first: #{bad[0]})")
    if extra:
        errs.append(f"{len(extra)} answers for items not on this page")
    return errs


def ingest(ans, page_map: Path, groups: Mapping[str, Iterable[str]] | Callable[[str], Iterable[str]] | None = None,
           delete_thumbnails: bool = False, path: Path | None = None) -> IngestResult:
    pm = json.loads(Path(page_map).read_text())
    errs = validate(ans, pm)
    if errs:
        raise IngestError("; ".join(errs))
    members = (lambda a: ()) if groups is None else groups if callable(groups) else (lambda a: groups.get(a, ()))
    res = IngestResult()
    counts: Counter = Counter()
    rank = quarantine.RESTRICTIVENESS
    # Page items whose groups overlap form one group; the most restrictive verdict given on the page wins it.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    page: list[tuple[str, str, str]] = []  # (asset_id, verdict, screen)
    for k, item in sorted(pm["items"].items(), key=lambda kv: int(kv[0])):
        verdict, a = ans["verdicts"][k], item["asset_id"]
        screen = item["screen"] if item["screen"] in quarantine.SCREENS else quarantine.SCREENS[0]
        counts[verdict] += 1
        page.append((a, verdict, screen))
        for m in members(a):
            parent[find(m)] = find(a)
        find(a)
    group_verdict: dict[str, str] = {}
    for a, verdict, _ in page:
        r = find(a)
        if r not in group_verdict or rank[verdict] > rank[group_verdict[r]]:
            group_verdict[r] = verdict
    on_page = {a for a, _, _ in page}
    done: set[str] = set()
    for a, _, screen in page:
        group = sorted(m for m in parent if find(m) == find(a) and m not in done)
        if not group:
            continue
        done.update(group)
        verdict = group_verdict[find(a)]
        quarantine.flag(group, screen, path=path)  # no-op for assets that already have a row
        rows = quarantine.load(path)
        for m in group:
            have = rows[m].get("verdict")
            # Never loosen: an existing owner verdict stricter than (or equal to) this one stands.
            if have != "pending" and have in rank and rank[have] >= rank[verdict]:
                if rank[have] > rank[verdict]:
                    res.kept_stricter += 1
                continue
            quarantine.record_verdict(m, verdict, OWNER, path=path)
        res.group_members += sum(m not in on_page for m in group)
    res.counts = dict(counts)
    if delete_thumbnails:
        thumbs = Path(pm["thumbs_dir"])
        if thumbs.name == "img" and thumbs.parent == Path(pm["page_dir"]) and thumbs.is_dir():
            shutil.rmtree(thumbs)
            res.thumbnails_deleted = True
    return res
