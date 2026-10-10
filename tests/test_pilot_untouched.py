"""Tests for the sources-untouched snapshot and verify."""

import json
import os
from pathlib import Path

from shoebox.config import Config, Source
from shoebox.ids import asset_id
from shoebox.pilot.untouched import (
    diff_snapshots,
    is_writable,
    remeasure,
    take_snapshot,
)


def setup(tmp_path: Path) -> tuple[Config, list[dict]]:
    root = tmp_path / "src"
    (root / "A").mkdir(parents=True)
    (root / "A" / "x.jpg").write_bytes(b"one")
    (root / "A" / "other.txt").write_bytes(b"not an item")
    (root / "B").mkdir()
    (root / "B" / "y.tif").write_bytes(b"two")
    cfg = Config(
        shoebox_root=tmp_path / "sb",
        sources={"fake": Source("fake", root, "owner", synced=False, hosted_consent=False)},
    )
    items = [
        {"asset_id": asset_id(root / "A/x.jpg"), "source": "fake", "relpath": "A/x.jpg"},
        {"asset_id": asset_id(root / "B/y.tif"), "source": "fake", "relpath": "B/y.tif"},
    ]
    return cfg, items


def test_snapshot_records_files_and_listings(tmp_path):
    cfg, items = setup(tmp_path)
    snap = take_snapshot(cfg, items)
    assert set(snap["files"]) == {"fake:A/x.jpg", "fake:A/other.txt", "fake:B/y.tif"}
    assert "blake3" not in snap["files"]["fake:A/other.txt"]
    rec = snap["files"]["fake:A/x.jpg"]
    assert rec["bytes"] == 3 and rec["blake3"] == items[0]["asset_id"]
    assert snap["dirs"]["fake:A"] == ["other.txt", "x.jpg"]
    assert snap["dirs"]["fake:"] == ["A", "B"]
    json.dumps(snap)


def test_no_change_is_clean(tmp_path):
    cfg, items = setup(tmp_path)
    snap = take_snapshot(cfg, items)
    assert diff_snapshots(snap, take_snapshot(cfg, items)) == []


def test_detects_content_mtime_new_and_removed(tmp_path):
    cfg, items = setup(tmp_path)
    root = cfg.sources["fake"].root
    snap = take_snapshot(cfg, items)
    (root / "A" / "x.jpg").write_bytes(b"ONE")
    os.utime(root / "A" / "x.jpg", (1, 1))
    (root / "A" / "new.tmp").write_bytes(b"")
    (root / "B" / "y.tif").unlink()
    (root / "C").mkdir()
    problems = diff_snapshots(snap, take_snapshot(cfg, items))
    text = "\n".join(problems)
    assert "fake:A/x.jpg" in text and "blake3" in text and "mtime" in text
    assert "new.tmp" in text
    assert "fake:B/y.tif" in text and "missing" in text
    assert "C" in text


def test_snapshot_without_items_covers_recipe_folders(tmp_path):
    """Taken before any pick: a file created in a listed-but-unpicked folder is caught."""
    from shoebox.pilot.select import Bucket, recipe_dirs

    cfg, _ = setup(tmp_path)
    root = cfg.sources["fake"].root
    (root / "A" / "sub" / "deep").mkdir(parents=True)
    (root / "A" / "#recycle").mkdir()
    (root / "Empty").mkdir()
    recipe = (Bucket("a", ("A", "Empty", "Missing"), 1, extra_files=("B/y.tif",)),)
    dirs = recipe_dirs(root, recipe)
    assert dirs == ["", "A", "A/sub", "B", "Empty", "Missing"]

    snap = take_snapshot(cfg, [], {"fake": dirs})
    assert snap["files"]["fake:A/x.jpg"] == {"bytes": 3, "mtime": os.stat(root / "A/x.jpg").st_mtime}
    assert "fake:B/y.tif" in snap["files"] and "blake3" not in snap["files"]["fake:B/y.tif"]
    assert snap["dirs"]["fake:Missing"] == [] and snap["dirs"]["fake:A/sub"] == ["deep"]
    assert diff_snapshots(snap, remeasure(cfg, snap)) == []

    (root / "Empty" / "index.db").write_bytes(b"")
    (root / "A" / "sub" / "x.tmp").write_bytes(b"")
    (root / "Missing").mkdir()
    os.utime(root / "A" / "other.txt", (5, 5))
    text = "\n".join(diff_snapshots(snap, remeasure(cfg, snap)))
    assert "fake:Empty/: new entries ['index.db']" in text
    assert "fake:A/sub/: new entries ['x.tmp']" in text
    assert "fake:/: new entries ['Missing']" in text
    assert "fake:A/other.txt: mtime changed" in text


def test_remeasure_hashes_only_items(tmp_path):
    cfg, items = setup(tmp_path)
    snap = take_snapshot(cfg, items[:1])
    assert "blake3" in snap["files"]["fake:A/x.jpg"]
    assert "blake3" not in snap["files"]["fake:A/other.txt"]
    after = remeasure(cfg, snap)
    assert set(after["files"]) == set(snap["files"]) and set(after["dirs"]) == set(snap["dirs"])
    assert "blake3" not in after["files"]["fake:A/other.txt"]


def test_remeasure_reads_old_snapshot_format(tmp_path):
    cfg, items = setup(tmp_path)
    root = cfg.sources["fake"].root
    st = os.stat(root / "A/x.jpg")
    old = {"files": {"fake:A/x.jpg": {"bytes": 3, "mtime": st.st_mtime, "blake3": items[0]["asset_id"]}},
           "dirs": {"fake:A": ["other.txt", "x.jpg"], "fake:": ["A", "B"]}}
    assert diff_snapshots(old, remeasure(cfg, old)) == []


class _Vfs:
    def __init__(self, flag: int) -> None:
        self.f_flag = flag


def test_is_writable_statvfs_read_only_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "statvfs", lambda p: _Vfs(os.ST_RDONLY))
    monkeypatch.setattr(os, "access", lambda p, mode: True)
    assert is_writable(tmp_path) is False


def test_is_writable_access_check(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "statvfs", lambda p: _Vfs(0))
    monkeypatch.setattr(os, "access", lambda p, mode: False)
    assert is_writable(tmp_path) is False
    monkeypatch.setattr(os, "access", lambda p, mode: mode == os.W_OK)
    assert is_writable(tmp_path) is True


def test_read_only_mount_passes_guard(monkeypatch, tmp_path):
    from shoebox.config import Source
    from shoebox.pilot.untouched import check_read_only

    monkeypatch.setattr(os, "statvfs", lambda p: _Vfs(os.ST_RDONLY | 0x2))
    check_read_only(Source("ro", tmp_path, "owner", synced=True, hosted_consent=False))


def _truncating_listing(monkeypatch, drop: set[str]):
    """Make folder listings omit `drop`, as the NAS's SMB listing of a very large folder did (it returned
    1000, 1900 or 10000 entries for the same unchanged folder)."""
    from shoebox.pilot import untouched

    real = untouched._listing

    def listing(path):
        names, plain = real(path)
        if names is None:
            return names, plain
        return [n for n in names if n not in drop], [n for n in plain if n not in drop]

    monkeypatch.setattr(untouched, "_listing", listing)
    return real


def test_listing_that_omits_existing_files_is_not_a_removal(tmp_path, monkeypatch):
    cfg, items = setup(tmp_path)
    snap = take_snapshot(cfg, items)
    _truncating_listing(monkeypatch, {"other.txt"})
    assert diff_snapshots(snap, remeasure(cfg, snap)) == []


def test_snapshot_with_a_truncated_listing_does_not_report_old_files_as_new(tmp_path, monkeypatch):
    from shoebox.pilot import untouched

    cfg, items = setup(tmp_path)
    real = _truncating_listing(monkeypatch, {"other.txt"})
    snap = take_snapshot(cfg, items)
    monkeypatch.setattr(untouched, "_listing", real)
    snap["started"] = snap["taken"] = "2999-01-01T00:00:00+00:00"  # every file was born before the snapshot
    assert diff_snapshots(snap, remeasure(cfg, snap)) == []


def test_real_new_and_removed_entries_are_still_reported(tmp_path):
    cfg, items = setup(tmp_path)
    snap = take_snapshot(cfg, items)
    snap["started"] = "2000-01-01T00:00:00+00:00"
    (cfg.sources["fake"].root / "A" / "new.jpg").write_bytes(b"new")
    (cfg.sources["fake"].root / "A" / "other.txt").unlink()
    problems = diff_snapshots(snap, remeasure(cfg, snap))
    assert "fake:A/: new entries ['new.jpg']" in problems
    assert "fake:A/: removed entries ['other.txt']" in problems
    assert "fake:A/other.txt: missing" in problems
