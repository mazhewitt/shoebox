"""Frames in, exact and `same_pixels` groups out (design D1/D6, spec asset-dedup, issue dedup/01).

- `exact`: one asset (one `asset_id`) found at two or more `(source, relpath)` locations.
- `same_pixels`: distinct assets whose final pixel-hash records carry the same `pixel_hash`.

Group id = `g-` + the first 12 hex of the smallest member `asset_id`. Groups span sources.
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from shoebox import store
from shoebox.config import Config

GROUPS_KIND = "dedup-groups"
GROUPS_VERSION = "dedup-groups-v1"

MEDIA_KINDS = ("image", "video")


def load_frames(frames: Iterable) -> list[dict]:
    """Rows of every frame given: pyarrow tables, parquet paths, or lists of row dicts, in order."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows: list[dict] = []
    for f in frames:
        if isinstance(f, pa.Table):
            rows.extend(f.to_pylist())
        elif isinstance(f, (str, Path)):
            rows.extend(pq.read_table(f).to_pylist())
        else:
            rows.extend(dict(r) for r in f)
    return rows


def media_rows(rows: Iterable[dict]) -> list[dict]:
    """Image and video rows that have an asset id, sorted by (source, relpath)."""
    return sorted((r for r in rows if r.get("asset_id") and r.get("kind") in MEDIA_KINDS),
                  key=lambda r: (r["source"], r["relpath"]))


def locations(rows: Iterable[dict]) -> dict[str, list[list[str]]]:
    """`{asset_id: [[source, relpath], ...]}`, each list sorted."""
    out: dict[str, list[list[str]]] = defaultdict(list)
    for r in media_rows(rows):
        out[r["asset_id"]].append([r["source"], r["relpath"]])
    return dict(out)


def group_id(asset_ids: Iterable[str]) -> str:
    return "g-" + min(asset_ids)[:12]


def pixel_links(assets: Iterable[str], pixels: dict[str, dict]) -> list[list[str]]:
    """Sorted lists (two or more) of assets sharing a pixel hash; only `ok` records count."""
    by_hash: dict[str, list[str]] = defaultdict(list)
    for a in sorted(set(assets)):
        rec = pixels.get(a)
        if rec and rec.get("status") == "ok" and rec.get("pixel_hash"):
            by_hash[rec["pixel_hash"]].append(a)
    return sorted((m for m in by_hash.values() if len(m) > 1), key=lambda m: m[0])


def build_groups(rows: Iterable[dict], pixels: dict[str, dict]) -> list[dict]:
    """Exact and same_pixels groups, sorted by (type, group_id). Members list their locations."""
    locs = locations(rows)
    groups = [{"group_id": group_id([a]), "type": "exact", "members": [{"asset_id": a, "locations": ls}]}
              for a, ls in locs.items() if len(ls) > 1]
    groups += [{"group_id": group_id(m), "type": "same_pixels",
                "members": [{"asset_id": a, "locations": locs[a]} for a in m]}
               for m in pixel_links(locs, pixels)]
    return sorted(groups, key=lambda g: (g["type"], g["group_id"]))


def inputs_digest(rows: Iterable[dict], pixels: dict[str, dict]) -> str:
    """Hash of the frame locations and the pixel hashes a grouping was built from."""
    h = hashlib.sha256()
    for r in media_rows(rows):
        h.update(f"L\t{r['source']}\t{r['relpath']}\t{r['asset_id']}\n".encode())
    for a in sorted(pixels):
        rec = pixels[a]
        if rec.get("status") == "ok":
            h.update(f"P\t{a}\t{rec.get('pixel_hash')}\n".encode())
    return h.hexdigest()[:16]


def groups_params(rows: Iterable[dict], pixels: dict[str, dict], pixel_key: tuple[str, str]) -> dict:
    """What a group set depends on. New inputs give a new store dir, so stale groups never mix in."""
    return {"version": GROUPS_VERSION, "pixelhash": list(pixel_key), "inputs": inputs_digest(rows, pixels)}


def write_records(cfg: Config, kind: str, model_id: str, params: dict, records: list[tuple[str, dict]]) -> Path:
    """Append `(key, payload)` records to `results/<kind>/<model_id>/<params_hash>/`, skipping final keys."""
    ph = store.params_hash(params)
    d = store.write_params(cfg, model_id, ph, params, kind=kind)
    have = store.index_records(store.iter_dir(d))
    new = [{"asset_id": key, "model_id": model_id, "params_hash": ph, "status": "ok", **payload}
           for key, payload in records if not store.is_final(have.get(key))]
    for i in range(0, len(new), 1000):  # one store write per thousand (an append costs ~240 ms over SMB)
        store.append_many(cfg, new[i:i + 1000], kind=kind)
    return d


def write_groups(cfg: Config, groups: list[dict], params: dict) -> Path:
    """Store groups under kind `dedup-groups`, keyed `<type>/<group_id>` (an exact and a same_pixels
    group can share a group id). Returns the store dir."""
    return write_records(cfg, GROUPS_KIND, GROUPS_VERSION, params,
                         [(f"{g['type']}/{g['group_id']}", g) for g in groups])
