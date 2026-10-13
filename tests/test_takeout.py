"""`shoebox takeout verify` (add-takeout-landing D1-D5) on synthetic zips."""

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


def test_failed_redo_removes_files_from_earlier_run(env, capsys, monkeypatch):
    """A done zip whose tree was damaged is redone; if the redo fails, none of its files may stay behind,
    including those written by the earlier run."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/x": b"x", "Takeout/y": b"y", "Takeout/z": b"z"})
    assert run(cfg, zips, capsys)[0] == 0
    os.chmod(tree(cfg) / "Takeout/x", 0o644)
    (tree(cfg) / "Takeout/x").unlink()
    real = takeout._copy_entry

    def flaky(zf, info, tmp):
        if info.filename == "Takeout/y":
            raise OSError("SMB hiccup")
        return real(zf, info, tmp)

    monkeypatch.setattr(takeout, "_copy_entry", flaky)
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert zip_rows(cfg)[f"takeout-{TS}-001.zip"]["status"] == "failed"
    assert files(tree(cfg)) == []


def test_failed_zip_keeps_files_owned_by_another_done_zip(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/archive_browser.html": b"same"})
    make_zip(zips, 2, {"Takeout/archive_browser.html": b"same", "Takeout/q.jpg": b"q" * 10},
             method=zipfile.ZIP_STORED)
    p = zips / f"takeout-{TS}-002.zip"
    data = bytearray(p.read_bytes())
    data[data.index(b"q" * 10) + 3] ^= 0xFF
    p.write_bytes(bytes(data))
    code, _, _ = run(cfg, zips, capsys)
    assert code != 0
    assert files(tree(cfg)) == ["Takeout/archive_browser.html"]


def test_case_only_duplicate_is_a_duplicate(env, capsys):
    """The NAS share is case-insensitive: IMG.JPG and img.jpg are one file on disk."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/IMG.JPG": b"1" * 10})
    make_zip(zips, 2, {"Takeout/img.jpg": b"2" * 20})
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert zip_rows(cfg)[f"takeout-{TS}-002.zip"]["status"] == "failed"
    assert (tree(cfg) / "Takeout/IMG.JPG").read_bytes() == b"1" * 10


def test_case_only_duplicate_identical_recorded_once(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/IMG.JPG": b"same"})
    make_zip(zips, 2, {"Takeout/img.jpg": b"same"})
    code, out, _ = run(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert len(pq.read_table(mdir(cfg) / "manifest.parquet").to_pylist()) == 1


def test_reconcile_counts_zip_entries_independently(env, capsys):
    """A truncated part file plus its file gone from the tree: disk = manifest rows, but the zips say more."""
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/a/1.jpg": b"1" * 100, "Takeout/a/2.jpg": b"2" * 100})
    assert run(cfg, zips, capsys)[0] == 0
    part = mdir(cfg) / "parts" / f"takeout-{TS}-001.zip.jsonl"
    lines = part.read_text().splitlines()
    gone = json.loads(lines[-1])["path"]
    part.write_text(lines[0] + "\n")
    os.chmod(tree(cfg) / gone, 0o644)
    (tree(cfg) / gone).unlink()
    code, out, _ = run(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    assert "zip file entries 2 != manifest rows 1" in out


# --- verify-only mode (--extracted, design D7): the tree was extracted on the NAS by 7-Zip ------------------


def nas_extract(zips: Path, cfg, nfd: bool = False) -> None:
    """Simulate `7z x -y -aos`: extract every zip of the export into the tree, first writer wins."""
    t = tree(cfg)
    t.mkdir(parents=True, exist_ok=True)
    for zp in sorted(zips.glob(f"takeout-{TS}-*.zip")):
        with zipfile.ZipFile(zp) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = unicodedata.normalize("NFD", info.filename) if nfd else info.filename
                dest = t / name
                if dest.exists():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(info))


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
    t = pq.read_table(mdir(cfg) / "manifest.parquet")
    assert t.column_names == ["zip", "path", "size", "crc32", "blake3"]
    rows = t.to_pylist()
    assert len(rows) == 3
    row = next(r for r in rows if r["path"].endswith("IMG_SECRET2.jpg"))
    p = tree(cfg) / row["path"]
    assert row["blake3"] == ids.asset_id(p) and row["size"] == 1500 and row["zip"].endswith("-002.zip")
    assert not os.access(p, os.W_OK)
    assert "SECRET" not in out
    assert snapshot(zips) == before


@pytest.mark.parametrize("damage,field", [("delete", "missing"), ("resize", "size_mismatch"),
                                          ("flip", "crc_mismatch")])
def test_x_damaged_file_fails_its_zip(env, capsys, damage, field):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    p = tree(cfg) / "Takeout/Google Photos/b/IMG_SECRET2.jpg"
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
    assert rows[f"takeout-{TS}-001.zip"]["status"] == "verified"
    bad = rows[f"takeout-{TS}-002.zip"]
    assert bad["status"] == "failed" and bad[field] == 1
    assert sum(bad[k] for k in ("missing", "size_mismatch", "crc_mismatch")) == 1


def test_x_extra_file_is_reconcile_error(env, capsys):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    (tree(cfg) / "Takeout/stray_SECRET.jpg").write_bytes(b"x")
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out and "reconcile mismatch" in out and "SECRET" not in out


def test_x_synology_metadata_ignored(env, capsys):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    ea = tree(cfg) / "Takeout/Google Photos/a/@eaDir/IMG_SECRET1.jpg@SynoEAStream"
    ea.parent.mkdir()
    ea.write_bytes(b"meta")
    (tree(cfg) / "Takeout/.DS_Store").write_bytes(b"ds")
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert "2 ignored" in out


def test_x_nfd_name_on_disk_matches_nfc_entry(env, capsys):
    cfg, zips = env
    nfc = unicodedata.normalize("NFC", "Takeout/Café/crème.jpg")
    make_zip(zips, 1, {nfc: b"c" * 50})
    nas_extract(zips, cfg, nfd=True)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    assert len(pq.read_table(mdir(cfg) / "manifest.parquet")) == 1


def test_x_nfd_entry_name_matches_nfc_on_disk(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {unicodedata.normalize("NFD", "Takeout/Café.jpg"): b"c" * 50})
    t = tree(cfg) / "Takeout"
    t.mkdir(parents=True)
    (t / unicodedata.normalize("NFC", "Café.jpg")).write_bytes(b"c" * 50)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out


def test_x_case_and_nfc_collision_identical_is_one_row(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/IMG_É.JPG": b"same"})
    make_zip(zips, 2, {unicodedata.normalize("NFD", "Takeout/img_é.jpg"): b"same", "Takeout/x": b"x"})
    nas_extract(zips, cfg)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code == 0 and CLEANUP in out
    rows = pq.read_table(mdir(cfg) / "manifest.parquet").to_pylist()
    assert len(rows) == 2
    dup = next(r for r in rows if r["path"] != "Takeout/x")
    assert dup["zip"] == f"takeout-{TS}-001.zip,takeout-{TS}-002.zip"


def test_x_case_collision_different_is_error(env, capsys):
    cfg, zips = env
    make_zip(zips, 1, {"Takeout/IMG.JPG": b"1" * 10})
    make_zip(zips, 2, {"Takeout/img.jpg": b"2" * 20})
    nas_extract(zips, cfg)
    code, out, _ = run_x(cfg, zips, capsys)
    assert code != 0 and CLEANUP not in out
    rows = zip_rows(cfg)
    assert rows[f"takeout-{TS}-001.zip"]["status"] == "verified"
    assert rows[f"takeout-{TS}-002.zip"]["status"] == "failed"
    assert rows[f"takeout-{TS}-002.zip"]["conflicts"] == 1


def test_x_resume_skips_verified_zips(env, capsys, monkeypatch):
    cfg, zips = env
    two_zips(zips)
    nas_extract(zips, cfg)
    p = tree(cfg) / "Takeout/Google Photos/b/IMG_SECRET2.jpg"
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
    assert len(pq.read_table(mdir(cfg) / "manifest.parquet")) == 3


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
