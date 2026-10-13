"""`shoebox takeout verify` (add-takeout-landing D1-D8) on synthetic zips."""

import json
import os
import unicodedata
import zipfile
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from shoebox import config, ids, takeout

TS = "20261012T083010Z"
CLEANUP = f"export {TS} for owner verified on the NAS; safe to delete from Drive"
COLUMNS = ["zip", "entry", "path", "size", "crc32", "blake3"]


@pytest.fixture
def env(tmp_path):
    zips = tmp_path / "ro" / "Takeout"
    zips.mkdir(parents=True)
    src = config.Source("importantfiles", tmp_path / "ro", "owner", synced=True, hosted_consent=True)
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={"importantfiles": src})
    return cfg, zips


def zname(n: int, ts: str = TS) -> str:
    return f"takeout-{ts}-{n:03d}.zip"


def stem(n: int, ts: str = TS) -> str:
    return zname(n, ts)[:-4]


def make_zip(zips: Path, n: int, entries: dict[str, bytes], ts: str = TS,
             method=zipfile.ZIP_DEFLATED) -> Path:
    p = zips / zname(n, ts)
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


def manifest(cfg) -> list[dict]:
    t = pq.read_table(mdir(cfg) / "manifest.parquet")
    assert t.column_names == COLUMNS
    return t.to_pylist()


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
    assert files(tree(cfg)) == [f"{stem(1)}/Takeout/Google Photos/a/IMG_SECRET1.jpg",
                                f"{stem(1)}/Takeout/Google Photos/a/IMG_SECRET1.jpg.json",
                                f"{stem(2)}/Takeout/Google Photos/b/IMG_SECRET2.jpg"]
    assert (tree(cfg) / stem(2) / "Takeout/Google Photos/b/IMG_SECRET2.jpg").read_bytes() == b"two" * 500
    assert {r["status"] for r in zip_rows(cfg).values()} == {"done"}
    t = manifest(cfg)
    assert len(t) == 3
    row = next(r for r in t if r["path"].endswith("IMG_SECRET2.jpg"))
    assert row["entry"] == "Takeout/Google Photos/b/IMG_SECRET2.jpg"
    assert row["path"] == f"{stem(2)}/Takeout/Google Photos/b/IMG_SECRET2.jpg"
    p = tree(cfg) / row["path"]
    assert row["blake3"] == ids.asset_id(p) and row["size"] == 1500 and row["zip"] == zname(2)
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
    assert rows[zname(2)]["status"] == "done"
    assert files(tree(cfg)) == [f"{stem(2)}/Takeout/y/c.jpg"]  # no partial file of the bad zip
    assert CLEANUP not in out


def test_unopenable_zip(env, capsys):
    cfg, zips = env
    (zips / zname(1)).write_bytes(b"not a zip at all")
    make_zip(zips, 2, {"Takeout/y/c.jpg": b"C"})
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0
    assert zip_rows(cfg)[zname(1)]["status"] == "failed"
    assert files(tree(cfg)) == [f"{stem(2)}/Takeout/y/c.jpg"]
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
    assert zip_rows(cfg)[zname(2)]["status"] == "failed"
    assert f"{stem(2)}/Takeout/b/2.jpg" not in files(tree(cfg))

    opened = []
    real_extract = takeout._extract_zip

    def spy(zip_path, *a, **k):
        opened.append(zip_path.name)
        return real_extract(zip_path, *a, **k)

    monkeypatch.setattr(takeout, "_extract_zip", spy)
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert opened == [zname(2)]
    assert sorted(r["path"] for r in manifest(cfg)) == [
        f"{stem(1)}/Takeout/a/1.jpg", f"{stem(2)}/Takeout/b/2.jpg", f"{stem(2)}/Takeout/b/3.jpg",
        f"{stem(3)}/Takeout/c/4.jpg"]


def test_resume_redoes_done_zip_with_missing_file(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100})
    assert run(cfg, zips, capsys)[0] == 0
    (tree(cfg) / stem(1) / "Takeout/a/1.jpg").unlink()
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert files(tree(cfg)) == [f"{stem(1)}/Takeout/a/1.jpg"]


def test_same_entry_in_two_zips_both_kept(env, capsys):
    """Takeout numbers duplicate names per zip: the same entry path in two zips is two different photos (D3)."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/Photos from 2019/2023-07-30(23).jpg": b"first"})
    make_zip(zips, 2, {"Takeout/Photos from 2019/2023-07-30(23).jpg": b"second!"})
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert (tree(cfg) / stem(1) / "Takeout/Photos from 2019/2023-07-30(23).jpg").read_bytes() == b"first"
    assert (tree(cfg) / stem(2) / "Takeout/Photos from 2019/2023-07-30(23).jpg").read_bytes() == b"second!"
    rows = manifest(cfg)
    assert sorted((r["zip"], r["size"]) for r in rows) == [(zname(1), 5), (zname(2), 7)]
    assert {r["entry"] for r in rows} == {"Takeout/Photos from 2019/2023-07-30(23).jpg"}


def test_trailing_space_album_stored_smb_safe(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/Google Photos/Europa park /x.jpg": b"x" * 10,
                       "Takeout/Google Photos/Trip./y.jpg": b"y"})
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert files(tree(cfg)) == [f"{stem(1)}/Takeout/Google Photos/Europa park/x.jpg",
                                f"{stem(1)}/Takeout/Google Photos/Trip/y.jpg"]
    row = next(r for r in manifest(cfg) if r["path"].endswith("x.jpg"))
    assert row["entry"] == "Takeout/Google Photos/Europa park /x.jpg"
    assert row["path"] == f"{stem(1)}/Takeout/Google Photos/Europa park/x.jpg"


def test_trailing_space_twin_in_one_zip_is_conflict(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/Album /x.jpg": b"1", "Takeout/Album/x.jpg": b"2"})
    make_zip(zips, 2, {"Takeout/ok.jpg": b"ok"})
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert zip_rows(cfg)[zname(1)]["status"] == "failed"
    assert files(tree(cfg)) == [f"{stem(2)}/Takeout/ok.jpg"]


def test_case_only_twin_in_one_zip_is_conflict(env, capsys):
    """The NAS share is case-insensitive: IMG.JPG and img.jpg in one zip folder would be one file."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/IMG.JPG": b"1" * 10, "Takeout/img.jpg": b"2" * 20})
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert zip_rows(cfg)[zname(1)]["status"] == "failed"


def test_case_only_across_zips_both_kept(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/IMG.JPG": b"1" * 10})
    make_zip(zips, 2, {"Takeout/img.jpg": b"2" * 20})
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert len(manifest(cfg)) == 2


@pytest.mark.parametrize("damage", ["delete", "resize"])
def test_reconcile_catches_missing_or_resized(env, capsys, damage):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100, "Takeout/a/2.jpg": b"2" * 100})
    assert run(cfg, zips, capsys)[0] == 0
    p = tree(cfg) / stem(1) / "Takeout/a/1.jpg"
    os.chmod(p, 0o644)
    if damage == "delete":
        p.unlink()
    else:
        p.write_bytes(b"short")
    problems = takeout.reconcile(mdir(cfg), tree(cfg))
    assert problems and all("1.jpg" not in m for m in problems)  # counts/kinds, no names
    (tree(cfg) / stem(1) / "Takeout/a/extra.jpg").write_bytes(b"e")
    assert any("not in the manifest" in m for m in takeout.reconcile(mdir(cfg), tree(cfg)))


def test_reconcile_unexpected_top_level_folder(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1"})
    assert run(cfg, zips, capsys)[0] == 0
    (tree(cfg) / "Takeout").mkdir()  # e.g. a leftover merged extract
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out and "unexpected top-level" in out


@pytest.mark.parametrize("damage", ["delete", "resize"])
def test_verify_reports_reconcile_mismatch(env, capsys, monkeypatch, damage):
    """The run itself reconciles: if resume trusted a damaged tree, the mismatch still fails the command."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100})
    assert run(cfg, zips, capsys)[0] == 0
    p = tree(cfg) / stem(1) / "Takeout/a/1.jpg"
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
    assert files(root / "20261012T083009Z") == [f"{stem(1, '20261012T083009Z')}/Takeout/archive_browser.html"]
    assert not (root / TS).exists()
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert files(root / TS) == [f"{stem(1)}/Takeout/a.jpg"]


def test_cli_registered(env, monkeypatch):
    from shoebox import cli

    args = cli.build_parser().parse_args(["takeout", "verify", "--zips", "/x", "--account", "owner"])
    assert args.func.__name__ == "cmd_verify"


def test_failed_redo_removes_files_from_earlier_run(env, capsys, monkeypatch):
    """A done zip whose tree was damaged is redone; if the redo fails, none of its files may stay behind,
    including those written by the earlier run."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/x": b"x", "Takeout/y": b"y", "Takeout/z": b"z"})
    assert run(cfg, zips, capsys)[0] == 0
    os.chmod(tree(cfg) / stem(1) / "Takeout/x", 0o644)
    (tree(cfg) / stem(1) / "Takeout/x").unlink()
    real = takeout._copy_entry

    def flaky(zf, info, tmp):
        if info.filename == "Takeout/y":
            raise OSError("SMB hiccup")
        return real(zf, info, tmp)

    monkeypatch.setattr(takeout, "_copy_entry", flaky)
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert zip_rows(cfg)[zname(1)]["status"] == "failed"
    assert files(tree(cfg)) == []


def test_failed_zip_leaves_other_zips_alone(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/archive_browser.html": b"same"})
    make_zip(zips, 2, {"Takeout/archive_browser.html": b"same", "Takeout/q.jpg": b"q" * 10},
             method=zipfile.ZIP_STORED)
    p = zips / zname(2)
    data = bytearray(p.read_bytes())
    data[data.index(b"q" * 10) + 3] ^= 0xFF
    p.write_bytes(bytes(data))
    code, _, _ = run(cfg, zips, capsys)
    assert code != 0
    assert files(tree(cfg)) == [f"{stem(1)}/Takeout/archive_browser.html"]


def test_reconcile_counts_zip_entries_independently(env, capsys):
    """A truncated part file plus its file gone from the tree: disk = manifest rows, but the zips say more."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100, "Takeout/a/2.jpg": b"2" * 100})
    assert run(cfg, zips, capsys)[0] == 0
    part = mdir(cfg) / "parts" / f"{zname(1)}.jsonl"
    lines = part.read_text().splitlines()
    gone = json.loads(lines[-1])["path"]
    part.write_text(lines[0] + "\n")
    os.chmod(tree(cfg) / gone, 0o644)
    (tree(cfg) / gone).unlink()
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert "zip file entries 2 != manifest rows 1" in out


# --- verify-only mode (--extracted, design D7/D8): the tree was extracted on the NAS by 7-Zip ---------------


def nas_extract(zips: Path, cfg, nfd: bool = False) -> None:
    """Simulate ~/mnt/shoebox/logs/takeout-perzip.sh: `7z x -y -aoa` of each zip into <export>/<zip stem>, then
    strip trailing spaces/dots from names, deepest first, skipping a rename whose target exists."""
    t = tree(cfg)
    t.mkdir(parents=True, exist_ok=True)
    for zp in sorted(zips.glob(f"takeout-{TS}-*.zip")):
        d = t / zp.name[:-4]
        with zipfile.ZipFile(zp) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = unicodedata.normalize("NFD", info.filename) if nfd else info.filename
                dest = d / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(info))
    for p in sorted(t.rglob("*"), key=lambda p: -len(p.parts)):
        n = p.name.rstrip(" .")
        if n != p.name and n and not (p.parent / n).exists():
            p.rename(p.parent / n)


def run_x(cfg, zips, capsys, **kw):
    code = takeout.verify_extracted(cfg, zips, "owner", TS, **kw)
    out = capsys.readouterr()
    return code, out.out, out.err


def two_zips(zips):
    make_zip(zips, 1, {"Takeout/Google Photos/a/IMG_SECRET1.jpg": b"one" * 1000,
                       "Takeout/Google Photos/a/IMG_SECRET1.jpg.json": b"{}"})
    make_zip(zips, 2, {"Takeout/Google Photos/b/IMG_SECRET2.jpg": b"two" * 500})


def test_x_all_good(env, capsys):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    before = snapshot(zips)
    code, out, _ = run_x(cfg, zips, capsys, workers=3)
    assert code == 0 and CLEANUP in out
    assert {r["status"] for r in zip_rows(cfg).values()} == {"verified"}
    rows = manifest(cfg)
    assert len(rows) == 3
    row = next(r for r in rows if r["path"].endswith("IMG_SECRET2.jpg"))
    assert row["path"] == f"{stem(2)}/Takeout/Google Photos/b/IMG_SECRET2.jpg"
    assert row["entry"] == "Takeout/Google Photos/b/IMG_SECRET2.jpg"
    p = tree(cfg) / row["path"]
    assert row["blake3"] == ids.asset_id(p) and row["size"] == 1500 and row["zip"] == zname(2)
    assert not os.access(p, os.W_OK)
    assert "SECRET" not in out
    assert snapshot(zips) == before


@pytest.mark.parametrize("damage,field", [("delete", "missing"), ("resize", "size_mismatch"),
                                          ("flip", "crc_mismatch")])
def test_x_damaged_file_fails_its_zip(env, capsys, damage, field):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    p = tree(cfg) / stem(2) / "Takeout/Google Photos/b/IMG_SECRET2.jpg"
    if damage == "delete":
        p.unlink()
    elif damage == "resize":
        p.write_bytes(b"short")
    else:
        data = bytearray(p.read_bytes())
        data[700] ^= 0x01
        p.write_bytes(bytes(data))
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out and "SECRET" not in out
    rows = zip_rows(cfg)
    assert rows[zname(1)]["status"] == "verified"
    bad = rows[zname(2)]
    assert bad["status"] == "failed" and bad[field] == 1
    assert sum(bad[k] for k in ("missing", "size_mismatch", "crc_mismatch")) == 1


def test_x_file_in_another_zips_folder_is_missing(env, capsys):
    """Per-zip layout: a file present only under another zip's folder does not satisfy this zip's entry."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a.jpg": b"a"})
    make_zip(zips, 2, {"Takeout/a.jpg": b"a"})
    nas_extract(zips, cfg)
    (tree(cfg) / stem(2) / "Takeout/a.jpg").unlink()
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out and zip_rows(cfg)[zname(2)]["missing"] == 1


def test_x_extra_file_is_reconcile_error(env, capsys):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    (tree(cfg) / stem(1) / "Takeout/stray_SECRET.jpg").write_bytes(b"x")
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out and "reconcile mismatch" in out and "SECRET" not in out


@pytest.mark.parametrize("item", ["dir", "file"])
def test_x_unexpected_top_level_is_reconcile_error(env, capsys, item):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    if item == "dir":
        (tree(cfg) / "Takeout").mkdir()  # an empty leftover of a merged extract
    else:
        (tree(cfg) / "notes.txt").write_bytes(b"n")
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out and "unexpected top-level" in out


def test_x_synology_metadata_ignored(env, capsys):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    ea = tree(cfg) / stem(1) / "Takeout/Google Photos/a/@eaDir/IMG_SECRET1.jpg@SynoEAStream"
    ea.parent.mkdir()
    ea.write_bytes(b"meta")
    (tree(cfg) / stem(1) / "Takeout/.DS_Store").write_bytes(b"ds")
    (tree(cfg) / "@eaDir").mkdir()
    (tree(cfg) / ".DS_Store").write_bytes(b"ds")
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert "3 ignored" in out


def test_x_same_entry_in_two_zips_different_bytes(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/Photos from 2019/2023-07-30(23).jpg": b"first",
                       "Takeout/Photos from 2019/2023-07-30(23).jpg.supplemental-metadata(23).json": b"{1}"})
    make_zip(zips, 2, {"Takeout/Photos from 2019/2023-07-30(23).jpg": b"second!",
                       "Takeout/Photos from 2019/2023-07-30(23).jpg.supplemental-metadata(23).json": b"{2}"})
    nas_extract(zips, cfg)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    rows = manifest(cfg)
    assert len(rows) == 4 and {r["zip"] for r in rows} == {zname(1), zname(2)}
    assert {r["size"] for r in rows if r["path"].endswith(".jpg")} == {5, 7}


def test_x_trailing_space_album(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/Google Photos/Europa park /x.jpg": b"x" * 10})
    nas_extract(zips, cfg)
    assert files(tree(cfg)) == [f"{stem(1)}/Takeout/Google Photos/Europa park/x.jpg"]
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    (row,) = manifest(cfg)
    assert row["entry"] == "Takeout/Google Photos/Europa park /x.jpg"
    assert row["path"] == f"{stem(1)}/Takeout/Google Photos/Europa park/x.jpg"


def test_x_trailing_space_twin_in_one_zip_is_conflict(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/Album /x.jpg": b"1", "Takeout/Album/x.jpg": b"2"})
    nas_extract(zips, cfg)  # the rename of "Album " is skipped: "Album" exists
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert zip_rows(cfg)[zname(1)]["conflicts"] == 1


def test_x_nfd_name_on_disk_matches_nfc_entry(env, capsys):
    cfg, zips = env
    nfc = unicodedata.normalize("NFC", "Takeout/Café/crème.jpg")
    make_zip(zips, 1, {nfc: b"c" * 50})
    nas_extract(zips, cfg, nfd=True)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert len(manifest(cfg)) == 1


def test_x_nfd_entry_name_matches_nfc_on_disk(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {unicodedata.normalize("NFD", "Takeout/Café.jpg"): b"c" * 50})
    t = tree(cfg) / stem(1) / "Takeout"
    t.mkdir(parents=True)
    (t / unicodedata.normalize("NFC", "Café.jpg")).write_bytes(b"c" * 50)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out


def test_x_case_and_nfc_twin_in_one_zip_is_conflict(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/IMG_É.JPG": b"same", unicodedata.normalize("NFD", "Takeout/img_é.jpg"): b"same"})
    make_zip(zips, 2, {"Takeout/x": b"x"})
    nas_extract(zips, cfg)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    rows = zip_rows(cfg)
    assert rows[zname(1)]["conflicts"] == 1 and rows[zname(2)]["status"] == "verified"


def test_x_resume_skips_verified_zips(env, capsys, monkeypatch):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    p = tree(cfg) / stem(2) / "Takeout/Google Photos/b/IMG_SECRET2.jpg"
    good = p.read_bytes()
    p.unlink()
    assert run_x(cfg, zips, capsys)[0] != 0
    p.write_bytes(good)
    hashed = []
    real = takeout._hash_file

    def spy(path):
        hashed.append(path.name)
        return real(path)

    monkeypatch.setattr(takeout, "_hash_file", spy)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert hashed == ["IMG_SECRET2.jpg"]
    assert len(manifest(cfg)) == 3


def test_x_refuses_missing_tree(env, capsys):
    cfg, zips = env
    two_zips(zips)
    with pytest.raises(ValueError, match="does not exist"):
        takeout.verify_extracted(cfg, zips, "owner", TS)
    assert not mdir(cfg).exists()


def test_x_refuses_writes_under_zips_or_source(env, capsys, tmp_path):
    cfg, zips = env
    two_zips(zips)
    before = snapshot(tmp_path / "ro")
    with pytest.raises(ValueError):
        takeout.verify_extracted(cfg, zips, "owner", TS, dest_root=zips / "out")
    with pytest.raises(ValueError, match="/Volumes"):
        takeout.verify_extracted(cfg, zips, "owner", TS, dest_root=Path("/Volumes/shoebox/x"))
    assert snapshot(tmp_path / "ro") == before


def test_x_cli(env):
    from shoebox import cli

    args = cli.build_parser().parse_args(["takeout", "verify", "--zips", "/x", "--account", "owner",
                                          "--export", TS, "--extracted", "--workers", "4"])
    assert args.extracted and args.workers == 4


def _case_sensitive_walk(monkeypatch, alias_of: dict[str, str]):
    """Simulate the NAS's case-sensitive btrfs, where a second spelling can exist next to the first: the walk
    also reports each alias name (on this case-insensitive host the alias opens the same bytes)."""
    real = os.walk

    def walk(top):
        for dirpath, dirnames, filenames in real(top):
            yield dirpath, dirnames, filenames + [alias_of[f] for f in filenames if f in alias_of]

    monkeypatch.setattr(takeout.os, "walk", walk)


def test_x_case_alias_not_named_by_any_entry_is_extra(env, capsys, monkeypatch):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/IMG_E.JPG": b"same"})
    nas_extract(zips, cfg)
    _case_sensitive_walk(monkeypatch, {"IMG_E.JPG": "img_e.jpg"})
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out and "not in the manifest" in out


@pytest.mark.parametrize("argv,expect", [
    (["--extracted", "--export", TS, "--workers", "4"], ("x", 4)),
    (["--export", TS], ("self", None)),
])
def test_x_cli_routes(env, monkeypatch, argv, expect):
    from shoebox import cli
    from shoebox.commands import takeout as cmd

    cfg, zips = env
    calls = []
    monkeypatch.setattr(cmd.config, "load", lambda _p: cfg)
    monkeypatch.setattr(cmd.takeout, "verify_extracted",
                        lambda c, z, a, e, dest_root=None, workers=8: calls.append(("x", workers)) or 0)
    monkeypatch.setattr(cmd.takeout, "verify",
                        lambda c, z, a, dest_root=None, export=None: calls.append(("self", None)) or 0)
    args = cli.build_parser().parse_args(["takeout", "verify", "--zips", str(zips), "--account", "owner", *argv])
    assert args.func(args) == 0 and calls == [expect]


def test_x_cli_extracted_needs_export(env, monkeypatch, capsys):
    from shoebox import cli
    from shoebox.commands import takeout as cmd

    cfg, zips = env
    monkeypatch.setattr(cmd.config, "load", lambda _p: cfg)
    monkeypatch.setattr(cmd.takeout, "verify_extracted", lambda *a, **k: pytest.fail("should refuse"))
    args = cli.build_parser().parse_args(["takeout", "verify", "--zips", str(zips), "--account", "owner",
                                          "--extracted"])
    assert args.func(args) == 2 and "--extracted needs --export TS" in capsys.readouterr().err
