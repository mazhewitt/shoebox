"""Tests for `shoebox pilot pick`: the read-only guard, items.jsonl format and README."""

import json
from pathlib import Path

import pytest

from shoebox.config import Source
from shoebox.ids import asset_id
from shoebox.pilot.pick import ITEM_KEYS, run_pick
from shoebox.pilot.select import Bucket
from shoebox.pilot.untouched import SourceWritable, check_read_only


def make_source(root: Path, synced: bool = False) -> Source:
    return Source(name="fake", root=root, person="owner", synced=synced, hosted_consent=False)


def touch(root: Path, rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_writable_synced_source_is_refused(tmp_path):
    with pytest.raises(SourceWritable) as exc:
        check_read_only(make_source(tmp_path, synced=True))
    assert str(tmp_path) in str(exc.value)


def test_writable_unsynced_source_is_allowed(tmp_path):
    check_read_only(make_source(tmp_path, synced=False))


def test_read_only_detection(monkeypatch, tmp_path):
    import os

    class Vfs:
        f_flag = os.ST_RDONLY

    monkeypatch.setattr(os, "statvfs", lambda p: Vfs())
    check_read_only(make_source(tmp_path, synced=True))


def test_run_pick_writes_items_and_readme(tmp_path):
    src = tmp_path / "src"
    touch(src, "P/L1.DNG", b"raw1")
    touch(src, "P/L1.JPG", b"jpg1")
    touch(src, "P/L2.DNG", b"raw2")
    touch(src, "S/a.tif", b"tif-a")
    touch(src, "S/a.jpg", b"jpg-a")
    touch(src, "S/b.tif", b"tif-b")
    recipe = (
        Bucket("pair", ("P",), 1, mode="pairs"),
        Bucket("scan", ("S",), 3),
        Bucket("empty", ("Nope",), 1),
    )
    out = tmp_path / "set"
    before = sorted(p.relative_to(src) for p in src.rglob("*"))
    items = run_pick(make_source(src), out, recipe=recipe, log=lambda m: None)
    assert sorted(p.relative_to(src) for p in src.rglob("*")) == before

    lines = [json.loads(line) for line in (out / "items.jsonl").read_text().splitlines()]
    assert lines == items
    assert [x["relpath"] for x in lines] == ["P/L1.DNG", "P/L1.JPG", "S/a.jpg", "S/a.tif", "S/b.tif"]
    for x in lines:
        assert list(x) == ITEM_KEYS
        assert x["source"] == "fake"
        assert x["asset_id"] == asset_id(src / x["relpath"])
        assert x["bytes"] == (src / x["relpath"]).stat().st_size
        assert isinstance(x["mtime"], float)
    by = {x["relpath"]: x for x in lines}
    assert by["P/L1.DNG"]["group_rule"] == "raw_jpeg_basename"
    assert by["P/L1.DNG"]["group_id"] == by["P/L1.JPG"]["group_id"]
    assert by["S/a.tif"]["group_rule"] == "scan_export_basename"
    assert by["S/b.tif"]["group_id"] is None
    assert by["S/a.tif"]["ext"] == "tif" and by["P/L1.DNG"]["bucket"] == "pair"

    readme = (out / "README.md").read_text()
    assert "empty" in readme and "Nope" in readme
    assert "| scan | 3 |" in readme
    assert str(sum(x["bytes"] for x in lines)) in readme.replace(",", "")


def test_run_pick_is_deterministic(tmp_path):
    src = tmp_path / "src"
    for i in range(9):
        touch(src, f"A/{i}.jpg", bytes([i]))
    recipe = (Bucket("a", ("A",), 4),)
    run_pick(make_source(src), tmp_path / "o1", recipe=recipe, log=lambda m: None)
    run_pick(make_source(src), tmp_path / "o2", recipe=recipe, log=lambda m: None)
    a = (tmp_path / "o1" / "items.jsonl").read_text()
    assert a == (tmp_path / "o2" / "items.jsonl").read_text()
    assert len(a.splitlines()) == 4


def test_run_pick_refuses_writable_synced(tmp_path):
    with pytest.raises(SourceWritable):
        run_pick(make_source(tmp_path, synced=True), tmp_path / "o", recipe=(), log=lambda m: None)
    assert not (tmp_path / "o").exists()
