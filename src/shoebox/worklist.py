"""Caption-once worklist (design D7, task 6.4) and frame-wide refusal counts (task 3.3).

Inputs are the latest frame of every configured source, the pixel-hash store, the derive index and the
quarantine. A configured source without a current frame fails closed (PrivacyError), as hosted sends do.

Queue per capture, in this order:
- `held`: some member or linked asset is refused by the quarantine for *local* stages (`pending`,
  `exclude`, incl. via a same-pixel copy). Local captioning refuses these too, so they wait for a verdict.
- `not_derived`: the canonical asset has no `ok` record in the derive index of the profile (videos
  included): nothing to caption yet. Counted, never sent.
- `local`: some member or linked asset is refused for the provider (`privacy.filter_items`, which
  applies the copy index and the quarantine): the capture goes to the local 12B queue.
- `hosted`: every member and linked asset is eligible and the canonical image is derived.
`eligible` is the privacy verdict alone, so a not_derived capture can still be `eligible: true`.
"""

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shoebox import config, copies, dedup, privacy, quarantine
from shoebox.dedup.groups import media_rows

QUEUES = ("hosted", "local", "held", "not_derived")
DEFAULT_BATCH_DISCOUNT = 0.5


@dataclass
class Inputs:
    rows: list[dict]
    frame_dates: dict[str, str]
    pixels: dict[str, dict]
    pixel_key: tuple[str, str]


def load_inputs(cfg: config.Config, profile: str = "infer-v1") -> Inputs:
    """Rows of the latest frame of every configured source (fail closed on a missing one) and the pixel hashes."""
    import pyarrow.parquet as pq

    from shoebox import derive

    latest, missing = copies.latest_frames(cfg)
    if missing:
        raise privacy.PrivacyError(
            f"no current inventory frame for source(s): {', '.join(missing)}. Run `shoebox inventory` first."
        )
    rows: list[dict] = []
    for path in sorted({p for _, p in latest.values()}):
        wanted = {s for s, (_, p) in latest.items() if p == path}
        rows += [r for r in pq.read_table(path).to_pylist() if r.get("source") in wanted]
    settings = derive.load_profile(profile)
    return Inputs(rows, {s: d.isoformat() for s, (d, _) in sorted(latest.items())},
                  derive.pixelhash_index(cfg, settings), derive.pixelhash_key(settings))


def _items(rows: list[dict]) -> list[dict]:
    return [{"asset_id": r["asset_id"], "source": r.get("source"), "relpath": r.get("relpath"),
             "group_id": r.get("group_id")} for r in rows if r.get("asset_id")]


def _by_source(s: privacy.Skip) -> str | None:
    """The restricting source: the restricted copy's source for copy_restricted, else the item's own."""
    return s.restricting_source or s.source


def frame_refusals(cfg: config.Config, provider: str = "gemini") -> dict:
    """Counts only: every frame row with an asset id, run through `privacy.filter_items` for `provider`."""
    rows = load_inputs(cfg).rows
    items = _items(rows)
    elig = privacy.filter_items(items, cfg, privacy.load_never_send(), provider=provider,
                                copies=copies.load(cfg), quarantine=quarantine.Checker.load())
    return {
        "items": len(items),
        "assets": len({it["asset_id"] for it in items}),
        "allowed": len(elig.allowed),
        "refused_assets": len({s.asset_id for s in elig.skipped}),
        "by_reason": dict(sorted(Counter(s.reason for s in elig.skipped).items())),
        "by_source": dict(sorted(Counter(_by_source(s) for s in elig.skipped).items(),
                                 key=lambda kv: str(kv[0]))),
    }


@dataclass
class Worklist:
    header: dict
    captures: list[dict]
    counts: dict


def build(cfg: config.Config, provider: str = "gemini", profile: str = "infer-v1",
          now: str | None = None) -> Worklist:
    from shoebox import derive

    inp = load_inputs(cfg, profile)
    index = copies.load(cfg)
    never = privacy.load_never_send()
    checker = quarantine.Checker.load()

    def consented(source: str, relpath: str) -> bool:
        return privacy._own_restriction({"source": source, "relpath": relpath}, cfg, never, provider) is None

    caps = dedup.captures(inp.rows, inp.pixels, consented)
    media = media_rows(inp.rows)
    elig = privacy.filter_items(_items(media), cfg, never, provider=provider, copies=index, quarantine=checker)
    refused: dict[str, privacy.Skip] = {}
    for s in elig.skipped:
        refused.setdefault(s.asset_id, s)
    derived = {a for a, r in derive.load_index(cfg, profile).items() if r.get("status") == "ok"}
    kind = {r["asset_id"]: r.get("kind") for r in media}

    out = []
    for c in caps:
        members = [m["asset_id"] for m in c["members"]]
        every = sorted({*members, *(x["asset_id"] for x in c["linked"])})
        held = [a for a in every if checker.refused(a, "local", index.pixel_group(a))]
        skips = [refused[a] for a in every if a in refused]
        if held:
            skips = [s for s in skips if s.reason == "quarantined"] or skips
        canon = c["canonical_asset_id"]
        if held:
            queue = "held"
        elif kind.get(canon) != "image" or canon not in derived:
            queue = "not_derived"
        elif skips:
            queue = "local"
        else:
            queue = "hosted"
        s = skips[0] if skips else None
        out.append({
            "type": "capture",
            "capture_id": c["capture_id"],
            "canonical_asset_id": canon,
            "canonical_reason": c["canonical_reason"],
            "rule": c["rule"],
            "rule_version": c["rule_version"],
            "members": members,
            "roles": {m["asset_id"]: m["role"] for m in c["members"]},
            "linked": c["linked"],
            "group_size": c["group_size"],
            "eligible": not skips and not held,
            "queue": queue,
            "refusal": ({"reason": s.reason, "restricting_source": _by_source(s)} if s
                        else {"reason": "quarantined", "restricting_source": None} if held else None),
        })

    q = Counter(c["queue"] for c in out)
    counts = {
        "files": len(media),
        "assets": len({r["asset_id"] for r in media}),
        "captures": len(out),
        "capture_assets": sum(len(c["members"]) + len(c["linked"]) for c in out),
        "eligible": sum(c["eligible"] for c in out),
        **{k: q.get(k, 0) for k in QUEUES},
        "refused_by_reason": dict(sorted(Counter(c["refusal"]["reason"] for c in out if c["refusal"]).items())),
    }
    check_counts(counts)
    header = {
        "type": "header",
        "dedup_version": dedup.CAPTURE_VERSION,
        "rule_versions": dict(dedup.RULE_VERSIONS),
        "sources": sorted(cfg.sources),
        "frame_dates": inp.frame_dates,
        "provider": provider,
        "profile": profile,
        "pixelhash": list(inp.pixel_key),
        "created_at": now or datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return Worklist(header, out, counts)


def check_counts(n: dict) -> None:
    """files >= assets = assets held by captures >= captures = sum of queues; hosted <= eligible."""
    assert n["files"] >= n["assets"], n
    assert n["assets"] == n["capture_assets"], n
    assert n["assets"] >= n["captures"], n
    assert sum(n[k] for k in QUEUES) == n["captures"], n
    assert n["hosted"] <= n["eligible"] <= n["captures"], n


def write(cfg: config.Config, wl: Worklist, name: str) -> Path:
    """`<shoebox_root>/worklists/<name>.jsonl`, written atomically (temp file, fsync, rename)."""
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"bad worklist name {name!r}")
    path = Path(cfg.shoebox_root).expanduser() / "worklists" / f"{name}.jsonl"
    config.check_write_location(cfg, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w") as f:
        for rec in (wl.header, *wl.captures):
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def batch_usd(cand: dict, n: int) -> tuple[float, str | None]:
    """Pre-flight USD for n captures at the batch price; a note when the discount is assumed."""
    est = privacy.estimate(cand, n).usd
    if cand.get("price_batch_discount") is not None:
        return est * float(cand["price_batch_discount"]), None
    return est * DEFAULT_BATCH_DISCOUNT, (
        f"candidate has no price_batch_discount; assumed {DEFAULT_BATCH_DISCOUNT} (Gemini Batch API half price)")


def summary_md(wl: Worklist, name: str, candidate: str, cand: dict) -> str:
    """Counts and USD only: no paths, no asset or capture ids."""
    n, h = wl.counts, wl.header
    usd, note = batch_usd(cand, n["hosted"])
    discount = cand.get("price_batch_discount", DEFAULT_BATCH_DISCOUNT)
    lines = [
        f"# Worklist {name}",
        "",
        f"- created: {h['created_at']}; provider {h['provider']}; profile {h['profile']}",
        f"- dedup: {h['dedup_version']}; sources: {', '.join(h['sources'])}",
        "- frame dates: " + ", ".join(f"{s} {d}" for s, d in h["frame_dates"].items()),
        "",
        "## Counts",
        "",
        f"files {n['files']} → assets {n['assets']} → captures {n['captures']} → eligible {n['eligible']}",
        "",
        f"queues: hosted {n['hosted']}, local {n['local']}, held {n['held']}, not_derived {n['not_derived']}",
        "",
        "refusals by reason: " + (", ".join(f"{k} {v}" for k, v in n["refused_by_reason"].items()) or "none"),
        "",
        "## Cost",
        "",
        (f"${usd:.4f} for {n['hosted']} hosted capture(s) with {candidate} at batch price "
         f"(discount {discount}; price_date {cand.get('price_date')})."),
    ]
    if note:
        lines += ["", f"Note: {note}."]
    lines += ["", "Counts check: queues sum to captures; captures hold every asset once (asserted)."]
    return "\n".join(lines) + "\n"
