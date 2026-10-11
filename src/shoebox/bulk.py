"""Worklist-driven caption runs (design D8, tasks 8.1/8.2) and the refusal fallback selection (D10, task 8.7).

The guard (`load_worklist`, `check_sources`), before any request:
- an inventory frame (parquet, by suffix or by its `PAR1` magic) is refused;
- a file without the worklist header record (e.g. a raw items list) is refused;
- a worklist whose header `sources` lacks a currently configured source is refused, naming it, unless a
  `--partial-sources` reason is given; the reason goes into the run record.
Eval-set runs (`shoebox label run --items`) do not come through here and are unchanged.

Routing: a hosted candidate (runtime `gemini-batch` or `gemini`) takes only `queue == "hosted"` rows, a local
one (`ollama`) only `queue == "local"` rows; `held` and `not_derived` rows never run.

Reuse (`plan`): a capture is skipped and counted as reused when its canonical asset, any member or any linked
asset has an `ok` record in the candidate's ordered label-source chain (`chain_names`), resolved through
`runners.source_index`. Only the remaining captures' canonical assets are sent.
"""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from shoebox import config, privacy, quarantine, runners, store

HOSTED_RUNTIMES = ("gemini-batch", "gemini")
LOCAL_RUNTIMES = ("ollama",)
# A candidate's label-source chain when neither the CLI nor the candidate's `label_sources` names one:
# the batch arm reuses the synchronous eval store's labels too (same prompt, schema and model).
DEFAULT_CHAINS = {"gemini-batch-v2": ["gemini-batch-v2", "gemini-medium-v2"]}


class Refused(Exception):
    """The run is refused before any request."""


# ---------- the guard ----------


def resolve_path(cfg: config.Config, worklist: str) -> Path:
    """A path, or a bare name meaning `<shoebox_root>/worklists/<name>.jsonl` (as `shoebox dedup worklist`)."""
    p = Path(worklist).expanduser()
    if p.exists() or "/" in worklist or p.suffix:
        return p
    return Path(cfg.shoebox_root).expanduser() / "worklists" / f"{worklist}.jsonl"


def load_worklist(path: Path) -> tuple[dict, list[dict]]:
    """(header, capture rows). Refuses an inventory frame and anything without the worklist header."""
    if path.suffix == ".parquet":
        raise Refused(f"{path.name} is an inventory frame; bulk runs take a worklist (`shoebox dedup worklist`)")
    with open(path, "rb") as f:
        if f.read(4) == b"PAR1":
            raise Refused(f"{path.name} is an inventory frame (parquet); bulk runs take a worklist "
                          "(`shoebox dedup worklist`)")
    try:
        recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (ValueError, UnicodeDecodeError) as e:
        raise Refused(f"{path.name} is not a worklist (not JSON lines: {e})") from e
    if not recs or not isinstance(recs[0], dict) or recs[0].get("type") != "header":
        raise Refused(f"{path.name} is not a worklist: no header record (a raw items list? eval sets run with "
                      "`shoebox label run --items`)")
    caps = recs[1:]
    if any(not isinstance(c, dict) or c.get("type") != "capture" for c in caps):
        raise Refused(f"{path.name} is not a worklist: a row after the header is not a capture")
    return recs[0], caps


def check_sources(header: dict, cfg: config.Config, partial_reason: str | None) -> list[str]:
    """The configured sources the worklist was not built over. Refuses if any, unless a reason is given."""
    missing = sorted(set(cfg.sources) - set(header.get("sources") or ()))
    if missing and not (partial_reason or "").strip():
        raise Refused(f"worklist lacks configured source(s): {', '.join(missing)}. Rebuild it with "
                      "`shoebox dedup worklist`, or give --partial-sources \"<reason>\" to caption by stages")
    return missing


def queue_for(res: runners.Resolved) -> str:
    rt = res.cand["runtime"]
    if rt in HOSTED_RUNTIMES:
        return "hosted"
    if rt in LOCAL_RUNTIMES:
        return "local"
    raise Refused(f"candidate {res.name} (runtime {rt}) is neither hosted ({', '.join(HOSTED_RUNTIMES)}) nor "
                  f"local ({', '.join(LOCAL_RUNTIMES)})")


# ---------- reuse ----------


def chain_names(res: runners.Resolved, override: str | None) -> list[str]:
    """The ordered label-source chain: `--label-sources a,b`, else the candidate's `label_sources`, else
    `DEFAULT_CHAINS`, else the candidate alone. The candidate itself is always first."""
    if override:
        names = [n.strip() for n in override.split(",") if n.strip()]
    else:
        names = list(res.cand.get("label_sources") or DEFAULT_CHAINS.get(res.name) or [res.name])
    return [res.name, *(n for n in names if n != res.name)]


def chain_index(cfg: config.Config, names: list[str], resolve=runners.resolve) -> dict[str, dict]:
    chain = [resolve(n) for n in names]
    return runners.source_index(cfg, runners.label_source(chain))


def capture_assets(c: dict) -> list[str]:
    """Canonical first, then members and linked assets."""
    out = [c["canonical_asset_id"]]
    for a in [*c.get("members", ()), *(x["asset_id"] for x in c.get("linked", ()))]:
        if a not in out:
            out.append(a)
    return out


@dataclass
class Plan:
    counts: dict
    to_send: list[dict]


def plan(captures: list[dict], queue: str, index: dict[str, dict]) -> Plan:
    """Captures of `queue`, less those with an `ok` label on any of their assets in `index`."""
    mine = [c for c in captures if c.get("queue") == queue]
    reused = [c for c in mine if any((index.get(a) or {}).get("status") == "ok" for a in capture_assets(c))]
    rset = {id(c) for c in reused}
    to_send = [c for c in mine if id(c) not in rset]
    by_queue = {q: sum(c.get("queue") == q for c in captures) for q in ("hosted", "local", "held", "not_derived")}
    counts = {
        "assets": sum(len(c.get("members", ())) + len(c.get("linked", ())) for c in captures),
        "captures": len(captures),
        "eligible": sum(bool(c.get("eligible")) for c in captures),
        **by_queue,
        "queue": queue,
        "for_candidate": len(mine),
        "reused": len(reused),
        "to_send": len(to_send),
    }
    assert counts["reused"] + counts["to_send"] == counts["for_candidate"] <= counts["captures"], counts
    assert sum(by_queue.values()) == counts["captures"], counts
    return Plan(counts, to_send)


def summary_line(n: dict) -> str:
    return (f"assets {n['assets']} -> captures {n['captures']} -> eligible {n['eligible']} -> {n['queue']} "
            f"{n['for_candidate']} -> reused {n['reused']} -> to send {n['to_send']}  "
            f"(queues: hosted {n['hosted']}, local {n['local']}, held {n['held']}, "
            f"not_derived {n['not_derived']})")


def items_for(captures: list[dict], cfg: config.Config) -> tuple[list[dict], list[str]]:
    """One item per capture (its canonical asset at its first location in the copy index, so the privacy
    filter checks a real source and relpath), and the canonical ids with no location in the current frames."""
    index = privacy.default_copy_index(cfg)
    items, unlocated = [], []
    for c in captures:
        aid = c["canonical_asset_id"]
        locs = sorted(index.locations(aid))
        if not locs:
            unlocated.append(aid)
            continue
        src, rel, _ = locs[0]
        items.append({"asset_id": aid, "source": src, "relpath": rel, "group_id": None})
    return items, unlocated


# ---------- run record ----------


def write_run_record(cfg: config.Config, candidate: str, rec: dict, path: Path | None = None) -> Path:
    """`results/bulk-runs/<candidate>/<started>_<random>.json`: guard decisions, chain, counts, outcome (no asset ids)."""
    if path is None:
        stamp = f"{rec['started'].replace(':', '')}_{uuid.uuid4().hex[:6]}"
        path = cfg.results_dir / "bulk-runs" / candidate / f"{stamp}.json"
    config.check_write_location(cfg, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def batch_transport():
    from shoebox.runners import gemini_batch

    return gemini_batch.HttpTransport()


# ---------- fallback (8.7) ----------


def fallback_selection(cfg: config.Config, primary: list[runners.Resolved]) -> list[str]:
    """Asset ids whose final record in the primary chain is `invalid` because it was refused, or truncated
    with no recovering retry (`runners._falls_through`, which classifies sync records from their raw)."""
    idx = runners.source_index(cfg, runners.label_source(primary))
    return sorted(a for a, r in idx.items() if store.is_final(r) and runners._falls_through(r))


def held_locally(asset_ids: list[str], cfg: config.Config) -> set[str]:
    """Assets the quarantine refuses for local stages (incl. via a same-pixel copy)."""
    checker = quarantine.Checker.load()
    index = privacy.default_copy_index(cfg)
    return {a for a in asset_ids if checker.refused(a, "local", index.pixel_group(a))}
