"""Known-item search eval (shoebox.evals.search) on tiny synthetic data: fake vectors, fake captions."""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from test_eval_helpers import write_jsonl

from shoebox import config
from shoebox import embed as embed_module
from shoebox.cli import main
from shoebox.evals import search

D = 768


def unit(*pairs, dim=D):
    """A unit vector with the given (index, value) entries."""
    v = np.zeros(dim, dtype=np.float32)
    for i, x in pairs:
        v[i] = x
    return v / np.linalg.norm(v)


def item(aid, group=None):
    return {"asset_id": aid, "source": "fake", "relpath": f"{aid}.jpg", "ext": "jpg", "bucket": "b",
            "group_id": group, "group_rule": "raw_jpeg_basename" if group else None}


def lab(aid, notes):
    return {"asset_id": aid, "medium": "digital", "digitiser": "camera", "colour_class": "colour", "notes": notes}


# --- queries ---------------------------------------------------------------------------------------

def test_queries_dedupe_and_group_aware_relevance():
    items = [item("a1", "g1"), item("a2", "g1"), item("b1"), item("c1", "g2"), item("c2", "g2"),
             item("d1"), item("e1", "g3"), item("e2", "g3"), item("f1")]
    labels = [
        lab("a1", "Bridge over canal"), lab("a2", "bridge  over canal "),  # a pair with one note -> one query
        lab("b1", "rose"),
        lab("c1", "Portrait"), lab("c2", "Portrait"),
        lab("d1", "portrait"),  # same text as group g2 (case differs) -> union
        lab("e1", "snowy house"), lab("e2", "snowy door"),  # one group, two notes -> two queries
        lab("f1", "  "),  # no note -> no query
    ]
    qb = search.build_queries(items, labels)
    by_text = {q["text"]: q for q in qb.queries}
    assert [q["text"] for q in qb.queries] == ["Bridge over canal", "rose", "Portrait", "snowy house", "snowy door"]
    assert by_text["Bridge over canal"]["relevant"] == ["a1", "a2"]
    assert by_text["rose"]["relevant"] == ["b1"]
    assert by_text["Portrait"]["relevant"] == ["c1", "c2", "d1"]
    assert by_text["snowy house"]["relevant"] == by_text["snowy door"]["relevant"] == ["e1", "e2"]
    # targets: one per capture group (a pair is one target); a merged note has one target per group
    assert by_text["Bridge over canal"]["relevant_groups"] == [["a1", "a2"]]
    assert by_text["rose"]["relevant_groups"] == [["b1"]]
    assert by_text["Portrait"]["relevant_groups"] == [["c1", "c2"], ["d1"]]
    assert qb.merged_groups == {by_text["Portrait"]["query_id"]: ["g2", "item:d1"]}
    assert qb.items_without_note == ["f1"]
    assert all(q["source"] == "owner_notes" for q in qb.queries)
    # the id comes from the normalised text: stable, and it does not reveal the text
    assert by_text["Portrait"]["query_id"] == search.query_id(" portrait ")
    assert all(q["query_id"].startswith("q-") and len(q["query_id"]) == 14 for q in qb.queries)


def test_cli_search_queries(tmp_path, capsys):
    sets = tmp_path / "sets"
    write_jsonl(sets / "s" / "items.jsonl", [item("a1", "g1"), item("a2", "g1"), item("b1")])
    write_jsonl(sets / "s" / "labels.jsonl", [lab("a1", "x y"), lab("a2", "x y"), lab("b1", "z")])
    assert main(["eval", "search-queries", "--set", "s", "--sets-dir", str(sets)]) == 0
    rows = [json.loads(line) for line in (sets / "s" / search.QUERIES_FILE).read_text().splitlines()]
    assert [r["relevant"] for r in rows] == [["a1", "a2"], ["b1"]]
    assert set(rows[0]) == {"query_id", "text", "relevant", "relevant_groups", "source"}
    assert rows[0]["relevant_groups"] == [["a1", "a2"]] and rows[1]["relevant_groups"] == [["b1"]]
    assert "2 queries" in capsys.readouterr().out


# --- ranking primitives ------------------------------------------------------------------------------

def test_rank_scores_is_deterministic_with_asset_id_tie_break():
    corpus = ["d", "c", "b", "a", "e"]
    scores = {"d": 0.5, "c": 0.9, "b": 0.5, "a": 0.5}  # e unscored
    ranking, s = search.rank_scores(corpus, scores)
    assert ranking == ["c", "a", "b", "d", "e"]
    assert s == [0.9, 0.5, 0.5, 0.5, None]
    assert search.rank_scores(list(reversed(corpus)), dict(reversed(scores.items()))) == (ranking, s)


def test_mrl_truncation_renormalises_and_drops_tail_dims():
    q = unit((0, 1.0), (300, 1.0))
    vecs = {"x": unit((0, 1.0), (1, 1.0)), "y": unit((0, 1.0), (400, 5.0))}
    full = search.cosine_scores(q, vecs, 768)
    short = search.cosine_scores(q, vecs, 256)
    assert full["x"] == pytest.approx(float(vecs["x"] @ q))
    # at 256 d the query is e0 and x is (e0+e1)/sqrt2, y is e0
    assert short["x"] == pytest.approx(1 / math.sqrt(2), abs=1e-6)
    assert short["y"] == pytest.approx(1.0, abs=1e-6)
    assert full["y"] < full["x"]  # the tail dims change the order
    t = search.truncate(vecs["y"], 256)
    assert t.shape == (256,) and float(np.linalg.norm(t)) == pytest.approx(1.0, abs=1e-6)


def test_blend_math_and_missing_caption_falls_back_to_image():
    img = {"a": 0.2, "b": 0.6, "c": 0.4}
    cap = {"a": 0.8, "b": 0.0}  # c has no caption vector
    assert search.blend_scores(img, cap, 0.5) == pytest.approx({"a": 0.5, "b": 0.3, "c": 0.4})
    assert search.blend_scores(img, cap, 0.3) == pytest.approx({"a": 0.62, "b": 0.18, "c": 0.4})
    assert "z" not in search.blend_scores(img, {"z": 1.0}, 0.5)  # no image vector -> unscored


def test_fts_query_construction():
    text = "Night scene in Venice, of the BRIDGE-over canal! Bridge (again) snake_case"
    assert search.fts_terms(text) == ["night", "scene", "venice", "bridge", "canal", "snake", "case"]
    assert search.fts_query(text) == '"night" OR "scene" OR "venice" OR "bridge" OR "canal" OR "snake" OR "case"'
    assert search.fts_query("of the and") == ""
    assert search.fts_terms("Café crème") == ["café", "crème"]


def test_fts_index_bm25_stemming_tags_and_missing_captions():
    docs = {
        "a": {"caption": "Two bridges over a canal at night.", "tags": ["venice"]},
        "b": {"caption": "A red rose in a garden.", "tags": ["flower", "garden"]},
        "c": {"caption": "", "tags": ["bridge"]},  # no caption: not indexed
        "d": {"caption": "A canal boat.", "tags": []},
    }
    idx = search.FtsIndex(docs)
    try:
        assert idx.n == 3
        s = idx.scores("bridge over the canal")  # porter: bridge ~ bridges
        assert set(s) == {"a", "d"} and s["a"] > s["d"] > 0
        assert set(idx.scores("Venice")) == {"a"}  # tags are searched
        assert idx.scores("the of") == {}
        assert idx.scores('quote" OR (paren') == {}  # FTS syntax in a query cannot break the MATCH
        ranking, scores = search.rank_scores(["a", "b", "c", "d"], idx.scores("garden flower"))
        assert ranking == ["b", "a", "c", "d"] and scores[1:] == [None, None, None]
    finally:
        idx.close()


def test_rrf_fusion():
    blend = (["a", "b", "c"], [0.9, 0.5, 0.1])
    fts = (["b", "a", "c"], [3.0, 1.0, None])  # c unmatched in fts: contributes nothing there
    s = search.rrf_scores([blend, fts], k=60)
    assert s["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert s["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert s["c"] == pytest.approx(1 / 63)
    # a == b: the stored order breaks the tie by asset_id (the metrics then score the tie symmetrically)
    assert search.rank_scores(["c", "b", "a"], s)[0] == ["a", "b", "c"]


def test_parse_route_and_defaults():
    r = search.parse_route("eg2-image@256")
    assert (r.kind, r.dim, r.caption_based) == ("eg2-image", 256, False)
    r = search.parse_route("eg2-blend@w0.3:g31b")
    assert (r.kind, r.cand, r.w, r.sensitivity) == ("eg2-blend", "g31b", 0.3, True)
    assert search.parse_route("hybrid:e4b-bf16").w == 0.5
    assert search.parse_route("eg2-blend:x").sensitivity is False
    assert search.parse_route("fts:gemini-low").dir_name == "fts_gemini-low"
    for bad in ("eg2-image@512", "fts@w0.3:x", "eg2-blend@w0.5:x", "bogus:x", "eg2-caption:"):
        with pytest.raises(ValueError):
            search.parse_route(bad)
    names = search.default_routes(["c1", "c2"])
    assert names[:2] == ["eg2-image@768", "eg2-image@256"] and len(names) == 2 + 2 * 6
    assert "eg2-blend@w0.7:c2" in names and len(search.default_routes(["c1"], sensitivity=False)) == 6


# --- metrics -----------------------------------------------------------------------------------------

def test_query_metrics_known_answers():
    m = search.query_metrics(["x", "a", "y", "b", "z"], ["a", "b"])
    assert m["rank_of_first_relevant"] == 2 and m["rr"] == 0.5
    assert m["hit@1"] == 0 and m["hit@5"] == 1 and m["hit@10"] == 1 and m["hit@20"] == 1
    assert m["recall@1"] == 0 and m["recall@5"] == 1.0
    assert m["tie_decided"] is False and m["rank_best"] == m["rank_worst"] == m["rank_expected"] == 2
    ideal = 1 + 1 / math.log2(3)
    assert m["ndcg@5"] == pytest.approx((1 / math.log2(3) + 1 / math.log2(5)) / ideal)
    assert m["ndcg@1"] == 0.0
    top = search.query_metrics(["a", "x"], ["a"])
    assert top["rr"] == 1.0 and top["ndcg@10"] == 1.0 and top["hit@1"] == 1
    none = search.query_metrics(["x", "y"], ["a"])
    assert none["rank_of_first_relevant"] is None and none["rr"] == 0.0 and none["hit@10"] == 0
    assert none["rank_expected"] is None and none["tie_decided"] is False
    deep = search.query_metrics([f"x{i}" for i in range(6)] + ["a"], ["a"])
    assert deep["rank_of_first_relevant"] == 7 and deep["hit@5"] == 0 and deep["hit@10"] == 1


def test_query_metrics_ties_are_scored_symmetrically():
    # an RRF-style exact tie at the top: the stored asset_id order puts the distractor first
    tie = 1 / 61 + 1 / 62
    m = search.query_metrics(["a", "b", "c"], ["b"], scores=[tie, 1 / 62 + 1 / 61, 0.01])
    assert m["rank_of_first_relevant"] == 2  # stored order (trace only)
    assert (m["rank_best"], m["rank_worst"], m["tie_decided"]) == (1, 2, True)
    assert m["rr"] == pytest.approx(0.75) and m["rank_expected"] == pytest.approx(1.5)
    assert m["hit@1"] == pytest.approx(0.5) and m["hit@5"] == 1.0
    assert m["ndcg@1"] == pytest.approx(0.5) and m["ndcg@5"] == pytest.approx(0.5 + 0.5 / math.log2(3))
    assert (m["rr_best"], m["rr_worst"]) == (1.0, 0.5)
    # swapping the tied pair in storage changes nothing
    swapped = search.query_metrics(["b", "a", "c"], ["b"], scores=[tie, tie, 0.01])
    assert {k: v for k, v in swapped.items() if k != "rank_of_first_relevant"} == pytest.approx(
        {k: v for k, v in m.items() if k != "rank_of_first_relevant"})
    # the unscored tail is one block: rank 3..6 equally likely
    tail = search.query_metrics(["x", "y", "a", "c", "d", "z"], ["d"], scores=[0.9, 0.5, None, None, None, None])
    assert (tail["rank_best"], tail["rank_worst"]) == (3, 6)
    assert tail["rank_expected"] == pytest.approx(4.5)
    assert tail["rr"] == pytest.approx((1 / 3 + 1 / 4 + 1 / 5 + 1 / 6) / 4)
    assert tail["hit@5"] == pytest.approx(0.75) and tail["hit@1"] == 0
    # two relevant items in one 4-item block: first-relevant offset j has P = C(3-j, 1) / C(4, 2)
    two = search.query_metrics(["x", "a", "b", "y"], ["a", "b"], scores=[0.5] * 4)
    assert two["rank_expected"] == pytest.approx(1 * 3 / 6 + 2 * 2 / 6 + 3 * 1 / 6)
    assert two["rr"] == pytest.approx(3 / 6 + (2 / 6) / 2 + (1 / 6) / 3)
    # no scores: the stored order counts, as before
    plain = search.query_metrics(["x", "a"], ["a"])
    assert plain["tie_decided"] is False and plain["rr"] == 0.5


def test_query_metrics_capture_group_is_one_target():
    # a DNG+JPG pair found at rank 1 (sibling at 4): perfect for a known-item query
    pair = search.query_metrics(["a1", "x", "y", "a2", "z"], ["a1", "a2"], groups=[["a1", "a2"]])
    assert pair["targets_n"] == 1 and pair["relevant_n"] == 2
    assert pair["ndcg@10"] == 1.0 and pair["recall@1"] == 1.0 and pair["rr"] == 1.0
    # without groups, each item is its own target: the old (penalised) behaviour
    flat = search.query_metrics(["a1", "x", "y", "a2", "z"], ["a1", "a2"])
    assert flat["ndcg@10"] < 1.0 and flat["recall@1"] == 0.5
    # a merged note over two different groups: two targets
    two = search.query_metrics(["c1", "x", "d1", "c2"], ["c1", "c2", "d1"], groups=[["c1", "c2"], ["d1"]])
    assert two["targets_n"] == 2 and two["recall@1"] == 0.5 and two["recall@5"] == 1.0
    assert two["ndcg@5"] == pytest.approx((1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3)))
    with pytest.raises(ValueError, match="partition"):
        search.query_metrics(["a1"], ["a1", "a2"], groups=[["a1"]])


def test_query_metrics_pool_coverage():
    m = search.query_metrics(["a", "x", "y", "z"], ["a"], ks=(1, 5))
    assert m["judged@1"] == 1.0 and m["judged@5"] == pytest.approx(1 / 4)  # top 5 of a 4-item corpus
    # a relevant item tied across the top-1 cut counts by its probability of being inside it
    t = search.query_metrics(["x", "a", "y"], ["a"], scores=[0.5, 0.5, 0.1], ks=(1,))
    assert t["judged@1"] == pytest.approx(0.5)


def test_bootstrap_is_reproducible():
    rr = [1.0, 0.5, 0.25, 1.0, 0.2, 0.1, 1.0, 0.333]
    a, b = search.bootstrap_ci(rr), search.bootstrap_ci(rr)
    assert a == b
    assert a[0] <= sum(rr) / len(rr) <= a[1]
    assert search.bootstrap_ci(rr, seed=1) != a
    assert search.bootstrap_ci([0.5] * 5) == (0.5, 0.5)
    assert search.bootstrap_ci([]) is None


def test_sign_test_and_paired():
    assert search.sign_test(5, 0) == pytest.approx(0.0625)
    assert search.sign_test(0, 0) == 1.0 and search.sign_test(3, 3) == 1.0
    assert search.sign_test(9, 1) == pytest.approx(2 * 11 / 1024)
    p = search.paired({"q1": 1.0, "q2": 0.5, "q3": 0.2, "q4": 1.0}, {"q1": 0.5, "q2": 0.5, "q3": 1.0, "q5": 1.0})
    assert (p["n"], p["wins"], p["losses"], p["ties"]) == (3, 1, 1, 1)
    assert p["mean_rr_delta"] == pytest.approx((0.5 + 0 - 0.8) / 3)


def test_paired_bootstrap_recovers_a_known_delta_and_pairing_matters():
    rng = np.random.default_rng(7)
    a_vals = rng.uniform(0, 1, 60)
    a = {f"q{i}": float(v) for i, v in enumerate(a_vals)}
    b = {q: v + 0.1 + float(rng.normal(0, 0.01)) for q, v in a.items()}
    pb = search.paired_bootstrap(b, a)
    lo, hi = pb["delta_ci95"]
    assert pb["n"] == 60 and lo < 0.1 < hi and lo > 0
    assert pb["mean_delta"] == pytest.approx(sum(b[q] - a[q] for q in a) / 60)
    # exact shift: the paired CI collapses; independent resampling of the two routes does not
    exact = search.paired_bootstrap({q: v + 0.1 for q, v in a.items()}, a)
    assert exact["delta_ci95"][1] - exact["delta_ci95"][0] == pytest.approx(0, abs=1e-9)
    r = np.random.default_rng(search.BOOTSTRAP_SEED)
    va, vb = np.array(list(a.values())), np.array([v + 0.1 for v in a.values()])
    ia = r.integers(0, 60, size=(1000, 60))
    ib = r.integers(0, 60, size=(1000, 60))
    d = vb[ib].mean(axis=1) - va[ia].mean(axis=1)
    unpaired = np.percentile(d, 97.5) - np.percentile(d, 2.5)
    assert unpaired > 0.05 and (hi - lo) < unpaired


def test_paired_bootstrap_reproducible_and_seeded():
    a = {f"q{i}": i / 10 for i in range(10)}
    b = {f"q{i}": ((i * 7) % 10) / 10 for i in range(10)}
    assert search.paired_bootstrap(a, b) == search.paired_bootstrap(a, b)
    assert search.paired_bootstrap(a, b, seed=1)["delta_ci95"] != search.paired_bootstrap(a, b)["delta_ci95"]


def test_paired_bootstrap_uses_shared_queries_only():
    pb = search.paired_bootstrap({"q1": 1.0, "q2": 0.5, "x": 100.0}, {"q1": 0.5, "q2": 0.5, "y": -100.0})
    assert pb["n"] == 2 and pb["mean_delta"] == pytest.approx(0.25)
    assert -0.0001 <= pb["delta_ci95"][0] and pb["delta_ci95"][1] <= 0.5 + 1e-9
    assert search.paired_bootstrap({"a": 1.0}, {"b": 1.0}) is None
    assert search.paired_bootstrap({}, {}) is None


def test_minimum_detectable_difference():
    deltas = [0.0, 1.0] * 50
    sd = float(np.std(deltas, ddof=1))
    assert search.mdd(deltas) == pytest.approx(2.80 * sd / math.sqrt(100), rel=1e-3)
    assert search.mdd([0.3] * 4) == 0.0
    assert search.mdd([0.3]) is None and search.mdd([]) is None
    pb = search.paired_bootstrap({f"q{i}": float(i % 2) for i in range(100)}, {f"q{i}": 0.0 for i in range(100)})
    assert pb["mdd"] == pytest.approx(search.mdd(deltas), rel=1e-9)


# --- routes over synthetic sources -------------------------------------------------------------------

CORPUS = ["a1", "a2", "b1", "c1"]  # a1/a2 are one capture group
QUERIES = [
    {"query_id": "q-bridge", "text": "bridge over canal", "relevant": ["a1", "a2"], "source": "owner_notes"},
    {"query_id": "q-rose", "text": "red rose", "relevant": ["b1"], "source": "owner_notes"},
    {"query_id": "q-dog", "text": "small dog", "relevant": ["c1"], "source": "owner_notes"},
]


def fake_sources(set_name="s", with_caption_vecs=True) -> search.Sources:
    qvec = {"bridge over canal": unit((0, 1.0)), "red rose": unit((1, 1.0)), "small dog": unit((2, 1.0), (500, 1.0))}
    image = {"a1": unit((0, 1.0), (3, 0.2)), "a2": unit((0, 0.9), (1, 0.3)), "b1": unit((3, 1.0)),
             "c1": unit((2, 1.0))}
    src = search.Sources(set_name, CORPUS, QUERIES, "sha-q", lambda t: qvec[t],
                         {"candidate": "eg2", "model_id": "m/eg2", "params_hash": "img", "query_prompt": "q: {text}"},
                         image)
    src.labels["cA"] = {"a1": {"caption": "Bridges over a canal.", "tags": ["venice"]},
                        "b1": {"caption": "A red rose.", "tags": ["flower"]}}  # a2, c1: no caption
    src.label_source["cA"] = {"candidate": "cA", "model_id": "m/cA", "params_hash": "lab"}
    if with_caption_vecs:
        src.caption_vecs["cA"] = {"a1": unit((0, 1.0)), "b1": unit((1, 1.0))}
        src.caption_store["cA"] = {"model_id": "m/eg2", "params_hash": "cap"}
    return src


def fake_cfg(tmp_path):
    root = tmp_path / "root"
    p = tmp_path / "shoebox.toml"
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    p.write_text(f'shoebox_root = "{root}"\n\n[sources.fake]\nroot = "{src}"\nperson = "o"\n'
                 "synced = false\nhosted_consent = false\n")
    return config.load(p), p


def test_routes_rank_whole_corpus():
    src = fake_sources()
    rows = {r: {x["query_id"]: x for x in search.run_route(search.parse_route(r), src)}
            for r in ["eg2-image@768", "eg2-image@256", "eg2-caption:cA", "eg2-blend:cA", "fts:cA", "hybrid:cA"]}
    for by_q in rows.values():
        assert all(sorted(x["ranking"]) == CORPUS and len(x["scores"]) == 4 for x in by_q.values())
    assert rows["eg2-image@768"]["q-bridge"]["ranking"][:2] == ["a1", "a2"]
    # caption-only: items without a caption vector are unscored and last, by asset_id
    cap = rows["eg2-caption:cA"]["q-rose"]
    assert cap["ranking"] == ["b1", "a1", "a2", "c1"] and cap["scores"][2:] == [None, None]
    # fts: only captioned items that match
    assert rows["fts:cA"]["q-dog"]["scores"] == [None] * 4 and rows["fts:cA"]["q-dog"]["ranking"] == CORPUS
    assert rows["fts:cA"]["q-bridge"]["ranking"][0] == "a1"
    # blend for c1 (no caption) is its image score
    blend = rows["eg2-blend:cA"]["q-dog"]
    assert blend["scores"][blend["ranking"].index("c1")] == pytest.approx(
        rows["eg2-image@768"]["q-dog"]["scores"][rows["eg2-image@768"]["q-dog"]["ranking"].index("c1")])
    # MRL: the query for "small dog" has weight in dim 500, dropped at 256
    s256 = rows["eg2-image@256"]["q-dog"]
    assert s256["ranking"][0] == "c1" and s256["scores"][0] == pytest.approx(1.0, abs=1e-6)
    hyb = rows["hybrid:cA"]["q-bridge"]
    assert hyb["ranking"][0] == "a1" and hyb["scores"][0] == pytest.approx(2 / 61)


def test_run_routes_writes_store_deterministically(tmp_path):
    cfg, _ = fake_cfg(tmp_path)
    names = ["eg2-image@768", "eg2-blend:cA", "eg2-blend@w0.3:cA", "fts:cA", "hybrid:cA", "eg2-caption:cB"]
    routes = [search.parse_route(n) for n in names]
    res = search.run_routes(cfg, fake_sources(), routes)
    assert set(res.written) == set(names[:-1])
    assert "no EG2 caption embeddings for cB" in res.skipped["eg2-caption:cB"]
    src = fake_sources(with_caption_vecs=False)
    src.labels["cA"] = {}
    assert "no captions" in src.missing(search.parse_route("fts:cA"))
    assert "no EG2 caption embeddings" in src.missing(search.parse_route("eg2-blend:cA"))
    assert src.missing(search.parse_route("eg2-image@256")) is None
    e = res.written["hybrid:cA"]
    path = tmp_path / "root" / "results" / "search" / "s" / "hybrid_cA" / f"{e['params_hash']}.jsonl"
    assert e["path"] == str(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["query_id"] for r in rows] == ["q-bridge", "q-rose", "q-dog"]
    assert set(rows[0]) == {"query_id", "route", "ranking", "scores"} and rows[0]["route"] == "hybrid:cA"
    params = json.loads(path.with_name(f"{e['params_hash']}.params.json").read_text())
    d = params["definition"]
    assert d["w"] == 0.5 and d["rrf_k"] == 60 and d["eg2"]["params_hash"] == "img"
    assert d["caption_embeddings"]["params_hash"] == "cap" and d["caption_source"]["params_hash"] == "lab"
    assert params["coverage"] == {"corpus_n": 4, "missing_image_vectors": 0, "missing_caption_vectors": 2,
                                  "missing_captions": 2}
    first = path.read_bytes()
    res2 = search.run_routes(cfg, fake_sources(), routes)
    assert res2.written["hybrid:cA"]["params_hash"] == e["params_hash"] and path.read_bytes() == first
    # w and k are hashed: the sensitivity blend has its own hash
    assert res.written["eg2-blend@w0.3:cA"]["params_hash"] != res.written["eg2-blend:cA"]["params_hash"]
    index = json.loads(res.index_path.read_text())
    assert set(index["routes"]) == set(names[:-1]) and index["set"] == "s"


def test_generate_spec_and_stale_queries(tmp_path):
    cfg, _ = fake_cfg(tmp_path)
    sets = tmp_path / "sets"
    qpath = sets / "s" / search.QUERIES_FILE
    write_jsonl(qpath, QUERIES)
    src = fake_sources()
    src.queries_sha256 = search.file_sha256(qpath)
    search.run_routes(cfg, src, [search.parse_route("eg2-image@768"), search.parse_route("fts:cA")])
    out = tmp_path / "gen"
    res = search.generate_spec(cfg, "s", ["eg2-image@768", "fts:cA"], sets_dir=sets, out_dir=out)
    spec = res.spec_path.read_text()
    assert spec.startswith("# generated — do not edit") and res.stimuli == 3
    assert "name: shoebox-search" in spec and 'name: "q-rose"' in spec and 'prompt: "red rose"' in spec
    assert '{"relevant": ["a1", "a2"], "k": [1, 5, 10, 20]}' in spec and '"relevant_n": "2"' in spec
    grouped = search.build_spec("e", "s", [{**QUERIES[0], "relevant_groups": [["a1", "a2"]]}], out / "r.json")
    assert '{"relevant": ["a1", "a2"], "k": [1, 5, 10, 20], "groups": [["a1", "a2"]]}' in grouped
    assert '"targets_n": "1"' in grouped
    doc = json.loads(res.routes_path.read_text())
    assert doc["query_ids"] == ["q-bridge", "q-rose", "q-dog"] and set(doc["routes"]) == {"eg2-image@768", "fts:cA"}
    with pytest.raises(ValueError, match="no rankings"):
        search.generate_spec(cfg, "s", ["hybrid:cA"], sets_dir=sets, out_dir=out)
    write_jsonl(qpath, QUERIES[:2])  # the queries changed after search-run
    with pytest.raises(ValueError, match="other queries"):
        search.generate_spec(cfg, "s", ["eg2-image@768"], sets_dir=sets, out_dir=out)


# --- report ------------------------------------------------------------------------------------------

def _trial(route, qid, ranking, routes_file, ph="p", scores=None, metadata=None):
    out = json.dumps({"query_id": qid, "route": route, "ranking": ranking, "scores": scores})
    m = metadata or search.query_metrics(ranking, {"q1": ["a"], "q2": ["b"], "q3": ["c", "d"]}[qid], scores=scores)
    ev = {"type": "custom", "data": {"shoebox": {"kind": "search", "set": "s", "routes_file": str(routes_file),
                                                  "route": route, "query_id": qid, "params_hash": ph}}}
    return {"type": "trial-result", "model": route, "stimulus": qid, "status": "success",
            "trajectory": {"events": [ev], "output": out},
            "gradeResult": {"details": [{"name": "retrieval", "graderType": "retrieval", "passed": m["hit@5"],
                                         "metadata": m}]}}


def test_report_metrics_comparison_and_no_text(tmp_path):
    qpath = tmp_path / "queries.jsonl"
    queries = [{"query_id": "q1", "text": "secret one", "relevant": ["a"], "source": "owner_notes"},
               {"query_id": "q2", "text": "secret two", "relevant": ["b"], "source": "owner_notes"},
               {"query_id": "q3", "text": "secret three", "relevant": ["c", "d"], "source": "owner_notes"}]
    write_jsonl(qpath, queries)
    routes = {n: {"params_hash": "p", "coverage": {"corpus_n": 6, "missing_captions": 1}}
              for n in ["eg2-image@768", "fts:cA", "hybrid:cA", "eg2-blend:cA", "eg2-caption:cA", "eg2-blend@w0.3:cA"]}
    rfile = tmp_path / "routes.json"
    rfile.write_text(json.dumps({"set": "s", "eval": "s-search", "queries_file": str(qpath),
                                 "queries_sha256": search.file_sha256(qpath), "query_ids": ["q1", "q2", "q3"],
                                 "routes": routes}))
    rank = {  # route -> {query -> ranking}
        "eg2-image@768": {"q1": ["x", "a"], "q2": ["x", "y", "b"], "q3": ["c", "d"]},  # rr 0.5, 1/3, 1
        "hybrid:cA": {"q1": ["a"], "q2": ["b"], "q3": ["x", "d"]},  # rr 1, 1, 0.5
        "fts:cA": {"q1": ["a"], "q2": ["x", "b"], "q3": ["x", "y", "z", "w", "v", "u", "c"]},
        "eg2-blend:cA": {"q1": ["a"], "q2": ["b"], "q3": ["c"]},  # perfect
        "eg2-caption:cA": {"q1": ["x", "a"], "q2": ["b"], "q3": ["c"]},
        "eg2-blend@w0.3:cA": {"q1": ["a"], "q2": ["b"], "q3": ["c"]},
    }
    lines = [_trial(r, q, rk, rfile) for r, by_q in rank.items() for q, rk in by_q.items()]
    lines.append({"type": "run-summary"})
    res = tmp_path / "results.jsonl"
    write_jsonl(res, lines)
    jpath, mpath, rep = search.write_report(res, tmp_path / "runs", "2026-10-10")
    assert jpath.name == "2026-10-10_s-search.json" and mpath.name == "2026-10-10_s-search.md"
    base = rep["routes"]["eg2-image@768"]
    assert base["mrr"] == pytest.approx((0.5 + 1 / 3 + 1) / 3)
    assert base["hit@1"] == pytest.approx(1 / 3) and base["hit@5"] == 1.0
    assert base["mean_rank"] == pytest.approx(2.0) and base["median_rank"] == 2
    assert base["mrr_ci95"][0] <= base["mrr"] <= base["mrr_ci95"][1]
    assert base["ndcg@20"] >= base["ndcg@10"] and "recall@20" in base and base["tie_decided_queries"] == 0
    assert base["pool_coverage"]["judged@1"] == pytest.approx(1 / 3)
    assert set(base["per_query"]["q3"]) >= {"recall@1", "recall@20", "ndcg@20", "rank_best", "rank_worst"}
    fts = rep["routes"]["fts:cA"]
    assert fts["hit@5"] == pytest.approx(2 / 3) and fts["hit@10"] == 1.0
    # the sensitivity blend ties the primary blend but is not a contender; ties go to the first name
    assert rep["best_caption_route"] == "eg2-blend:cA"
    cmp = rep["best_vs_baseline"]
    assert (cmp["wins"], cmp["losses"], cmp["ties"]) == (2, 0, 1) and cmp["sign_test_p"] == pytest.approx(0.5)
    row = rep["candidates"]["cA"]
    assert row["blend"]["mrr"] == 1.0 and row["missing_captions"] == 1
    assert row["mean_mrr"] == pytest.approx((rep["routes"]["eg2-caption:cA"]["mrr"] + fts["mrr"] + 1.0
                                             + rep["routes"]["hybrid:cA"]["mrr"]) / 4)
    assert row["blend_sensitivity_mrr"] == {"0.3": 1.0, "0.5": 1.0, "0.7": None}
    assert rep["complete"] is True and rep["blockers"] == []
    for p in (jpath, mpath):
        text = p.read_text()
        assert "secret" not in text  # no query text in reports
    md = mpath.read_text()
    assert "not tuned on held-out data" in md and "eg2-blend@w0.3:cA (sensitivity)" in md
    assert "## Pool coverage" in md and "nDCG@20" in md and "100%" not in md
    for k in ("rr_delta_ci95", "rr_mdd", "ndcg@20_delta", "ndcg@20_delta_ci95", "ndcg@20_mdd", "mean_rr_delta"):
        assert k in cmp
    assert cmp["rr_delta_ci95"][0] <= cmp["mean_rr_delta"] <= cmp["rr_delta_ci95"][1]
    lo, hi = cmp["rr_delta_ci95"]
    assert f"{lo:.3f}–{hi:.3f}" in md and f"{cmp['rr_mdd']:.3f}" in md
    nlo, nhi = cmp["ndcg@20_delta_ci95"]
    assert f"{nlo:.3f}–{nhi:.3f}" in md and f"{cmp['ndcg@20_mdd']:.3f}" in md
    assert "paired bootstrap" in md and "for information only" in md and "MDD" in md


def test_report_blockers_for_missing_and_disagreeing_trials(tmp_path):
    qpath = tmp_path / "queries.jsonl"
    write_jsonl(qpath, [{"query_id": "q1", "text": "t", "relevant": ["a"]},
                        {"query_id": "q2", "text": "u", "relevant": ["b"]}])
    rfile = tmp_path / "routes.json"
    rfile.write_text(json.dumps({"set": "s", "queries_file": str(qpath), "queries_sha256": search.file_sha256(qpath),
                                 "query_ids": ["q1", "q2"], "routes": {"eg2-image@768": {"params_hash": "p"}}}))
    good = _trial("eg2-image@768", "q1", ["a"], rfile)
    good["gradeResult"]["details"][0]["metadata"]["rr"] = 0.5  # grader disagrees with Python
    missing = {"type": "trial-result", "model": "eg2-image@768", "stimulus": "q2", "status": "error",
               "error": f"shoebox-search replay: missing key (route=eg2-image@768, query_id=q2, params_hash=p) in x "
                        f"[set=s, routes_file={rfile}]"}
    res = tmp_path / "r.jsonl"
    write_jsonl(res, [good, missing])
    rep = search.build_report(search.load_trials(res), res, "2026-10-10")
    r = rep["routes"]["eg2-image@768"]
    assert r["status_counts"] == {"ok": 1, "missing": 1} and r["not_run"] == ["q2"]
    assert rep["complete"] is False
    assert any("no usable trial for 1 of 2" in b for b in rep["blockers"])
    assert any("disagrees" in b for b in rep["blockers"])


def test_report_scores_ties_and_flags_a_tie_unaware_grader(tmp_path):
    qpath = tmp_path / "queries.jsonl"
    write_jsonl(qpath, [{"query_id": "q1", "text": "t", "relevant": ["a"], "relevant_groups": [["a"]]},
                        {"query_id": "q2", "text": "u", "relevant": ["b"], "relevant_groups": [["b"]]}])
    rfile = tmp_path / "routes.json"
    rfile.write_text(json.dumps({"set": "s", "queries_file": str(qpath), "queries_sha256": search.file_sha256(qpath),
                                 "query_ids": ["q1", "q2"], "routes": {"hybrid:cA": {"params_hash": "p"}}}))
    tie = 1 / 61 + 1 / 62
    trials = [_trial("hybrid:cA", "q1", ["a", "x", "y"], rfile, scores=[tie, tie, 0.1]),  # won a tie
              _trial("hybrid:cA", "q2", ["x", "b", "y"], rfile, scores=[tie, tie, 0.1])]  # lost a tie
    res = tmp_path / "r.jsonl"
    write_jsonl(res, trials)
    rep = search.build_report(search.load_trials(res), res, "2026-10-10")
    r = rep["routes"]["hybrid:cA"]
    assert rep["complete"] is True, rep["blockers"]
    assert r["mrr"] == pytest.approx(0.75) and r["hit@1"] == pytest.approx(0.5)  # the asset_id order is moot
    assert (r["mrr_worst"], r["mrr_best"], r["tie_decided_queries"]) == (0.5, 1.0, 2)
    assert r["per_query"]["q1"]["rr"] == r["per_query"]["q2"]["rr"] == pytest.approx(0.75)
    # a grader that scored the stored order (rr 1.0 for q1) disagrees with the tie-aware metrics: blocker
    stale = search.query_metrics(["a", "x", "y"], ["a"])
    write_jsonl(res, [_trial("hybrid:cA", "q1", ["a", "x", "y"], rfile, scores=[tie, tie, 0.1], metadata=stale),
                      trials[1]])
    rep = search.build_report(search.load_trials(res), res, "2026-10-10")
    assert rep["complete"] is False and any("disagrees" in b for b in rep["blockers"])


# --- distractor pools: corpus, exclusions, diagnostics -------------------------------------------------

def _pool_set(sets, exclude=None):
    """A pool-style set: targets a1/a2 (one group), b1, c1 plus distractors x1..x3; no labels.jsonl."""
    items = [{**item(a, g), "role": "target"} for a, g in (("a1", "g1"), ("a2", "g1"), ("b1", None), ("c1", None))]
    items += [{**item(x), "bucket": "distractor", "role": "distractor"} for x in ("x1", "x2", "x3")]
    write_jsonl(sets / "p" / "items.jsonl", items)
    write_jsonl(sets / "p" / search.QUERIES_FILE, QUERIES)
    if exclude is not None:
        write_jsonl(sets / "p" / search.EXCLUDE_FILE, exclude)
    return sets / "p"


def _excl(aid):
    return {"asset_id": aid, "reason": "near_duplicate_of_target", "decided_by": "owner", "ts": "2026-10-10"}


def test_load_corpus_with_and_without_exclusions(tmp_path):
    set_dir = _pool_set(tmp_path / "sets")
    corpus, excl = search.load_corpus(set_dir, QUERIES)
    assert corpus == ["a1", "a2", "b1", "c1", "x1", "x2", "x3"] and excl is None
    assert search.corpus_info(corpus, QUERIES, excl) == {"n": 7, "targets": 4, "distractors": 3, "excluded": 0}
    set_dir = _pool_set(tmp_path / "sets2", exclude=[_excl("x2")])
    corpus, excl = search.load_corpus(set_dir, QUERIES)
    assert corpus == ["a1", "a2", "b1", "c1", "x1", "x3"]
    assert excl == {"file": search.EXCLUDE_FILE, "n": 1,
                    "sha256": search.file_sha256(set_dir / search.EXCLUDE_FILE)}
    assert search.corpus_info(corpus, QUERIES, excl) == {"n": 6, "targets": 4, "distractors": 2, "excluded": 1}
    bad = _pool_set(tmp_path / "sets3", exclude=[_excl("x1"), _excl("a2")])  # a2 is relevant to q-bridge
    with pytest.raises(ValueError, match="relevant"):
        search.load_corpus(bad, QUERIES)
    junk = _pool_set(tmp_path / "sets4", exclude=[{"reason": "no id"}])
    with pytest.raises(ValueError, match="asset_id"):
        search.load_corpus(junk, QUERIES)


def test_exclusions_change_the_params_hash_only_when_present(tmp_path):
    src = fake_sources()
    r = search.parse_route("eg2-image@768")
    plain = search.route_definition(r, src)
    assert "exclude" not in plain  # sets without exclude.jsonl keep their old hashes
    src.exclude = {"file": search.EXCLUDE_FILE, "n": 1, "sha256": "abc"}
    d = search.route_definition(r, src)
    assert d["exclude"] == src.exclude
    assert search.store.params_hash(d) != search.store.params_hash(plain)
    src.exclude = {"file": search.EXCLUDE_FILE, "n": 1, "sha256": "abd"}
    assert search.store.params_hash(search.route_definition(r, src)) != search.store.params_hash(d)


def _fake_image_store(monkeypatch, vectors):
    class Res:
        model_id, params_hash, cand = "m/eg2", "img", {"query_prompt": "q: {text}"}

    monkeypatch.setattr(search, "load_image_store", lambda cfg, cand: (Res, vectors))
    qvec = {"bridge over canal": unit((0, 1.0)), "red rose": unit((1, 1.0)), "small dog": unit((2, 1.0))}
    monkeypatch.setattr(embed_module, "embed_query", lambda cand, text, dim: qvec[text])


def test_search_run_on_a_pool_set_without_labels(tmp_path, monkeypatch, capsys):
    cfg, toml = fake_cfg(tmp_path)
    monkeypatch.setenv("SHOEBOX_CONFIG", str(toml))
    sets = tmp_path / "sets"
    set_dir = _pool_set(sets, exclude=[_excl("x3")])
    assert not (set_dir / "labels.jsonl").exists()
    vectors = {"a1": unit((0, 1.0), (3, 0.2)), "a2": unit((0, 0.9), (1, 0.3)), "b1": unit((1, 0.5), (3, 1.0)),
               "c1": unit((2, 1.0)), "x1": unit((1, 1.0)), "x2": unit((5, 1.0)),
               "x3": unit((0, 1.0)), "zz": unit((0, 1.0))}  # x3 excluded, zz not in the set
    _fake_image_store(monkeypatch, vectors)
    rc = main(["eval", "search-run", "--set", "p", "--routes", "eg2-image@768", "--sets-dir", str(sets)])
    assert rc == 0, capsys.readouterr().err
    index = json.loads((search.search_dir(cfg, "p") / "index.json").read_text())
    e = index["routes"]["eg2-image@768"]
    assert e["corpus"] == {"n": 6, "targets": 4, "distractors": 2, "excluded": 1}
    rows = search.read_jsonl(Path(e["path"]))
    assert all(sorted(r["ranking"]) == ["a1", "a2", "b1", "c1", "x1", "x2"] for r in rows)
    params = json.loads(Path(e["path"]).with_name(f"{e['params_hash']}.params.json").read_text())
    assert params["definition"]["exclude"]["n"] == 1
    # diagnostics: the target's rank and what is ranked above it (top 10)
    diag = {d["query_id"]: d for d in search.read_jsonl(Path(e["diagnostics"]))}
    assert diag["q-rose"]["rank_best"] == 2 and [a["asset_id"] for a in diag["q-rose"]["above"]] == ["x1"]
    assert diag["q-bridge"]["above"] == [] and diag["q-bridge"]["rank_expected"] == 1
    assert set(diag["q-rose"]) == {"query_id", "route", "rank_best", "rank_worst", "rank_expected", "above"}
    assert "red rose" not in Path(e["diagnostics"]).read_text()


def test_diagnostics_cap_at_ten_and_handle_unranked():
    ranking = [f"x{i:02d}" for i in range(15)] + ["t"]
    d = search.query_diagnostics("q", "r", ranking, [1.0 - i / 100 for i in range(16)], ["t"])
    assert d["rank_best"] == 16 and [a["asset_id"] for a in d["above"]] == ranking[:10]
    assert d["above"][0]["score"] == 1.0
    none = search.query_diagnostics("q", "r", ["x"], [None], ["t"])
    assert none["rank_best"] is None and none["above"] == [{"asset_id": "x", "score": None}]


def test_report_header_shows_corpus_and_above_target(tmp_path):
    qpath = tmp_path / "queries.jsonl"
    write_jsonl(qpath, [{"query_id": "q1", "text": "t", "relevant": ["a"]}])
    rfile = tmp_path / "routes.json"
    rfile.write_text(json.dumps({"set": "s", "queries_file": str(qpath), "queries_sha256": search.file_sha256(qpath),
                                 "query_ids": ["q1"], "routes": {"eg2-image@768": {"params_hash": "p"}},
                                 "corpus": {"n": 50, "targets": 4, "distractors": 46, "excluded": 2}}))
    res = tmp_path / "r.jsonl"
    write_jsonl(res, [_trial("eg2-image@768", "q1", ["x", "y", "a"], rfile)])
    _, mpath, rep = search.write_report(res, tmp_path / "runs", "2026-10-10")
    assert rep["corpus"] == {"n": 50, "targets": 4, "distractors": 46, "excluded": 2}
    assert "- Corpus: 50 items (4 targets, 46 distractors); excluded: 2" in mpath.read_text()
    assert rep["routes"]["eg2-image@768"]["per_query"]["q1"]["above_target"] == ["x", "y"]


def test_generate_spec_records_corpus(tmp_path):
    cfg, _ = fake_cfg(tmp_path)
    sets = tmp_path / "sets"
    set_dir = _pool_set(sets, exclude=[_excl("x1")])
    src = fake_sources(set_name="p")
    src.queries_sha256 = search.file_sha256(set_dir / search.QUERIES_FILE)
    search.run_routes(cfg, src, [search.parse_route("eg2-image@768")])
    res = search.generate_spec(cfg, "p", ["eg2-image@768"], sets_dir=sets, out_dir=tmp_path / "gen")
    doc = json.loads(res.routes_path.read_text())
    assert doc["corpus"] == {"n": 6, "targets": 4, "distractors": 2, "excluded": 1}


def test_load_sources_keeps_only_corpus_items_from_the_stores(tmp_path, monkeypatch):
    """Label/vector stores hold other sets' items (and excluded ones): they must not score or sway FTS."""
    import shoebox.commands.embed as embed_cmd
    from shoebox import runners

    cfg, _ = fake_cfg(tmp_path)
    set_dir = _pool_set(tmp_path / "sets", exclude=[_excl("x3")])
    _fake_image_store(monkeypatch, {a: unit((0, 1.0)) for a in ("a1", "x3", "zz")})

    class R:
        name, model_id, params_hash, kind, retry = "c", "m/x", "ph", "labels", None

        def store_dir(self, cfg):
            return tmp_path / "none"

    monkeypatch.setattr(runners, "resolve", lambda cand: R())
    monkeypatch.setattr(embed_cmd, "resolve", lambda cand, cap: (R(), None))
    docs = {a: {"caption": "bridge", "tags": []} for a in ("a1", "x1", "x3", "zz")}
    monkeypatch.setattr(search, "_label_docs", lambda cfg, source: docs)
    src = search.load_sources(cfg, "p", [search.parse_route("fts:cA")], sets_dir=set_dir.parent, log=lambda m: None)
    assert set(src.image) == {"a1"} and set(src.labels["cA"]) == {"a1", "x1"}
    assert src.exclude["n"] == 1 and "x3" not in src.corpus


# --- eval-1k routes: metadata, fusion, filters, expansion (tasks 5.1-5.3) ------------------------------

def frow(date=None, kind="exif", colour="colour", make=None, model=None, software=None):
    return {"date": date, "date_kind": kind, "colour_guess": colour, "make": make, "model": model,
            "software": software}


def test_meta_tokens_split_folders_drop_camera_codes_and_date_from_exif_only():
    toks = search.meta_tokens("Holidays/Italy2007/Zermatt-rope-park/IMG_1234.JPG", frow("2007-08-12", "exif"))
    assert toks[:6] == ["holidays", "italy", "2007", "zermatt", "rope", "park"]
    assert "img" not in toks and "1234" not in toks
    assert "2007" in toks and "august" in toks
    toks = search.meta_tokens("famPhotos/SummerBBQ_party/DSC01234 granny.jpg", frow("2019-07-01", "mtime"))
    assert toks[:5] == ["fam", "photos", "summer", "bbq", "party"]
    assert "dsc01234" not in toks and "granny" in toks
    assert "2019" not in toks and "july" not in toks  # an mtime date is not a capture date
    toks = search.meta_tokens("x/20070812_153000_beach.jpg", frow("2007-08-12", "filename"))
    assert "20070812" not in toks and "153000" not in toks and "beach" in toks and "august" in toks
    toks = search.meta_tokens("scans/roll_3/frame.tif", frow("2015-01-01", "scan", make="Nikon", model="LS-5000"))
    assert toks[-1] == "scan" and "2015" not in toks and "january" not in toks
    assert "the" not in search.meta_tokens("the/of/garden.jpg", frow())


def meta_sources():
    src = fake_sources()
    known = src.embed_query
    src.embed_query = lambda t: known(t) if t in ("bridge over canal", "red rose", "small dog") else unit((0, 1.0))
    src.corpus = ["a1", "a2", "b1", "c1", "x1"]
    src.image["x1"] = unit((4, 1.0))
    src.items = {"a1": {"relpath": "2005/Venice-trip/IMG_0001.jpg"}, "a2": {"relpath": "2005/Venice-trip/IMG_0002.jpg"},
                 "b1": {"relpath": "garden/roses/DSC0101.jpg"}, "c1": {"relpath": "pets/rexTheDog/P1010023.jpg"},
                 "x1": {"relpath": "scans/neg/000123.tif"}}
    src.frame = {"a1": frow("2005-05-01"), "a2": frow("2005-05-01"), "b1": frow("2018-06-01", colour="mono"),
                 "c1": frow(None, kind=None), "x1": frow("1975-01-01", "filename", make="Nikon", model="LS-5000",
                                                          colour="mono")}
    return src


def test_meta_route_ranks_a_folder_name_match_first():
    src = meta_sources()
    q = {"query_id": "q", "text": "photos of rex the dog", "relevant": []}
    ranking, scores = search.rank_scores(src.corpus, search.route_scores(search.parse_route("meta"), q, src))
    assert ranking[0] == "c1" and scores[1] is None
    q2 = {"query_id": "q2", "text": "venice in may", "relevant": []}
    r2, _ = search.rank_scores(src.corpus, search.route_scores(search.parse_route("meta"), q2, src))
    assert set(r2[:2]) == {"a1", "a2"}
    assert src.missing(search.parse_route("meta")) is None
    bare = fake_sources()
    assert "frame" in bare.missing(search.parse_route("meta"))


def test_image_meta_and_hybrid_meta_fuse():
    src = meta_sources()
    q = {"query_id": "q-dog", "text": "small dog", "relevant": ["c1"]}
    img = search.rank_scores(src.corpus, search.route_scores(search.parse_route("eg2-image@768"), q, src))
    meta = search.rank_scores(src.corpus, search.route_scores(search.parse_route("meta"), q, src))
    fused = search.route_scores(search.parse_route("image-meta"), q, src)
    assert fused == pytest.approx(search.rrf_scores([img, meta]))
    assert fused["c1"] == pytest.approx(2 / 61)
    blend = search.rank_scores(src.corpus, search.route_scores(search.parse_route("eg2-blend:cA"), q, src))
    fts = search.rank_scores(src.corpus, search.route_scores(search.parse_route("fts:cA"), q, src))
    hm = search.route_scores(search.parse_route("hybrid-meta:cA"), q, src)
    assert hm == pytest.approx(search.rrf_scores([blend, fts, meta]))
    d = search.route_definition(search.parse_route("hybrid-meta:cA"), src)
    assert d["fused"] == ["eg2-blend:cA", "fts:cA", "meta"] and "meta" in d


@pytest.mark.parametrize("text,want", [
    ("a beach at sunset", {}),
    ("black and white photo of granny", {"mono": True}),
    ("B&W portrait", {"mono": True}),
    ("bw street", {"mono": True}),
    ("black-and-white monochrome", {"mono": True}),
    ("greyscale dog", {"mono": True}),
    ("film photo of a car", {"scan": True}),
    ("scanned slide of mum", {"scan": True}),
    ("old prints from the attic", {"scan": True}),
    ("prints on the wall", {}),
    ("a negative of the house", {"scan": True}),
    ("christmas 2018", {"years": [2018, 2018]}),
    ("parties in the 70s", {"years": [1970, 1979]}),
    ("1970s kitchen", {"years": [1970, 1979]}),
    ("holidays in the 2000s", {"years": [2000, 2009]}),
    ("skiing from 2005 to 2008", {"years": [2005, 2008]}),
    ("b&w film photos of the 70s", {"mono": True, "scan": True, "years": [1970, 1979]}),
    ("filming a scene, 123 bikes", {}),
])
def test_parse_filters(text, want):
    assert search.parse_filters(text) == want


def test_soft_filter_keeps_order_within_tiers_and_preserves_ties():
    scores = {"a": 0.9, "b": 0.5, "c": 0.5, "d": 0.1}  # e unscored
    out = search.apply_soft_filter(scores, {"c", "d", "e"})
    ranking, s = search.rank_scores(["a", "b", "c", "d", "e"], out)
    assert ranking == ["c", "d", "a", "b", "e"]  # e passes the filter but was never retrieved: stays last
    assert s[-1] is None and s[0] > s[1] > s[2] > s[3]
    tied = search.apply_soft_filter(scores, {"b", "c"})
    assert tied["b"] == tied["c"]  # a tie stays a tie
    assert search.rank_scores(["a", "b", "c", "d", "e"], search.apply_soft_filter(scores, set()))[0] == \
        ["a", "b", "c", "d", "e"]


def test_filter_attributes_from_labels_for_captioner_routes_and_frame_otherwise():
    src = meta_sources()
    # label says a1 is mono; the frame says b1 and x1 are mono
    src.labels["cA"]["a1"]["colour_class"] = "toned"
    src.labels["cA"]["b1"]["colour_class"] = "colour"
    q = {"query_id": "q-bw", "text": "black and white bridge over canal", "relevant": []}
    img = search.rank_scores(src.corpus, search.route_scores(search.parse_route("eg2-image@768"), q, src))[0]
    imgf = search.rank_scores(src.corpus, search.route_scores(search.parse_route("eg2-image@768+filt"), q, src))[0]
    assert imgf == [a for a in img if a in {"b1", "x1"}] + [a for a in img if a not in {"b1", "x1"}]
    blend = search.rank_scores(src.corpus, search.route_scores(search.parse_route("eg2-blend:cA"), q, src))[0]
    blendf = search.rank_scores(src.corpus, search.route_scores(search.parse_route("eg2-blend:cA+filt"), q, src))[0]
    assert blendf[0] == "a1" and blendf[1:] == [a for a in blend if a != "a1"]
    # years come from the frame only; an unknown year fails
    q2 = {"query_id": "q-y", "text": "dog in the 70s", "relevant": []}
    r = search.rank_scores(src.corpus, search.route_scores(search.parse_route("eg2-image@768+filt"), q2, src))[0]
    assert r[0] == "x1"
    scan = {"query_id": "q-s", "text": "scanned negative", "relevant": []}
    rs = search.rank_scores(src.corpus, search.route_scores(search.parse_route("fts:cA+filt"), scan, src))[0]
    assert rs == search.rank_scores(src.corpus, search.route_scores(search.parse_route("fts:cA"), scan, src))[0]
    src.labels["cA"]["b1"]["medium"] = "film_slide"
    retrieved = search.route_scores(search.parse_route("fts:cA"), scan, src)
    rs = search.rank_scores(src.corpus, search.route_scores(search.parse_route("fts:cA+filt"), scan, src))[0]
    if "b1" in retrieved:
        assert rs[0] == "b1"
    else:  # a filter never retrieves: an FTS miss stays below everything the route found
        assert rs.index("b1") >= len(retrieved)
    blendf = search.rank_scores(src.corpus, search.route_scores(search.parse_route("eg2-blend:cA+filt"), scan, src))[0]
    assert blendf[0] == "b1"  # dense routes score every item, so the scan moves to the front
    # no constraint: identical scores
    plain = {"query_id": "q-p", "text": "bridge over canal", "relevant": []}
    assert search.route_scores(search.parse_route("hybrid:cA+filt"), plain, src) == \
        search.route_scores(search.parse_route("hybrid:cA"), plain, src)


class FakeModel:
    def __init__(self, out):
        self.out, self.calls = out, []

    def __call__(self, text):
        self.calls.append(text)
        return self.out(text)


def fake_expander(fn=None, prompt="p1"):
    model = FakeModel(fn or (lambda t: {"keywords": ["Canal", "boat"], "rewrite": "a canal in venice"}))
    return search.Expander("ollama/fake", {"prompt_sha256": prompt, "model_digest": "d", "options": {}}, model), model


def test_expansion_cache_is_keyed_and_reused(tmp_path):
    cfg, _ = fake_cfg(tmp_path)
    exp, model = fake_expander()
    out = search.expand_queries(cfg, QUERIES, exp)
    assert set(out) == {"q-bridge", "q-rose", "q-dog"} and len(model.calls) == 3
    assert out["q-bridge"] == {"keywords": ["canal", "boat"], "rewrite": "a canal in venice"}
    again = search.expand_queries(cfg, QUERIES, exp)
    assert again == out and len(model.calls) == 3  # cache hit, no new calls
    d = search.store.store_dir(cfg, "ollama/fake", exp.params_hash, "expansions")
    assert d.is_dir() and (d / "params.json").exists()
    exp2, model2 = fake_expander(prompt="p2")
    assert exp2.params_hash != exp.params_hash
    search.expand_queries(cfg, QUERIES[:1], exp2)
    assert len(model2.calls) == 1
    assert search.load_expansions(cfg, exp) == out
    # bad output is stored invalid, not used
    exp3, _ = fake_expander(lambda t: {"keywords": "nope"}, prompt="p3")
    assert search.expand_queries(cfg, QUERIES[:1], exp3) == {}


def test_expansion_changes_fts_and_embedding_inputs():
    src = fake_sources()
    seen = []
    base = src.embed_query

    def embed(text):
        seen.append(text)
        return base(text) if text in ("bridge over canal", "red rose", "small dog") else unit((3, 1.0))

    src.embed_query = embed
    src.expansions = {"q-dog": {"keywords": ["rose", "flower"], "rewrite": "a puppy"}}
    src.expansion = {"model_id": "ollama/fake", "params_hash": "eph"}
    q = QUERIES[2]
    assert search.route_scores(search.parse_route("fts:cA"), q, src) == {}
    s = search.route_scores(search.parse_route("fts:cA+exp"), q, src)
    assert set(s) == {"b1"}  # "rose" / "flower" now match b1's caption and tags
    v = src.query_vector(q, exp=True)
    want = unit((2, 1.0), (500, 1.0)) + unit((3, 1.0))
    assert np.allclose(v, want / np.linalg.norm(want), atol=1e-6)
    assert "a puppy" in seen
    assert np.allclose(src.query_vector(q), unit((2, 1.0), (500, 1.0)))
    img = search.route_scores(search.parse_route("eg2-image@768+exp"), q, src)
    assert img["b1"] > search.route_scores(search.parse_route("eg2-image@768"), q, src)["b1"]
    # a query without an expansion makes the +exp route unrunnable
    assert "expand" in src.missing(search.parse_route("fts:cA+exp"))
    src.expansions.update({"q-bridge": {"keywords": [], "rewrite": ""}, "q-rose": {"keywords": [], "rewrite": ""}})
    assert src.missing(search.parse_route("fts:cA+exp")) is None


def test_route_suffixes_round_trip_and_change_the_hash():
    for name in ("eg2-image@768+filt", "hybrid:e4b-bf16-v2+filt", "hybrid:x+exp+filt", "meta+exp", "image-meta",
                 "hybrid-meta:c.1", "eg2-blend:c+exp", "eg2-blend@w0.3:c+filt"):
        r = search.parse_route(name)
        assert r.name == name and search.parse_route(r.name) == r and ":" not in r.dir_name
    r = search.parse_route("hybrid:x+exp+filt")
    assert (r.kind, r.cand, r.exp, r.filt, r.w) == ("hybrid", "x", True, True, 0.5)
    assert search.parse_route("meta").cand is None and not search.parse_route("image-meta").caption_based
    assert search.parse_route("hybrid-meta:x").caption_based
    for bad in ("hybrid:x+filt+exp", "meta:x", "image-meta:x", "hybrid-meta", "fts:x+foo", "eg2-image@768+exp+exp"):
        with pytest.raises(ValueError):
            search.parse_route(bad)
    src = meta_sources()
    src.expansions = {q["query_id"]: {"keywords": [], "rewrite": ""} for q in QUERIES}
    src.expansion = {"model_id": "ollama/fake", "params_hash": "eph"}
    h = {n: search.store.params_hash(search.route_definition(search.parse_route(n), src))
         for n in ("hybrid:cA", "hybrid:cA+exp", "hybrid:cA+filt", "hybrid:cA+exp+filt")}
    assert len(set(h.values())) == 4
    d = search.route_definition(search.parse_route("hybrid:cA+exp+filt"), src)
    assert d["expansion"]["params_hash"] == "eph" and d["filter"]["version"] == search.FILTER_VERSION
    plain = search.route_definition(search.parse_route("hybrid:cA"), src)
    assert "expansion" not in plain and "filter" not in plain
    src.expansion = {"model_id": "ollama/fake", "params_hash": "eph2"}
    assert search.store.params_hash(search.route_definition(search.parse_route("hybrid:cA+exp"), src)) != \
        h["hybrid:cA+exp"]


def test_eval1k_route_grid():
    names = search.eval1k_routes(["c1"])
    assert names[:3] == ["eg2-image@768", "meta", "image-meta"]
    for n in ("eg2-caption:c1", "eg2-blend:c1", "fts:c1", "hybrid:c1", "hybrid-meta:c1",
              "eg2-image@768+exp+filt", "image-meta+filt", "eg2-blend:c1+exp", "hybrid-meta:c1+exp+filt"):
        assert n in names
    assert len(names) == len(set(names)) == 3 + 5 + 4 * 3
    assert all(search.parse_route(n) for n in names)
    assert len(search.eval1k_routes(["c1", "c2"])) == 3 + 2 * 5 + 2 * 3 + 2 * 2 * 3


def test_search_run_with_queries_file_and_empty_relevant(tmp_path, monkeypatch, capsys):
    cfg, toml = fake_cfg(tmp_path)
    monkeypatch.setenv("SHOEBOX_CONFIG", str(toml))
    sets = tmp_path / "sets"
    set_dir = _pool_set(sets)
    rows = [{"query_id": "k1", "text": "red rose", "kind": "known_item", "style": "visual", "source_asset_id": "b1",
             "relevant": ["b1"], "split": "dev", "provenance": {}},
            {"query_id": "c1", "text": "small dog", "kind": "category", "facet": "subject", "source_asset_id": None,
             "relevant": [], "split": "dev", "provenance": {}}]
    write_jsonl(set_dir / "queries-v1.jsonl", rows)
    _fake_image_store(monkeypatch, {a: unit((i, 1.0)) for i, a in enumerate(("a1", "a2", "b1", "c1", "x1", "x2", "x3"))})
    rc = main(["eval", "search-run", "--set", "p", "--routes", "eg2-image@768", "--sets-dir", str(sets),
               "--queries", "queries-v1.jsonl"])
    assert rc == 0, capsys.readouterr().err
    e = json.loads((search.search_dir(cfg, "p") / "index.json").read_text())["routes"]["eg2-image@768"]
    assert e["queries_sha256"] == search.file_sha256(set_dir / "queries-v1.jsonl")
    out = search.read_jsonl(Path(e["path"]))
    assert [r["query_id"] for r in out] == ["k1", "c1"] and all(len(r["ranking"]) == 7 for r in out)
    assert e["queries_file"] == "queries-v1.jsonl"
    diag = {d["query_id"]: d for d in search.read_jsonl(Path(e["diagnostics"]))}
    assert diag["c1"]["rank_best"] is None
