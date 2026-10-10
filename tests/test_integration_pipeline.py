"""Integration: the pilot pipeline end to end on synthetic originals, through the real CLI in subprocesses.

synthetic originals -> derive -> label run rules-v1 -> label one / resolve -> eval gen -> (Vally replay, if
evals/vally/node_modules exists) -> eval report -> pilot sheet. A temp SHOEBOX_CONFIG keeps every write in
tmp_path; no model endpoint is called (rules-v1 only).
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import pyvips
from PIL import Image

from shoebox import config, derive, ids, store
from shoebox.config import REPO_ROOT

VALLY_DIR = REPO_ROOT / "evals" / "vally"
VALLY = VALLY_DIR / "node_modules" / ".bin" / "vally"
SET = "pilot-v0"
RECORD_FIELDS = {"asset_id", "model_id", "params_hash", "status", "output", "raw", "error", "usage", "latency_ms",
                 "usd", "machine", "runtime", "ts"}
INDEX_FIELDS = {"asset_id", "profile", "status", "path", "derived_blake3", "width", "height", "src_width",
                "src_height", "src_bits", "src_kind", "rendered_from", "error", "tools"}


def _phone_jpeg(path: Path) -> None:
    yy, xx = np.mgrid[0:600, 0:900]
    arr = np.stack([(xx * 255 // 900), (yy * 255 // 600), np.full_like(xx, 140)], -1).astype(np.uint8)
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "Apple", "iPhone 12"
    Image.fromarray(arr).save(path, quality=90, exif=exif)


def _grey_png(path: Path) -> None:
    Image.fromarray(np.tile(np.linspace(0, 255, 500, dtype=np.uint8), (300, 1)), "L").save(path)


def _tiff16(path: Path) -> None:
    arr = (np.random.default_rng(0).random((400, 640, 3)) * 65535).astype(np.uint16)
    img = pyvips.Image.new_from_memory(arr.tobytes(), 640, 400, 3, "ushort").copy(interpretation="rgb16")
    img.tiffsave(str(path))


@pytest.fixture
def world(tmp_path):
    src = tmp_path / "src"
    (src / "Phone Photos").mkdir(parents=True)
    (src / "Scans").mkdir()
    _phone_jpeg(src / "Phone Photos" / "IMG_0001.jpg")
    _grey_png(src / "Scans" / "grey.png")
    _tiff16(src / "Scans" / "scan16.tif")
    (src / "Scans" / "broken.tif").write_bytes(b"Service Unavailable" + b"\0" * 64)  # a failed sync download
    cfg_path = tmp_path / "shoebox.toml"
    cfg_path.write_text(
        f'shoebox_root = "{tmp_path / "sb"}"\n'
        f'[sources.syn]\nroot = "{src}"\nperson = "owner"\nsynced = false\nhosted_consent = false\n'
    )
    items = []
    for bucket, rel in [("phone", "Phone Photos/IMG_0001.jpg"), ("scan", "Scans/grey.png"),
                        ("scan", "Scans/scan16.tif"), ("scan", "Scans/broken.tif")]:
        p = src / rel
        st = p.stat()
        items.append({"asset_id": ids.asset_id(p), "source": "syn", "relpath": rel, "ext": p.suffix[1:].lower(),
                      "bytes": st.st_size, "mtime": st.st_mtime, "bucket": bucket, "group_id": None,
                      "group_rule": None})
    sets = tmp_path / "sets"
    (sets / SET).mkdir(parents=True)
    items_path = sets / SET / "items.jsonl"
    items_path.write_text("".join(json.dumps(i) + "\n" for i in items))
    labels_path = sets / SET / "labels.jsonl"
    labels_path.write_text(json.dumps({"asset_id": items[0]["asset_id"], "medium": "digital", "digitiser": "phone",
                                       "colour_class": "colour", "notes": "", "labelled_by": "owner",
                                       "method": "blind", "ts": "2026-10-10T00:00:00Z"}) + "\n")
    env = {**os.environ, "SHOEBOX_CONFIG": str(cfg_path), "VALLY_TELEMETRY_OPTOUT": "1"}
    return {"cfg": config.load(cfg_path), "env": env, "items": items, "items_path": items_path, "sets": sets,
            "labels_path": labels_path, "tmp": tmp_path}


def shoebox(w, *args, check=True) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, "-m", "shoebox.cli", *map(str, args)], env=w["env"], cwd=REPO_ROOT,
                          capture_output=True, text=True, check=False)
    if check:
        assert proc.returncode == 0, f"{args}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    return proc


def test_pipeline_end_to_end(world):
    w, cfg, items = world, world["cfg"], world["items"]
    ok_ids = [it["asset_id"] for it in items[:3]]
    broken = items[3]["asset_id"]

    # derive: one index record per attempt in the contract's fields; the broken file is an error record
    shoebox(w, "derive", "--profile", "infer-v1", "--items", w["items_path"])
    index = derive.load_index(cfg, "infer-v1")
    assert {a: r["status"] for a, r in index.items()} == {**dict.fromkeys(ok_ids, "ok"), broken: "error"}
    for aid in ok_ids:
        rec = index[aid]
        assert INDEX_FIELDS <= set(rec)
        assert cfg.shoebox_root / rec["path"] == derive.derived_path(cfg, "infer-v1", aid)
        assert ids.bytes_id((cfg.shoebox_root / rec["path"]).read_bytes()) == rec["derived_blake3"]
        assert max(rec["width"], rec["height"]) <= 2048

    # label run: rules-v1 fills the keyed store; the item without a derived image is skipped, not an error
    run = shoebox(w, "label", "run", "--candidate", "rules-v1", "--items", w["items_path"])
    assert "ok 3" in run.stdout and "skipped 1" in run.stdout, run.stdout
    resolved = json.loads(shoebox(w, "label", "resolve", "--candidate", "rules-v1").stdout)
    assert set(resolved) == {"candidate", "model_id", "params_hash", "store_dir"}
    assert resolved["model_id"] == "rules-v1"
    sdir = Path(resolved["store_dir"])
    assert sdir == store.store_dir(cfg, "rules-v1", resolved["params_hash"])
    assert sorted(p.name for p in sdir.iterdir()) == ["params.json", "part-0000.jsonl"]
    params = json.loads((sdir / "params.json").read_text())
    assert params["candidate"] == "rules-v1" and params["params_hash"] == resolved["params_hash"]
    recs = store.index(cfg, "rules-v1", resolved["params_hash"])
    assert set(recs) == set(ok_ids)
    for r in recs.values():
        assert set(r) == RECORD_FIELDS and r["status"] == "ok", r
        assert r["output"]["caption"] is None and r["output"]["tags"] == []
    assert recs[ok_ids[0]]["output"]["digitiser"] == "phone"  # from the EXIF Make/Model

    # a re-run is all cache hits; label one prints exactly the stored record and nothing else on stdout
    again = shoebox(w, "label", "run", "--candidate", "rules-v1", "--items", w["items_path"])
    assert "ok 0" in again.stdout and "cached 3" in again.stdout, again.stdout
    one = shoebox(w, "label", "one", "--candidate", "rules-v1", "--asset-id", ok_ids[1],
                  "--items", w["items_path"])
    assert one.stdout.count("\n") == 1 and json.loads(one.stdout) == recs[ok_ids[1]]
    assert shoebox(w, "label", "one", "--candidate", "rules-v1", "--asset-id", broken, "--items", w["items_path"],
                   check=False).returncode == 3

    # eval gen: one stimulus per ok derived item; the store dir comes from `label resolve`
    gen_dir = w["tmp"] / "generated"
    shoebox(w, "eval", "gen", "--set", SET, "--candidates", "rules-v1", "--sets-dir", w["sets"],
            "--out-dir", gen_dir)
    cands = json.loads((gen_dir / "candidates.json").read_text())
    assert cands["candidates"]["rules-v1"]["store_dir"] == str(sdir)
    assert cands["items"] == [it["asset_id"] for it in items] and set(cands["skipped_items"]) == {broken}
    spec = (gen_dir / "eval.yaml").read_text()
    for aid in ok_ids:
        assert f'"{derive.derived_path(cfg, "infer-v1", aid)}"' in spec
    assert broken not in spec.split("stimuli:")[1]

    # Vally replay over the stored records, then the report
    if shutil.which("node") and VALLY.exists():
        out = w["tmp"] / "vally-out"
        vally = subprocess.run(
            [str(VALLY), "eval", "-e", str(gen_dir / "eval.yaml"),
             "--executor-plugin", str(VALLY_DIR / "plugins" / "shoebox-vlm.js"),
             "--grader-plugin", str(VALLY_DIR / "plugins" / "shoebox-graders.js"),
             "--model", "rules-v1", "--output-dir", str(out), "--workers", "2"],
            env=w["env"], cwd=VALLY_DIR, capture_output=True, text=True, check=False)
        found = list(out.glob("*/results.jsonl"))
        assert found, f"{vally.stdout[-2000:]}\n{vally.stderr[-2000:]}"
        runs = w["tmp"] / "runs"
        shoebox(w, "eval", "report", "--results", found[0], "--out-dir", runs, "--date", "2026-10-10")
        rep = json.loads((runs / f"2026-10-10_{SET}.json").read_text())
        c = rep["candidates"]["rules-v1"]
        assert c["n"] == 3 and c["status_counts"]["ok"] == 3 and c["params_hash"] == resolved["params_hash"]
        assert c["fields"]["digitiser"]["labelled_n"] == 1 and c["fields"]["digitiser"]["accuracy"] == 1.0
        assert rep["set_items"]["n"] == 4 and set(rep["set_items"]["skipped"]) == {broken}
        assert not rep["gate_eligible"]  # an item was skipped by gen, so the run can't count for a gate

    # pilot sheet: rules-v1's outputs, owner labels, and image paths that resolve to the derived files
    sheet = cfg.results_dir / SET / "sheet.html"
    shoebox(w, "pilot", "sheet", "--items", w["items_path"], "--labels", w["labels_path"], "--out", sheet)
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', sheet.read_text(), re.DOTALL)
    data = json.loads(m.group(1))
    assert [c["name"] for c in data["candidates"]] == ["rules-v1"]
    assert data["labels"][ok_ids[0]]["method"] == "blind"
    rows = {r["asset_id"]: r for r in data["items"]}
    for aid in ok_ids:
        assert (sheet.parent / rows[aid]["img"]).resolve() == derive.derived_path(cfg, "infer-v1", aid).resolve()
        if not rows[aid]["blind_slice"]:
            (out,) = rows[aid]["outputs"].values()
            assert out["status"] == "ok" and out["params_hash"] == resolved["params_hash"]
    assert not any(rows[a]["outputs"] for a in rows if rows[a]["blind_slice"])
