"""tools/e2e_fake_batch.py (task 10.1) on a synthetic five-asset library. Fake transport only."""

import importlib.util
from pathlib import Path

import pytest
from test_bulk import capture, label_rec, wb, write_worklist  # noqa: F401 - wb is a fixture
from test_gemini import env  # noqa: F401 - a fixture (used by b)
from test_gemini_batch import AIDS, b  # noqa: F401 - a fixture (used by wb)

from shoebox import privacy, quarantine, runners, store

_spec = importlib.util.spec_from_file_location(
    "e2e_fake_batch", Path(__file__).resolve().parents[1] / "tools" / "e2e_fake_batch.py")
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)


def screen_ok(cfg, aids):
    res = runners.resolve("nsfw-coreml-v1")
    for a in aids:
        store.append(cfg, {"asset_id": a, "model_id": res.model_id, "params_hash": res.params_hash,
                           "status": "ok", "output": {"score": 0.01}}, res.kind)


@pytest.fixture
def lib(wb, tmp_path):  # noqa: F811
    """Five captures: 0 reused (sync label), 1 local, 2/3/4 hosted. All screened; a real spend line."""
    screen_ok(wb.cfg, AIDS)
    store.append(wb.cfg, label_rec(wb.medium, AIDS[0]))
    caps = [capture(AIDS[0]), capture(AIDS[1], queue="local"), capture(AIDS[2]), capture(AIDS[3]),
            capture(AIDS[4])]
    wb.wl = write_worklist(wb.cfg, "e2e", caps)
    privacy.append_spend("x", 1, 0.5, "2026-01-01")
    wb.real_spend = Path(privacy.SPEND_PATH).read_bytes()
    wb.scratch = tmp_path / "scratch"
    wb.runs = tmp_path / "runs"
    return wb


def run(lib, **kw):
    return e2e.run_e2e(lib.cfg, lib.wl, "gemini-batch-v2", lib.scratch, lib.runs, files=6, **kw)


def test_clean_run_passes_reconciles_and_leaves_the_real_tree_alone(lib):
    before = e2e.snapshot(lib.cfg.results_dir)
    r = run(lib)
    assert r.rc == 0, (r.problems, r.violations)
    assert r.bulk_rc == 0 and not r.violations and not r.problems
    t = r.table
    assert (t["files"], t["captures"], t["hosted"], t["reused"], t["to_send"], t["requests_sent"]) == (6, 5, 4, 1, 3, 3)
    assert e2e.snapshot(lib.cfg.results_dir) == before
    assert Path(privacy.SPEND_PATH).read_bytes() == lib.real_spend
    # labels and ledger landed in the overlay, not the real store
    own = store.index(lib.cfg, lib.res.model_id, lib.res.params_hash)
    assert own == {}
    ov = e2e.replace(lib.cfg, shoebox_root=lib.scratch / "root")
    assert {a for a, r_ in store.index(ov, lib.res.model_id, lib.res.params_hash).items()
            if r_["status"] == "ok"} == set(AIDS[2:])
    assert len((lib.scratch / "spend.jsonl").read_text().splitlines()) == 2


def test_report_has_counts_and_quote_but_no_ids(lib):
    r = run(lib, limit=2)
    assert r.rc == 0, r.problems
    md = r.report.read_text()
    assert r.report.name.endswith("_e2e-fake-batch.md")
    assert "requests sent 2" in md and "to send 3" in md and "price_date" in md and "$" in md
    assert not any(a[:12] in md for a in AIDS)
    assert "Pictures/" not in md


def test_smuggled_quarantined_asset_is_caught(lib, monkeypatch):
    """Both runtime quarantine guards disabled: the asset is sent, and the independent checker catches it."""
    quarantine.flag([AIDS[3]], "nudity")
    monkeypatch.setattr(quarantine.Checker, "load", classmethod(lambda cls, *a, **k: cls({})))
    r = run(lib)
    assert r.rc == 1
    assert r.violations["quarantined"] == 1


def test_checker_flags_quarantine_on_a_member_and_already_labelled(lib):
    caps = [capture(AIDS[2], members=[AIDS[2], AIDS[3]]), capture(AIDS[4])]
    before = e2e.Before(labelled={AIDS[4]}, final_own=set(), screened=set(AIDS))
    v = e2e.check_sent([AIDS[2], AIDS[4], AIDS[3]], caps, lib.cfg, before,
                       qrows={AIDS[3]: {"asset_id": AIDS[3], "verdict": "private"}})
    assert v["quarantined"] == 2  # capture 2 via its member, and the member itself
    assert v["already_labelled"] == 1 and v["not_canonical"] == 1


def test_checker_include_verdict_is_clear_and_unscreened_is_caught(lib):
    caps = [capture(AIDS[2])]
    before = e2e.Before(labelled=set(), final_own=set(), screened=set())
    v = e2e.check_sent([AIDS[2]], caps, lib.cfg, before, qrows={AIDS[2]: {"verdict": "include"}})
    assert dict(v) == {"unscreened": 1}


def test_snapshot_detects_a_write_to_the_real_results_tree(lib):
    before = e2e.snapshot(lib.cfg.results_dir)
    (lib.cfg.results_dir / "batch-jobs").mkdir(parents=True, exist_ok=True)
    assert any(e2e.snapshot_diff(before, e2e.snapshot(lib.cfg.results_dir)).values())


def test_run_that_writes_the_real_tree_fails(lib, monkeypatch):
    """An overlay with no writable dirs symlinks results/ whole, so the run writes the real tree: caught."""
    monkeypatch.setattr(e2e, "writable_paths", lambda cfg, res: [])
    r = run(lib)
    assert r.rc == 1 and "the real results tree changed" in r.problems


def test_http_transport_construction_inside_the_run_is_forbidden(lib, monkeypatch):
    from shoebox.runners import gemini_batch

    def submit(self, model, requests, display_name):
        gemini_batch.HttpTransport()  # as if the run reached for the real transport

    monkeypatch.setattr(e2e.RecordingTransport, "submit", submit)
    r = run(lib)
    assert r.rc == 1 and any("HttpTransport is forbidden" in p for p in r.problems)


def test_scratch_refusals(tmp_path):
    for bad in (Path("/Volumes/x/e2e"), e2e.REPO_ROOT / "e2e-scratch", Path.home() / "Documents" / "e2e"):
        with pytest.raises(SystemExit):
            e2e.check_scratch(bad, tmp_path / "root")
    with pytest.raises(SystemExit):
        e2e.check_scratch(tmp_path / "root" / "x", tmp_path / "root")
    full = tmp_path / "full"
    full.mkdir()
    (full / "f").write_text("x")
    with pytest.raises(SystemExit):
        e2e.check_scratch(full, tmp_path / "root")


def test_reconcile_catches_a_mismatch():
    counts = {"assets": 5, "captures": 5, "eligible": 4, "hosted": 4, "for_candidate": 4, "reused": 1,
              "fallback": 0, "to_send": 3, "sent": 3}
    _, probs = e2e.reconcile(6, counts, {"submitted": 3}, 3, None)
    assert probs == []
    _, probs = e2e.reconcile(6, counts, {"submitted": 2}, 2, None)
    assert probs and any("requests sent 2" in p for p in probs)


def test_unscreened_queue_row_is_counted_and_not_sent(lib):
    """The worklist's `unscreened` queue reconciles and none of its captures is sent."""
    caps = [capture(AIDS[2]), {**capture(AIDS[3]), "queue": "unscreened", "eligible": True}]
    lib.wl = write_worklist(lib.cfg, "e2e-u", caps)
    r = run(lib)
    assert r.rc == 0, r.problems
    assert r.table["unscreened"] == 1 and r.table["requests_sent"] == 1, r.table
