"""Colour/mono heuristic scoring and tuning (tasks 7.2-7.3): synthetic frame rows, labels and stores only."""

import itertools
import json
from pathlib import Path

import pytest

from shoebox import config, privacy, quarantine
from shoebox.evals import colour
from shoebox.runners import rules
from shoebox.runners.rules import (
    DEFAULT_THRESHOLDS,
    ColourThresholds,
    Header,
    classify_colour,
)


def aid(i: int) -> str:
    return f"{i:04x}" + "d" * 60


def _old_classify(stats: dict, h: Header) -> tuple[str, float]:
    """classify_colour as it was before thresholds became data (frozen copy for the regression grid)."""
    if h.grey:
        return "mono", 0.95
    c50, c95, hue_r = stats["c50"], stats["c95"], stats["hue_r"]
    if c95 < 2:
        return "mono", 0.9
    if c95 < 3.5:
        return "mono", 0.75
    if c95 >= 30:
        return "colour", 0.85
    if hue_r >= 0.92 and c50 >= 6:
        return "toned", 0.7
    if c95 >= 15 and hue_r < 0.75:
        return "colour", 0.75
    return "unknown", 0.3


# ---------- thresholds as data ----------


def test_default_thresholds_reproduce_the_old_rule_on_a_grid():
    c95s = [0, 1.99, 2, 2.5, 3.49, 3.5, 5, 6, 14.99, 15, 20, 29.99, 30, 60]
    c50s = [0, 3, 5.99, 6, 10, 40]
    hues = [0, 0.5, 0.7499, 0.75, 0.8, 0.9199, 0.92, 1.0]
    n = 0
    for c95, c50, hr, grey in itertools.product(c95s, c50s, hues, (False, True)):
        s = {"c95": c95, "c50": c50, "hue_r": hr, "cmean": c50, "chromatic": 0.5}
        assert classify_colour(s, Header(grey=grey)) == _old_classify(s, Header(grey=grey)), s
        assert classify_colour(s, Header(grey=grey), DEFAULT_THRESHOLDS) == _old_classify(s, Header(grey=grey))
        n += 1
    assert n > 1000


def test_thresholds_are_frozen_and_change_the_outcome():
    with pytest.raises(AttributeError):
        DEFAULT_THRESHOLDS.mono_c95 = 9  # type: ignore[misc]
    s = {"c95": 3.0, "c50": 1.0, "hue_r": 0.2}
    assert classify_colour(s, Header())[0] == "mono"
    assert classify_colour(s, Header(), ColourThresholds(mono_c95=2.5))[0] == "unknown"
    t = ColourThresholds(toned_hue_r=0.5, toned_c50=0.5)
    assert classify_colour({"c95": 10, "c50": 1.0, "hue_r": 0.6}, Header(), t)[0] == "toned"


# ---------- origin strata ----------


@pytest.mark.parametrize("row,want", [
    ({"make": "Nikon", "model": "LS-5000", "ext": "tif"}, "scan"),
    ({"make": "", "model": "", "software": "VueScan 9", "ext": "jpg"}, "scan"),
    ({"make": "Canon", "model": "CanoScan 9000F", "ext": "tif"}, "scan"),
    ({"make": "Fuji", "model": "Frontier SP-3000", "ext": "jpg"}, "scan"),
    ({"make": "SEIKO EPSON CORP.", "model": "Perfection V600", "ext": "jpg"}, "scan"),
    ({"make": "Apple", "model": "iPhone 12", "ext": "heic"}, "phone"),
    ({"make": "samsung", "model": "SM-G991B", "ext": "jpg"}, "phone"),
    ({"make": "Google", "model": "Pixel 7", "ext": "jpg"}, "phone"),
    ({"make": "NIKON CORPORATION", "model": "NIKON D70", "ext": "nef"}, "camera"),
    ({"make": "Sony", "model": "ILCE-7M3", "ext": "arw"}, "camera"),
    ({"make": "FUJIFILM", "model": "X-T4", "ext": "jpg"}, "camera"),
    ({"make": "EASTMAN KODAK COMPANY", "model": "KODAK EASYSHARE C743", "ext": "jpg"}, "camera"),
    ({"make": "SEIKO EPSON CORP.", "model": "R-D1", "ext": "jpg"}, "camera"),
    ({"make": "", "model": "", "ext": "cr2"}, "camera"),
    ({"make": "", "model": "", "ext": "jpg"}, "received"),
    ({"make": None, "model": None, "software": None, "ext": "png"}, "received"),
    ({"make": "EASTMAN KODAK COMPANY", "model": "", "ext": "jpg"}, "received"),  # mixed maker, no camera model
])
def test_origin_stratum(row, want):
    assert colour.origin_stratum(row) == want


# ---------- frame guess ----------


def frow(i=0, c95=1.0, c50=0.5, hue=0.1, guess=None, **kw):
    s = {"c95": c95, "c50": c50, "hue_r": hue}
    g = guess or classify_colour(s, Header())[0]
    return {"asset_id": aid(i), "source": "own", "relpath": f"P/{i}.jpg", "kind": "image", "ext": "jpg",
            "make": "Canon", "model": "EOS", "software": None, "chroma_p95": c95, "chroma_median": c50,
            "chroma_mean": c50, "chromatic": 0.1, "hue_r": hue, "colour_guess": g, "group_id": None, **kw}


def test_guess_recomputes_from_frame_columns_and_infers_header_grey():
    assert colour.guess(frow(c95=3.0), ColourThresholds(mono_c95=2.5)) == "unknown"
    # header-grey: the frame says mono although the stats alone do not (grey is not a frame column)
    grey = frow(c95=8.0, c50=2.0, hue=0.3, guess="mono")
    assert colour.guess(grey, DEFAULT_THRESHOLDS) == "mono"
    assert colour.guess(grey, ColourThresholds(mono_c95=0.1)) == "mono"
    nostats = {**frow(), "chroma_p95": None, "chroma_median": None, "hue_r": None, "colour_guess": "toned"}
    assert colour.guess(nostats, ColourThresholds(mono_c95=0.1)) == "toned"
    assert colour.guess(frow(c95=8.0, guess="colour")) == "colour"  # t=None: the frame's own guess


# ---------- scoring ----------


def test_score_counts_mono_per_stratum_and_confusion():
    rows = [("scan", "mono", "mono")] * 5 + [("scan", "mono", "colour"), ("phone", "colour", "mono"),
                                             ("phone", "toned", "toned"), ("camera", None, "mono")]
    sc = colour.score([{"stratum": s, "guess": g, "ref": r} for s, g, r in rows])
    o = sc["overall"]
    assert (o["tp"], o["fp"], o["fn"], o["n"]) == (5, 1, 2, 9)
    assert o["precision"] == pytest.approx(5 / 6, abs=1e-4) and o["recall"] == pytest.approx(5 / 7, abs=1e-4)
    assert sc["per_stratum"]["scan"]["fp"] == 1 and sc["per_stratum"]["phone"]["fn"] == 1
    assert sc["per_stratum"]["received"]["n"] == 0
    assert sc["confusion"]["mono"]["colour"] == 1 and sc["confusion"]["none"]["mono"] == 1


def audit_rec(i, ref, conf="high"):
    return {"asset_id": aid(i), "key": f"c-{i:03d}", "status": "ok",
            "reference": {"description": "SECRET-DESC", "colour_class": ref, "confidence": {"colour_class": conf}}}


def test_audit_scores_frame_guess_against_trusted_reference_only():
    recs = [audit_rec(0, "mono"), audit_rec(1, "mono"), audit_rec(2, "colour"), audit_rec(3, "mono", "low"),
            audit_rec(4, "mono")]
    frows = {aid(0): frow(0, guess="mono", make="Nikon", model="LS-50"), aid(1): frow(1, guess="mono"),
             aid(2): frow(2, guess="mono"), aid(3): frow(3, guess="mono"), aid(4): frow(4, c95=40, guess="colour")}
    rep = colour.audit(recs, frows)
    o = rep["frame"]["overall"]
    assert (o["tp"], o["fp"], o["fn"]) == (2, 1, 1)
    assert rep["n_trusted"] == 4 and rep["reference_mono"] == 3 and rep["predicted_mono"] == 3
    assert rep["frame"]["per_stratum"]["scan"]["tp"] == 1
    assert "SECRET" not in json.dumps(rep) and "P/" not in json.dumps(rep)


# ---------- draw ----------


def cfg_for(tmp_path, consent=True):
    src = config.Source("own", tmp_path / "own", "owner", synced=False, hosted_consent=consent,
                        hosted_models=("gemini", "claude") if consent else ())
    return config.Config(shoebox_root=tmp_path / "sb", sources={"own": src})


GEAR = {"scan": ("Nikon", "LS-50"), "phone": ("Apple", "iPhone 8"), "camera": ("Canon", "EOS 5D"),
        "received": ("", "")}


def corpus(n_per=60):
    rows, i = [], 0
    for (make, model) in GEAR.values():
        for k in range(n_per):
            kind = k % 4  # mono, toned, near (colour c95 20), plain colour
            st = [{"c95": 1.0}, {"c95": 10, "c50": 8, "hue": 0.95}, {"c95": 20, "hue": 0.9}, {"c95": 60, "hue": 0.3}][kind]
            rows.append(frow(i, make=make, model=model, **st))
            i += 1
    return rows


def test_draw_buckets_strata_split_and_eligibility(tmp_path):
    cfg = cfg_for(tmp_path)
    rows = corpus()
    rows.append({**rows[0], "relpath": "P/copy.jpg"})  # a second location of asset 0: one row per asset
    rows.append({**frow(9000), "kind": "video"})
    derived = {r["asset_id"] for r in rows} - {aid(1)}
    rows[4]["group_id"] = rows[8]["group_id"] = "g1"
    qpath = tmp_path / "q.jsonl"
    quarantine.flag([aid(12)], "nudity", path=qpath)
    never = privacy.NeverSend(frozenset({aid(16)}), (), ())
    items, meta = colour.draw(rows, derived, {aid(20)}, cfg, seed=7, n_mono=40, n_near=40, floor=5,
                              never=never, quarantine=quarantine.Checker.load(qpath))
    ids = [it["asset_id"] for it in items]
    assert len(ids) == len(set(ids)) == 80
    assert not {aid(1), aid(12), aid(16), aid(20), aid(9000)} & set(ids)
    assert not {aid(4), aid(8)} <= set(ids)
    assert {it["bucket"] for it in items} == {"mono", "near"}
    assert all(it["split"] in ("dev", "test") for it in items)
    assert 20 <= sum(it["split"] == "dev" for it in items) <= 60
    for it in items:
        assert {"asset_id", "source", "relpath", "ext", "group_id", "origin"} <= set(it)
        assert it["origin"] in colour.STRATA
    per = meta["buckets"]["mono"]["drawn"]
    assert all(per[s] >= 5 for s in colour.STRATA)
    assert meta["seed"] == 7 and meta["skipped"]["quarantined"] >= 1 and meta["skipped"]["never_send"] >= 1
    again, _ = colour.draw(rows, derived, {aid(20)}, cfg, seed=7, n_mono=40, n_near=40, floor=5,
                           never=never, quarantine=quarantine.Checker.load(qpath))
    assert again == items
    assert "P/" not in json.dumps(meta)


def test_draw_excludes_capture_group_siblings_of_audit_assets(tmp_path):
    rows = corpus(8)
    rows[0]["group_id"] = rows[4]["group_id"] = "g2"
    kw = {"seed": 1, "n_mono": 100, "n_near": 100, "floor": 1}
    ids = {it["asset_id"] for it in colour.draw(rows, {r["asset_id"] for r in rows}, set(), cfg_for(tmp_path), **kw)[0]}
    assert aid(0) in ids or aid(4) in ids
    items, meta = colour.draw(rows, {r["asset_id"] for r in rows}, {aid(0)}, cfg_for(tmp_path), **kw)
    assert not {aid(0), aid(4)} & {it["asset_id"] for it in items}
    assert meta["dropped"]["caption_audit_group"] == 1


def test_near_threshold_bucket_rule():
    assert colour.bucket(frow(c95=1.0)) == "mono"
    assert colour.bucket(frow(c95=2.5)) == "near"  # mono with c95 >= 2
    assert colour.bucket(frow(c95=10, c50=8, hue=0.95)) == "mono"  # toned
    assert colour.bucket(frow(c95=44, hue=0.3)) == "near"
    assert colour.bucket(frow(c95=46, hue=0.3)) is None
    assert colour.bucket(frow(c95=10, hue=0.8)) == "near"  # unknown


def test_draw_logs_shortfalls_below_the_floor(tmp_path):
    rows = [r for r in corpus() if not (r["make"] == "Apple" and int(r["asset_id"][:4], 16) % 4 != 0)][:]
    phone_mono = [r for r in rows if r["make"] == "Apple"]
    _items, meta = colour.draw(rows, {r["asset_id"] for r in rows}, set(), cfg_for(tmp_path), seed=1,
                              n_mono=40, n_near=40, floor=25)
    assert meta["buckets"]["mono"]["shortfall"]["phone"] == 25 - len(phone_mono)
    assert meta["buckets"]["near"]["shortfall"]["phone"] == 25


def test_draw_propagates_privacy_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise privacy.PrivacyError("3 image(s) at restricted locations have no pixel hash")

    monkeypatch.setattr(privacy, "filter_items", boom)
    with pytest.raises(privacy.PrivacyError, match="pixel hash"):
        colour.draw(corpus(4), {aid(i) for i in range(16)}, set(), cfg_for(tmp_path), seed=1)


def test_draw_uses_claude_provider(tmp_path):
    items, meta = colour.draw(corpus(8), {aid(i) for i in range(32)}, set(), cfg_for(tmp_path, consent=False),
                              seed=1, n_mono=4, n_near=4, floor=1)
    assert items == [] and meta["skipped"]["no_consent"] > 0


# ---------- reference ----------


def test_trusted_label_from_opus_records():
    ok = {"status": "ok", "output": {"colour_class": "mono", "confidence": {"colour_class": 0.9}}}
    assert colour.trusted_label(ok) == "mono"
    assert colour.trusted_label({**ok, "output": {**ok["output"], "confidence": {"colour_class": 0.4}}}) is None
    assert colour.trusted_label({**ok, "status": "error"}) is None
    assert colour.trusted_label(None) is None


# ---------- tune / freeze / test ----------


def eval_rows(n_per=30):
    """Per stratum: mono at c95 1.0 (ref mono), near-mono at c95 3.0 (ref mono in scans, colour elsewhere),
    colour at c95 50 (ref colour)."""
    out, i = [], 0
    for origin in colour.STRATA:
        for k in range(n_per):
            c95, ref = [(1.0, "mono"), (3.0, "mono" if origin == "scan" else "colour"), (50, "colour")][k % 3]
            out.append({"asset_id": aid(i), "stratum": origin, "row": frow(i, c95=c95), "ref": ref,
                        "split": "dev" if k % 2 else "test", "gemini": "colour" if c95 == 3.0 else ref})
            i += 1
    return out


def test_tune_maximises_recall_subject_to_precision_in_every_stratum():
    rows = [r for r in eval_rows() if r["split"] == "dev"]
    res = colour.tune(rows, gate=0.98, min_pred=3)
    assert res["chosen"]["mono_c95"] < 3.0  # 3.5 lets the c95-3.0 colour rows in as mono outside scans
    assert all(v["status"] == "pass" for v in res["strata"].values())
    assert res["grid"] and all("mono_c95" in g for g in res["grid"])
    assert res["combination"]["overall"]["fp"] == 0


def test_tune_marks_strata_with_too_few_predicted_mono_insufficient():
    rows = [r for r in eval_rows() if r["split"] == "dev"]
    res = colour.tune(rows, gate=0.98, min_pred=10)
    assert all(v["status"] == "insufficient" for v in res["strata"].values())


def test_freeze_and_test_refuses_a_second_run(tmp_path):
    rows = eval_rows()
    tuned = colour.tune([r for r in rows if r["split"] == "dev"], gate=0.98, min_pred=3)
    fpath = tmp_path / "colour-thresholds.json"
    doc = colour.freeze(fpath, tuned, {"dev_rows": 60})
    assert doc["hash"] == colour.thresholds_hash(colour.load_frozen(fpath)[0])
    runs = tmp_path / "runs"
    test_rows = [r for r in rows if r["split"] == "test"]
    led = tmp_path / "ledger.jsonl"
    rep = colour.test_report(test_rows, fpath, runs, "2026-10-11", gate=0.98, min_pred=3, ledger=led)
    assert rep["frozen_hash"] == doc["hash"] and rep["test"]["overall"]["precision"] == 1.0
    assert (runs / "2026-10-11_colour-heuristic.json").exists() and (runs / "2026-10-11_colour-heuristic.md").exists()
    with pytest.raises(colour.AlreadyScored):
        colour.test_report(test_rows, fpath, runs, "2026-10-12", gate=0.98, min_pred=3, ledger=led)
    with pytest.raises(colour.AlreadyScored):  # another --out-dir does not skip the guard
        colour.test_report(test_rows, fpath, tmp_path / "elsewhere", "2026-10-12", gate=0.98, min_pred=3, ledger=led)
    with pytest.raises(ValueError, match="reason"):
        colour.test_report(test_rows, fpath, runs, "2026-10-12", gate=0.98, min_pred=3, force_reason="", ledger=led)
    again = colour.test_report(test_rows[:5], fpath, runs, "2026-10-11", gate=0.98, min_pred=3,
                               force_reason="reference relabelled", ledger=led)
    assert again["force_rescore_reason"] == "reference relabelled"
    first = json.loads((runs / "2026-10-11_colour-heuristic.json").read_text())
    assert first["test"]["overall"]["n"] == len(test_rows) and "force_rescore_reason" not in first
    assert again["previous"] == "2026-10-11_colour-heuristic.json"
    assert (runs / f"{again['report']}.json").exists() and again["report"] != "2026-10-11_colour-heuristic"
    assert not (tmp_path / "elsewhere").exists()


def test_test_report_refuses_without_full_labelled_coverage(tmp_path):
    rows = eval_rows()
    fpath, runs, led = tmp_path / "t.json", tmp_path / "runs", tmp_path / "ledger.jsonl"
    colour.freeze(fpath, colour.tune([r for r in rows if r["split"] == "dev"], gate=0.98, min_pred=3), {})
    test_rows = [r for r in rows if r["split"] == "test"]
    with pytest.raises(colour.NotReady, match="0 of 0"):
        colour.test_report([], fpath, runs, "2026-10-11", gate=0.98, min_pred=3, ledger=led)
    with pytest.raises(colour.NotReady, match="50 of 60"):
        colour.test_report(test_rows, fpath, runs, "2026-10-11", gate=0.98, min_pred=3, ledger=led,
                           coverage=(50, 60))
    assert not runs.exists() and not led.exists()
    rep = colour.test_report(test_rows, fpath, runs, "2026-10-11", gate=0.98, min_pred=3, ledger=led,
                             coverage=(60, 60))
    assert rep["coverage"] == {"labelled": 60, "total": 60}


def test_report_has_counts_only_and_comparisons(tmp_path):
    rows = eval_rows()
    fpath = tmp_path / "t.json"
    colour.freeze(fpath, colour.tune([r for r in rows if r["split"] == "dev"], gate=0.98, min_pred=3), {})
    for r in rows:
        r["row"]["relpath"] = "Secret/Folder/IMG_0001.jpg"
    rep = colour.test_report([r for r in rows if r["split"] == "test"], fpath, tmp_path / "runs", "2026-10-11",
                             gate=0.98, min_pred=3, ledger=tmp_path / "l.jsonl", audit={"frame": {"overall": {"tp": 35}}},
                             comparisons=colour.comparisons(tmp_path / "none.json"))
    md = (tmp_path / "runs" / "2026-10-11_colour-heuristic.md").read_text()
    assert "Secret" not in md and "IMG_0001" not in md and "Secret" not in json.dumps(rep)
    assert "0.974" in md and "toned" in md
    for s in colour.STRATA:
        assert s in md


def test_comparisons_from_caption_audit_json(tmp_path):
    p = tmp_path / "a.json"
    from shoebox.evals import caption_audit as ca

    def arm(ok, bad):  # the real build_report shape: structured[arm]["overall"]["mono"]["precision"]
        conf = dict.fromkeys(("medium", "digitiser", "colour_class", "rotate_cw"), "high")
        ref = {"medium": "print", "digitiser": "x", "colour_class": "mono", "rotate_cw": 0, "confidence": conf}
        pairs = [(ref, {"colour_class": "mono"})] * ok + [({**ref, "colour_class": "colour"}, {"colour_class": "mono"})] * bad
        return {"overall": ca._structured(pairs)}

    p.write_text(json.dumps({"structured": {"gemini-medium-v2": arm(37, 1), "g12b-q4-v2": arm(30, 0)}}))
    c = colour.comparisons(p)
    assert c["gemini-medium-v2"]["precision"] == ca._ratio(37, 38)
    assert c["g12b-q4-v2"]["precision"] == 1.0 and c["g12b-q4-v2"]["source"].endswith("a.json")
    c = colour.comparisons(tmp_path / "missing.json")
    assert c["gemini-medium-v2"]["precision"] == 0.974 and "RECOMMENDATION" in c["gemini-medium-v2"]["source"]


def test_rules_module_exports_threshold_names():
    assert rules.DEFAULT_THRESHOLDS == ColourThresholds()


# ---------- CLI ----------


def write_frame(path, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.Table.from_pylist([{c: r.get(c) for c in colour.FRAME_COLUMNS} for r in rows]), path)
    return path


def label(ref, conf=0.9):
    return {"status": "ok", "output": {"colour_class": ref, "confidence": {"colour_class": conf}}}


def test_cli_draw_tune_test(tmp_path, monkeypatch, capsys):
    from shoebox.cli import main
    from shoebox.commands import eval as eval_cmd
    from shoebox.evals import judge

    cfg = cfg_for(tmp_path)
    monkeypatch.setattr(eval_cmd, "_cfg", lambda: cfg)
    monkeypatch.setattr(privacy, "NEVER_SEND_PATH", tmp_path / "never-send.txt")
    rows = corpus(40)
    for r in rows:
        r["relpath"] = f"Secret/{r['relpath']}"
    frame = write_frame(tmp_path / "frame.parquet", rows)
    idx = cfg.derived_dir / "infer-v1" / "index.jsonl"
    idx.parent.mkdir(parents=True)
    idx.write_text("".join(json.dumps({"asset_id": r["asset_id"], "status": "ok"}) + "\n" for r in rows))
    sets = tmp_path / "sets"
    (sets / "eval-1k").mkdir(parents=True)
    judge.write_jsonl(sets / "eval-1k" / "caption-audit-sample.jsonl", [{"key": "c-001", "asset_id": aid(0)}])
    judge.write_jsonl(sets / "eval-1k" / "items.jsonl", [{"asset_id": aid(0), "source": "own", "relpath": "x"}])
    common = ["--frame", str(frame), "--sets-dir", str(sets)]
    assert main(["eval", "colour-draw", *common, "--n-mono", "40", "--n-near", "40", "--floor", "5"]) == 0
    items = [json.loads(x) for x in (sets / colour.SET_NAME / "items.jsonl").read_text().splitlines()]
    assert len(items) == 80 and aid(0) not in {it["asset_id"] for it in items}
    assert "Secret" not in capsys.readouterr().out + (sets / colour.SET_NAME / colour.SAMPLE_META).read_text()

    by = {r["asset_id"]: r for r in rows}
    refs = {it["asset_id"]: label("mono" if by[it["asset_id"]]["chroma_p95"] < 2 else "colour") for it in items}
    monkeypatch.setattr(colour, "load_labels", lambda cfg, cand: refs if cand == colour.REFERENCE_CANDIDATE else {})
    frozen, runs = tmp_path / "frozen.json", tmp_path / "runs"
    monkeypatch.setattr(colour, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    assert main(["eval", "colour-tune", *common, "--frozen", str(frozen), "--min-pred", "2"]) == 0
    assert json.loads(frozen.read_text())["hash"]
    test = ["eval", "colour-test", *common, "--frozen", str(frozen), "--min-pred", "2", "--out-dir", str(runs),
            "--date", "2026-10-11"]
    assert main(test) == 0
    out = capsys.readouterr()
    assert main(test) == 1
    assert "already scored" in capsys.readouterr().err
    assert main([*test, "--force-rescore", "new labels"]) == 0
    blob = out.out + "".join(p.read_text() for p in runs.iterdir())
    assert "Secret" not in blob and "Pictures" not in blob


def test_cli_draw_privacy_error_exits_1(tmp_path, monkeypatch, capsys):
    from shoebox.cli import main
    from shoebox.commands import eval as eval_cmd

    monkeypatch.setattr(eval_cmd, "_cfg", lambda: cfg_for(tmp_path))

    def boom(*a, **k):
        raise privacy.PrivacyError("no pixel hash for 3 images")

    monkeypatch.setattr(privacy, "filter_items", boom)
    frame = write_frame(tmp_path / "f.parquet", corpus(4))
    (tmp_path / "sets" / "eval-1k").mkdir(parents=True)
    (tmp_path / "sets" / "eval-1k" / "caption-audit-sample.jsonl").write_text("")
    idx = cfg_for(tmp_path).derived_dir / "infer-v1" / "index.jsonl"
    idx.parent.mkdir(parents=True)
    idx.write_text(json.dumps({"asset_id": aid(0), "status": "ok"}) + "\n")
    assert main(["eval", "colour-draw", "--frame", str(frame), "--sets-dir", str(tmp_path / "sets")]) == 1
    assert "pixel hash" in capsys.readouterr().err
    assert not (tmp_path / "sets" / colour.SET_NAME).exists()


def test_frozen_repo_thresholds_are_the_recorded_ones():
    """The thresholds the 7.3 test half was scored with (evals/runs/2026-10-12_colour-heuristic.md); changing
    them needs a new tune/freeze and a new test-half score, not an edit."""
    import tomllib

    root = Path(__file__).parents[1]
    t, _ = colour.load_frozen(root / "evals" / "colour-thresholds.json")
    assert colour.thresholds_hash(t) == "2228f47c8e77df69"
    assert t.mono_c95 == 2.0
    gates = tomllib.loads((root / "evals" / "gates.toml").read_text())
    assert gates["colour_filter"]["heuristic_hash"] == "2228f47c8e77df69"
    assert gates["colour_filter"]["source"] == {"scan": "heuristic", "received": "heuristic",
                                                "phone": "unavailable", "camera": "unavailable"}
