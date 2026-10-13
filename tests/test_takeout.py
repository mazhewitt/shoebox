"""`shoebox takeout verify` (add-takeout-landing D1-D5) on synthetic zips."""

import json
import os
import zipfile
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from shoebox import config, ids, takeout

TS = "20261012T083010Z"
CLEANUP = f"export {TS} for owner verified on the NAS; safe to delete from Drive"


@pytest.fixture
def env(tmp_path):
    zips = tmp_path / "ro" / "Takeout"
    zips.mkdir(parents=True)
    src = config.Source("importantfiles", tmp_path / "ro", "owner", synced=True, hosted_consent=True)
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={"importantfiles": src})
    return cfg, zips


def make_zip(zips: Path, n: int, entries: dict[str, bytes], ts: str = TS,
             method=zipfile.ZIP_DEFLATED) -> Path:
    p = zips / f"takeout-{ts}-{n:03d}.zip"
    with zipfile.ZipFile(p, "w", compression=method) as z:
        z.writestr("Takeout/", b"")  # a directory entry: not a file
        for name, data in entries.items():
            z.writestr(name, data)
    return p


def run(cfg, zips, capsys, **kw):
    code = takeout.verify(cfg, zips, "owner", **kw)
    out = capsys.readouterr()
    return code, out.out, out.err


def tree(cfg) -> Path:
    return cfg.shoebox_root / "originals" / "takeout" / "owner" / TS


def mdir(cfg) -> Path:
    return cfg.shoebox_root / "originals" / "takeout" / "owner" / f"{TS}.manifest"


def zip_rows(cfg) -> dict[str, dict]:
    rows = {}
    for line in (mdir(cfg) / "zips.jsonl").read_text().splitlines():
        r = json.loads(line)
        rows[r["zip"]] = r
    return rows


def snapshot(d: Path) -> dict:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in d.rglob("*")}


def files(d: Path) -> list[str]:
    return sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file())


def test_good_zip(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/Google Photos/a/IMG_SECRET1.jpg": b"one" * 1000,
                       "Takeout/Google Photos/a/IMG_SECRET1.jpg.json": b"{}"})
    make_zip(zips, 2, {"Takeout/Google Photos/b/IMG_SECRET2.jpg": b"two" * 500})
    before = snapshot(zips)
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0
    assert files(tree(cfg)) == ["Takeout/Google Photos/a/IMG_SECRET1.jpg",
                                "Takeout/Google Photos/a/IMG_SECRET1.jpg.json",
                                "Takeout/Google Photos/b/IMG_SECRET2.jpg"]
    assert (tree(cfg) / "Takeout/Google Photos/b/IMG_SECRET2.jpg").read_bytes() == b"two" * 500
    assert {r["status"] for r in zip_rows(cfg).values()} == {"done"}
    t = pq.read_table(mdir(cfg) / "manifest.parquet").to_pylist()
    assert len(t) == 3
    row = next(r for r in t if r["path"].endswith("IMG_SECRET2.jpg"))
    p = tree(cfg) / row["path"]
    assert row["blake3"] == ids.asset_id(p) and row["size"] == 1500 and row["zip"].endswith("-002.zip")
    assert not os.access(p, os.W_OK)  # chmod a-w
    assert CLEANUP in out
    assert "SECRET" not in out  # counts only
    assert snapshot(zips) == before  # nothing written under the zips folder
    assert not list(mdir(cfg).glob("tmp/*"))


def test_corrupt_entry_crc(env, capsys):
    cfg, zips = env
    bad = make_zip(zips, 1, {"Takeout/x/a.jpg": b"A" * 4000, "Takeout/x/b.jpg": b"B" * 4000},
                   method=zipfile.ZIP_STORED)
    data = bytearray(bad.read_bytes())
    i = data.index(b"B" * 4000) + 100
    data[i] ^= 0xFF
    bad.write_bytes(bytes(data))
    make_zip(zips, 2, {"Takeout/y/c.jpg": b"C" * 10})
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0
    rows = zip_rows(cfg)
    assert rows[bad.name]["status"] == "failed"
    assert rows[f"takeout-{TS}-002.zip"]["status"] == "done"
    assert files(tree(cfg)) == ["Takeout/y/c.jpg"]  # no partial file of the bad zip (a.jpg was fine, still gone)
    assert CLEANUP not in out


def test_unopenable_zip(env, capsys):
    cfg, zips = env
    (zips / f"takeout-{TS}-001.zip").write_bytes(b"not a zip at all")
    make_zip(zips, 2, {"Takeout/y/c.jpg": b"C"})
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0
    assert zip_rows(cfg)[f"takeout-{TS}-001.zip"]["status"] == "failed"
    assert files(tree(cfg)) == ["Takeout/y/c.jpg"]
    assert CLEANUP not in out


def test_resume_after_interrupt(env, capsys, monkeypatch):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100})
    make_zip(zips, 2, {"Takeout/b/2.jpg": b"2" * 100, "Takeout/b/3.jpg": b"3" * 100})
    make_zip(zips, 3, {"Takeout/c/4.jpg": b"4" * 100})
    real = takeout._copy_entry
    calls = {"n": 0}

    def flaky(zf, info, tmp):
        if info.filename == "Takeout/b/3.jpg" and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("SMB hiccup")
        return real(zf, info, tmp)

    monkeypatch.setattr(takeout, "_copy_entry", flaky)
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert zip_rows(cfg)[f"takeout-{TS}-002.zip"]["status"] == "failed"
    assert "Takeout/b/2.jpg" not in files(tree(cfg))

    opened = []
    real_extract = takeout._extract_zip

    def spy(zip_path, *a, **k):
        opened.append(zip_path.name)
        return real_extract(zip_path, *a, **k)

    monkeypatch.setattr(takeout, "_extract_zip", spy)
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert opened == [f"takeout-{TS}-002.zip"]
    t = pq.read_table(mdir(cfg) / "manifest.parquet").to_pylist()
    assert sorted(r["path"] for r in t) == ["Takeout/a/1.jpg", "Takeout/b/2.jpg", "Takeout/b/3.jpg",
                                            "Takeout/c/4.jpg"]


def test_resume_redoes_done_zip_with_missing_file(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100})
    assert run(cfg, zips, capsys)[0] == 0
    (tree(cfg) / "Takeout/a/1.jpg").unlink()
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert files(tree(cfg)) == ["Takeout/a/1.jpg"]


def test_duplicate_identical_recorded_once(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/archive_browser.html": b"same"})
    make_zip(zips, 2, {"Takeout/archive_browser.html": b"same", "Takeout/x.jpg": b"x"})
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    t = pq.read_table(mdir(cfg) / "manifest.parquet").to_pylist()
    dup = [r for r in t if r["path"] == "Takeout/archive_browser.html"]
    assert len(dup) == 1 and dup[0]["zip"] == f"takeout-{TS}-001.zip,takeout-{TS}-002.zip"
    assert len(t) == 2


def test_duplicate_different_bytes_is_error(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/archive_browser.html": b"first"})
    make_zip(zips, 2, {"Takeout/archive_browser.html": b"second", "Takeout/x.jpg": b"x"})
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert zip_rows(cfg)[f"takeout-{TS}-002.zip"]["status"] == "failed"
    assert (tree(cfg) / "Takeout/archive_browser.html").read_bytes() == b"first"
    assert files(tree(cfg)) == ["Takeout/archive_browser.html"]


@pytest.mark.parametrize("damage", ["delete", "resize"])
def test_reconcile_catches_missing_or_resized(env, capsys, damage):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100, "Takeout/a/2.jpg": b"2" * 100})
    assert run(cfg, zips, capsys)[0] == 0
    p = tree(cfg) / "Takeout/a/1.jpg"
    os.chmod(p, 0o644)
    if damage == "delete":
        p.unlink()
    else:
        p.write_bytes(b"short")
    problems = takeout.reconcile(mdir(cfg), tree(cfg))
    assert problems and all("1.jpg" not in m for m in problems)  # counts/kinds, no names
    (tree(cfg) / "Takeout/a/extra.jpg").write_bytes(b"e")
    assert any("not in the manifest" in m for m in takeout.reconcile(mdir(cfg), tree(cfg)))


@pytest.mark.parametrize("damage", ["delete", "resize"])
def test_verify_reports_reconcile_mismatch(env, capsys, monkeypatch, damage):
    """The run itself reconciles: if resume trusted a damaged tree, the mismatch still fails the command."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100})
    assert run(cfg, zips, capsys)[0] == 0
    p = tree(cfg) / "Takeout/a/1.jpg"
    os.chmod(p, 0o644)
    p.unlink() if damage == "delete" else p.write_bytes(b"short")
    monkeypatch.setattr(takeout, "_intact", lambda tree, rows: True)
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out and "reconcile mismatch" in out and "1.jpg" not in out

def test_dest_under_source_root_refused(env, capsys, tmp_path):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1"})
    before = snapshot(tmp_path / "ro")
    with pytest.raises(ValueError, match="source root"):
        takeout.verify(cfg, zips, "owner", dest_root=tmp_path / "ro" / "out")
    assert snapshot(tmp_path / "ro") == before
    with pytest.raises(ValueError, match="/Volumes"):
        takeout.verify(cfg, zips, "owner", dest_root=Path("/Volumes/shoebox/x"))


def test_separate_exports_and_filter(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a.jpg": b"a"})
    make_zip(zips, 1, {"Takeout/archive_browser.html": b"h"}, ts="20261012T083009Z")
    code, out, _ = run(cfg, zips, capsys, export="20261012T083009Z")
    assert code == 0
    root = cfg.shoebox_root / "originals" / "takeout" / "owner"
    assert files(root / "20261012T083009Z") == ["Takeout/archive_browser.html"]
    assert not (root / TS).exists()
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert files(root / TS) == ["Takeout/a.jpg"]


def test_cli_registered(env, monkeypatch):
    from shoebox import cli

    args = cli.build_parser().parse_args(["takeout", "verify", "--zips", "/x", "--account", "owner"])
    assert args.func.__name__ == "cmd_verify"
