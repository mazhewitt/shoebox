"""Graded dev/test scoring (tasks 7.1, 7.3) on synthetic rankings and qrels."""

import json
import math

import pytest

from shoebox.cli import main
from shoebox.evals import graded

SECRET = "SECRET-QUERY-TEXT"


def d(rank: int) -> float:
    return 1.0 / math.log2(rank + 1)


# --- metrics --------------------------------------------------------------------------------------


def test_ndcg_graded_linear_gains_and_ideal_from_all_judged():
    grades = {"a": 2, "b": 1, "c": 0, "z": 2}  # z judged 2 but never ranked: still in the ideal
    m = graded.graded_query(["b", "a", "c", "x"], None, grades)
    dcg = 1 * d(1) + 2 * d(2)
    idcg = 2 * d(1) + 2 * d(2) + 1 * d(3)
    assert m["ndcg"] == pytest.approx(dcg / idcg)


def test_unjudged_counts_as_zero_and_coverage_reported():
    grades = {"a": 2, "c": 0}
    m = graded.graded_query(["x", "a", "c", "y"], None, grades, k=4)
    assert m["ndcg"] == pytest.approx(2 * d(2) / (2 * d(1)))
    assert m["judged"] == pytest.approx(2 / 4)


def test_ties_are_expected_over_orders():
    # a (grade 2) and x (unjudged) tie at the top: a is at rank 1 or 2 with p=1/2 each
    m = graded.graded_query(["x", "a"], [1.0, 1.0], {"a": 2}, k=20)
    assert m["ndcg"] == pytest.approx((d(1) + d(2)) / 2 / d(1))
    # a tie block straddling the cut-off counts its in-cutoff share for coverage
    m = graded.graded_query(["x", "a", "b"], [1.0, 0.5, 0.5], {"a": 2}, k=2)
    assert m["judged"] == pytest.approx(0.5 / 2)


def test_query_with_no_positive_grade_is_excluded_and_counted():
    queries = [{"query_id": "q1", "kind": "category", "split": "dev", "relevant": [], "text": SECRET},
               {"query_id": "q2", "kind": "category", "split": "dev", "relevant": [], "text": SECRET}]
    rows = {"q1": (["a", "b"], None), "q2": (["a", "b"], None)}
    qrels = {"q1": {"a": 0, "b": 0}, "q2": {"a": 0, "b": 2}}
    per, excluded = graded.score_queries(queries, rows, qrels, splits={"dev"})
    assert list(per) == ["q2"] and excluded == ["q1"]


def test_known_item_rr_hit_and_category_recall():
    queries = [{"query_id": "k", "kind": "known_item", "split": "dev", "relevant": ["t"]},
               {"query_id": "c", "kind": "category", "split": "dev", "relevant": []}]
    ranking = ["x", "y", "t"] + [f"n{i}" for i in range(30)]
    rows = {"k": (ranking, None), "c": (["g2a", "g1"] + [f"n{i}" for i in range(30)] + ["g2b"], None)}
    qrels = {"k": {"t": 2}, "c": {"g2a": 2, "g2b": 2, "g1": 1}}
    per, _ = graded.score_queries(queries, rows, qrels, splits={"dev"})
    assert per["k"]["rr"] == pytest.approx(1 / 3) and per["k"]["hit@10"] == 1.0
    assert per["c"]["recall2@20"] == pytest.approx(0.5)
    assert per["c"]["recall1@20"] == pytest.approx(2 / 3)
    assert "rr" not in per["c"]


# --- dev choice and freeze --------------------------------------------------------------------------


def test_dev_choice_best_per_captioner_ties_to_simpler_route():
    means = {"eg2-blend:g12b-q4": 0.5, "eg2-blend:g12b-q4+exp": 0.6, "hybrid:g12b-q4": 0.6,
             "eg2-image@768": 0.4, "eg2-image@768+filt": 0.4, "meta": 0.3,
             "fts:opus-v2": 0.7, "eg2-blend@w0.3:opus-v2": 0.9}
    fr = graded.choose_frozen(means)
    assert fr["g12b-q4"]["route"] == "hybrid:g12b-q4"  # tie at 0.6: fewer suffixes wins
    assert fr["none"]["route"] == "eg2-image@768"  # tie at 0.4: no suffix
    assert fr["opus-v2"]["route"] == "fts:opus-v2"  # sensitivity routes never compete
    assert graded.choose_frozen({"meta": 0.4, "image-meta": 0.4})["none"]["route"] == "image-meta"  # then name


def _write_set(tmp_path, owner_grades=False):
    sets = tmp_path / "sets"
    sd = sets / "s"
    sd.mkdir(parents=True)
    qs = []
    for i in range(12):
        split = "dev" if i < 4 else "test"
        kind = "known_item" if i % 2 else "category"
        q = {"query_id": f"q{i:02d}", "kind": kind, "split": split, "relevant": [f"t{i}"] if kind == "known_item" else [],
             "text": SECRET}
        q["style" if kind == "known_item" else "facet"] = "visual" if kind == "known_item" else "scene"
        qs.append(q)
    qs.append({"query_id": "own0", "kind": "owner_note", "split": "owner", "relevant": ["t99"], "text": SECRET})
    (sd / "queries-v1.jsonl").write_text("".join(json.dumps(q) + "\n" for q in qs))
    gem, opus = [], []
    for q in qs:
        tid = q["relevant"][0] if q["relevant"] else f"t{q['query_id']}"
        g = {"query_id": q["query_id"], "asset_id": tid, "grade": 2}
        if q["split"] in ("dev", "test", "owner"):
            gem.append(g)
        if q["split"] == "test" or (owner_grades and q["split"] == "owner"):
            opus.append(g)
    (sd / "judgments-v1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in gem))
    (sd / "judgments-opus.jsonl").write_text("".join(json.dumps(r) + "\n" for r in opus))
    # routes: the target at rank r(route); gemini route better than e4b on test
    rank = {"eg2-blend:gemini-medium-v2": 1, "eg2-blend:gemini-medium-v2+exp": 1, "eg2-blend:e4b-bf16": 2,
            "eg2-image@768": 5, "fts:e4b-bf16": 9}
    entries = {}
    for route, r in rank.items():
        p = tmp_path / f"{route.replace(':', '_')}.jsonl"
        rows = []
        for q in qs:
            tid = q["relevant"][0] if q["relevant"] else f"t{q['query_id']}"
            ranking = [f"n{j}" for j in range(r - 1)] + [tid] + [f"m{j}" for j in range(25)]
            rows.append({"query_id": q["query_id"], "route": route, "ranking": ranking,
                         "scores": [1.0 - j / 100 for j in range(len(ranking))]})
        p.write_text("".join(json.dumps(x) + "\n" for x in rows))
        entries[route] = {"path": str(p), "queries_file": "queries-v1.jsonl", "params_hash": "h"}
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"set": "s", "routes": entries}))
    return sets, idx


def _dev(sets, idx, out, *extra):
    return main(["eval", "graded-dev", "--set", "s", "--qrels", "judgments-v1.jsonl", "--index", str(idx),
                 "--sets-dir", str(sets), "--out-dir", str(out), "--date", "2026-10-11", *extra])


def test_graded_dev_freezes_and_refuses_overwrite(tmp_path, capsys):
    sets, idx = _write_set(tmp_path)
    out = tmp_path / "runs"
    assert _dev(sets, idx, out) == 0
    fr = json.loads((sets / "s" / "frozen-routes.json").read_text())
    assert fr["routes"]["gemini-medium-v2"]["route"] == "eg2-blend:gemini-medium-v2"  # tie with +exp: simpler
    assert fr["routes"]["none"]["route"] == "eg2-image@768"
    assert fr["qrels"] == "judgments-v1.jsonl" and fr["chosen_on"] == "dev"
    rep = json.loads((out / "2026-10-11_s-dev.json").read_text())
    assert rep["routes"]["eg2-image@768"]["n"] == 4  # dev only, known_item + category
    assert _dev(sets, idx, out) == 2
    assert "exists" in capsys.readouterr().err
    assert _dev(sets, idx, out, "--force") == 0
    for f in out.iterdir():
        assert SECRET not in f.read_text()


def _test(sets, idx, out, monkeypatch, *extra):
    monkeypatch.setattr(graded, "measure_costs", lambda cfg, caps: {
        "e4b-bf16": {"local": True, "img_per_hour": 1000.0}, "gemini-medium-v2": {"local": False, "usd_per_image": 0.001}})
    monkeypatch.setattr(graded, "load_config", lambda: None)
    return main(["eval", "graded-test", "--set", "s", "--qrels", "judgments-opus.jsonl", "--secondary",
                 "judgments-v1.jsonl", "--index", str(idx), "--sets-dir", str(sets), "--out-dir", str(out),
                 "--date", "2026-10-11", "--audit-dir", str(out), "--spend", str(out / "spend.jsonl"), *extra])


def test_graded_test_refuses_without_freeze(tmp_path, monkeypatch, capsys):
    sets, idx = _write_set(tmp_path)
    assert _test(sets, idx, tmp_path / "runs", monkeypatch) == 2
    assert "frozen-routes.json" in capsys.readouterr().err


def test_graded_test_report(tmp_path, monkeypatch):
    sets, idx = _write_set(tmp_path)
    out = tmp_path / "runs"
    assert _dev(sets, idx, out) == 0
    out.mkdir(exist_ok=True)
    (out / "2026-10-11_s-caption-audit.json").write_text(json.dumps(
        {"reference_arm": "gemini-medium-v2",
         "arms": {"e4b-bf16": {"error_rate": {"mean": 0.30}}, "gemini-medium-v2": {"error_rate": {"mean": 0.10}}},
         "structured": {"e4b-bf16": {"overall": {"mono": {"precision": 0.9}}},
                        "gemini-medium-v2": {"overall": {"mono": {"precision": 1.0}}}}}))
    (out / "spend.jsonl").write_text(json.dumps({"usd": 40.0}) + "\n")
    assert _test(sets, idx, out, monkeypatch) == 0
    rep = json.loads((out / "2026-10-11_s-test.json").read_text())
    ref = rep["frozen"]["gemini-medium-v2"]
    assert ref["mean_ndcg"] == pytest.approx(1.0) and ref["n"] == 8
    e4b = rep["frozen"]["e4b-bf16"]
    assert e4b["paired"]["mean_delta"] == pytest.approx(d(2) - 1.0)
    assert e4b["paired"]["non_inferior"] is False
    assert rep["owner_note"]["qrels"] == "judgments-v1.jsonl"  # no Opus grades on the owner split
    assert "eg2-blend:gemini-medium-v2+exp" in rep["appendix_not_used"]
    assert rep["decision"]["recommendation"]["captioner"] == "gemini-medium-v2"
    assert rep["decision"]["remaining_usd"] == pytest.approx(110.0)
    for f in out.iterdir():
        assert SECRET not in f.read_text()


# --- paired non-inferiority and decision logic -----------------------------------------------------


def test_non_inferiority_on_constructed_delta():
    ref = {f"q{i}": 0.5 for i in range(50)}
    near = {q: v - 0.01 for q, v in ref.items()}
    far = {q: v - 0.05 for q, v in ref.items()}
    assert graded.non_inferiority(near, ref)["non_inferior"] is True
    p = graded.non_inferiority(far, ref)
    assert p["non_inferior"] is False and p["delta_ci95"][1] == pytest.approx(-0.05)
    # mean delta -0.02 is inside the margin, but the noisy lower bound is not: the CI decides, not the mean
    noisy = {q: v - 0.02 + (0.3 if i % 2 else -0.3) for i, (q, v) in enumerate(ref.items())}
    p = graded.non_inferiority(noisy, ref)
    assert p["mean_delta"] == pytest.approx(-0.02) and p["delta_ci95"][0] < -0.03
    assert p["non_inferior"] is False


def _c(**kw):
    base = {"search_ni": True, "caption_pass": True, "local": False, "usd": 0.0, "hours": None, "mono_ok": True}
    return base | kw


def test_decision_local_pass_wins_cost_tie_and_cheapest():
    cands = {"gemini-medium-v2": _c(usd=100.0), "e4b-bf16": _c(local=True, hours=50.0),
             "g12b-q4": _c(local=True, hours=90.0), "none": _c(caption_pass=None)}
    dec = graded.decide(cands, remaining_usd=100.0)
    assert dec["recommendation"]["captioner"] == "e4b-bf16"
    # a 0-USD non-local survivor (subscription) that sorts first by name ties on cost: local wins
    dec = graded.decide(cands | {"a-subscription": _c(usd=0.0)}, remaining_usd=100.0)
    assert dec["recommendation"]["captioner"] == "e4b-bf16" and dec["recommendation"]["rule"].startswith("3")


def test_decision_drops_failures_and_falls_back_to_gemini_batch():
    cands = {"gemini-medium-v2": _c(usd=120.0), "e4b-bf16": _c(local=True, hours=50.0, caption_pass=False),
             "g12b-q4": _c(local=True, hours=90.0, search_ni=False), "none": _c(caption_pass=None),
             "opus-v2": _c(caption_pass=None)}
    dec = graded.decide(cands, remaining_usd=50.0)
    r = dec["recommendation"]
    assert r["captioner"] == "gemini-medium-v2" and r["rule"].startswith("4")
    assert r["batch_usd"] == pytest.approx(60.0) and r["fits_budget"] is False
    assert dec["fallback"] == "none"
    assert set(dec["dropped"]) == {"e4b-bf16", "g12b-q4"}


def test_decision_mono_below_gate_blocks_colour_filter_only():
    cands = {"gemini-medium-v2": _c(usd=100.0), "e4b-bf16": _c(local=True, hours=50.0, mono_ok=False)}
    dec = graded.decide(cands, remaining_usd=100.0)
    assert dec["recommendation"]["captioner"] == "e4b-bf16"
    assert dec["recommendation"]["colour_filter"] is False


def test_measure_costs_skips_a_local_model_not_pulled(monkeypatch):
    """A frozen local captioner whose model is not pulled on this machine is reported unavailable, not fatal."""
    from shoebox import runners

    def resolve(name):
        raise RuntimeError(f"model {name} is not pulled in the local Ollama")

    monkeypatch.setattr(runners, "resolve", resolve)
    monkeypatch.setattr(runners, "load_candidates", lambda *a: {"e4b-bf16": {"runtime": "ollama"}})
    out = graded.measure_costs(None, ["e4b-bf16", "none"])
    assert out == {"e4b-bf16": {"local": True, "n": 0, "subscription": False, "usd_per_image": None,
                                "img_per_hour": None, "unavailable": "model e4b-bf16 is not pulled in the local Ollama"}}
