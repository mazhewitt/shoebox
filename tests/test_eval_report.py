import json

import pytest
from test_eval_helpers import label

from shoebox.cli import main
from shoebox.evals import report


def _field_details(lab, status, expected):
    out = [
        {
            "name": "label-status",
            "graderType": "label-status",
            "passed": status == "ok",
            "score": 1.0,
        }
    ]
    for f, exp in expected.items():
        pred = (lab or {}).get(f)
        md = {
            "field": f,
            "status": status,
            "predicted": pred,
            "abstained": pred == "unknown",
        }
        if (
            exp is None or exp == "unknown"
        ):  # owner "unknown" = unlabelled (as the grader does)
            md |= {
                "expected": None,
                "unlabelled": True,
                "owner_unknown": exp == "unknown",
            }
            passed = True
        else:
            md |= {"expected": exp, "unlabelled": False}
            passed = status == "ok" and pred == exp
        out.append(
            {
                "name": f"field-match {f}",
                "graderType": "field-match",
                "passed": passed,
                "metadata": md,
            }
        )
    return out


def trial_line(
    cand,
    aid,
    model_id,
    ph,
    status="ok",
    lab=None,
    latency=1000,
    usd=0.0,
    expected=None,
    mode="replay",
    set_name="pilot-v0",
    candidates_file=None,
):
    expected = expected or {"medium": None, "digitiser": None, "colour_class": None}
    ev = {
        "shoebox": {
            "set": set_name, "mode": mode, "candidates_file": candidates_file, "candidate": cand, "asset_id": aid, "model_id": model_id,
            "params_hash": ph, "status": status, "usd": usd, "latency_ms": latency,
            "input_tokens": 100, "output_tokens": 20, "thinking_tokens": None,
        }
    }  # fmt: skip
    output = json.dumps(lab) if status == "ok" else f"[shoebox:{status}] x"
    return {
        "type": "trial-result",
        "stimulus": aid,
        "model": cand,
        "status": "success",
        "gradeResult": {
            "passed": True,
            "details": _field_details(
                lab if status == "ok" else None, status, expected
            ),
        },
        "trajectory": {
            "stimulus": {"name": aid, "tags": {}},
            "events": [{"type": "custom", "data": ev}],
            "output": output,
            "metadata": {"model": cand},
        },
    }


def missing_line(cand, aid):
    msg = f"shoebox-vlm replay: missing key (candidate={cand}, asset_id={aid}, model_id=m, params_hash=p) in /x"
    return {
        "type": "trial-result",
        "stimulus": aid,
        "model": cand,
        "status": "error",
        "error": msg,
        "trajectory": None,
    }


@pytest.fixture
def results(tmp_path):
    exp_a1 = {"medium": "digital", "digitiser": None, "colour_class": "colour"}
    lines = [
        trial_line(
            "A", "a1", "rules-v1", "pa", lab=label(), latency=10, expected=exp_a1
        ),
        trial_line(
            "A", "a2", "rules-v1", "pa", lab=label(colour_class="unknown"), latency=20
        ),
        trial_line("A", "a3", "rules-v1", "pa", lab=label(), latency=30),
        trial_line(
            "B",
            "a1",
            "ollama/x",
            "pb",
            lab=label(medium="print"),
            latency=1000,
            usd=0.002,
            expected=exp_a1,
        ),
        trial_line(
            "B", "a2", "ollama/x", "pb", lab=label(colour_class="unknown"), latency=2000
        ),
        trial_line("B", "a3", "ollama/x", "pb", status="invalid", latency=3000),
        missing_line("B", "a4"),
        {"type": "run-summary", "passed": False},
    ]
    p = tmp_path / "results.jsonl"
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return p


def test_status_validity_and_cost(results):
    rep = report.build_report(
        report.load_trials(results), "pilot-v0", results, "2026-10-10"
    )
    a, b = rep["candidates"]["A"], rep["candidates"]["B"]
    assert a["model_id"] == "rules-v1" and a["params_hash"] == "pa"
    assert b["status_counts"] == {
        "ok": 2,
        "invalid": 1,
        "error": 0,
        "missing": 1,
        "exec_error": 0,
    }
    assert b["n"] == 4 and b["validity_rate"] == 0.5
    assert a["validity_rate"] == 1.0
    assert b["usd"] == pytest.approx(0.002)
    assert b["latency_ms"]["p50"] == 2000 and b["latency_ms"]["p90"] == pytest.approx(
        2800
    )
    assert b["images_per_hour"] == pytest.approx(3_600_000 / 2000)
    assert a["tokens"]["input"] == 300
    # a missing key makes the run ineligible for any gate
    assert rep["gate_eligible"] is False and rep["modes"] == ["replay"]


def test_fields_distribution_abstain_accuracy(results):
    rep = report.build_report(
        report.load_trials(results), "pilot-v0", results, "2026-10-10"
    )
    a, b = rep["candidates"]["A"]["fields"], rep["candidates"]["B"]["fields"]
    assert a["colour_class"]["distribution"] == {"colour": 2, "unknown": 1}
    assert a["colour_class"]["abstain_rate"] == pytest.approx(1 / 3)
    # accuracy only over labelled items, with n
    assert a["medium"]["labelled_n"] == 1 and a["medium"]["accuracy"] == 1.0
    assert b["medium"]["labelled_n"] == 1 and b["medium"]["accuracy"] == 0.0
    assert a["digitiser"]["labelled_n"] == 0 and a["digitiser"]["accuracy"] is None


def test_pairwise_agreement(results):
    rep = report.build_report(
        report.load_trials(results), "pilot-v0", results, "2026-10-10"
    )
    med = rep["agreement"]["medium"]["A"]["B"]
    assert med == {
        "agree": 1,
        "n": 2,
        "rate": 0.5,
    }  # a1 differs, a2 agrees; a3 invalid for B
    assert rep["agreement"]["colour_class"]["B"]["A"]["rate"] == 1.0


def test_regraded_lines_without_type(tmp_path):
    line = trial_line("A", "a1", "rules-v1", "pa", lab=label())
    regraded = {
        "runId": "r",
        "status": "success",
        "trajectory": line["trajectory"],
        "gradeResult": line["gradeResult"],
    }
    err = {
        "runId": "r",
        "status": "error",
        "trajectory": None,
        "gradeResult": None,
        "error": missing_line("C", "a9")["error"],
    }
    p = tmp_path / "regraded.jsonl"
    p.write_text(json.dumps(regraded) + "\n" + json.dumps(err) + "\n")
    trials = report.load_trials(p)
    assert [(t.candidate, t.asset_id, t.status) for t in trials] == [
        ("A", "a1", "ok"),
        ("C", "a9", "missing"),
    ]


def test_live_mode_not_gate_eligible(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(
        json.dumps(trial_line("A", "a1", "m", "p", lab=label(), mode="live")) + "\n"
    )
    rep = report.build_report(report.load_trials(p), "pilot-v0", p, "2026-10-10")
    assert rep["gate_eligible"] is False and rep["modes"] == ["live"]


def test_cli_report_writes_json_and_md(results, tmp_path):
    out = tmp_path / "runs"
    rc = main(
        [
            "eval",
            "report",
            "--results",
            str(results),
            "--date",
            "2026-10-10",
            "--out-dir",
            str(out),
        ]
    )
    assert rc == 0
    doc = json.loads((out / "2026-10-10_pilot-v0.json").read_text())
    assert doc["set"] == "pilot-v0" and set(doc["candidates"]) == {"A", "B"}
    md = (out / "2026-10-10_pilot-v0.md").read_text()
    assert "| A | `rules-v1` | `pa` |" in md and "Agreement on `medium`" in md


def test_percentile():
    assert report.percentile([], 50) is None
    assert report.percentile([5], 90) == 5
    assert report.percentile([1, 2, 3, 4], 50) == 2.5


def _report(tmp_path, lines, **kw):
    p = tmp_path / "r.jsonl"
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return report.build_report(
        report.load_trials(p), kw.pop("set_name", None), p, "2026-10-10", **kw
    )


def test_abstain_counts_toward_coverage_not_precision(tmp_path):
    lab = {"medium": "digital", "digitiser": None, "colour_class": None}
    rep = _report(
        tmp_path,
        [
            trial_line("A", "a1", "m", "p", lab=label(), expected=lab),
            trial_line("A", "a2", "m", "p", lab=label(medium="unknown"), expected=lab),
            trial_line("A", "a3", "m", "p", lab=label(medium="print"), expected=lab),
        ],
    )
    med = rep["candidates"]["A"]["fields"]["medium"]
    assert med["labelled_n"] == 3
    assert med["accuracy"] == pytest.approx(1 / 3)
    assert med["coverage"] == pytest.approx(2 / 3)  # the abstain lowers coverage ...
    assert med["precision"] == pytest.approx(1 / 2)  # ... not precision


def test_owner_unknown_is_unlabelled_and_precision_bounded(tmp_path):
    unk = {"medium": "unknown", "digitiser": None, "colour_class": None}
    dig = {"medium": "digital", "digitiser": None, "colour_class": None}
    legacy = trial_line("A", "a3", "m", "p", lab=label(medium="unknown"), expected=dig)
    md = legacy["gradeResult"]["details"][1][
        "metadata"
    ]  # an older grader: expected "unknown", scored
    md |= {"expected": "unknown", "unlabelled": False}
    legacy["gradeResult"]["details"][1]["passed"] = True
    rep = _report(
        tmp_path,
        [
            trial_line("A", "a1", "m", "p", lab=label(), expected=dig),
            trial_line("A", "a2", "m", "p", lab=label(medium="unknown"), expected=unk),
            legacy,
        ],
    )
    med = rep["candidates"]["A"]["fields"]["medium"]
    assert med["labelled_n"] == 1 and med["owner_unknown_n"] == 2
    assert med["accuracy"] == 1.0 and med["coverage"] == 1.0 and med["precision"] == 1.0
    for f in report.FIELDS:
        p = rep["candidates"]["A"]["fields"][f]["precision"]
        assert p is None or 0 <= p <= 1


def test_replayed_error_record_not_gate_eligible(tmp_path):
    rep = _report(
        tmp_path,
        [
            trial_line("A", "a1", "m", "p", lab=label()),
            trial_line("A", "a2", "m", "p", status="error"),
        ],
    )
    assert rep["candidates"]["A"]["status_counts"]["error"] == 1
    assert rep["gate_eligible"] is False
    assert any("error" in b for b in rep["gate_blockers"])


def _cands_file(tmp_path, items, skipped, sha=None):
    p = tmp_path / "candidates.json"
    p.write_text(
        json.dumps(
            {
                "set": "pilot-v0",
                "items": items,
                "skipped_items": skipped,
                "items_sha256": sha or "s1",
            }
        )
    )
    return p


def test_completeness_skipped_and_unrun_items_block_gate(tmp_path):
    cf = _cands_file(tmp_path, ["a1", "a2", "a3"], {"a3": "no ok derived image"})
    lines = [
        trial_line("A", "a1", "m", "p", lab=label(), candidates_file=str(cf)),
        trial_line("A", "a2", "m", "p", lab=label(), candidates_file=str(cf)),
        trial_line("B", "a1", "n", "q", lab=label(), candidates_file=str(cf)),
    ]
    rep = _report(tmp_path, lines)
    assert rep["set_items"] == {
        "n": 3,
        "skipped": {"a3": "no ok derived image"},
        "candidates_file": str(cf),
    }
    assert rep["candidates"]["A"]["not_run"] == ["a3"]
    assert rep["candidates"]["B"]["not_run"] == ["a2", "a3"]
    assert rep["gate_eligible"] is False
    assert any("skipped" in b for b in rep["gate_blockers"])
    assert any("B" in b for b in rep["gate_blockers"])
    md = report.to_markdown(rep)
    assert "a3" in md and "no ok derived image" in md


def test_complete_replay_run_is_gate_eligible(tmp_path):
    cf = _cands_file(tmp_path, ["a1"], {})
    rep = _report(
        tmp_path,
        [trial_line("A", "a1", "m", "p", lab=label(), candidates_file=str(cf))],
    )
    assert rep["gate_blockers"] == [] and rep["gate_eligible"] is True


def test_unknown_completeness_blocks_gate(tmp_path):
    rep = _report(tmp_path, [trial_line("A", "a1", "m", "p", lab=label())])
    assert rep["gate_eligible"] is False and rep["set_items"] is None
    assert any("candidates.json" in b for b in rep["gate_blockers"])


def test_explicit_candidates_file_overrides_events(tmp_path):
    cf = _cands_file(tmp_path, ["a1"], {})
    rep = _report(
        tmp_path, [trial_line("A", "a1", "m", "p", lab=label())], candidates_file=cf
    )
    assert rep["gate_eligible"] is True


def test_set_taken_from_trials(tmp_path):
    rep = _report(
        tmp_path, [trial_line("A", "a1", "m", "p", lab=label(), set_name="other-v1")]
    )
    assert rep["set"] == "other-v1"
    with pytest.raises(ValueError, match="other-v1"):
        _report(
            tmp_path,
            [trial_line("A", "a1", "m", "p", lab=label(), set_name="other-v1")],
            set_name="pilot-v0",
        )
    with pytest.raises(ValueError, match="more than one set"):
        _report(
            tmp_path,
            [
                trial_line("A", "a1", "m", "p", lab=label(), set_name="s1"),
                trial_line("A", "a2", "m", "p", lab=label(), set_name="s2"),
            ],
        )


def test_cli_report_names_file_after_trial_set(tmp_path, capsys):
    p = tmp_path / "r.jsonl"
    p.write_text(
        json.dumps(trial_line("A", "a1", "m", "p", lab=label(), set_name="other-v1"))
        + "\n"
    )
    out = tmp_path / "runs"
    assert (
        main(
            [
                "eval",
                "report",
                "--results",
                str(p),
                "--date",
                "2026-10-10",
                "--out-dir",
                str(out),
            ]
        )
        == 0
    )
    assert (out / "2026-10-10_other-v1.json").exists()
    rc = main(
        [
            "eval",
            "report",
            "--results",
            str(p),
            "--set",
            "pilot-v0",
            "--out-dir",
            str(out),
        ]
    )
    assert rc == 2 and "other-v1" in capsys.readouterr().err


def test_missing_key_message_carries_set_and_candidates_file(tmp_path):
    cf = _cands_file(tmp_path, ["a1"], {})
    line = missing_line("C", "a1")
    line["error"] += f" [set=other-v1, candidates_file={cf}]"
    rep = _report(tmp_path, [line])
    assert rep["set"] == "other-v1" and rep["set_items"]["candidates_file"] == str(cf)
    assert (
        rep["candidates"]["C"]["status_counts"]["missing"] == 1
        and rep["gate_eligible"] is False
    )
