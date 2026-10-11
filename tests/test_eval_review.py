"""Owner review page, owner review ingest, Opus usefulness rescore ingest and the report additions.
Synthetic packets and images in tmp dirs only."""

import json
import re

import pytest

from shoebox import config, privacy, store
from shoebox.evals import caption_audit as ca
from shoebox.evals import review as rv

RUBRIC = "rubric text\n- **5 – excellent.** all there.\n- **1 – useless.** nothing.\n"


def aid(i: int) -> str:
    return f"{i:04x}" + "d" * 60


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "NEVER_SEND_PATH", tmp_path / "never-send.txt")
    return config.Config(shoebox_root=tmp_path / "shoebox", sources={})


STRATA = ["2000-09|other|noscan", "unknown|mono|scan", "unknown|other|scan", "2010-14|mono|noscan"]


def make_packets(tmp_path, n=40, seed=5):
    """n photos in batches of 10; arms shuffled per photo; caption text names the arm in a SECRET form."""
    d = tmp_path / "pk"
    (d / "images").mkdir(parents=True)
    rows, mapping, photos = [], {}, []
    for i in range(1, n + 1):
        k = f"c-{i:03d}"
        rows.append({"key": k, "asset_id": aid(i), "stratum": STRATA[i % len(STRATA)]})
        order = ca.blind_order(k, ca.ARMS, seed)
        mapping[k] = dict(zip("ABCDE", order, strict=True))
        img = d / "images" / f"{k}.jpg"
        img.write_bytes(f"JPEG{i}".encode())
        caps = [{"label": lab, "caption": None if (i == 3 and arm == "g12b-q4") else
                 f"SECRET CAPTION #{ca.ARMS.index(arm)} </script><b>",
                 "tags": ["t1", "t2"], "medium": "print", "digitiser": None, "colour_class": "colour",
                 "rotate_cw": None} for lab, arm in mapping[k].items()]
        photos.append({"key": k, "image": str(img), "captions": caps})
    for b in range(0, n, 10):
        (d / f"batch-{b // 10 + 1:03d}.json").write_text(json.dumps({"batch": b // 10 + 1, "photos": photos[b:b + 10]}))
    (d / "map.json").write_text(json.dumps(mapping))
    (d / "packets.json").write_text(json.dumps({"seed": seed}))
    return d, rows, mapping


def make_queries(n=12):
    kinds = [("known_item", "vague_recall"), ("known_item", "literal"), ("category", "category")]
    return [{"query_id": f"q-{i:03d}", "text": f"QUERY TEXT {i}", "kind": kinds[i % 3][0],
             "style": kinds[i % 3][1]} for i in range(n)]


def build(tmp_path, out_name="page", seed=7, n=12):
    d, rows, mapping = make_packets(tmp_path)
    qs = make_queries()
    out = tmp_path / out_name
    pm = rv.build_page(d, rows, qs, [q["query_id"] for q in qs[:10]], n, seed, out, RUBRIC)
    return d, rows, mapping, qs, out, pm


# ---------- page ----------


def page_data(out):
    html = (out / "index.html").read_text()
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.DOTALL)
    return html, json.loads(m.group(1))


def test_page_inline_offline_blind_seeded(tmp_path):
    _d, rows, mapping, qs, out, pm = build(tmp_path)
    html, data = page_data(out)
    assert "http://" not in html and "https://" not in html and "//cdn" not in html
    assert not re.search(r'<(script|link|img)[^>]+(src|href)=["\'](?!img/)', html)  # no external resources
    assert not any(arm in html for arm in ca.ARMS) and aid(1) not in html and "SECRET CAPTION" in html
    assert "</script><b>" not in html.split('<script id="data"')[1].split("</script>")[0]  # escaped in JSON
    assert data["page_id"] == pm["page_id"] and data["seed"] == 7 and len(data["photos"]) == 12
    assert [q["query_id"] for q in data["searches"]] == [q["query_id"] for q in qs[:10]]
    assert data["searches"][0]["text"] == "QUERY TEXT 0" and "kind" not in data["searches"][0]
    assert "excellent" in html
    # images copied, referenced relatively
    for p in data["photos"]:
        assert p["image"] == f"img/{p['key']}.jpg"
        assert (out / p["image"]).read_bytes() == f"JPEG{int(p['key'][2:])}".encode()
    assert sorted(x.name for x in (out / "img").iterdir()) == sorted(f"{p['key']}.jpg" for p in data["photos"])
    # page-map consistent: page label -> arm == packet label -> arm, via the caption shown
    pmap = json.loads((out / "page-map.json").read_text())
    assert pmap == pm and set(pmap["photos"]) == {p["key"] for p in data["photos"]}
    by_key = {r["key"]: r for r in rows}
    reshuffled = 0
    for p in data["photos"]:
        e = pmap["photos"][p["key"]]
        assert e["asset_id"] == by_key[p["key"]]["asset_id"]
        assert [c["label"] for c in p["captions"]] == list("ABCDE")
        for c in p["captions"]:
            arm = e["labels"][c["label"]]
            assert mapping[p["key"]][e["packet_labels"][c["label"]]] == arm
            if c["caption"] is not None:
                assert c["caption"].startswith(f"SECRET CAPTION #{ca.ARMS.index(arm)} ")
        reshuffled += e["packet_labels"] != dict(zip("ABCDE", "ABCDE", strict=True))
    assert reshuffled > 0
    # spread: scans and mono photos both present
    strata = [by_key[k]["stratum"] for k in pmap["photos"]]
    assert sum("|mono|" in s for s in strata) >= 4 and sum(s.endswith("|scan") and "|mono|" not in s
                                                            for s in strata) >= 4
    # seeded: same seed same page, other seed differs
    _, _, _, _, out2, pm2 = build(tmp_path / "b", "page", seed=7)
    assert pm2 == pm and (out2 / "index.html").read_text() == html
    _, _, _, _, _, pm3 = build(tmp_path / "c", "page", seed=8)
    assert pm3["photos"] != pm["photos"]


def test_page_refuses_evals_dir_and_unknown_query(tmp_path):
    d, rows, _ = make_packets(tmp_path)
    with pytest.raises(ValueError):
        rv.build_page(d, rows, make_queries(), ["q-000"], 5, 1, config.REPO_ROOT / "evals" / "x", RUBRIC)
    with pytest.raises(ValueError):
        rv.build_page(d, rows, make_queries(), ["q-999"], 5, 1, tmp_path / "o", RUBRIC)


# ---------- review ingest ----------


QMD = "# Queries\n\nsome text\n\nowner realism check: pending\n\nmore\n"


def answers(pm, n_photos=None, verdicts=None, **over):
    keys = sorted(pm["photos"])[:n_photos]
    caps = {k: {"labels": {lab: (i + j) % 5 + 1 for j, lab in enumerate("ABCDE")}, "note": "SECRET NOTE"}
            for i, k in enumerate(keys)}
    verdicts = verdicts or {q: ("would" if i % 3 else "wouldnt") for i, q in enumerate(pm["queries"])}
    srch = {q: {"verdict": v, "rephrase": "SECRET PHRASE" if v == "wouldnt" else ""} for q, v in verdicts.items()}
    return {"page_id": pm["page_id"], "seed": pm["seed"], "captions": caps, "searches": srch, "ts": "x", **over}


def test_review_ingest_unblinds_stores_and_updates_queries_md(env, tmp_path):
    _d, _rows, _mapping, qs, out, pm = build(tmp_path)
    qmd = tmp_path / "QUERIES.md"
    qmd.write_text(QMD)
    ans = answers(pm)
    res = rv.review_ingest(env, ans, out, RUBRIC, qs, qmd)
    assert res.stored == 12 + 10 and res.already == 0 and not res.conflicts
    recs = list(store.iter_records(env, rv.OWNER_MODEL_ID, res.params_hash, rv.OWNER_KIND))
    caps = {r["key"]: r for r in recs if r["review"] == "caption"}
    k = min(pm["photos"])
    assert caps[k]["asset_id"] == pm["photos"][k]["asset_id"]
    for lab, arm in pm["photos"][k]["labels"].items():
        assert caps[k]["useful"][arm] == ans["captions"][k]["labels"][lab]
    srch = {r["asset_id"]: r for r in recs if r["review"] == "search"}
    assert srch["q-000"]["verdict"] == "wouldnt" and srch["q-000"]["query_kind"] == "known_item"
    text = qmd.read_text()
    line = next(x for x in text.splitlines() if x.startswith("owner realism check:"))
    # 10 queries; i % 3 == 0 -> wouldn't: 0, 3, 6, 9 -> 6 would / 4 wouldn't
    assert "would type 6, wouldn't 4 of 10 (60.0%)" in line
    assert "known_item 3/7" in line and "category 3/3" in line and "QUERY TEXT" not in text
    assert text.replace(line, "owner realism check: pending") == QMD
    again = rv.review_ingest(env, ans, out, RUBRIC, qs, qmd)
    assert again.stored == 0 and again.already == 22 and qmd.read_text() == text
    assert len(list(store.iter_records(env, rv.OWNER_MODEL_ID, res.params_hash, rv.OWNER_KIND))) == 22
    changed = answers(pm)
    changed["captions"][k]["labels"]["A"] = changed["captions"][k]["labels"]["A"] % 5 + 1
    assert rv.review_ingest(env, changed, out, RUBRIC, qs, qmd).conflicts == [k]
    assert rv.owner_params(RUBRIC, 7) != rv.owner_params(RUBRIC + "x", 7) != rv.owner_params(RUBRIC, 8)


def test_review_ingest_partial_answers_store_complete_ones_only(env, tmp_path):
    _d, _rows, _mapping, qs, out, pm = build(tmp_path)
    qmd = tmp_path / "QUERIES.md"
    qmd.write_text(QMD)
    ans = answers(pm, n_photos=3)
    first = min(ans["captions"])
    del ans["captions"][first]["labels"]["C"]
    ans["searches"]["q-001"]["verdict"] = None
    res = rv.review_ingest(env, ans, out, RUBRIC, qs, qmd)
    assert res.stored == 2 + 9 and res.incomplete == 1 + 1
    assert "9 of 10 answered" in qmd.read_text()


@pytest.mark.parametrize("bad", [
    lambda a: {**a, "page_id": "other"},
    lambda a: {**a, "seed": 1},
    lambda a: {**a, "captions": {**a["captions"], "c-999": {"labels": {"A": 3}}}},
    lambda a: {**a, "captions": {k: {"labels": {**v["labels"], "F": 3}} for k, v in a["captions"].items()}},
    lambda a: {**a, "captions": {k: {"labels": {**v["labels"], "A": 6}} for k, v in a["captions"].items()}},
    lambda a: {**a, "captions": {k: {"labels": {**v["labels"], "A": True}} for k, v in a["captions"].items()}},
    lambda a: {**a, "searches": {"q-999": {"verdict": "would"}}},
    lambda a: {**a, "searches": {"q-000": {"verdict": "maybe"}}},
])
def test_review_ingest_validation_stores_nothing(env, tmp_path, bad):
    _d, _rows, _mapping, qs, out, pm = build(tmp_path)
    qmd = tmp_path / "QUERIES.md"
    qmd.write_text(QMD)
    with pytest.raises(rv.IngestError):
        rv.review_ingest(env, bad(answers(pm)), out, RUBRIC, qs, qmd)
    assert not (env.results_dir / rv.OWNER_KIND).exists() and qmd.read_text() == QMD


# ---------- rescore ingest ----------


def rescore_lines(mapping, skip=()):
    return [{"key": k, "facts": "SECRET FACTS", "useful": {lab: "ABCDE".index(lab) + 1 for lab in "ABCDE"}}
            for k in sorted(mapping) if k not in skip]


def write_rescore(d, lines, name="rescore-001.jsonl"):
    (d / name).write_text("".join(json.dumps(x) + "\n" for x in lines))


def test_rescore_ingest_unblinds_and_is_idempotent(env, tmp_path):
    d, rows, mapping = make_packets(tmp_path, n=6)
    write_rescore(d, rescore_lines(mapping)[:3])
    write_rescore(d, rescore_lines(mapping)[3:], "rescore-002.jsonl")
    res = rv.rescore_ingest(env, d, rows, RUBRIC)
    assert res.stored == 6 and res.already == 0
    recs = {r["key"]: r for r in store.iter_records(env, ca.MODEL_ID, res.params_hash, rv.RESCORE_KIND)}
    for k, m in mapping.items():
        assert recs[k]["asset_id"] == aid(int(k[2:]))
        assert recs[k]["useful"] == {arm: "ABCDE".index(lab) + 1 for lab, arm in m.items()}
    again = rv.rescore_ingest(env, d, rows, RUBRIC)
    assert again.stored == 0 and again.already == 6 and again.params_hash == res.params_hash
    assert rv.rescore_ingest(env, d, rows, RUBRIC + "v2").params_hash != res.params_hash


@pytest.mark.parametrize("bad", [
    lambda m: rescore_lines(m)[:-1],
    lambda m: rescore_lines(m) + rescore_lines(m)[:1],
    lambda m: rescore_lines(m) + [{"key": "c-999", "useful": {lab: 3 for lab in "ABCDE"}}],
    lambda m: [{**x, "useful": {lab: 3 for lab in "ABCD"}} for x in rescore_lines(m)],
    lambda m: [{**x, "useful": {**x["useful"], "A": 0}} for x in rescore_lines(m)],
    lambda m: [{**x, "useful": {**x["useful"], "A": 2.5}} for x in rescore_lines(m)],
])
def test_rescore_ingest_validation_stores_nothing(env, tmp_path, bad):
    d, rows, mapping = make_packets(tmp_path, n=6)
    write_rescore(d, bad(mapping))
    with pytest.raises(rv.IngestError):
        rv.rescore_ingest(env, d, rows, RUBRIC)
    assert not (env.results_dir / rv.RESCORE_KIND).exists()


# ---------- report ----------


REF_ARM, C_ARM = "gemini-medium-v2", "e4b-bf16"


def hand():
    """6 photos, two arms. Unanchored (old) useful: ref 4, cand 3 everywhere.
    Rubric (new): ref 4,4,4,5,5,5 (mean 4.5); cand 2,2,3,3,4,4 (mean 3.0) -> paired diff -1.5.
    Owner on photos 1-4: ref 5,4,5,4 (4.5); cand 2,3,3,4 (3.0)."""
    audits = [{"asset_id": aid(i), "key": f"c-{i:03d}", "sets": {REF_ARM: {"useful": 4}, C_ARM: {"useful": 3}}}
              for i in range(1, 7)]
    ref_new, c_new = [4, 4, 4, 5, 5, 5], [2, 2, 3, 3, 4, 4]
    rescore = [{"asset_id": aid(i), "key": f"c-{i:03d}", "useful": {REF_ARM: ref_new[i - 1], C_ARM: c_new[i - 1]}}
               for i in range(1, 7)]
    own_ref, own_c = [5, 4, 5, 4], [2, 3, 3, 4]
    owner = [{"asset_id": aid(i), "key": f"c-{i:03d}", "review": "caption", "note": "SECRET NOTE",
              "useful": {REF_ARM: own_ref[i - 1], C_ARM: own_c[i - 1]}} for i in range(1, 5)]
    owner.append({"asset_id": "q-1", "review": "search", "verdict": "would", "rephrase": "SECRET"})
    return audits, rescore, owner


def test_report_usefulness_rubric_owner_agreement():
    audits, rescore, owner = hand()
    u = rv.usefulness_report(audits, rescore, owner, b=400, seed=3, reference=REF_ARM)
    a = u["arms"]
    assert a[REF_ARM]["rubric"]["mean"] == 4.5 and a[C_ARM]["rubric"]["mean"] == 3.0
    assert a[REF_ARM]["unanchored"]["mean"] == 4.0 and a[C_ARM]["unanchored"]["mean"] == 3.0
    assert a[C_ARM]["n"] == 6
    lo, hi = a[C_ARM]["rubric"]["ci95"]
    assert 2 <= lo < 3.0 < hi <= 4
    p = u["paired_vs_reference"][C_ARM]
    assert p["diff"] == -1.5 and p["ci95"][1] < 0 and REF_ARM not in u["paired_vs_reference"]
    o = u["owner"]
    assert o["photos"] == 4
    assert o["arms"][REF_ARM]["owner"]["mean"] == 4.5 and o["arms"][C_ARM]["owner"]["mean"] == 3.0
    assert o["arms"][C_ARM]["opus_rubric"]["mean"] == 2.5  # cand rubric on photos 1-4: 2,2,3,3
    # mean abs diff over 8 (photo, arm) pairs: ref |5-4|,0,|5-4|,|4-5| = 3; cand 0,1,0,1 = 2 -> 5/8
    assert o["agreement"]["mean_abs_diff"] == 0.625
    # per photo Spearman over 2 arms: photo 1 (5>2 vs 4>2) +1; 2 (4>3, 4>2) +1; 3 (5>3, 4>3) +1;
    # 4 (4=4 owner tie) undefined -> mean over 3 photos = 1.0
    assert o["agreement"]["spearman_per_photo"] == {"mean": 1.0, "n": 3}
    md = rv.render_md(u)
    assert "SECRET" not in md + json.dumps(u) and "0.625" in md


def test_spearman_ties_and_inverse():
    assert rv.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)
    assert rv.spearman([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1.0)
    assert rv.spearman([3, 3, 3], [1, 2, 3]) is None


def test_build_report_includes_usefulness_only_when_present():
    audits, rescore, owner = hand()
    recs = [{**r, "reference": None, "stratum": "s"} for r in audits]
    for r in recs:
        r["sets"] = {arm: {"wrong": 0, "invented": 0, "missing": 0, "useful": v["useful"], "shown": None}
                     for arm, v in r["sets"].items()}
    assert "usefulness" not in ca.build_report(recs, {}, b=50)
    rep = ca.build_report(recs, {}, b=50, rescore=rescore, owner=owner)
    assert rep["usefulness"]["arms"][C_ARM]["rubric"]["mean"] == 3.0
    assert "Usefulness" in ca.render_md(rep)


# ---------- CLI ----------


def test_cli_review_page_ingest_rescore_and_report(env, tmp_path, monkeypatch):
    from shoebox.cli import main
    from shoebox.commands import eval as eval_cmd
    from shoebox.evals import judge

    monkeypatch.setattr(eval_cmd, "_cfg", lambda: env)
    d, rows, mapping = make_packets(tmp_path, n=12)
    sets = tmp_path / "sets"
    sd = sets / "s"
    sd.mkdir(parents=True)
    judge.write_jsonl(sd / ca.SAMPLE_FILE, rows)
    qs = make_queries()
    judge.write_jsonl(sd / "queries-v1.jsonl", qs)
    (sd / "QUERIES.md").write_text(QMD)
    real = tmp_path / "realism.json"
    real.write_text(json.dumps([q["query_id"] for q in qs[:5]]))
    page = tmp_path / "page"
    common = ["--set", "s", "--sets-dir", str(sets)]
    assert main(["eval", "review-page", *common, "--packets", str(d), "--n", "6", "--seed", "3",
                 "--realism", str(real), "--out", str(page)]) == 0
    pm = json.loads((page / "page-map.json").read_text())
    ansf = tmp_path / "answers.json"
    ansf.write_text(json.dumps(answers(pm)))
    assert main(["eval", "review-ingest", *common, "--answers", str(ansf), "--page", str(page)]) == 0
    assert "of 5 answered" in (sd / "QUERIES.md").read_text()
    write_rescore(d, rescore_lines(mapping))
    assert main(["eval", "rescore-ingest", *common, "--dir", str(d)]) == 0
    # a caption audit to report on
    for r in rows:
        store.append(env, {"asset_id": r["asset_id"], "model_id": ca.MODEL_ID, "params_hash": "h" * 16,
                           "status": "ok", "kind": ca.KIND, "key": r["key"], "stratum": r["stratum"],
                           "reference": None, "sets": {arm: {"wrong": 0, "invented": 0, "missing": 0, "useful": 3,
                                                             "shown": None} for arm in ca.ARMS}}, ca.KIND)
    out = tmp_path / "runs"
    assert main(["eval", "caption-report", "--set", "s", "--date", "2026-10-15", "--bootstrap", "50",
                 "--out-dir", str(out)]) == 0
    rep = json.loads((out / "2026-10-15_s-caption-audit.json").read_text())
    assert rep["usefulness"]["owner"]["photos"] == 6 and rep["usefulness"]["arms"]["e4b-bf16"]["n"] == 12
    blob = "".join(p.read_text() for p in out.iterdir())
    assert "SECRET" not in blob and "QUERY TEXT" not in blob
