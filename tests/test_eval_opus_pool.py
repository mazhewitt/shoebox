"""Opus judging of a whole split's pool: pool "sample", report without Gemini grades, qrels export. Synthetic."""

import json

from test_eval_audit import aid, make_pool, write_packet_dir

from shoebox import config, store
from shoebox.cli import main
from shoebox.commands import eval as eval_cmd
from shoebox.evals import audit, judge


def test_pool_sample_takes_every_pair_of_the_split_graded_or_not():
    pool, gem = make_pool(nq=4, per=20)
    pool[3]["split"] = "dev"
    del gem[f"q-00:{aid(0)}"]  # Gemini never judged this pair: Opus still does
    rows = audit.pool_sample(pool, gem, "test")
    assert [r["query_id"] for r in rows] == ["q-00", "q-01", "q-02"]
    assert sum(len(r["pairs"]) for r in rows) == 60
    first = {p["asset_id"]: p for p in rows[0]["pairs"]}
    assert first[aid(0)]["gemini_grade"] is None
    assert first[aid(1)]["gemini_grade"] == gem[f"q-00:{aid(1)}"]
    assert first[aid(19)]["target"] is True and first[aid(19)]["routes"] == ["r-c"]
    assert all(p["reasons"] == [audit.POOL_REASON] for r in rows for p in r["pairs"])
    assert "SECRET" not in json.dumps(rows)
    s = audit.pool_summary(rows)
    assert s == {"queries": 3, "pairs": 60, "gemini_graded": 59, "no_gemini": 1}


def test_report_excludes_pairs_without_gemini_grade_and_counts_them():
    pool, gem = make_pool(nq=4, per=20)
    del gem[f"q-00:{aid(0)}"]
    rows = audit.pool_sample(pool, gem, "test")
    opus = {f"{r['query_id']}:{p['asset_id']}": 2 for r in rows for p in r["pairs"]}
    rep = audit.build_report(rows, opus, {}, b=50)
    assert rep["overall"]["n"] == 79
    assert rep["no_gemini_grade"] == 1
    assert rep["opus_graded_pairs"] == 79
    # unchanged for a sample where every pair has a Gemini grade
    full = audit.build_report(audit.sample(pool, {**gem, f"q-00:{aid(0)}": 1}, 1.0, 7), opus, {}, b=50)
    assert full["no_gemini_grade"] == 0 and full["overall"]["n"] == 80


def test_opus_qrels_shape_matches_gemini_export(tmp_path):
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={})
    d = tmp_path / "pk"
    write_packet_dir(d, {"g1": {"query_id": "q-1", "asset_ids": [aid(2), aid(1)]}})
    (d / "grades-001.jsonl").write_text(json.dumps({"grid_id": "g1", "grades": [{"n": 1, "grade": 0},
                                                                                {"n": 2, "grade": 2}]}) + "\n")
    res = audit.ingest(cfg, d, "p")
    rows = audit.opus_qrels(cfg)
    assert rows == [{"query_id": "q-1", "asset_id": aid(1), "grade": 2, "model_id": audit.MODEL_ID,
                     "params_hash": res.params_hash},
                    {"query_id": "q-1", "asset_id": aid(2), "grade": 0, "model_id": audit.MODEL_ID,
                     "params_hash": res.params_hash}]
    assert audit.gemini_grades(rows) == {f"q-1:{aid(1)}": 2, f"q-1:{aid(2)}": 0}


def test_cli_pool_sample_packets_option_and_opus_export(tmp_path, monkeypatch, capsys):
    pool, gem = make_pool(nq=3, per=20)
    pool[2]["split"] = "dev"
    sets = tmp_path / "sets"
    d = sets / "s"
    d.mkdir(parents=True)
    judge.write_jsonl(d / "pool-v2.jsonl", pool)
    judge.write_jsonl(d / "judgments-v1.jsonl", [{"query_id": k.split(":")[0], "asset_id": k.split(":")[1],
                                                 "grade": g, "model_id": "gemini/x", "params_hash": "h"}
                                                for k, g in list(gem.items())[1:]])
    assert main(["eval", "audit-sample", "--set", "s", "--all", "--split", "test", "--sets-dir", str(sets)]) == 0
    rows = [json.loads(x) for x in (d / "opus-pool-test.jsonl").read_text().splitlines()]
    assert [r["query_id"] for r in rows] == ["q-00", "q-01"]
    assert rows[0]["pairs"][0]["gemini_grade"] is None
    assert not (d / audit.SAMPLE_FILE).exists()
    assert "1 without a Gemini grade" in capsys.readouterr().out
    assert main(["eval", "audit-sample", "--set", "s", "--all", "--sets-dir", str(sets)]) == 2  # --split required

    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={})
    monkeypatch.setattr(eval_cmd, "_cfg", lambda: cfg)
    pk = tmp_path / "pk"
    write_packet_dir(pk, {"g1": {"query_id": "q-00", "asset_ids": [aid(0)]}})
    (pk / "grades-001.jsonl").write_text(json.dumps({"grid_id": "g1", "grades": [{"n": 1, "grade": 1}]}) + "\n")
    audit.ingest(cfg, pk, "p")
    capsys.readouterr()
    assert main(["eval", "audit-packets", "--set", "s", "--sample", "nope.jsonl", "--out", str(tmp_path / "o"),
                 "--sets-dir", str(sets)]) == 2
    assert "nope.jsonl" in capsys.readouterr().err
    assert main(["eval", "judgments", "--set", "s", "--source", "opus", "--sets-dir", str(sets)]) == 0
    out = [json.loads(x) for x in (d / "judgments-opus.jsonl").read_text().splitlines()]
    assert out == [{"query_id": "q-00", "asset_id": aid(0), "grade": 1, "model_id": audit.MODEL_ID,
                    "params_hash": store.params_hash(audit.audit_params("p", 5))}]

    capsys.readouterr()
    assert main(["eval", "audit-report", "--set", "s", "--sample", "opus-pool-test.jsonl", "--sets-dir", str(sets),
                 "--out-dir", str(tmp_path / "runs"), "--date", "2026-10-11", "--bootstrap", "10"]) == 0
    rep = json.loads((tmp_path / "runs" / "2026-10-11_s-judge-audit.json").read_text())
    assert rep["no_gemini_grade"] == 1 and rep["opus_graded_pairs"] == 0
