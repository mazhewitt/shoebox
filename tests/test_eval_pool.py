"""`shoebox eval search-pool`: a known-item set plus random distractors drawn from a synthetic frame."""

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_eval_helpers import write_jsonl

from shoebox import config
from shoebox.cli import main
from shoebox.evals import pool
from shoebox.pilot.select import MAX_BYTES

FRAME_COLS = ["source", "person", "synced", "relpath", "folder", "name", "ext", "kind", "bytes", "mtime",
              "asset_id", "status", "error", "width", "height", "bits", "src_kind", "date", "date_kind", "make",
              "model", "software", "exif", "chroma_mean", "chroma_p95", "chromatic", "hue_r", "colour_guess",
              "group_id", "group_rule", "dup_n"]


def h(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def row(tag, relpath=None, **kw):
    relpath = relpath or f"Pictures/Folder/{tag}.jpg"
    r = dict.fromkeys(FRAME_COLS)
    r.update(source="own", person="owner", synced=False, relpath=relpath, folder=str(Path(relpath).parent),
             name=Path(relpath).name, ext=Path(relpath).suffix.lstrip(".").lower(), kind="image", bytes=1000,
             mtime=1.5e9, asset_id=h(tag), status="ok", src_kind="jpeg", date="2014-05-01T10:00:00",
             colour_guess="colour", dup_n=1)
    r.update(kw)
    return r


def write_frame(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "src"
    other = tmp_path / "other"
    src.mkdir()
    other.mkdir()
    toml = tmp_path / "shoebox.toml"
    toml.write_text(f'shoebox_root = "{tmp_path / "root"}"\n'
                    f'[sources.own]\nroot = "{src}"\nperson = "owner"\nsynced = false\nhosted_consent = true\n'
                    f'[sources.kids]\nroot = "{other}"\nperson = "kyra"\nsynced = false\nhosted_consent = false\n')
    cfg = config.load(toml)
    sets = tmp_path / "sets"
    base = [
        {"asset_id": h("t1"), "source": "own", "relpath": "Pictures/T/t1.dng", "ext": "dng", "bytes": 5,
         "mtime": 1.0, "bucket": "b", "group_id": "g-t1", "group_rule": "raw_jpeg_basename"},
        {"asset_id": h("t2"), "source": "own", "relpath": "Pictures/T/t2.jpg", "ext": "jpg", "bytes": 5,
         "mtime": 1.0, "bucket": "b", "group_id": None, "group_rule": None},
    ]
    write_jsonl(sets / "base" / "items.jsonl", base)
    (sets / "base" / pool.search.QUERIES_FILE).write_text(
        json.dumps({"query_id": "q-1", "text": "x", "relevant": [h("t2")]}) + "\n")
    never = tmp_path / "never-send.txt"
    never.write_text(f"# comment\n{h('ns-id')}\nPictures/Secret/**\n")
    return cfg, sets, never, base


def build(env, frame, n=100, seed=7, out="pool"):
    cfg, sets, never, _ = env
    return pool.build_pool(cfg, sets / "base", frame, n, seed, sets / out, never_send_path=never)


def items_of(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_each_exclusion_rule(env, tmp_path):
    rows = [
        row("ok1"), row("ok2", ext="tif", relpath="Pictures/Scans/ok2.tif", src_kind="tiff"),
        row("video", kind="video", src_kind="video"),
        row("sidecar", kind="sidecar", asset_id=None, src_kind=None),
        row("err", status="error"),
        row("statonly", status="stat_only"),
        row("noid", asset_id=None),
        row("gif", relpath="Pictures/Folder/gif.gif", src_kind="gif"),
        row("kyra", source="kids", person="kyra"),
        row("unknown", source="nowhere"),
        row("t2"),  # a copy of a target at another path
        row("ns-id"),  # never-send by asset_id
        row("ns-glob", relpath="Pictures/Secret/x.jpg"),  # never-send by glob
        row("sib", group_id="g-pair", group_rule="raw_jpeg_basename"),  # sibling of target t1 in the frame
        row("t1", relpath="Pictures/T/t1.dng", src_kind="raw", group_id="g-pair"),
        row("huge", bytes=MAX_BYTES + 1),
    ]
    frame = write_frame(tmp_path / "frame.parquet", rows)
    res = build(env, frame)
    dist = [it for it in items_of(res.out_dir / "items.jsonl") if it["role"] == "distractor"]
    assert sorted(it["asset_id"] for it in dist) == sorted([h("ok1"), h("ok2")])
    ex = json.loads((res.out_dir / "pool.json").read_text())["exclusions"]
    assert ex == {"not_image": 2, "status_not_ok": 2, "no_asset_id": 1, "unrenderable": 1,
                  "not_owner_source": 1, "unknown_source": 1, "base_set": 2, "never_send": 2,
                  "target_group": 1, "too_large": 1}


def test_copy_rules_dedupe_and_never_send_on_any_copy(env, tmp_path):
    rows = [
        row("dup", relpath="Pictures/B/dup.jpg"), row("dup", relpath="Pictures/A/dup.jpg"),
        row("dup", source="own", relpath="Pictures/A/a-dup.jpg"),
        row("leak", relpath="Pictures/Ok/leak.jpg"), row("leak", relpath="Pictures/Secret/leak.jpg"),
        row("nsg", relpath="Pictures/Secret/nsg.jpg", group_id="g-ns"),  # never-send: its siblings too
        row("nsg-sib", relpath="Pictures/Ok/nsg-sib.jpg", group_id="g-ns"),
    ]
    res = build(env, write_frame(tmp_path / "f.parquet", rows))
    dist = [it for it in items_of(res.out_dir / "items.jsonl") if it["role"] == "distractor"]
    assert [(d["asset_id"], d["relpath"]) for d in dist] == [(h("dup"), "Pictures/A/a-dup.jpg")]
    ex = json.loads((res.out_dir / "pool.json").read_text())["exclusions"]
    assert ex == {"never_send": 2, "copy_excluded": 1, "never_send_group": 1}


def test_output_files_and_determinism(env, tmp_path):
    _, sets, _, base = env
    rows = [row(f"r{i}", relpath=f"Pictures/F{i % 3}/r{i}.jpg", date=f"{1990 + i}-01-01", colour_guess="mono")
            for i in range(40)]
    frame = write_frame(tmp_path / "frame.parquet", rows)
    a = build(env, frame, n=10, seed=20261010, out="p1")
    b = build(env, frame, n=10, seed=20261010, out="p2")
    for name in ("items.jsonl", "search-known-item.jsonl", "pool.json"):
        assert (a.out_dir / name).read_bytes() == (b.out_dir / name).read_bytes(), name
    assert (a.out_dir / "README.md").read_text().replace("# p1", "# p2") == (b.out_dir / "README.md").read_text()
    c = build(env, frame, n=10, seed=1, out="p3")
    assert (c.out_dir / "items.jsonl").read_bytes() != (a.out_dir / "items.jsonl").read_bytes()
    items = items_of(a.out_dir / "items.jsonl")
    assert [{k: v for k, v in it.items() if k != "role"} for it in items[:2]] == base
    assert [it["role"] for it in items] == ["target"] * 2 + ["distractor"] * 10
    dist = items[2:]
    assert [d["asset_id"] for d in dist] == sorted(d["asset_id"] for d in dist)
    assert set(dist[0]) == {"asset_id", "source", "relpath", "ext", "bytes", "mtime", "bucket", "group_id",
                            "group_rule", "role"} and dist[0]["bucket"] == "distractor"
    assert ((a.out_dir / "search-known-item.jsonl").read_bytes()
            == (sets / "base" / "search-known-item.jsonl").read_bytes())
    meta = json.loads((a.out_dir / "pool.json").read_text())
    assert meta["base_set"] == "base" and meta["seed"] == 20261010
    assert (meta["n_requested"], meta["n_drawn"], meta["eligible_assets"]) == (10, 10, 40)
    assert meta["frame"]["sha256"] == hashlib.sha256(frame.read_bytes()).hexdigest()
    readme = (a.out_dir / "README.md").read_text()
    assert "Pictures/F0" in readme and "| jpg |" in readme and "1990s" in readme and "mono" in readme
    assert not any(d["relpath"] in readme or Path(d["relpath"]).name in readme for d in dist)
    # fewer eligible than requested: draw them all
    d = build(env, frame, n=500, seed=1, out="p4")
    assert d.n_drawn == 40


def test_refuses_existing_out_set_and_missing_queries(env, tmp_path):
    _, sets, _, _ = env
    frame = write_frame(tmp_path / "frame.parquet", [row("a")])
    build(env, frame, out="p")
    with pytest.raises(FileExistsError):
        build(env, frame, out="p")
    (sets / "base" / pool.search.QUERIES_FILE).unlink()
    with pytest.raises(FileNotFoundError):
        build(env, frame, out="q")


def test_cli_search_pool(env, tmp_path, monkeypatch, capsys):
    _, sets, never, _ = env
    frame = write_frame(tmp_path / "frame.parquet", [row(f"r{i}") for i in range(5)])
    monkeypatch.setenv("SHOEBOX_CONFIG", str(tmp_path / "shoebox.toml"))
    rc = main(["eval", "search-pool", "--set", "base", "--frame", str(frame), "--n", "3", "--seed", "5",
               "--out-set", "pool5", "--sets-dir", str(sets), "--never-send", str(never)])
    assert rc == 0
    assert len(items_of(sets / "pool5" / "items.jsonl")) == 5
    assert "3 distractors" in capsys.readouterr().out


def _unit(*pairs):
    import numpy as np

    v = np.zeros(768, dtype=np.float32)
    for i, x in pairs:
        v[i] = x
    return v / np.linalg.norm(v)


def _neardup_set(sets: Path) -> Path:
    items = [{"asset_id": a, "source": "own", "relpath": f"T/{a}.jpg", "role": "target"} for a in ("t1", "t2")]
    items += [{"asset_id": a, "source": "own", "relpath": f"D/{a}.jpg", "role": "distractor"}
              for a in ("d0", "d1", "d2", "d3", "d5")]
    write_jsonl(sets / "p" / "items.jsonl", items)
    (sets / "p" / pool.search.QUERIES_FILE).write_text('{"query_id": "q", "text": "x", "relevant": ["t1"]}\n')
    return sets / "p"


def test_near_dups_ranks_distractors_by_max_cosine_to_any_target(tmp_path):
    set_dir = _neardup_set(tmp_path / "sets")
    vecs = {"t1": _unit((0, 1)), "t2": _unit((1, 1)),
            "d1": _unit((0, 1), (2, 0.1)),  # ~0.995 to t1
            "d2": _unit((1, 1), (2, 1)),  # ~0.707 to t2
            "d3": _unit((0, 1), (1, 1)),  # ~0.707 to t1 and t2: tie -> the smaller target id
            "d0": _unit((5, 1))}  # 0; d5 has no vector
    before = (set_dir / "items.jsonl").read_bytes()
    res = pool.near_dups(set_dir, vecs, threshold=0.9, top=3)
    rows = pool.search.read_jsonl(set_dir / "near-dups.jsonl")
    assert [r["asset_id"] for r in rows] == ["d1", "d2", "d3"]  # >= 0.9, plus the top 3 regardless; d2/d3 tie
    assert rows[0]["target_asset_id"] == "t1" and rows[0]["target_relpath"] == "T/t1.jpg"
    assert rows[0]["relpath"] == "D/d1.jpg" and rows[0]["cosine"] > 0.99
    assert rows[2]["target_asset_id"] == "t1"
    assert set(rows[0]) == {"asset_id", "relpath", "target_asset_id", "target_relpath", "cosine", "excluded"}
    assert (res.distractors, res.with_vectors, res.above_threshold, res.written) == (5, 4, 1, 3)
    assert (set_dir / "items.jsonl").read_bytes() == before  # never changes the corpus
    assert not (set_dir / pool.search.EXCLUDE_FILE).exists()


def test_cli_search_neardups(tmp_path, monkeypatch, capsys):
    cfg_dir = tmp_path
    (cfg_dir / "shoebox.toml").write_text(f'shoebox_root = "{tmp_path / "root"}"\n')
    monkeypatch.setenv("SHOEBOX_CONFIG", str(cfg_dir / "shoebox.toml"))
    sets = tmp_path / "sets"
    _neardup_set(sets)
    vecs = {"t1": _unit((0, 1)), "t2": _unit((1, 1)), "d1": _unit((0, 1))}
    monkeypatch.setattr(pool.search, "load_image_store", lambda cfg, cand: (None, vecs))
    assert main(["eval", "search-neardups", "--set", "p", "--sets-dir", str(sets), "--top", "0"]) == 0
    out = capsys.readouterr().out
    assert "1 of 5 distractors" in out and "near-dups.jsonl" in out
