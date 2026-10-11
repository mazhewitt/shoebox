"""Opus audit of the Gemini judgments (design D10, task 6.3): synthetic pools, stores and derived images only."""

import io
import json
import math

import pytest
from derived_fixture import write_derived
from PIL import Image

from shoebox import config, privacy, store
from shoebox.evals import audit, judge


def aid(i: int) -> str:
    return f"{i:03x}" + "a" * 61


def jpeg(colour=(30, 120, 200), size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "JPEG")
    return buf.getvalue()


ROUTES = ["r-a", "r-b", "r-c"]


def make_pool(nq=12, per=20):
    """Each query: `per` photos; route r-a ranks the first 10, r-b photos 5..14, r-c photos 10..19.
    Query 0..5 known-item with target photo 19 (only r-c retrieves it), others category."""
    pool, gem = [], {}
    for q in range(nq):
        qid = f"q-{q:02d}"
        ids = [aid(q * 100 + i) for i in range(per)]
        routes = {a: [r for r, lo in zip(ROUTES, (0, 5, 10)) if lo <= i < lo + 10] for i, a in enumerate(ids)}
        known = q < 6
        rel = [ids[19]] if known else []
        pool.append({"query_id": qid, "text": f"SECRET QUERY TEXT {q}", "kind": "known_item" if known else "category",
                     "split": "test", "relevant": rel, "asset_ids": ids, "routes": routes})
        for i, a in enumerate(ids):
            gem[f"{qid}:{a}"] = (i + q) % 3
    return pool, gem


def pairs_of(rows):
    return {(r["query_id"], p["asset_id"]): p for r in rows for p in r["pairs"]}


# ---------- sample ----------


def test_sample_is_deterministic_and_covers_every_route_stratum():
    pool, gem = make_pool()
    rows = audit.sample(pool, gem, 0.13, 7)
    assert rows == audit.sample(list(reversed(pool)), dict(reversed(gem.items())), 0.13, 7)
    assert rows != audit.sample(pool, gem, 0.13, 8)
    got = pairs_of(rows)
    kinds = {r["query_id"]: r["kind"] for r in pool}
    for route in ROUTES:
        for g in (0, 1, 2):
            for kind in ("known_item", "category"):
                stratum = [k for k in gem if gem[k] == g and kinds[k.split(":")[0]] == kind
                           and route in next(r for r in pool if r["query_id"] == k.split(":")[0])["routes"][k.split(":")[1]]]
                hit = sum((k.split(":")[0], k.split(":")[1]) in got for k in stratum)
                assert hit >= math.ceil(0.13 * len(stratum)) >= 1, (route, g, kind)
    s = audit.sample_summary(rows, pool, gem)
    assert s["judged_pairs"] == 240 and 0.13 <= s["fraction"] < 0.35
    assert all(p["reasons"] for p in got.values())
    assert [r["query_id"] for r in rows] == sorted(r["query_id"] for r in rows)
    assert "text" not in rows[0]


def test_sample_includes_every_target_graded_below_two():
    pool, gem = make_pool()
    targets = [(r["query_id"], r["relevant"][0]) for r in pool if r["relevant"]]
    for q, a in targets:
        gem[f"{q}:{a}"] = 0 if q != "q-05" else 2
    got = pairs_of(audit.sample(pool, gem, 0.0, 7))  # frac 0: only quota-of-zero strata, so only targets
    assert set(got) == {t for t in targets if t[0] != "q-05"}
    assert all(audit.TARGET_REASON in p["reasons"] and p["target"] for p in got.values())


def test_unjudged_pairs_are_not_sampled_and_unrouted_targets_get_their_own_bucket():
    pool, gem = make_pool(nq=1)
    pool[0]["routes"][pool[0]["relevant"][0]] = []
    gem = {k: v for k, v in gem.items() if not k.endswith(pool[0]["asset_ids"][0])}
    got = pairs_of(audit.sample(pool, gem, 1.0, 7))
    assert ("q-00", pool[0]["asset_ids"][0]) not in got
    assert got[("q-00", pool[0]["relevant"][0])]["routes"] == [audit.NO_ROUTE]


# ---------- packets ----------


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "NEVER_SEND_PATH", tmp_path / "never-send.txt")
    src = tmp_path / "src"
    src.mkdir()
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={
        "own": config.Source("own", src, "owner", False, True, hosted_models=("gemini", "claude")),
        "gem": config.Source("gem", src, "owner", False, True)})  # gemini only: refused for claude
    return cfg


def test_packets_grids_map_and_privacy_skip(env, tmp_path):
    cfg = env
    pool, gem = make_pool(nq=2, per=20)
    rows = audit.sample(pool, gem, 1.0, 7)  # everything: 40 pairs
    items = []
    for r in pool:
        for i, a in enumerate(r["asset_ids"]):
            items.append({"asset_id": a, "source": "gem" if i == 3 else "own",
                          "relpath": f"Pictures/Trip {i}/x{i}.jpg", "group_id": None})
            if i != 7:
                write_derived(cfg, a, jpeg((i * 10, 50, 90)))
    frames = {it["asset_id"]: {"date": "2004-07-01T10:00:00", "date_kind": "exif"} for it in items}
    out = tmp_path / "packets"
    res = audit.packets(cfg, rows, pool, items, frames, out, grid=10, per_batch=3, seed=5)
    reasons = {(s["query_id"], s["asset_id"]): s["reason"] for s in res.skipped}
    assert reasons[("q-00", pool[0]["asset_ids"][3])] == "no_consent"
    assert reasons[("q-01", pool[1]["asset_ids"][7])] == "no_derived"
    assert len(res.skipped) == 4
    mapping = json.loads((out / "map.json").read_text())
    assert all(len(g["asset_ids"]) <= 10 for g in res.grids)
    shown = sorted(a for g in mapping.values() for a in g["asset_ids"])
    assert shown == sorted(p["asset_id"] for r in rows for p in r["pairs"]
                           if (r["query_id"], p["asset_id"]) not in reasons)
    batches = [json.loads(p.read_text()) for p in sorted(out.glob("batch-*.json"))]
    assert [len(b["grids"]) for b in batches] == [3, 1]
    listed = [g for b in batches for g in b["grids"]]
    for g in listed:
        assert g["grid_id"] in mapping and len(g["photos"]) == len(mapping[g["grid_id"]]["asset_ids"])
        assert [p["n"] for p in g["photos"]] == list(range(1, len(g["photos"]) + 1))
        assert g["photos"][0]["note"].startswith("taken 2004-07-01; folder: Pictures/Trip ")
        assert g["query"].startswith("SECRET QUERY TEXT")
        with Image.open(g["image"]) as im:
            n = len(g["photos"])
            assert im.size == (min(5, n) * audit.CELL_PX, -(-n // 5) * audit.CELL_PX)
    text = "".join(p.read_text() for p in out.glob("batch-*.json"))
    assert not any(a in text for a in shown)  # no asset_ids for the subagent to read
    again = audit.packets(cfg, rows, pool, items, frames, tmp_path / "p2", grid=10, per_batch=3, seed=5)
    assert again.grids == res.grids


def test_packets_refuse_evals_dir(env):
    with pytest.raises(ValueError):
        audit.check_out_dir(config.REPO_ROOT / "evals" / "scratch")


def test_contact_sheet_numbers_cells_in_red(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(jpeg((0, 0, 0), (400, 400)))
        paths.append(p)
    with Image.open(io.BytesIO(audit.contact_sheet(paths))) as im:
        im = im.convert("RGB")
        reds = [im.getpixel((x, y)) for x in range(audit.CELL_PX, audit.CELL_PX + 80) for y in range(80)]
    assert any(r > 150 and g < 90 and b < 90 for r, g, b in reds)


# ---------- ingest ----------


def write_packet_dir(d, grids, seed=5):
    d.mkdir(parents=True, exist_ok=True)
    (d / "map.json").write_text(json.dumps(grids))
    (d / "packets.json").write_text(json.dumps({"seed": seed}))


def test_ingest_validates_and_stores_idempotently(env, tmp_path):
    cfg = env
    d = tmp_path / "pk"
    write_packet_dir(d, {"g1": {"query_id": "q-1", "asset_ids": [aid(1), aid(2)]},
                         "g2": {"query_id": "q-2", "asset_ids": [aid(3)]}})
    (d / "grades-001.jsonl").write_text(json.dumps({"grid_id": "g1", "grades": [{"n": 2, "grade": 0},
                                                                                {"n": 1, "grade": 2}]}) + "\n")
    res = audit.ingest(cfg, d, "prompt v2")
    assert res.stored == 2 and res.missing_grids == ["g2"]
    assert res.params_hash == store.params_hash(audit.audit_params("prompt v2", 5))
    assert res.params_hash != store.params_hash(audit.audit_params("prompt v2", 6))
    ph, grades = audit.opus_grades(cfg)
    assert ph == res.params_hash and grades == {f"q-1:{aid(1)}": 2, f"q-1:{aid(2)}": 0}
    recs = list(store.iter_records(cfg, audit.MODEL_ID, ph, judge.KIND))
    assert {r["model_id"] for r in recs} == {"claude/opus-5.5"}
    again = audit.ingest(cfg, d, "prompt v2")
    assert again.stored == 0 and again.already == 2
    assert len(list(store.iter_records(cfg, audit.MODEL_ID, ph, judge.KIND))) == 2


@pytest.mark.parametrize("grades", [
    [{"n": 1, "grade": 2}],  # cell 2 missing
    [{"n": 1, "grade": 2}, {"n": 1, "grade": 1}],  # graded twice
    [{"n": 1, "grade": 3}, {"n": 2, "grade": 0}],  # bad grade
    [{"n": 1, "grade": 1}, {"n": 3, "grade": 0}],  # no such cell
])
def test_ingest_rejects_bad_grades_and_stores_nothing(env, tmp_path, grades):
    cfg = env
    d = tmp_path / "pk"
    write_packet_dir(d, {"g1": {"query_id": "q-1", "asset_ids": [aid(1), aid(2)]},
                         "g2": {"query_id": "q-2", "asset_ids": [aid(3)]}})
    (d / "grades-001.jsonl").write_text(
        json.dumps({"grid_id": "g2", "grades": [{"n": 1, "grade": 1}]}) + "\n"
        + json.dumps({"grid_id": "g1", "grades": grades}) + "\n")
    with pytest.raises(audit.IngestError):
        audit.ingest(cfg, d, "p")
    assert not (cfg.results_dir / judge.KIND).exists()


def test_ingest_rejects_unknown_grid(env, tmp_path):
    d = tmp_path / "pk"
    write_packet_dir(d, {"g1": {"query_id": "q-1", "asset_ids": [aid(1)]}})
    (d / "grades-001.jsonl").write_text(json.dumps({"grid_id": "zz", "grades": [{"n": 1, "grade": 1}]}) + "\n")
    with pytest.raises(audit.IngestError, match="unknown grid_id"):
        audit.ingest(env, d, "p")


# ---------- kappa and report ----------


def test_kappa_perfect_chance_and_hand_computed():
    perfect = [[5, 0, 0], [0, 7, 0], [0, 0, 3]]
    assert audit.weighted_kappa(perfect, "linear") == pytest.approx(1.0)
    assert audit.weighted_kappa(perfect, "quadratic") == pytest.approx(1.0)
    chance = [[4, 4, 4], [4, 4, 4], [4, 4, 4]]
    assert audit.weighted_kappa(chance, "linear") == pytest.approx(0.0)
    assert audit.weighted_kappa(chance, "quadratic") == pytest.approx(0.0)
    # rows 12/10/8, cols 13/8/9, n=30. In units of 1/900: linear observed 120, expected 402;
    # quadratic observed 60, expected 307.
    m = [[10, 2, 0], [3, 5, 2], [0, 1, 7]]
    assert audit.weighted_kappa(m, "linear") == pytest.approx(1 - 120 / 402)
    assert audit.weighted_kappa(m, "quadratic") == pytest.approx(1 - 60 / 307)
    assert audit.weighted_kappa([[0] * 3] * 3) is None
    assert audit.weighted_kappa([[4, 0, 0], [0, 0, 0], [0, 0, 0]]) is None


def test_report_breakdowns_rule_and_no_query_text():
    pool, gem = make_pool()
    rows = audit.sample(pool, gem, 0.2, 7)
    opus = {f"{r['query_id']}:{p['asset_id']}": p["gemini_grade"] for r in rows for p in r["pairs"]}
    meta = {"set": "s", "date": "2026-10-13", "gemini_model_id": "gemini/x", "gemini_params_hash": "g",
            "opus_model_id": audit.MODEL_ID, "opus_params_hash": "o"}
    rep = audit.build_report(rows, opus, meta, b=200)
    assert rep["overall"]["kappa_linear"] == 1.0 and rep["rejudge_test_pool"] is False
    assert rep["overall"]["ci95_linear"] == [1.0, 1.0]
    assert set(rep["per_route"]) == set(ROUTES) and set(rep["per_kind"]) == {"known_item", "category"}
    flipped = {k: 2 - v for k, v in opus.items()}
    bad = audit.build_report(rows, flipped, meta, b=200)
    assert bad["overall"]["kappa_linear"] < 0 and bad["rejudge_test_pool"] is True
    assert bad["per_gemini_grade"]["0"]["opus"][2] == bad["per_gemini_grade"]["0"]["n"]
    lo, hi = bad["overall"]["ci95_linear"]
    assert lo <= bad["overall"]["kappa_linear"] <= hi
    md = audit.render_md(bad)
    assert "SECRET" not in md and "SECRET" not in json.dumps(bad)
    assert "re-judge the test pool" in md and "0.6" in md


def test_cli_audit_sample_writes_ids_only(tmp_path, capsys):
    from shoebox.cli import main

    pool, gem = make_pool()
    d = tmp_path / "sets" / "s"
    d.mkdir(parents=True)
    judge.write_jsonl(d / "pool-v2.jsonl", pool)
    judge.write_jsonl(d / "judgments-v1.jsonl", [{"query_id": k.split(":")[0], "asset_id": k.split(":")[1],
                                                 "grade": g, "model_id": "gemini/x", "params_hash": "h"}
                                                for k, g in gem.items()])
    assert main(["eval", "audit-sample", "--set", "s", "--sets-dir", str(tmp_path / "sets")]) == 0
    text = (d / audit.SAMPLE_FILE).read_text()
    assert "SECRET" not in text and json.loads(text.splitlines()[0])["pairs"]
