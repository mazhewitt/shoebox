"""End-to-end tests of the `shoebox pilot` subcommands on fake data."""

import json
from pathlib import Path

import pytest

from shoebox import config
from shoebox.cli import main
from shoebox.ids import asset_id


@pytest.fixture
def fake(tmp_path, monkeypatch) -> tuple[Path, Path]:
    src = tmp_path / "src"
    (src / "A").mkdir(parents=True)
    (src / "A" / "x.jpg").write_bytes(b"pixels")
    cfg = config.Config(
        shoebox_root=tmp_path / "sb",
        sources={"importantfiles": config.Source("importantfiles", src, "owner", synced=True, hosted_consent=True)},
    )
    monkeypatch.setattr(config, "load", lambda path=None: cfg)
    items = tmp_path / "items.jsonl"
    items.write_text(json.dumps({
        "asset_id": asset_id(src / "A/x.jpg"), "source": "importantfiles", "relpath": "A/x.jpg", "ext": "jpg",
        "bytes": 6, "mtime": 0.0, "bucket": "b", "group_id": None, "group_rule": None,
    }) + "\n")
    return src, items


def test_pick_refuses_writable_synced_source(fake, tmp_path, capsys):
    assert main(["pilot", "pick", "--out-dir", str(tmp_path / "out")]) == 2
    assert str(fake[0]) in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_snapshot_and_verify(fake, tmp_path, monkeypatch, capsys):
    src, items = fake
    monkeypatch.setattr("shoebox.pilot.untouched.is_writable", lambda p: False)
    snap = tmp_path / "res" / "snap.json"
    assert main(["pilot", "snapshot", "--out", str(snap), "--items", str(items)]) == 0
    assert main(["pilot", "verify-untouched", "--snapshot", str(snap)]) == 0
    (src / "A" / "stray.tmp").write_bytes(b"")
    capsys.readouterr()
    assert main(["pilot", "verify-untouched", "--snapshot", str(snap)]) == 1
    assert "stray.tmp" in capsys.readouterr().out


def test_snapshot_refuses_writable_synced_source(fake, tmp_path):
    _, items = fake
    assert main(["pilot", "snapshot", "--out", str(tmp_path / "s.json"), "--items", str(items)]) == 2


def test_sheet_command(fake, tmp_path):
    _, items = fake
    assert main(["pilot", "sheet", "--items", str(items), "--labels", str(tmp_path / "none.jsonl")]) == 0
    assert (tmp_path / "sb" / "results" / "pilot-v0" / "sheet.html").is_file()


def test_pick_reports_errors_without_traceback(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("shoebox.pilot.untouched.is_writable", lambda p: False)

    def over_cap(*a, **k):
        raise ValueError("recipe picked 60 files, over the cap of 50")

    monkeypatch.setattr("shoebox.pilot.pick.select", over_cap)
    assert main(["pilot", "pick", "--out-dir", str(tmp_path / "out")]) == 2
    assert "over the cap" in capsys.readouterr().err


def test_snapshot_before_any_pick(fake, tmp_path, monkeypatch, capsys):
    """No items.jsonl yet: the snapshot covers the recipe's folders, and verify catches a new file there."""
    src, _ = fake
    monkeypatch.setattr("shoebox.pilot.untouched.is_writable", lambda p: False)
    (src / "Pictures" / "Screenshots").mkdir(parents=True)
    snap = tmp_path / "res" / "before.json"
    assert main(["pilot", "snapshot", "--out", str(snap), "--items", str(tmp_path / "none.jsonl")]) == 0
    data = json.loads(snap.read_text())
    assert "importantfiles:Pictures/Screenshots" in data["dirs"]
    assert "importantfiles:Pictures/Google Photos/1969" in data["dirs"]
    assert main(["pilot", "verify-untouched", "--snapshot", str(snap)]) == 0
    (src / "Pictures" / "Screenshots" / ".DS_Store").write_bytes(b"")
    capsys.readouterr()
    assert main(["pilot", "verify-untouched", "--snapshot", str(snap)]) == 1
    assert ".DS_Store" in capsys.readouterr().out
