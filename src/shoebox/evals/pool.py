"""A known-item search set with distractors: a base set's items and queries plus a uniform random draw of
other photos from the sampling frame (`shoebox eval search-pool`; docs/evals.md, "Distractor pool").

The queries and their relevant sets are the base set's, unchanged: the distractors carry no judgment, so
an unjudged distractor that also fits a query counts as a miss (the known-item caveat).
"""

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from shoebox import derive, privacy
from shoebox.config import Config, check_write_location
from shoebox.evals import search
from shoebox.pilot.select import MAX_BYTES

OWNER = "owner"
RENDERABLE_SRC_KINDS = frozenset(derive.KIND_BY_EXT.values())  # what derive.render can decode
FRAME_COLUMNS = ("source", "relpath", "ext", "kind", "bytes", "mtime", "asset_id", "status", "src_kind", "date",
                 "colour_guess", "group_id", "group_rule")
ITEM_KEYS = ("asset_id", "source", "relpath", "ext", "bytes", "mtime", "bucket", "group_id", "group_rule")
OUT_FILES = ("items.jsonl", search.QUERIES_FILE, "pool.json", "README.md")

# Per-path reasons, checked in this order (the first that applies is counted).
ROW_RULES = {
    "not_image": "kind == 'image'",
    "status_not_ok": "status == 'ok'",
    "no_asset_id": "asset_id is not null",
    "unrenderable": f"ext has a derive decoder and src_kind in {sorted(RENDERABLE_SRC_KINDS)}",
    "unknown_source": "source is in config/shoebox.toml",
    "not_owner_source": f"the source's person is {OWNER!r} (others' photos are never drawn)",
    "base_set": "asset_id is not in the base set",
    "never_send": "not on evals/never-send.txt (asset_id or path glob, shoebox.privacy parsing)",
    "target_group": "not in a frame capture group (group_id) that holds a target",
    "never_send_group": "not in a frame capture group that holds a never-send path",
    "too_large": f"bytes <= {MAX_BYTES} (shoebox.pilot.select.MAX_BYTES)",
}
# A hash excluded for one of these on any path is excluded on every path ("copy_excluded").
ASSET_REASONS = ("unknown_source", "not_owner_source", "base_set", "never_send", "target_group",
                 "never_send_group")


@dataclass
class PoolResult:
    out_dir: Path
    n_requested: int
    n_drawn: int
    eligible_assets: int
    exclusions: dict[str, int] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_frame(path: Path) -> list[dict]:
    names = pq.read_schema(path).names
    missing = [c for c in FRAME_COLUMNS if c not in names]
    if missing:
        raise ValueError(f"frame {path} lacks columns {missing}")
    return pq.read_table(path, columns=list(FRAME_COLUMNS)).to_pylist()


def _row_reason(r: dict, cfg: Config, base_ids: set[str], never: privacy.NeverSend,
                target_groups: set[str], never_groups: set[str]) -> str | None:
    if r.get("kind") != "image":
        return "not_image"
    if r.get("status") != "ok":
        return "status_not_ok"
    if not r.get("asset_id"):
        return "no_asset_id"
    if derive.KIND_BY_EXT.get(str(r.get("ext") or "").lower().lstrip(".")) is None \
            or r.get("src_kind") not in RENDERABLE_SRC_KINDS:
        return "unrenderable"
    source = cfg.sources.get(r.get("source") or "")
    if source is None:
        return "unknown_source"
    if source.person != OWNER:
        return "not_owner_source"
    if r["asset_id"] in base_ids:
        return "base_set"
    if never.matches(r["asset_id"], r.get("relpath")):
        return "never_send"
    if r.get("group_id") and r["group_id"] in target_groups:
        return "target_group"
    if r.get("group_id") and r["group_id"] in never_groups:
        return "never_send_group"
    if r.get("bytes") is not None and int(r["bytes"]) > MAX_BYTES:
        return "too_large"
    return None


def eligible(rows: list[dict], cfg: Config, base_ids: set[str], never: privacy.NeverSend
             ) -> tuple[dict[str, dict], dict[str, int], int]:
    """({asset_id: representative row}, {reason: paths excluded}, duplicate paths dropped)."""
    target_groups = {r["group_id"] for r in rows if r.get("group_id") and r.get("asset_id") in base_ids}
    never_groups = {r["group_id"] for r in rows if r.get("group_id") and never.matches(r.get("asset_id"),
                                                                                       r.get("relpath"))}
    reasons = [_row_reason(r, cfg, base_ids, never, target_groups, never_groups) for r in rows]
    bad_ids = {r["asset_id"] for r, why in zip(rows, reasons, strict=True) if why in ASSET_REASONS}
    counts: Counter = Counter()
    by_id: dict[str, list[dict]] = {}
    for r, why in zip(rows, reasons, strict=True):
        if why is None and r["asset_id"] in bad_ids:
            why = "copy_excluded"
        if why:
            counts[why] += 1
        else:
            by_id.setdefault(r["asset_id"], []).append(r)
    reps = {aid: min(rs, key=lambda r: (r["source"], r["relpath"])) for aid, rs in by_id.items()}
    dups = sum(len(rs) - 1 for rs in by_id.values())
    order = [*ROW_RULES, "copy_excluded"]
    return reps, {k: counts[k] for k in order if counts[k]}, dups


def draw(asset_ids: list[str], n: int, seed: int) -> list[str]:
    """Uniform without replacement over the sorted ids; returned sorted by asset_id."""
    pool_ids = sorted(asset_ids)
    k = min(n, len(pool_ids))
    idx = np.random.default_rng(seed).choice(len(pool_ids), size=k, replace=False) if k else []
    return sorted(pool_ids[int(i)] for i in idx)


def distractor_item(r: dict) -> dict:
    out = {"asset_id": r["asset_id"], "source": r["source"], "relpath": r["relpath"],
           "ext": str(r.get("ext") or "").lower().lstrip("."), "bytes": r.get("bytes"), "mtime": r.get("mtime"),
           "bucket": "distractor", "group_id": r.get("group_id"), "group_rule": r.get("group_rule")}
    return {**{k: out[k] for k in ITEM_KEYS}, "role": "distractor"}


def _folder(relpath: str) -> str:
    """The first two folder components (never a file name)."""
    parts = Path(relpath).parent.parts[:2]
    return "/".join(parts) if parts else "(source root)"


def _era(date) -> str:
    s = str(date or "")
    if len(s) >= 4 and s[:4].isdigit():
        return f"{s[:3]}0s"
    return "unknown"


def _table(title: str, counter: Counter) -> list[str]:
    out = [f"## Distractors by {title}", "", f"| {title} | files |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))]
    return [*out, ""]


def readme(out_name: str, meta: dict, reps: list[dict], n_targets: int) -> str:
    lines = [f"# {out_name}", "",
             (f"Known-item search set: the {n_targets} items and the queries of `{meta['base_set']}`, plus "
             f"{meta['n_drawn']} distractors drawn uniformly at random (seed {meta['seed']}) from "
             f"{meta['eligible_assets']} eligible distinct photos in the sampling frame. Written by "
             "`shoebox eval search-pool`; details in `pool.json` and docs/evals.md (\"Distractor pool\"). "
             "No image bytes and no file names here."), "",
             f"- Targets: {n_targets}", f"- Distractors: {meta['n_drawn']} (requested {meta['n_requested']})",
             f"- Frame sha256: `{meta['frame']['sha256']}`", ""]
    lines += _table("folder", Counter(_folder(r["relpath"]) for r in reps))
    lines += _table("ext", Counter(str(r.get("ext") or "").lower() for r in reps))
    lines += _table("src_kind", Counter(str(r.get("src_kind")) for r in reps))
    lines += _table("colour_guess", Counter(str(r.get("colour_guess")) for r in reps))
    lines += _table("date era", Counter(_era(r.get("date")) for r in reps))
    lines += ["## Exclusions (frame paths)", "", "| reason | paths |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in meta["exclusions"].items()]
    lines += ["", f"Duplicate paths of an eligible photo (one kept per asset_id): {meta['duplicate_paths']}", ""]
    return "\n".join(lines)


def _write(path: Path, text: str | bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(text if isinstance(text, bytes) else text.encode())
    os.replace(tmp, path)


def build_pool(cfg: Config, base_dir: Path, frame_path: Path, n: int, seed: int, out_dir: Path,
               never_send_path: Path | None = None, force: bool = False) -> PoolResult:
    base_dir, frame_path, out_dir = Path(base_dir), Path(frame_path), Path(out_dir)
    if n < 0:
        raise ValueError("n must be >= 0")
    qpath = base_dir / search.QUERIES_FILE
    if not qpath.exists():
        raise FileNotFoundError(f"{qpath} missing: run `shoebox eval search-queries --set {base_dir.name}`")
    if out_dir.resolve() == base_dir.resolve():
        raise ValueError("the pool set must be a new set, not the base set")
    if not force and any((out_dir / f).exists() for f in OUT_FILES):
        raise FileExistsError(f"{out_dir} already holds a pool (pass --force to overwrite)")
    check_write_location(cfg, out_dir)
    base_lines = [line for line in (base_dir / "items.jsonl").read_text().splitlines() if line.strip()]
    base = [json.loads(line) for line in base_lines]
    base_ids = {it["asset_id"] for it in base}
    never = privacy.load_never_send(never_send_path)
    rows = read_frame(frame_path)
    reps, exclusions, dups = eligible(rows, cfg, base_ids, never)
    chosen = [reps[a] for a in draw(list(reps), n, seed)]
    meta = {
        "base_set": base_dir.name, "frame": {"path": str(frame_path), "sha256": _sha256(frame_path),
                                             "rows": len(rows)},
        "seed": seed, "n_requested": n, "n_drawn": len(chosen), "eligible_assets": len(reps),
        "draw": "numpy.random.default_rng(seed).choice over the sorted eligible asset_ids, without replacement; "
                "written sorted by asset_id",
        "dedupe": "one row per asset_id: the smallest (source, relpath) among its eligible paths",
        "eligibility": ROW_RULES, "asset_level_reasons": list(ASSET_REASONS),
        "max_bytes": MAX_BYTES, "never_send_sha256": (_sha256(Path(never_send_path or privacy.NEVER_SEND_PATH))
                                                      if Path(never_send_path or privacy.NEVER_SEND_PATH).exists()
                                                      else None),
        "exclusions": exclusions, "duplicate_paths": dups,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({**it, "role": "target"}, ensure_ascii=False) for it in base]
    lines += [json.dumps(distractor_item(r), ensure_ascii=False) for r in chosen]
    _write(out_dir / "items.jsonl", "".join(line + "\n" for line in lines))
    _write(out_dir / search.QUERIES_FILE, qpath.read_bytes())
    _write(out_dir / "pool.json", json.dumps(meta, indent=2) + "\n")
    _write(out_dir / "README.md", readme(out_dir.name, meta, chosen, len(base)))
    return PoolResult(out_dir, n, len(chosen), len(reps), exclusions)


# --- near-duplicate review --------------------------------------------------------------------------

NEAR_DUPS_FILE = "near-dups.jsonl"


@dataclass
class NearDups:
    path: Path
    distractors: int
    with_vectors: int
    above_threshold: int
    written: int
    max_cosine: float | None


def near_dups(set_dir: Path, vectors: dict, threshold: float = 0.90, top: int = 200) -> NearDups:
    """For every distractor, the max cosine (768-d EG2 image vectors) to any target. Writes
    `<set_dir>/near-dups.jsonl`, cosine descending (ties by asset_id), for every distractor at or above
    `threshold` plus the `top` highest regardless. For review only: the corpus is not changed.

    Targets are items with `role: "target"`; in a set without roles, the queries' relevant items."""
    set_dir = Path(set_dir)
    items = search.read_jsonl(set_dir / "items.jsonl")
    first: dict[str, dict] = {}
    for it in items:
        first.setdefault(it["asset_id"], it)
    if any("role" in it for it in items):
        targets = sorted(a for a, it in first.items() if it.get("role") == "target")
    else:
        rel = set().union(*(q["relevant"] for q in search.read_jsonl(set_dir / search.QUERIES_FILE)))
        targets = sorted(a for a in first if a in rel)
    tset = set(targets)
    distractors = sorted(a for a in first if a not in tset)
    excluded = set()
    if (set_dir / search.EXCLUDE_FILE).exists():
        excluded = {r.get("asset_id") for r in search.read_jsonl(set_dir / search.EXCLUDE_FILE)}
    tv = [a for a in targets if a in vectors]
    dv = [a for a in distractors if a in vectors]
    rows = []
    if tv and dv:
        T = np.stack([np.asarray(vectors[a], dtype=np.float32) for a in tv])
        D = np.stack([np.asarray(vectors[a], dtype=np.float32) for a in dv])
        T /= np.linalg.norm(T, axis=1, keepdims=True)
        D /= np.linalg.norm(D, axis=1, keepdims=True)
        sims = D @ T.T
        best = sims.argmax(axis=1)  # first max: the smallest target asset_id on a tie
        for i, a in enumerate(dv):
            t = tv[int(best[i])]
            rows.append({"asset_id": a, "relpath": first[a].get("relpath"), "target_asset_id": t,
                         "target_relpath": first[t].get("relpath"), "cosine": round(float(sims[i, best[i]]), 6),
                         "excluded": a in excluded})
    rows.sort(key=lambda r: (-r["cosine"], r["asset_id"]))
    above = sum(r["cosine"] >= threshold for r in rows)
    keep = [r for i, r in enumerate(rows) if r["cosine"] >= threshold or i < top]
    path = set_dir / NEAR_DUPS_FILE
    _write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep))
    return NearDups(path, len(distractors), len(dv), above, len(keep), rows[0]["cosine"] if rows else None)
