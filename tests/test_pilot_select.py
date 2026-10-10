"""Tests for the pilot recipe: walking bucket folders, even spreading, pairs, groups."""

from pathlib import Path

from shoebox.pilot.select import (
    Bucket,
    FileEntry,
    assign_groups,
    select,
    spread,
    walk,
)


def touch(root: Path, rel: str, data: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_spread_is_even_and_deterministic():
    seq = list(range(10))
    assert spread(seq, 1) == [4]
    assert spread(seq, 2) == [0, 9]
    assert spread(seq, 4) == [0, 3, 6, 9]
    assert spread(seq, 20) == seq
    assert spread([], 3) == []
    assert spread(seq, 0) == []


def test_walk_skips_hidden_junk_and_respects_depth(tmp_path):
    touch(tmp_path, "B/a.JPG")
    touch(tmp_path, "B/.hidden.jpg")
    touch(tmp_path, "B/Thumbs.db")
    touch(tmp_path, "B/notes.txt")
    touch(tmp_path, "B/#recycle/x.jpg")
    touch(tmp_path, "B/$RECYCLE.BIN/x.jpg")
    touch(tmp_path, "B/.tmp.drivedownload/x.jpg")
    touch(tmp_path, "B/cat.lrdata/x.jpg")
    touch(tmp_path, "B/sub/b.heic")
    touch(tmp_path, "B/sub/deeper/c.tif")
    entries = walk(tmp_path, "B", max_depth=2)
    assert [e.relpath for e in entries] == ["B/a.JPG", "B/sub/b.heic"]
    assert entries[0].ext == "jpg"
    assert entries[0].bytes == 1


def test_walk_skips_oversize(tmp_path):
    touch(tmp_path, "B/big.tif", b"x" * 20)
    touch(tmp_path, "B/small.tif", b"x" * 5)
    assert [e.relpath for e in walk(tmp_path, "B", max_bytes=10)] == ["B/small.tif"]


def test_walk_missing_folder_is_empty(tmp_path):
    assert walk(tmp_path, "Nope") == []


def test_select_files_pairs_and_logging(tmp_path):
    for i in range(6):
        touch(tmp_path, f"P/L{i}.DNG")
        touch(tmp_path, f"P/L{i}.JPG")
    touch(tmp_path, "P/lonely.DNG")
    for i in range(5):
        touch(tmp_path, f"F/s{i}.tif")
    recipe = (
        Bucket("pair", ("P",), 2, mode="pairs"),
        Bucket("files", ("F",), 3),
        Bucket("gone", ("Missing",), 2),
    )
    logs: list[str] = []
    picks, skipped = select(tmp_path, recipe, log=logs.append)
    rels = [(b, e.relpath) for b, e in picks]
    assert rels == [
        ("pair", "P/L0.DNG"), ("pair", "P/L0.JPG"),
        ("pair", "P/L5.DNG"), ("pair", "P/L5.JPG"),
        ("files", "F/s0.tif"), ("files", "F/s2.tif"), ("files", "F/s4.tif"),
    ]
    assert [b for b, _ in skipped] == ["gone"]
    assert any("Missing" in m for m in logs)


def test_select_per_folder_spread_folders_and_extras(tmp_path):
    for y in (1969, 1970):
        touch(tmp_path, f"G/{y}/a.jpg")
        touch(tmp_path, f"G/{y}/b.jpg")
    for y in (2010, 2012, 2013, 2015, 2019):
        touch(tmp_path, f"G/{y}/p.jpg")
    touch(tmp_path, "X/root.psd")
    touch(tmp_path, "Q/one.psd")
    touch(tmp_path, "Q/voucher.psd")
    touch(tmp_path, "Q/v-bucks.psd")
    recipe = (
        Bucket("old", ("G/1969", "G/1970", "G/1998"), 1, per_folder=True),
        Bucket("tens", tuple(f"G/{y}" for y in range(2010, 2020)), 3, spread_folders=True),
        Bucket("psd", ("Q",), 1, exts=frozenset({"psd"}), extra_files=("X/root.psd",),
               exclude_words=("voucher", "v-bucks")),
    )
    picks, skipped = select(tmp_path, recipe, log=lambda m: None)
    rels = [e.relpath for _, e in picks]
    assert rels == [
        "G/1969/a.jpg", "G/1970/a.jpg",
        "G/2010/p.jpg", "G/2013/p.jpg", "G/2019/p.jpg",
        "X/root.psd", "Q/one.psd",
    ]
    assert skipped == []


def test_select_prefers_heic_when_none_picked(tmp_path):
    for i in range(8):
        touch(tmp_path, f"Ph/{i:02d}.jpg")
    touch(tmp_path, "Ph/03b.HEIC")
    recipe = (Bucket("phone", ("Ph",), 4, want_exts=frozenset({"heic", "heif"})),)
    picks, _ = select(tmp_path, recipe, log=lambda m: None)
    exts = [e.ext for _, e in picks]
    assert len(picks) == 4 and "heic" in exts


def test_select_never_picks_a_file_twice(tmp_path):
    for i in range(3):
        touch(tmp_path, f"A/{i}.jpg")
    recipe = (Bucket("one", ("A",), 3), Bucket("two", ("A",), 3))
    picks, skipped = select(tmp_path, recipe, log=lambda m: None)
    assert len(picks) == 3
    assert [b for b, _ in skipped] == ["two"]


def test_select_enforces_total_cap(tmp_path):
    for i in range(10):
        touch(tmp_path, f"A/{i}.jpg")
    recipe = (Bucket("a", ("A",), 10),)
    try:
        select(tmp_path, recipe, max_total=5, log=lambda m: None)
    except ValueError as e:
        assert "5" in str(e)
    else:
        raise AssertionError("expected cap error")


def _item(relpath: str, aid: str) -> dict:
    e = FileEntry(relpath, 1, 0.0)
    return {"asset_id": aid, "relpath": relpath, "ext": e.ext}


def test_assign_groups_raw_jpeg_and_scan_export():
    items = [
        _item("P/L1.DNG", "bb" * 32),
        _item("P/L1.JPG", "aa" * 32),
        _item("S/scan.tif", "cc" * 32),
        _item("S/scan.jpg", "dd" * 32),
        _item("S/other.jpg", "ee" * 32),
        _item("T/L1.JPG", "ff" * 32),
    ]
    assign_groups(items)
    by = {i["relpath"]: (i["group_id"], i["group_rule"]) for i in items}
    assert by["P/L1.DNG"] == ("g-" + "aa" * 6, "raw_jpeg_basename")
    assert by["P/L1.JPG"] == ("g-" + "aa" * 6, "raw_jpeg_basename")
    assert by["S/scan.tif"] == ("g-" + "cc" * 6, "scan_export_basename")
    assert by["S/scan.jpg"] == ("g-" + "cc" * 6, "scan_export_basename")
    assert by["S/other.jpg"] == (None, None)
    assert by["T/L1.JPG"] == (None, None)


def test_unreadable_folder_is_logged_and_skipped(tmp_path, monkeypatch):
    import os

    touch(tmp_path, "A/ok/1.jpg")
    touch(tmp_path, "A/locked/2.jpg")
    real = os.scandir

    def scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    logs: list[str] = []
    picks, _ = select(tmp_path, (Bucket("a", ("A",), 5),), log=logs.append)
    assert [f.relpath for _, f in picks] == ["A/ok/1.jpg"]
    assert any("[a] folder unreadable, skipped: A/locked" in m for m in logs)
