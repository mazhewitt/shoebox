"""Worklist (task 6.4, design D7) and the frame-wide privacy check (task 3.3, spec hosted-eligibility).

Synthetic frames, pixel-hash store, derive index and quarantine under tmp_path; nothing reads a real source.
"""

import json
from argparse import Namespace
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shoebox import config, derive, inventory, privacy, quarantine, store, worklist
from shoebox.commands import dedup as dedup_cmd
from shoebox.commands import privacy as privacy_cmd


def aid(n: int) -> str:
    return f"{n:064x}"


def row(source, relpath, asset):
    folder, _, name = relpath.rpartition("/")
    ext = name.rsplit(".", 1)[-1].lower()
    kind = "video" if ext in inventory.VIDEO_EXTS else "image"
    return {"source": source, "person": "p", "relpath": relpath, "folder": folder, "name": name, "ext": ext,
            "kind": kind, "asset_id": asset, "status": "ok", "width": 4000, "height": 3000, "date": None,
            "date_kind": None, "make": None, "model": None, "exif": None, "group_id": None, "group_rule": None,
            "dup_n": 1}


CAND = {"media_resolution": "medium", "est_prompt_tokens": 2100, "est_output_tokens": 400,
        "est_thinking_tokens": 200, "price_input_usd_per_mtok": 0.75, "price_output_usd_per_mtok": 3.75,
        "price_date": "2026-10-10"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """owner (consented to gemini) and kyra (no consent). Captures:
    a1 single, derived -> hosted;  a2+a3 same pixels, a3 in kyra -> local (copy_restricted by kyra);
    a4 CR2 + a5 JPG raw/jpeg pair, a5 pending -> held;  a6 not derived -> not_derived;
    a7 video -> not_derived;  a8 in kyra, derived -> local (no_consent)."""
    monkeypatch.setattr(privacy, "NEVER_SEND_PATH", tmp_path / "never-send.txt")
    sources = {
        "owner": config.Source("owner", tmp_path / "owner", "owner", synced=False, hosted_consent=True,
                               added=date(2026, 10, 1)),
        "kyra": config.Source("kyra", tmp_path / "kyra", "kyra", synced=False, hosted_consent=False,
                              added=date(2026, 10, 1)),
    }
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources=sources)
    frames = cfg.shoebox_root / "frame"
    frames.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        row("owner", "2004/a.jpg", aid(1)), row("owner", "2004/b.jpg", aid(2)),
        row("owner", "2005/c.CR2", aid(4)), row("owner", "2005/c.jpg", aid(5)),
        row("owner", "2006/d.jpg", aid(6)), row("owner", "2006/v.mp4", aid(7)),
    ]), frames / "frame-2026-10-05.parquet")
    pq.write_table(pa.Table.from_pylist([
        row("kyra", "x/b2.jpg", aid(3)), row("kyra", "x/e.jpg", aid(8)),
    ]), frames / "frame-2026-10-06.parquet")
    settings = derive.load_profile("infer-v1")
    model_id, ph = derive.pixelhash_key(settings)
    for a, h in ((2, "h-b"), (3, "h-b")):
        store.append(cfg, {"asset_id": aid(a), "model_id": model_id, "params_hash": ph, "status": "ok",
                           "pixel_hash": h, "width": 4000, "height": 3000}, kind="pixelhash")
    idx = derive.index_path(cfg, "infer-v1")
    idx.parent.mkdir(parents=True)
    idx.write_text("".join(json.dumps({"asset_id": aid(a), "status": "ok"}) + "\n" for a in (1, 2, 3, 4, 5, 8))
                   + json.dumps({"asset_id": aid(6), "status": "error"}) + "\n")
    quarantine.flag([aid(5)], "nudity")
    return cfg


def by_member(caps, a):
    return next(c for c in caps if a in c["members"])


def test_worklist_rows_and_queues(world):
    wl = worklist.build(world, provider="gemini")
    caps = wl.captures
    assert len(caps) == 6
    assert by_member(caps, aid(1))["queue"] == "hosted" and by_member(caps, aid(1))["eligible"] is True
    assert by_member(caps, aid(1))["refusal"] is None
    pair = by_member(caps, aid(2))
    assert set(pair["members"]) == {aid(2), aid(3)}
    assert pair["eligible"] is False and pair["queue"] == "local"
    assert pair["refusal"]["reason"] in ("copy_restricted", "no_consent")
    assert pair["refusal"]["restricting_source"] == "kyra"
    held = by_member(caps, aid(4))
    assert aid(5) in held["members"] and held["eligible"] is False and held["queue"] == "held"
    assert held["refusal"]["reason"] == "quarantined"
    assert by_member(caps, aid(6))["queue"] == "not_derived"
    assert by_member(caps, aid(7))["queue"] == "not_derived"
    assert by_member(caps, aid(8))["queue"] == "local"
    assert by_member(caps, aid(8))["refusal"] == {"reason": "no_consent", "restricting_source": "kyra"}
    for c in caps:
        assert c["type"] == "capture" and set(c) >= {
            "capture_id", "canonical_asset_id", "canonical_reason", "rule", "roles", "linked", "group_size"}


def test_counts_add_up(world):
    wl = worklist.build(world, provider="gemini")
    n = wl.counts
    assert (n["files"], n["assets"], n["captures"]) == (8, 8, 6)
    assert (n["hosted"], n["local"], n["held"], n["not_derived"]) == (1, 2, 1, 2)
    assert n["eligible"] == 3  # a1, a6, a7 pass privacy; only a1 is captionable
    worklist.check_counts(n)
    with pytest.raises(AssertionError):
        worklist.check_counts({**n, "local": 3})


def test_header_fields(world):
    wl = worklist.build(world, provider="gemini", now="2026-10-11T00:00:00+00:00")
    h = wl.header
    assert h["type"] == "header" and h["provider"] == "gemini" and h["created_at"].startswith("2026-10-11")
    assert h["sources"] == ["kyra", "owner"]
    assert h["frame_dates"] == {"owner": "2026-10-05", "kyra": "2026-10-06"}
    assert h["dedup_version"] and h["rule_versions"]


def test_missing_source_fails_closed(world, tmp_path):
    cfg = config.Config(world.shoebox_root, {**world.sources, "new": config.Source(
        "new", tmp_path / "new", "x", synced=False, hosted_consent=True)})
    with pytest.raises(privacy.PrivacyError, match="new"):
        worklist.build(cfg, provider="gemini")


def test_cli_writes_worklist_and_path_free_summary(world, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda *a, **k: world)
    monkeypatch.setattr(dedup_cmd, "_candidate", lambda name: CAND)
    runs = tmp_path / "runs"
    rc = dedup_cmd.cmd_worklist(Namespace(provider="gemini", candidate="gemini-medium-v2", name="t1",
                                          profile="infer-v1", runs_dir=runs))
    assert rc == 0
    lines = [json.loads(x) for x in (world.shoebox_root / "worklists" / "t1.jsonl").read_text().splitlines()]
    assert lines[0]["type"] == "header" and len(lines) == 7
    md = next(runs.glob("*_worklist-t1.md")).read_text()
    for needle in ("2004/", "x/e.jpg", "owner/", aid(1), aid(1)[:12], ".jpg", str(tmp_path)):
        assert needle not in md
    assert "files 8" in md and "captures 6" in md and "hosted 1" in md
    est = privacy.estimate(CAND, 1).usd * 0.5
    assert f"${est:.4f}" in md and "2026-10-10" in md and "0.5" in md


def test_batch_discount_from_candidate():
    assert worklist.batch_usd({**CAND, "price_batch_discount": 0.4}, 10)[0] == pytest.approx(
        privacy.estimate(CAND, 10).usd * 0.4)
    usd, note = worklist.batch_usd(CAND, 10)
    assert usd == pytest.approx(privacy.estimate(CAND, 10).usd * 0.5) and note


# --- 3.3 privacy check over the frames -----------------------------------------------------------


def test_frame_refusal_counts(world):
    rep = worklist.frame_refusals(world, provider="gemini")
    assert rep["by_reason"] == {"copy_restricted": 1, "no_consent": 2, "quarantined": 1}
    assert rep["by_source"] == {"kyra": 3, "owner": 1}
    assert rep["items"] == 8 and rep["allowed"] == 4


def test_privacy_check_all_frames_prints_counts_no_paths(world, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "load", lambda *a, **k: world)
    rc = privacy_cmd.cmd_check(Namespace(checklist=tmp_path / "none.toml", items=None, provider="gemini",
                                         allowed_out=None, all_frames=True))
    out = capsys.readouterr().out
    assert "copy_restricted: 1" in out and "quarantined: 1" in out and "no_consent: 2" in out
    assert "by source kyra: 3" in out
    assert ".jpg" not in out and "2004" not in out and aid(5) not in out
    assert rc == 1  # no checklist: hosted runs refused, but the counts are still reported
