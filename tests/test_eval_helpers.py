"""Synthetic fixtures for the eval tests: a fake shoebox_root, pilot set, derived images and label store.

No owner photos: images are flat-colour JPEGs generated here.
"""

import json
from pathlib import Path

from PIL import Image

from shoebox.ids import bytes_id

COLOURS = [(200, 40, 40), (128, 128, 128), (40, 160, 60), (20, 20, 200)]


def label(medium="digital", digitiser="camera", colour_class="colour", tags=()):
    return {
        "caption": "A test card.",
        "tags": list(tags),
        "medium": medium,
        "digitiser": digitiser,
        "colour_class": colour_class,
        "confidence": {"medium": 0.9, "digitiser": 0.9, "colour_class": 0.9},
    }


def record(
    asset_id, model_id, params_hash, status="ok", output=None, latency_ms=1000, usd=0.0
):
    return {
        "asset_id": asset_id,
        "model_id": model_id,
        "params_hash": params_hash,
        "status": status,
        "output": output if status == "ok" else None,
        "raw": "not json" if status == "invalid" else "",
        "error": "connection refused" if status == "error" else None,
        "usage": {"input_tokens": 300, "output_tokens": 60, "thinking_tokens": None},
        "latency_ms": latency_ms,
        "usd": usd,
        "machine": "test, synthetic",
        "runtime": "test 0",
        "ts": "2026-10-10T00:00:00Z",
    }


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def make_world(tmp: Path, n_ok: int = 3) -> dict:
    """Builds config, items (n_ok with ok derived JPEGs + 1 failed derive), and stores for candA/candB."""
    root = tmp / "shoebox_root"
    src = tmp / "source-ro"
    src.mkdir(parents=True)
    cfg_path = tmp / "shoebox.toml"
    cfg_path.write_text(
        f'shoebox_root = "{root}"\n\n[sources.fake]\nroot = "{src}"\nperson = "owner"\n'
        "synced = false\nhosted_consent = false\n"
    )
    ids = [bytes_id(f"synthetic-{i}".encode()) for i in range(n_ok + 1)]
    items, index = [], []
    for i, aid in enumerate(ids):
        items.append(
            {
                "asset_id": aid,
                "source": "fake",
                "relpath": f"img{i}.jpg",
                "ext": "jpg",
                "bytes": 1000,
                "mtime": 1500000000.0,
                "bucket": "bucket-a" if i % 2 == 0 else "bucket-b",
                "group_id": None,
                "group_rule": None,
            }
        )
        rel = f"derived/infer-v1/{aid[:2]}/{aid}.jpg"
        if i < n_ok:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (96, 64), COLOURS[i % len(COLOURS)]).save(p, quality=90)
            index.append(
                {
                    "asset_id": aid,
                    "profile": "infer-v1",
                    "status": "ok",
                    "path": rel,
                    "error": None,
                }
            )
        else:
            index.append(
                {
                    "asset_id": aid,
                    "profile": "infer-v1",
                    "status": "error",
                    "path": None,
                    "error": "boom",
                }
            )
    write_jsonl(root / "derived" / "infer-v1" / "index.jsonl", index)
    sets = tmp / "sets"
    write_jsonl(sets / "pilot-v0" / "items.jsonl", items)

    ok_ids = ids[:n_ok]
    store_a = root / "results" / "labels" / "rules-v1" / "pa"
    store_b = root / "results" / "labels" / "ollama__fake_1" / "pb"
    write_jsonl(
        store_a / "part-0000.jsonl",
        [record(a, "rules-v1", "pa", output=label(), latency_ms=10) for a in ok_ids],
    )
    b_rows = [
        record(ok_ids[0], "ollama/fake:1", "pb", status="error")
    ]  # retried below: ok wins
    b_rows.append(
        record(
            ok_ids[0],
            "ollama/fake:1",
            "pb",
            output=label(medium="print"),
            latency_ms=2000,
            usd=0.001,
        )
    )
    b_rows.append(
        record(
            ok_ids[1],
            "ollama/fake:1",
            "pb",
            output=label(colour_class="mono"),
            latency_ms=4000,
        )
    )
    b_rows += [
        record(a, "ollama/fake:1", "pb", status="invalid", latency_ms=3000)
        for a in ok_ids[2:]
    ]
    write_jsonl(store_b / "part-0000.jsonl", b_rows)
    resolutions = tmp / "resolutions.json"
    resolutions.write_text(
        json.dumps(
            {
                "candA": {
                    "model_id": "rules-v1",
                    "params_hash": "pa",
                    "store_dir": str(store_a),
                },
                "candB": {
                    "model_id": "ollama/fake:1",
                    "params_hash": "pb",
                    "store_dir": str(store_b),
                },
                # same model, a params_hash with no records: every replay is a missing key
                "candC": {
                    "model_id": "rules-v1",
                    "params_hash": "pc",
                    "store_dir": "results/labels/rules-v1/pc",
                },
            }
        )
    )
    return {
        "root": root,
        "cfg_path": cfg_path,
        "sets": sets,
        "ids": ids,
        "ok_ids": ok_ids,
        "resolutions": resolutions,
        "store_a": store_a,
        "store_b": store_b,
    }
