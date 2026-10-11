"""Synthetic query tooling (design D9, tasks 4.1-4.2): sources, packets, corpus summary, ingest and split."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import pyvips
from derived_fixture import write_derived
from test_eval_helpers import write_jsonl

from shoebox import config, store
from shoebox.evals import queries


def h(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def item(tag, relpath=None, group_id=None, role="corpus", source="own"):
    return {"asset_id": h(tag), "source": source, "relpath": relpath or f"Pictures/Trip/{tag}.jpg", "ext": "jpg",
            "bytes": 5, "mtime": 1.0, "bucket": "b", "group_id": group_id, "group_rule": None, "role": role}


def frow(it, date="2005:06:01 10:00:00", date_kind="exif", colour="colour", make="NIKON", model="D70",
         software=None):
    return {"source": it["source"], "relpath": it["relpath"], "folder": str(Path(it["relpath"]).parent),
            "asset_id": it["asset_id"], "date": date, "date_kind": date_kind, "make": make, "model": model,
            "software": software, "colour_guess": colour, "group_id": it["group_id"]}


def write_frame(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


@pytest.fixture
def world(tmp_path):
    """60 items over 4 strata (one tiny: a single mono scan from the 1980s), with two capture groups."""
    items, frame = [], []
    for i in range(30):
        it = item(f"a{i}", group_id="g-pair" if i < 3 else None)
        items.append(it)
        frame.append(frow(it))
    for i in range(20):
        it = item(f"b{i}", group_id="g-b" if i < 4 else None)
        items.append(it)
        frame.append(frow(it, date="2017:01:01 00:00:00", colour="mono"))
    for i in range(9):
        it = item(f"c{i}")
        items.append(it)
        frame.append(frow(it, date=None, date_kind="mtime"))
    one = item("scan1", relpath="Pictures/Scans/scan1.tif")
    items.append(one)
    frame.append(frow(one, date="1985:01:01 00:00:00", colour="mono", make="Nikon", model="LS-50 ED"))
    sets = tmp_path / "sets"
    write_jsonl(sets / "s" / "items.jsonl", items)
    fpath = write_frame(tmp_path / "frame.parquet", frame)
    return {"sets": sets, "set_dir": sets / "s", "frame": fpath, "items": items, "tmp": tmp_path}


def read(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


# ---------- is_scan / era ----------

def test_is_scan():
    assert queries.is_scan({"make": "Nikon", "model": "LS-50 ED", "software": None})
    assert queries.is_scan({"make": None, "model": "Super COOLSCAN 5000", "software": None})
    assert queries.is_scan({"make": "EPSON", "model": "Perfection V600", "software": None})
    assert queries.is_scan({"make": None, "model": None, "software": "VueScan 9.7"})
    assert queries.is_scan({"make": None, "model": None, "software": "Nikon Scan 4.0.2 W"})
    assert queries.is_scan({"make": None, "model": None, "software": "SilverFast 8"})
    assert not queries.is_scan({"make": "NIKON CORPORATION", "model": "NIKON D70", "software": "Ver.1.02"})
    assert not queries.is_scan({"make": "Apple", "model": "iPhone 12", "software": "Adobe Photoshop CS2"})
    assert not queries.is_scan({})


def test_era_rules():
    assert queries.era({"date": "1985:01:01", "date_kind": "exif"}) == "<1990"
    assert queries.era({"date": "1994:01:01", "date_kind": "filename"}) == "1990s"
    assert queries.era({"date": "2009:12:31", "date_kind": "exif"}) == "2000-09"
    assert queries.era({"date": "2012-01-01", "date_kind": "exif"}) == "2010-14"
    assert queries.era({"date": "2019:01:01", "date_kind": "exif"}) == "2015-19"
    assert queries.era({"date": "2023:01:01", "date_kind": "exif"}) == "2020+"
    assert queries.era({"date": "2023:01:01", "date_kind": "scan"}) == "unknown"
    assert queries.era({"date": "2023:01:01", "date_kind": "mtime"}) == "unknown"
    assert queries.era({"date": None, "date_kind": None}) == "unknown"


# ---------- query-sources ----------

def test_sources_deterministic_one_per_group_min_one(world):
    a = queries.write_sources(world["set_dir"], world["frame"], n=12, seed=7)
    rows_a = read(a.path)
    side_a = json.loads(a.sidecar.read_text())
    b = queries.write_sources(world["set_dir"], world["frame"], n=12, seed=7)
    assert read(b.path) == rows_a and json.loads(b.sidecar.read_text()) == side_a
    assert len(rows_a) == 12
    assert [r["key"] for r in rows_a] == [f"s-{i:03d}" for i in range(1, 13)]
    groups = [r["group_id"] or r["asset_id"] for r in rows_a]
    assert len(groups) == len(set(groups))
    strata = Counter(r["stratum"] for r in rows_a)
    assert strata["<1990|mono|scan"] == 1  # tiny stratum still gets one
    assert set(strata) == set(side_a["strata"])
    assert side_a["seed"] == 7 and side_a["n"] == 12 and side_a["mix"] == queries.STYLE_MIX
    assert {r["asset_id"] for r in rows_a if r["stratum"] == "<1990|mono|scan"} == {h("scan1")}
    r0 = rows_a[0]
    assert set(r0) == {"key", "asset_id", "group_id", "style_assigned", "stratum", "date", "folder", "filename"}
    other = read(queries.write_sources(world["set_dir"], world["frame"], n=12, seed=8).path)
    assert [r["asset_id"] for r in other] != [r["asset_id"] for r in rows_a]


def test_allocate_largest_remainder():
    assert queries.allocate({"a": 50, "b": 30, "c": 19, "d": 1}, 10) == {"a": 5, "b": 3, "c": 1, "d": 1}
    assert sum(queries.allocate({"a": 7, "b": 7, "c": 7}, 10).values()) == 10
    with pytest.raises(ValueError):
        queries.allocate({"a": 1, "b": 1, "c": 1}, 2)


def test_style_mix_drawn_per_seed():
    s1 = queries.assign_styles(200, 1)
    assert s1 == queries.assign_styles(200, 1)
    assert s1 != queries.assign_styles(200, 2)
    c = Counter(s1)
    assert c == {"vague_recall": 40, "visual": 50, "attribute": 40, "place_time": 30, "people_role": 20, "text": 20}


# ---------- packets ----------

def jpeg(w=1200, hgt=900) -> bytes:
    return pyvips.Image.black(w, hgt, bands=3).jpegsave_buffer()


def test_packets_skip_restricted(world, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    cfg = config.Config(shoebox_root=tmp_path / "root", sources={
        "own": config.Source("own", src, "owner", synced=False, hosted_consent=True,
                             held_back=("Pictures/Held/**",), hosted_models=("gemini", "claude")),
        "gonly": config.Source("gonly", tmp_path / "g", "owner", synced=False, hosted_consent=True,
                               hosted_models=("gemini",)),
    })
    ok = [item(f"p{i}") for i in range(3)]
    held = item("held", relpath="Pictures/Held/x.jpg")
    gonly = item("gonly", source="gonly")
    items = [*ok, held, gonly]
    set_dir = tmp_path / "sets" / "p"
    write_jsonl(set_dir / "items.jsonl", items)
    write_jsonl(set_dir / "query-sources.jsonl", [
        {"key": f"s-{i + 1:03d}", "asset_id": x["asset_id"], "group_id": None, "style_assigned": "visual",
         "stratum": "x", "date": "2005", "folder": "Pictures/Trip", "filename": "f.jpg"}
        for i, x in enumerate(items)])
    for x in items:
        write_derived(cfg, x["asset_id"], data=jpeg())
    out = tmp_path / "packets"
    res = queries.write_packets(cfg, set_dir, out, batch=2, others=[],
                                never=queries.privacy.NeverSend(frozenset(), (), ()))
    packed = [e for p in res.paths for e in json.loads(Path(p).read_text())]
    assert [e["key"] for e in packed] == ["s-001", "s-002", "s-003"]
    assert len(res.paths) == 2
    assert {s["key"]: s["reason"] for s in res.skipped} == {"s-004": "held_back", "s-005": "no_consent"}
    img = pyvips.Image.new_from_file(packed[0]["image"])
    assert max(img.width, img.height) == 768
    assert Path(packed[0]["image"]).parent.parent == out
    assert set(packed[0]) == {"key", "style_assigned", "date", "folder", "filename", "image"}


# ---------- corpus summary ----------

def test_corpus_summary(world):
    md = queries.corpus_summary(world["set_dir"], world["frame"])
    assert "| Pictures/Trip | 59 |" in md
    assert "| 2005 | 30 |" in md
    assert "Scans: 1" in md
    assert "Mono: 21" in md
    assert "| NIKON | 59 |" in md
    assert "scan1" not in md and ".jpg" not in md


# ---------- ingest ----------

@pytest.fixture
def ingest_env(world, tmp_path):
    cfg = config.Config(shoebox_root=tmp_path / "root", sources={})
    res = queries.write_sources(world["set_dir"], world["frame"], n=12, seed=7)
    sources = read(res.path)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "query-writer-v1.md").write_text("qw")
    (prompts / "category-writer-v1.md").write_text("cw")
    known = [{"key": s["key"], "style_assigned": s["style_assigned"], "style_used": s["style_assigned"],
              "query": f"photo number {i} of the trip", "fallback_reason": None} for i, s in enumerate(sources)]
    category = [{"n": i + 1, "facet": f, "query": f"{f} things {i}"}
                for i, f in enumerate(["subject", "scene", "event", "attribute", "place_time", "combo"] * 2)]
    notes = tmp_path / "notes.jsonl"
    write_jsonl(notes, [
        {"query_id": "q-a", "text": "Roman ruins", "relevant": [h("a5"), h("zz")], "source": "owner_notes"},
        {"query_id": "q-b", "text": "Elsewhere", "relevant": [h("zz")], "source": "owner_notes"},
    ])
    return {**world, "cfg": cfg, "sources": sources, "prompts": prompts, "known": known, "category": category,
            "notes": notes}


def run_ingest(env, known=None, category=None, dev_pin=None):
    kp = env["tmp"] / "known.jsonl"
    cp = env["tmp"] / "category.jsonl"
    write_jsonl(kp, env["known"] if known is None else known)
    write_jsonl(cp, env["category"] if category is None else category)
    return queries.ingest(env["cfg"], env["set_dir"], [kp], cp, "claude/opus-5.5", seed=20261012,
                          dev_pin=dev_pin, notes_path=env["notes"], prompts_dir=env["prompts"])


def test_ingest_validation_errors(ingest_env):
    k = [dict(x) for x in ingest_env["known"]]
    missing = k[0]["key"]
    k = k[1:]
    k[0]["query"] = "dsc_2530 at the beach"
    k[1]["query"] = "Beach Day"
    k[2]["style_used"] = "text" if k[2]["style_assigned"] != "text" else "visual"
    k[2]["fallback_reason"] = None
    k[3]["style_used"] = "attribute"
    k[3]["fallback_reason"] = "no people words"
    k[4]["query"] = "one two three four five six seven eight nine ten eleven twelve thirteen"
    cat = [dict(x) for x in ingest_env["category"]]
    cat[1]["n"] = 1
    cat[2]["facet"] = "mood"
    with pytest.raises(queries.IngestError) as e:
        run_ingest(ingest_env, known=k, category=cat)
    msg = "\n".join(e.value.errors)
    assert f"{missing}: missing" in msg
    assert f"{k[0]['key']}: camera code" in msg
    assert f"{k[1]['key']}: not lowercase" in msg
    assert f"{k[2]['key']}: style_used" in msg
    assert f"{k[3]['key']}: fallback must use visual" in msg
    assert f"{k[4]['key']}: 13 words" in msg
    assert "category 1: duplicate n" in msg
    assert "category 3: facet" in msg
    assert not (ingest_env["set_dir"] / "queries-v1.jsonl").exists()
    assert not store.store_dirs(ingest_env["cfg"], kind="queries")


def test_ingest_cli_exit_2(ingest_env, monkeypatch, capsys):
    from shoebox.cli import main

    k = ingest_env["known"][1:]
    write_jsonl(ingest_env["tmp"] / "k.jsonl", k)
    write_jsonl(ingest_env["tmp"] / "c.jsonl", ingest_env["category"])
    monkeypatch.setattr("shoebox.commands.eval._cfg", lambda: ingest_env["cfg"])
    rc = main(["eval", "query-ingest", "--set", "s", "--sets-dir", str(ingest_env["sets"]),
               "--known", str(ingest_env["tmp"] / "k.jsonl"), "--category", str(ingest_env["tmp"] / "c.jsonl"),
               "--model-id", "claude/opus-5.5", "--notes", str(ingest_env["notes"]),
               "--prompts-dir", str(ingest_env["prompts"])])
    assert rc == 2
    assert "missing" in capsys.readouterr().err


def test_ingest_stores_keyed_idempotent(ingest_env):
    res = run_ingest(ingest_env)
    cfg = ingest_env["cfg"]
    recs = list(store.iter_records(cfg, "claude/opus-5.5", res.params_hash, kind="queries"))
    assert len(recs) == 12 + 12
    s0 = ingest_env["sources"][0]
    idx = store.index(cfg, "claude/opus-5.5", res.params_hash, kind="queries")
    assert idx[s0["asset_id"]]["output"]["query"] == "photo number 0 of the trip"
    assert idx[s0["asset_id"]]["status"] == "ok"
    assert idx["category-001"]["output"]["facet"] == "subject"
    assert (store.store_dir(cfg, "claude/opus-5.5", res.params_hash, "queries") / "params.json").exists()
    first = (ingest_env["set_dir"] / "queries-v1.jsonl").read_text()
    res2 = run_ingest(ingest_env)
    assert res2.params_hash == res.params_hash
    assert len(list(store.iter_records(cfg, "claude/opus-5.5", res.params_hash, kind="queries"))) == 24
    assert (ingest_env["set_dir"] / "queries-v1.jsonl").read_text() == first
    other_prompt = ingest_env["prompts"] / "query-writer-v1.md"
    other_prompt.write_text("changed")
    assert run_ingest(ingest_env).params_hash != res.params_hash


def test_ingest_rows_relevant_and_owner_notes(ingest_env):
    run_ingest(ingest_env)
    rows = read(ingest_env["set_dir"] / "queries-v1.jsonl")
    kinds = Counter(r["kind"] for r in rows)
    assert kinds == {"known_item": 12, "category": 12, "owner_note": 1}
    for r in rows:
        assert r["query_id"].startswith("q-") and len(r["query_id"]) == 14
    items = {x["asset_id"]: x for x in ingest_env["items"]}
    for r in rows:
        if r["kind"] == "known_item":
            g = items[r["source_asset_id"]]["group_id"]
            mates = {a for a, x in items.items() if g and x["group_id"] == g}
            assert set(r["relevant"]) == {r["source_asset_id"]} | mates
            assert r["relevant"][0] == r["source_asset_id"]
            assert r["provenance"]["model_id"] == "claude/opus-5.5"
            assert r["style"] in queries.STYLE_MIX
        elif r["kind"] == "category":
            assert r["relevant"] == [] and r["source_asset_id"] is None and r["facet"]
        else:
            assert r["relevant"] == [h("a5")] and r["split"] == "owner"
            assert r["provenance"]["writer"] == "owner"
    # a group with mates must be among the sources for this check to mean anything
    grouped = [r for r in rows if r["kind"] == "known_item" and len(r["relevant"]) > 1]
    assert grouped


def test_split_no_group_in_both(ingest_env):
    run_ingest(ingest_env)
    rows = read(ingest_env["set_dir"] / "queries-v1.jsonl")
    items = {x["asset_id"]: x for x in ingest_env["items"]}
    by = {"dev": set(), "test": set()}
    for r in rows:
        if r["kind"] == "known_item":
            by[r["split"]] |= {items[a]["group_id"] or a for a in r["relevant"]}
    assert by["dev"] and by["test"] and not (by["dev"] & by["test"])
    ks = Counter(r["split"] for r in rows if r["kind"] == "known_item")
    assert ks == {"dev": 4, "test": 8}
    cs = Counter(r["split"] for r in rows if r["kind"] == "category")
    assert cs == {"dev": 4, "test": 8}
    md = (ingest_env["set_dir"] / "QUERIES.md").read_text()
    assert "owner realism check: pending" in md


def test_check_splits_detects_overlap():
    rows = [{"kind": "known_item", "split": "dev", "relevant": ["x"]},
            {"kind": "known_item", "split": "test", "relevant": ["x"]}]
    with pytest.raises(ValueError):
        queries.check_splits(rows, {"x": None})


def test_dev_pin_honoured(ingest_env):
    run_ingest(ingest_env)
    rows = read(ingest_env["set_dir"] / "queries-v1.jsonl")
    tests = [r for r in rows if r["kind"] == "known_item" and r["split"] == "test"]
    target = tests[0]["source_asset_id"]
    items = {x["asset_id"]: x for x in ingest_env["items"]}
    pin = ingest_env["tmp"] / "pin.jsonl"
    write_jsonl(pin, [items[target]])
    run_ingest(ingest_env, dev_pin=pin)
    rows2 = read(ingest_env["set_dir"] / "queries-v1.jsonl")
    assert next(r for r in rows2 if r.get("source_asset_id") == target)["split"] == "dev"


def test_sources_one_per_group_when_groups_crowd(tmp_path):
    """6 capture groups, one holding 6 of 11 photos: n=6 must take one photo from each group."""
    items = [item(f"g{i}", group_id="big") for i in range(6)] + [item(f"x{i}") for i in range(5)]
    write_jsonl(tmp_path / "s" / "items.jsonl", items)
    fpath = write_frame(tmp_path / "f.parquet", [frow(x) for x in items])
    for seed in range(5):
        rows = queries.write_sources(tmp_path / "s", fpath, n=6, seed=seed).rows
        assert Counter(r["group_id"] for r in rows)["big"] == 1
    with pytest.raises(ValueError):
        queries.write_sources(tmp_path / "s", fpath, n=7, seed=1)


def test_packets_leave_out_quarantined(world, tmp_path):
    from shoebox import quarantine

    src = tmp_path / "src"
    src.mkdir()
    cfg = config.Config(shoebox_root=tmp_path / "root", sources={
        "own": config.Source("own", src, "owner", synced=False, hosted_consent=True,
                             hosted_models=("gemini", "claude"))})
    items = [item(f"q{i}") for i in range(2)]
    set_dir = tmp_path / "sets" / "q"
    write_jsonl(set_dir / "items.jsonl", items)
    write_jsonl(set_dir / "query-sources.jsonl", [
        {"key": f"s-{i + 1:03d}", "asset_id": x["asset_id"], "group_id": None, "style_assigned": "visual",
         "stratum": "x", "date": "2005", "folder": "Pictures/Trip", "filename": "f.jpg"}
        for i, x in enumerate(items)])
    for x in items:
        write_derived(cfg, x["asset_id"], data=jpeg())
    quarantine.flag([items[1]["asset_id"]], "nudity")
    res = queries.write_packets(cfg, set_dir, tmp_path / "packets", others=[],
                                never=queries.privacy.NeverSend(frozenset(), (), ()))
    packed = [e["key"] for p in res.paths for e in json.loads(Path(p).read_text())]
    assert packed == ["s-001"]
    assert res.skipped == [{"key": "s-002", "reason": "quarantined"}]
