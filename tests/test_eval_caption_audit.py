"""Opus caption audit (task 6.5, D11): synthetic items, frame rows, label stores and derived images only."""

import io
import json
from types import SimpleNamespace

import pytest
from derived_fixture import write_derived
from PIL import Image

from shoebox import config, privacy, store
from shoebox.evals import caption_audit as ca


def aid(i: int) -> str:
    return f"{i:04x}" + "c" * 60


def jpeg(size=(1600, 1200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 120, 30)).save(buf, "JPEG")
    return buf.getvalue()


# ---------- sample ----------


def corpus(n=300):
    """n items; every 3rd item pairs with the next as a capture group; strata by index."""
    items, frows = [], {}
    for i in range(n):
        g = f"g{i // 2}" if i % 3 == 0 else None
        items.append({"asset_id": aid(i), "source": "own", "relpath": f"P/{i}.jpg", "group_id": g})
        scan = i % 10 == 0
        mono = i % 7 == 0
        frows[aid(i)] = {"date": f"{1985 + i % 40}-01-01", "date_kind": "exif" if i % 5 else "mtime",
                         "make": "Nikon" if scan else "Canon", "model": "LS-50" if scan else "EOS",
                         "software": None, "colour_guess": "mono" if mono else "colour"}
    # group mates share the group id: item 3k and 3k+1 both get g
    for i in range(0, n - 1, 3):
        items[i + 1]["group_id"] = items[i]["group_id"]
    return items, frows


def dev_queries(items):
    qs = []
    for i in range(0, 60, 6):
        qs.append({"query_id": f"q{i}", "kind": "known_item", "split": "dev", "source_asset_id": aid(i),
                   "relevant": [aid(i)]})
    qs.append({"query_id": "qt", "kind": "known_item", "split": "test", "source_asset_id": aid(61),
               "relevant": [aid(61)]})
    qs.append({"query_id": "qc", "kind": "category", "split": "dev", "source_asset_id": None, "relevant": []})
    return qs


def test_sample_deterministic_excludes_dev_groups_one_per_group_and_overweights():
    items, frows = corpus()
    qs = dev_queries(items)
    rows, meta = ca.draw(items, frows, qs, 60, 11)
    again, _ = ca.draw(list(reversed(items)), frows, list(reversed(qs)), 60, 11)
    assert rows == again
    assert rows != ca.draw(items, frows, qs, 60, 12)[0]
    assert len(rows) == 60 and [r["key"] for r in rows] == [f"c-{i:03d}" for i in range(1, 61)]
    by_id = {it["asset_id"]: it for it in items}
    groups = [by_id[r["asset_id"]].get("group_id") or r["asset_id"] for r in rows]
    assert len(set(groups)) == len(groups)
    dev = {by_id[aid(i)].get("group_id") or aid(i) for i in range(0, 60, 6)}
    assert dev and not dev & set(groups)
    assert meta["excluded_dev_groups"] == len(dev)
    # over-weighting: scan/mono strata get a larger share of the sample than of the candidates
    flagged = lambda s: "|mono|" in s or s.endswith("|scan")
    cand_share = sum(v["candidates"] for s, v in meta["strata"].items() if flagged(s)) / meta["candidates"]
    drawn_share = sum(flagged(r["stratum"]) for r in rows) / len(rows)
    assert drawn_share > cand_share * 1.5
    assert all(r["stratum"] for r in rows) and set(rows[0]) == {"key", "asset_id", "stratum"}


# ---------- packets ----------


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "NEVER_SEND_PATH", tmp_path / "never-send.txt")
    src = tmp_path / "src"
    src.mkdir()
    return config.Config(shoebox_root=tmp_path / "shoebox", sources={
        "own": config.Source("own", src, "owner", False, True, hosted_models=("gemini", "claude")),
        "gem": config.Source("gem", src, "owner", False, True)})


def fake_resolve(arm):
    return SimpleNamespace(name=arm, model_id=f"m/{arm}", params_hash=f"h{len(arm):02d}" + "0" * 13, retry=None,
                           kind="labels")


def put_label(cfg, arm, a, status="ok", rotate=None, colour="colour"):
    r = fake_resolve(arm)
    out = {"caption": f"SECRET CAPTION #{ca.ARMS.index(arm)}", "tags": ["x"], "medium": "print", "digitiser": "film_scanner",
           "colour_class": colour, "confidence": {}}
    if arm.endswith("-v2"):
        out["rotate_cw"] = 0 if rotate is None else rotate
    store.append(cfg, {"asset_id": a, "model_id": r.model_id, "params_hash": r.params_hash, "status": status,
                       "output": out if status == "ok" else None})


def setup_packets(cfg, n=6):
    rows = [{"key": f"c-{i:03d}", "asset_id": aid(i), "stratum": "s"} for i in range(1, n + 1)]
    items = [{"asset_id": aid(i), "source": "gem" if i == 2 else "own", "relpath": f"P/{i}.jpg", "group_id": None}
             for i in range(1, n + 1)]
    for i in range(1, n + 1):
        write_derived(cfg, aid(i), jpeg())
        for arm in ca.ARMS:
            if not (arm == "g12b-q4" and i == 3):
                put_label(cfg, arm, aid(i), status="invalid" if (arm == "e4b-bf16" and i == 4) else "ok")
    return rows, items


def test_packets_blind_seeded_map_missing_arm_and_privacy_skip(env, tmp_path):
    cfg = env
    rows, items = setup_packets(cfg)
    labels, sources = ca.load_arms(cfg, ca.ARMS, resolve=fake_resolve)
    out = tmp_path / "pk"
    res = ca.packets(cfg, rows, items, labels, out, per_batch=2, seed=5, sources=sources)
    assert {s["key"]: s["reason"] for s in res.skipped} == {"c-002": "no_consent"}
    assert sorted(res.missing, key=lambda m: m["key"]) == [{"key": "c-003", "arm": "g12b-q4"}, {"key": "c-004", "arm": "e4b-bf16"}]
    mapping = json.loads((out / "map.json").read_text())
    assert sorted(mapping) == ["c-001", "c-003", "c-004", "c-005", "c-006"]
    for m in mapping.values():
        assert sorted(m) == list("ABCDE") and sorted(m.values()) == sorted(ca.ARMS)
    assert len({tuple(m.values()) for m in mapping.values()}) > 1  # shuffled per photo
    batches = [json.loads(p.read_text()) for p in sorted(out.glob("batch-*.json"))]
    assert [len(b["photos"]) for b in batches] == [2, 2, 1]
    text = "".join(p.read_text() for p in out.glob("batch-*.json"))
    assert not any(arm in text for arm in ca.ARMS) and aid(1) not in text
    for b in batches:
        for p in b["photos"]:
            m = mapping[p["key"]]
            assert [c["label"] for c in p["captions"]] == list("ABCDE")
            for c in p["captions"]:
                arm = m[c["label"]]
                missing = (p["key"], arm) in {("c-003", "g12b-q4"), ("c-004", "e4b-bf16")}
                assert (c["caption"] is None) == missing
                if not missing:
                    assert c["caption"] == f"SECRET CAPTION #{ca.ARMS.index(arm)}"
                    assert (c["rotate_cw"] is None) == (not arm.endswith("-v2"))
            with Image.open(p["image"]) as im:
                assert max(im.size) == ca.THUMB_PX
    assert (out / "INSTRUCTIONS.md").read_text() == ca.instructions_text()
    again = ca.packets(cfg, rows, items, labels, tmp_path / "pk2", per_batch=2, seed=5, sources=sources)
    assert json.loads((tmp_path / "pk2" / "map.json").read_text()) == mapping and again.missing == res.missing
    ca.packets(cfg, rows, items, labels, tmp_path / "pk3", per_batch=2, seed=6, sources=sources)
    assert json.loads((tmp_path / "pk3" / "map.json").read_text()) != mapping


def test_packets_refuse_evals_dir(env):
    with pytest.raises(ValueError):
        ca.packets(env, [], [], {}, config.REPO_ROOT / "evals" / "scratch")


# ---------- ingest ----------


REF = {"description": "SECRET DESC", "medium": "print", "digitiser": "flatbed_scanner", "colour_class": "mono",
       "rotate_cw": 0, "confidence": {"medium": "high", "digitiser": "high", "colour_class": "high",
                                      "rotate_cw": "high"}}


def audit_line(key, labels="ABCDE", **over):
    sets = {lab: {"wrong": i % 2, "invented": 0, "missing": 1, "useful": 3, "notes": "SECRET NOTE"}
            for i, lab in enumerate(labels)}
    return {"key": key, "reference": REF, "sets": sets, **over}


def packet_dir(env, tmp_path):
    rows, items = setup_packets(env)
    labels, sources = ca.load_arms(env, ca.ARMS, resolve=fake_resolve)
    d = tmp_path / "pk"
    ca.packets(env, rows, items, labels, d, per_batch=10, seed=5, sources=sources)
    return d, rows, json.loads((d / "map.json").read_text())


def write_audit(d, lines):
    (d / "audit-001.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))


def test_ingest_unblinds_and_is_idempotent(env, tmp_path):
    d, rows, mapping = packet_dir(env, tmp_path)
    write_audit(d, [audit_line(k) for k in mapping])
    res = ca.ingest(env, d, rows, "instr")
    assert res.stored == 5 and res.already == 0
    ph, recs = ca.audit_records(env)
    assert ph == res.params_hash and len(recs) == 5
    r = next(x for x in recs if x["key"] == "c-001")
    assert r["asset_id"] == aid(1) and r["kind"] == ca.KIND
    for lab, arm in mapping["c-001"].items():
        assert r["sets"][arm]["wrong"] == "ABCDE".index(lab) % 2
        assert r["sets"][arm]["shown"]["colour_class"] == "colour"
    r4 = next(x for x in recs if x["key"] == "c-004")
    assert r4["sets"]["e4b-bf16"]["shown"] is None
    again = ca.ingest(env, d, rows, "instr")
    assert again.stored == 0 and again.already == 5 and again.params_hash == res.params_hash
    assert len(list(store.iter_records(env, ca.MODEL_ID, ph, ca.KIND))) == 5
    assert ca.ingest(env, d, rows, "other instr").params_hash != ph


@pytest.mark.parametrize("bad", [
    lambda m: [audit_line(k) for k in list(m)[:-1]],  # a key missing
    lambda m: [audit_line(k) for k in m] + [audit_line(next(iter(m)))],  # a key twice
    lambda m: [audit_line(k) for k in m] + [audit_line("c-999")],  # unknown key
    lambda m: [audit_line(k, labels="ABCD") if i == 0 else audit_line(k) for i, k in enumerate(m)],
    lambda m: [audit_line(k, sets={**audit_line(k)["sets"], "A": {"wrong": -1, "invented": 0, "missing": 0,
                                                                  "useful": 3}}) for k in m],
    lambda m: [audit_line(k, sets={**audit_line(k)["sets"], "A": {"wrong": 0, "invented": 0, "missing": 0,
                                                                  "useful": 6}}) for k in m],
    lambda m: [audit_line(k, sets={**audit_line(k)["sets"], "A": {"wrong": 1.5, "invented": 0, "missing": 0,
                                                                  "useful": 2}}) for k in m],
    lambda m: [audit_line(k, reference={**REF, "medium": "photo"}) for k in m],
    lambda m: [audit_line(k, reference={**REF, "rotate_cw": 45}) for k in m],
    lambda m: [audit_line(k, reference={**REF, "confidence": {**REF["confidence"], "medium": "sure"}}) for k in m],
])
def test_ingest_validation_stores_nothing(env, tmp_path, bad):
    d, rows, mapping = packet_dir(env, tmp_path)
    write_audit(d, bad(mapping))
    with pytest.raises(ca.IngestError):
        ca.ingest(env, d, rows, "instr")
    assert not (env.results_dir / ca.KIND).exists()


# ---------- report ----------


def rec(i, sets, ref=None, stratum="2000-09|other|scan"):
    return {"asset_id": aid(i), "key": f"c-{i:03d}", "reference": ref or REF, "sets": sets, "stratum": stratum}


def s(wrong=0, invented=0, missing=0, useful=3, colour="colour", rotate=None, medium="print"):
    return {"wrong": wrong, "invented": invented, "missing": missing, "useful": useful, "notes": "SECRET NOTE",
            "shown": {"medium": medium, "digitiser": "flatbed_scanner", "colour_class": colour,
                      "rotate_cw": rotate, "tags": 1}}


def hand_example():
    """10 photos. Reference arm R=gemini-medium-v2 never errs; C=e4b-bf16 errs on photos 1..6.
    Reference colour truth: mono (high) on 1-3, colour on 4-9, mono with low confidence on 10.
    C says mono on 1, 2, 4 and 10 -> precision 2/3, recall 2/3 (10 excluded). R says mono exactly on 1-3 and 10.
    Rotation truth: 90 (high) on photo 1, else 0; R predicts 90 on 1 and 0 elsewhere; C has none (v1)."""
    recs = []
    for i in range(1, 11):
        ref = dict(REF, colour_class="mono" if i <= 3 or i == 10 else "colour", rotate_cw=90 if i == 1 else 0,
                   confidence=dict(REF["confidence"], colour_class="low" if i == 10 else "high"))
        cmono = i in (1, 2, 4, 10)
        rmono = i <= 3 or i == 10
        sets = {"e4b-bf16": s(wrong=1 if i <= 3 else 0, invented=1 if 4 <= i <= 6 else 0, missing=2,
                              useful=2, colour="mono" if cmono else "colour", medium="digital" if i <= 5 else "print"),
                "gemini-medium-v2": s(missing=1, useful=4, colour="mono" if rmono else "colour",
                                      rotate=90 if i == 1 else 0)}
        recs.append(rec(i, sets, ref, stratum="a|mono|scan" if i <= 3 else "b|other|noscan"))
    return recs


def test_report_numbers_paired_ci_mono_precision_and_rotation():
    rep = ca.build_report(hand_example(), {"set": "x"}, b=500, seed=3)
    c, r = rep["arms"]["e4b-bf16"], rep["arms"]["gemini-medium-v2"]
    assert c["n"] == 10 and c["error_rate"]["mean"] == 0.6 and r["error_rate"]["mean"] == 0.0
    assert c["wrong"]["mean"] == 0.3 and c["invented"]["mean"] == 0.3 and c["missing"]["mean"] == 2.0
    assert c["useful"]["mean"] == 2.0 and r["useful"]["mean"] == 4.0
    lo, hi = c["error_rate"]["ci95"]
    assert 0 < lo <= 0.6 <= hi <= 1
    d = rep["paired_vs_reference"]["e4b-bf16"]
    assert d["error_rate"]["diff"] == 0.6 and d["error_rate"]["ci95"][0] > 0  # candidate worse: positive
    assert d["useful"]["diff"] == -2.0 and d["useful"]["ci95"][1] < 0
    assert d["d11_error_rate_pass"] is False
    assert "gemini-medium-v2" not in rep["paired_vs_reference"]
    st = rep["structured"]["e4b-bf16"]
    m = st["overall"]["mono"]
    assert (m["tp"], m["fp"], m["fn"]) == (2, 1, 1) and m["precision"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["recall"] == pytest.approx(2 / 3, abs=1e-4) and m["d11_precision_pass"] is False
    assert rep["structured"]["gemini-medium-v2"]["overall"]["mono"]["precision"] == 1.0
    assert st["overall"]["colour_class"]["n"] == 9 and st["overall"]["medium"]["accuracy"] == 0.5
    assert st["per_stratum"]["a|mono|scan"]["medium"]["accuracy"] == 0.0
    rot = rep["structured"]["gemini-medium-v2"]["overall"]["rotate_cw"]
    assert rot["rotated"] == {"n": 1, "correct": 1, "accuracy": 1.0}
    assert rot["upright"] == {"n": 9, "correct": 9, "accuracy": 1.0}
    assert st["overall"]["rotate_cw"]["rotated"]["n"] == 0  # v1 arm: no rotate_cw
    md = ca.render_md(rep)
    blob = md + json.dumps(rep)
    assert "SECRET" not in blob and "e4b-bf16" in md


def test_report_skips_arms_without_caption():
    recs = hand_example()
    recs[0]["sets"]["e4b-bf16"] = dict(s(), shown=None)
    rep = ca.build_report(recs, {}, b=100)
    assert rep["arms"]["e4b-bf16"]["n"] == 9 and rep["arms"]["e4b-bf16"]["no_caption"] == 1
    # an uncaptioned photo is an error (error_rate over all 10) but adds nothing to the counts (over 9)
    full = ca.build_report(hand_example(), {}, b=100)["arms"]["e4b-bf16"]
    recs2 = hand_example()
    first_err = 1.0 if recs2[0]["sets"]["e4b-bf16"]["wrong"] + recs2[0]["sets"]["e4b-bf16"]["invented"] else 0.0
    assert rep["arms"]["e4b-bf16"]["error_rate"]["mean"] == round(full["error_rate"]["mean"] + (1.0 - first_err) / 10, 4)


# ---------- CLI ----------


def test_cli_caption_sample_writes_ids_only(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from shoebox.cli import main
    from shoebox.evals import judge

    items, frows = corpus()
    d = tmp_path / "sets" / "s"
    d.mkdir(parents=True)
    judge.write_jsonl(d / "items.jsonl", items)
    judge.write_jsonl(d / "queries-v1.jsonl", dev_queries(items))
    frame = tmp_path / "frame.parquet"
    cols = ("source", "relpath", "asset_id", "date", "date_kind", "make", "model", "software", "colour_guess")
    table = [{"source": it["source"], "relpath": it["relpath"], "asset_id": it["asset_id"],
              **frows[it["asset_id"]]} for it in items]
    pq.write_table(pa.Table.from_pylist([{c: r[c] for c in cols} for r in table]), frame)
    assert main(["eval", "caption-sample", "--set", "s", "--n", "40", "--frame", str(frame),
                 "--sets-dir", str(tmp_path / "sets")]) == 0
    rows = [json.loads(x) for x in (d / ca.SAMPLE_FILE).read_text().splitlines()]
    assert len(rows) == 40 and all(set(r) == {"key", "asset_id", "stratum"} for r in rows)
    assert "P/" not in (d / ca.SAMPLE_FILE).read_text() + (d / ca.SAMPLE_META).read_text()


def test_cli_caption_report_no_text(env, tmp_path, monkeypatch):
    from shoebox.cli import main
    from shoebox.commands import eval as eval_cmd

    d, rows, mapping = packet_dir(env, tmp_path)
    write_audit(d, [audit_line(k) for k in mapping])
    sets = tmp_path / "sets" / "s"
    sets.mkdir(parents=True)
    (sets / ca.SAMPLE_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(eval_cmd, "_cfg", lambda: env)
    assert main(["eval", "caption-ingest", "--set", "s", "--dir", str(d), "--sets-dir", str(tmp_path / "sets")]) == 0
    out = tmp_path / "runs"
    assert main(["eval", "caption-report", "--set", "s", "--date", "2026-10-14", "--bootstrap", "50",
                 "--out-dir", str(out)]) == 0
    blob = "".join(p.read_text() for p in out.glob("2026-10-14_s-caption-audit.*"))
    assert len(list(out.iterdir())) == 2 and "SECRET" not in blob and "gemini-medium-v2" in blob
