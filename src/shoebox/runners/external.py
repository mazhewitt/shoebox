"""Externally produced labels (runtime `external`), e.g. Claude Opus as Claude Code subagents with no API.

shoebox never runs these: `packets` writes what the producer reads, and `ingest` turns what it wrote into normal
label records in the keyed store (same shape as `runners.run_and_store`), so every label reader works unchanged.

1. `packets(cfg, items, out)`: `out/images/<key>.jpg` (1024 px thumbnails of the derived image),
   `out/batch-NNN.json` (`{batch, prompt_file, answer_file, answer_format, photos: [{key, image}]}`, no
   asset_ids) and `out/map.json` (key -> asset_id). Items the provider may not see, or without a derived
   image, are skipped and reported.
2. `ingest(cfg, res, dir)`: reads `dir/labels-*.jsonl` lines `{asset_id, output}` or `{key, output}` (keys via
   `dir/map.json`), lowercases tags (`runners.normalise_label`), validates against the candidate's full schema
   (a null caption is invalid), and appends `ok` or `invalid` (raw kept) records for assets not already final.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from shoebox import derive, privacy, store
from shoebox.config import Config
from shoebox.runners import Resolved, _now, _params_doc, machine, normalise_label

THUMB_PX = 1024
NO_USAGE = {"input_tokens": None, "output_tokens": None, "thinking_tokens": None}


def runtime_version(cand: dict) -> str:
    return cand.get("producer") or "external"


def run_one(cand, item, image_path, cfg):
    raise RuntimeError(f"outputs for runtime external (candidate {cand.get('name')}) are ingested with "
                       "`shoebox label ingest`, not run")


class IngestError(ValueError):
    pass


@dataclass
class IngestResult:
    ok: int = 0
    invalid: int = 0
    already: int = 0


def _validate(output, schema: dict) -> tuple[dict | None, str | None]:
    out = normalise_label(output)
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(out), key=lambda e: list(e.path))
    if errors:
        return None, "schema: " + "; ".join(
            f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5])
    if out.get("caption") is None:
        return None, "schema: null caption is valid for the rule baseline only"
    return out, None


def read_labels(d: Path) -> list[tuple[str, object]]:
    """`[(asset_id, output)]` from `d/labels-*.jsonl`; raises IngestError listing every problem."""
    d = Path(d)
    keymap = json.loads((d / "map.json").read_text()) if (d / "map.json").exists() else {}
    rows, errors, seen = [], [], {}
    for path in sorted(d.glob("labels-*.jsonl")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path.name}:{i}"
            try:
                row = json.loads(line)
            except ValueError as e:
                errors.append(f"{where}: not JSON: {e}")
                continue
            if not isinstance(row, dict) or "output" not in row:
                errors.append(f"{where}: expected {{asset_id|key, output}}")
                continue
            a = row.get("asset_id") or keymap.get(row.get("key"))
            if not a:
                errors.append(f"{where}: unknown key {row.get('key')!r}")
                continue
            if a in seen and seen[a] != row["output"]:
                errors.append(f"{where}: {a} labelled twice, differently")
                continue
            if a not in seen:
                seen[a] = row["output"]
                rows.append((a, row["output"]))
    if errors:
        raise IngestError("; ".join(errors))
    return rows


def ingest(cfg: Config, res: Resolved, d: Path, items: list[dict] | None = None) -> IngestResult:
    """Validate the files first (nothing is stored if a line is unreadable or an asset is not in `items`),
    then store one record per asset not already final. Schema failures are stored as `invalid`, not refused."""
    if res.cand.get("runtime") != "external":
        raise IngestError(f"candidate {res.name} has runtime {res.cand.get('runtime')}, not external")
    rows = read_labels(d)
    if items is not None:
        known = {it["asset_id"] for it in items}
        bad = sorted(a for a, _ in rows if a not in known)
        if bad:
            raise IngestError(f"{len(bad)} asset_ids not in the items list: {bad[:5]}")
    schema = json.loads(res.cand["schema_text"])
    done = store.index(cfg, res.model_id, res.params_hash, res.kind)
    out = IngestResult()
    if rows:
        store.write_params(cfg, res.model_id, res.params_hash, _params_doc(res), res.kind)
    for a, raw in rows:
        if store.is_final(done.get(a)):
            out.already += 1
            continue
        output, err = _validate(raw, schema)
        status = "invalid" if err else "ok"
        rec = {"asset_id": a, "model_id": res.model_id, "params_hash": res.params_hash, "status": status,
               "output": output, "raw": json.dumps(raw, ensure_ascii=False) if err else None, "error": err,
               "usage": dict(NO_USAGE), "latency_ms": None, "usd": 0.0, "machine": machine(),
               "runtime": res.runtime, "ts": _now()}
        store.append(cfg, rec, res.kind)
        done[a] = rec
        setattr(out, status, getattr(out, status) + 1)
    return out


@dataclass
class Packets:
    keys: dict[str, str] = field(default_factory=dict)  # key -> asset_id
    skipped: list[dict] = field(default_factory=list)  # {asset_id, reason}
    batches: list[Path] = field(default_factory=list)


def packets(cfg: Config, items: list[dict], out: Path, per_batch: int = 25, provider: str = "claude",
            others: list[dict] = (), profile: str = "infer-v1", prompt_file: Path | None = None) -> Packets:
    import pyvips

    from shoebox.evals.audit import check_out_dir

    check_out_dir(out)
    seen, uniq = set(), []
    for it in items:
        if it["asset_id"] not in seen:
            seen.add(it["asset_id"])
            uniq.append(it)
    elig = privacy.filter_items(uniq, cfg, others=list(others), provider=provider)
    res = Packets(skipped=[{"asset_id": s.asset_id, "reason": s.reason} for s in elig.skipped])
    (Path(out) / "images").mkdir(parents=True, exist_ok=True)
    listed = []
    for it in elig.allowed:
        a = it["asset_id"]
        src = derive.derived_path(cfg, profile, a)
        if not src.is_file():
            res.skipped.append({"asset_id": a, "reason": "no_derived"})
            continue
        key = f"k-{len(res.keys) + 1:04d}"
        img = Path(out) / "images" / f"{key}.jpg"
        pyvips.Image.thumbnail(str(src), THUMB_PX).write_to_file(str(img), Q=85, strip=True)
        res.keys[key] = a
        listed.append({"key": key, "image": str(img.resolve())})
    for b in range(0, len(listed), per_batch):
        n = b // per_batch + 1
        path = Path(out) / f"batch-{n:03d}.json"
        path.write_text(json.dumps({"batch": n, "prompt_file": str(prompt_file) if prompt_file else None,
                                    "answer_file": f"labels-{n:03d}.jsonl",
                                    "answer_format": {"key": "k-0001", "output": {"caption": "..."}},
                                    "photos": listed[b:b + per_batch]}, indent=1))
        res.batches.append(path)
    (Path(out) / "map.json").write_text(json.dumps(res.keys, indent=1))
    (Path(out) / "packets.json").write_text(json.dumps({"profile": profile, "thumb_px": THUMB_PX,
                                                        "provider": provider, "photos": len(res.keys),
                                                        "skipped": res.skipped}, indent=1))
    return res
