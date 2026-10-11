"""Quarantine file (design D3, spec content-screen): latest row per asset wins, verdicts propagate to the group."""

import json

import pytest

from shoebox import quarantine as q

A, B, C = "a" * 64, "b" * 64, "c" * 64


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_flag_appends_pending_row_with_hash_only(tmp_path):
    p = tmp_path / "quarantine.jsonl"
    assert q.flag([A], "nudity", path=p, now="2026-10-11T10:00:00+00:00") == 1
    assert _rows(p) == [{"asset_id": A, "screen": "nudity", "flagged_at": "2026-10-11T10:00:00+00:00",
                         "verdict": "pending"}]
    assert q.load(p)[A]["verdict"] == "pending"


def test_missing_file_is_empty(tmp_path):
    assert q.load(tmp_path / "none.jsonl") == {}
    assert not q.Checker.load(tmp_path / "none.jsonl").refused(A, "hosted")


def test_unknown_screen_or_verdict_refused(tmp_path):
    p = tmp_path / "q.jsonl"
    with pytest.raises(ValueError):
        q.flag([A], "violence", path=p)
    q.flag([A], "nudity", path=p)
    with pytest.raises(ValueError):
        q.record_verdict(A, "maybe", "owner", path=p)
    with pytest.raises(KeyError):
        q.record_verdict(B, "include", "owner", path=p)  # never flagged
    with pytest.raises(ValueError):
        q.Checker.load(p).refused(A, "elsewhere")


@pytest.mark.parametrize("verdict, hosted, local", [
    ("pending", True, True),
    ("private", True, False),
    ("exclude", True, True),
    ("include", False, False),
])
def test_verdict_effect(tmp_path, verdict, hosted, local):
    p = tmp_path / "q.jsonl"
    q.flag([A], "nudity", path=p)
    if verdict != "pending":
        q.record_verdict(A, verdict, "owner", path=p, now="2026-10-12T00:00:00+00:00")
    chk = q.Checker.load(p)
    assert chk.refused(A, "hosted") is hosted
    assert chk.refused(A, "local") is local
    assert not chk.refused(B, "hosted")  # no row: clear


def test_latest_row_wins_and_verdict_keeps_flag_fields(tmp_path):
    p = tmp_path / "q.jsonl"
    q.flag([A], "document", path=p, now="t0")
    q.record_verdict(A, "exclude", "owner", path=p, now="t1")
    q.record_verdict(A, "include", "owner", path=p, now="t2")
    row = q.load(p)[A]
    assert row == {"asset_id": A, "screen": "document", "flagged_at": "t0", "verdict": "include",
                   "verdict_by": "owner", "verdict_at": "t2"}
    assert not q.Checker.load(p).refused(A, "hosted")


def test_flag_does_not_requeue(tmp_path):
    p = tmp_path / "q.jsonl"
    q.flag([A, B], "nudity", path=p)
    q.record_verdict(A, "exclude", "owner", path=p)
    assert q.flag([A, B, C, C], "nudity", path=p) == 1  # only C is new, once
    rows = q.load(p)
    assert rows[A]["verdict"] == "exclude"
    assert rows[B]["verdict"] == "pending"
    assert [r["asset_id"] for r in _rows(p)].count(B) == 1


def test_verdict_propagates_through_same_pixel_hash(tmp_path):
    p = tmp_path / "q.jsonl"
    q.flag([A], "nudity", path=p)
    chk = q.Checker.load(p, groups=q.same_pixel_groups({A: "px1", B: "px1", C: "px2"}))
    assert chk.refused(B, "hosted") and chk.refused(B, "local")
    assert chk.verdict(B) == "pending"
    assert not chk.refused(C, "hosted")


def test_most_restrictive_in_group_wins(tmp_path):
    p = tmp_path / "q.jsonl"
    q.flag([A, B], "nudity", path=p)
    q.record_verdict(A, "include", "owner", path=p)
    q.record_verdict(B, "private", "owner", path=p)
    chk = q.Checker.load(p, groups={A: [B]})  # e.g. a later capture/dedup group
    assert chk.verdict(A) == "private"
    assert chk.refused(A, "hosted") and not chk.refused(A, "local")
    q.record_verdict(B, "exclude", "owner", path=p)
    assert q.Checker.load(p, groups={A: [B]}).refused(A, "local")


def test_extra_group_argument(tmp_path):
    p = tmp_path / "q.jsonl"
    q.flag([A], "nudity", path=p)
    assert q.Checker.load(p).refused(B, "hosted", group=[A])
