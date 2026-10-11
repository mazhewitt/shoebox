"""tools/frame_diff.py on synthetic frames (task 2.2)."""

import importlib.util
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

_spec = importlib.util.spec_from_file_location("frame_diff", Path(__file__).parents[1] / "tools" / "frame_diff.py")
fd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fd)


def _frame(path, relpaths, **over):
    rows = [{"source": "s", "relpath": r, "bytes": 1, "exif": json.dumps({"a": 1, "b": 2}), **over.get(r, {})}
            for r in relpaths]
    pq.write_table(pa.Table.from_pylist(rows), path)
    return str(path)


def _evals(tmp_path, relpaths):
    p = tmp_path / "items.jsonl"
    p.write_text("".join(json.dumps({"relpath": r}) + "\n" for r in relpaths))
    return ["--eval-items", str(p)]


KEEP = ["a/1.jpg", "b/Lightroom Catalog.lrcat", "b/x.ini"]
NEW_SKIP = ["c/.thumbnails/t.jpg", "d/iPod Photo Cache/F00/p.ithmb", "e/Cat Previews.lrdata/1/p.jpg",
            "f/@SynoEAStream/x", "g/@SynoResource/y"]


def test_newly_skipped_paths_only_is_ok(tmp_path, capsys):
    old = _frame(tmp_path / "o.parquet", KEEP + NEW_SKIP)
    new = _frame(tmp_path / "n.parquet", KEEP, **{"a/1.jpg": {"exif": json.dumps({"b": 2, "a": 1})}})
    assert fd.main([old, new, *_evals(tmp_path, ["a/1.jpg"])]) == 0
    out = capsys.readouterr().out
    assert "newly skipped: 5" in out and "OK" in out


def test_old_rule_dirs_are_not_newly_skipped():
    assert fd.newly_skipped("x/@eaDir/t.jpg") is None
    assert fd.newly_skipped("x/IPOD PHOTO CACHE/t.jpg") == "iPod Photo Cache"
    assert fd.newly_skipped(".thumbnails") is None  # a file name, not a dir


def test_other_changes_fail(tmp_path, capsys):
    old = _frame(tmp_path / "o.parquet", KEEP)
    for new in (_frame(tmp_path / "n1.parquet", KEEP[:2]),                       # removed, not skipped
                _frame(tmp_path / "n2.parquet", [*KEEP, "z.jpg"]),                # added
                _frame(tmp_path / "n3.parquet", KEEP, **{"a/1.jpg": {"bytes": 2}})):  # changed
        assert fd.main([old, new, *_evals(tmp_path, [])]) == 1
    assert "DIFFERS" in capsys.readouterr().out


def test_eval_item_under_a_newly_skipped_path_fails(tmp_path, capsys):
    old = _frame(tmp_path / "o.parquet", KEEP + NEW_SKIP[:1])
    new = _frame(tmp_path / "n.parquet", KEEP)
    assert fd.main([old, new, *_evals(tmp_path, [NEW_SKIP[0]])]) == 1
    assert "eval items under a newly skipped path: 1 of 1" in capsys.readouterr().out
