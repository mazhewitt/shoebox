"""`shoebox inventory`: the sampling frame, on synthetic fixtures in a tmp source."""

import io
import json
import os
import random
import shutil
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
import pyvips
from PIL import Image

from shoebox import config, ids, inventory, inventory_readme
from shoebox.cli import main
from shoebox.config import Config, Source
from shoebox.pilot.untouched import SourceWritable

TAKEN_TS = 1562243696  # 2019-07-04T12:34:56Z


def _jpeg(path: Path, colour=(200, 40, 40), dto: str | None = None, make: str | None = None) -> None:
    im = Image.new("RGB", (64, 48), colour)
    ex = Image.Exif()
    if make:
        ex[271], ex[272] = make, "Model X"
    if dto:
        ex.get_ifd(0x8769)[36867] = dto
    im.save(path, exif=ex.tobytes())


def _fake_raw_bytes(tag: bytes) -> bytes:
    return b"FAKE-RAW " + tag


class _FakeRaw:
    """Stands in for LibRaw: no synthetic NEF/DNG is possible (as in tests/test_derive.py)."""

    def __init__(self, data: bytes):
        self.data = data
        self.white_level = 16383
        self.sizes = type("S", (), {"width": 6000, "height": 4000})()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_thumb(self):
        import rawpy

        buf = io.BytesIO()
        Image.new("RGB", (160, 120), (20, 60, 220)).save(buf, "JPEG")
        return rawpy.Thumbnail(format=rawpy.ThumbFormat.JPEG, data=buf.getvalue())


@pytest.fixture
def fake_rawpy(monkeypatch):
    import rawpy

    seen = []

    def imread(f):
        data = f.read()
        if not data.startswith(b"FAKE-RAW"):
            raise rawpy.LibRawFileUnsupportedError("not a raw")
        seen.append(data)
        return _FakeRaw(data)

    monkeypatch.setattr(rawpy, "imread", imread)
    return seen


def build_tree(src: Path) -> None:
    """One of each fixture kind, plus skip litter."""
    (src / "Pictures" / "2004 Trip").mkdir(parents=True)
    trip = src / "Pictures" / "2004 Trip"
    _jpeg(trip / "exif.jpg", dto="2004:05:06 07:08:09", make="Canon")
    import pillow_heif

    pillow_heif.register_heif_opener()
    Image.new("RGB", (64, 48), (10, 200, 10)).save(trip / "phone.heic")
    grey = (np.linspace(0, 65535, 60 * 40).reshape(40, 60)).astype(np.uint16)
    (trip / "grey16.tif").write_bytes(pyvips.Image.new_from_array(grey).copy(interpretation="grey16")
                                      .tiffsave_buffer())
    Image.new("RGB", (50, 40), (180, 120, 30)).save(trip / "scan.tif", tiffinfo={
        271: "EPSON", 272: "Perfection V600", 305: "EPSON Scan", 306: "2010:01:02 03:04:05"})
    (trip / "DSC_0001.NEF").write_bytes(_fake_raw_bytes(b"nef"))
    (trip / "IMG_0002.dng").write_bytes(_fake_raw_bytes(b"dng"))
    _jpeg(trip / "IMG_0002.JPG", colour=(20, 60, 220))
    (trip / "IMG_0004.ARW").write_bytes(_fake_raw_bytes(b"arw"))
    _jpeg(trip / "IMG_0004.jpg", colour=(20, 60, 200))
    _jpeg(trip / "scan.jpg", colour=(180, 120, 31))
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer

    psd = PSDImage.new("RGB", (40, 30))
    psd.append(PixelLayer.frompil(Image.new("RGB", (40, 30), (200, 30, 30)), psd, "bg"))
    psd.append(PixelLayer.frompil(Image.new("RGB", (20, 10), (30, 30, 200)), psd, "top"))
    psd.save(trip / "layers.psd")
    (src / "Screens").mkdir()
    Image.new("RGB", (117, 253), (240, 240, 240)).save(src / "Screens" / "Screenshot 2021-03-04 at 10.11.12.png")
    (src / "Takeout").mkdir()
    Image.new("RGB", (32, 32), (90, 90, 90)).save(src / "Takeout" / "IMG_0003.jpg")
    (src / "Takeout" / "IMG_0003.jpg.supplemental-metadata.json").write_text(
        json.dumps({"title": "IMG_0003.jpg", "photoTakenTime": {"timestamp": str(TAKEN_TS)}}))
    (src / "Takeout" / "orphan.json").write_text("{}")
    (src / "Takeout" / "clip.mp4").write_bytes(os.urandom(4096))
    shutil.copy(trip / "exif.jpg", src / "Takeout" / "copy.jpg")
    # never opened: unreadable, so any open would error
    (src / "Docs").mkdir()
    (src / "Docs" / "secret.pdf").write_bytes(b"%PDF-1.4 secret")
    os.chmod(src / "Docs" / "secret.pdf", 0)
    # skipped
    (src / "#recycle").mkdir()
    _jpeg(src / "#recycle" / "gone.jpg")
    (src / "Lightroom" / "Cat Previews.lrdata").mkdir(parents=True)
    _jpeg(src / "Lightroom" / "Cat Previews.lrdata" / "p.jpg")
    (src / ".DS_Store").write_bytes(b"x")
    (trip / "._exif.jpg").write_bytes(b"x")
    (trip / "Thumbs.db").write_bytes(b"x")


@pytest.fixture
def env(tmp_path, fake_rawpy):
    src = tmp_path / "src"
    src.mkdir()
    build_tree(src)
    cfg = Config(shoebox_root=tmp_path / "sb",
                 sources={"test": Source("test", src, "owner", synced=False, hosted_consent=False)})
    yield cfg, src, tmp_path / "sb" / "frame"
    os.chmod(src / "Docs" / "secret.pdf", 0o644)


def _run(cfg, out, **kw) -> dict:
    opts = inventory.Options(out_dir=out, date="2026-10-10", workers=3, **kw)
    return inventory.run(cfg, opts, log=lambda m: None)


def _rows(out: Path) -> dict[str, dict]:
    return {r["relpath"]: r for r in pq.read_table(out / "frame-2026-10-10.parquet").to_pylist()}


def test_frame_rows_and_schema(env):
    cfg, src, out = env
    summary = _run(cfg, out)
    assert summary["untouched_ok"] is True
    table = pq.read_table(out / "frame-2026-10-10.parquet")
    assert table.column_names == inventory.COLUMNS
    rows = _rows(out)
    trip = "Pictures/2004 Trip"
    expected = {f"{trip}/{n}" for n in ("exif.jpg", "phone.heic", "grey16.tif", "scan.tif", "DSC_0001.NEF",
                                        "IMG_0002.dng", "IMG_0002.JPG", "layers.psd", "IMG_0004.ARW",
                                        "IMG_0004.jpg", "scan.jpg")} | {
        "Screens/Screenshot 2021-03-04 at 10.11.12.png", "Takeout/IMG_0003.jpg",
        "Takeout/IMG_0003.jpg.supplemental-metadata.json", "Takeout/orphan.json", "Takeout/clip.mp4",
        "Takeout/copy.jpg", "Docs/secret.pdf"}
    assert set(rows) == expected
    for rel, r in rows.items():
        if r["kind"] in ("image", "video"):
            assert r["status"] == "ok", (rel, r["error"])
            assert r["asset_id"] == ids.asset_id(src / rel)
        else:
            assert r["asset_id"] is None
        assert r["source"] == "test" and r["person"] == "owner" and r["synced"] is False

    j = rows[f"{trip}/exif.jpg"]
    assert (j["src_kind"], j["width"], j["height"], j["bits"]) == ("jpeg", 64, 48, 8)
    assert (j["date"], j["date_kind"], j["make"]) == ("2004-05-06T07:08:09", "exif", "Canon")
    assert json.loads(j["exif"])["ExifIFD:DateTimeOriginal"] == "2004:05:06 07:08:09"
    assert j["colour_guess"] == "colour" and j["chroma_p95"] > 30 and j["chroma_mean"] > 30
    assert j["dup_n"] == 2 and rows["Takeout/copy.jpg"]["dup_n"] == 2

    h = rows[f"{trip}/phone.heic"]
    assert (h["src_kind"], h["width"], h["kind"]) == ("heif", 64, "image")
    g = rows[f"{trip}/grey16.tif"]
    assert (g["src_kind"], g["bits"], g["colour_guess"]) == ("tiff", 16, "mono")
    s = rows[f"{trip}/scan.tif"]
    assert (s["src_kind"], s["bits"], s["date"], s["date_kind"]) == ("tiff", 8, "2010-01-02T03:04:05", "scan")
    assert s["software"] == "EPSON Scan"
    n = rows[f"{trip}/DSC_0001.NEF"]
    assert (n["src_kind"], n["width"], n["height"], n["bits"]) == ("raw", 6000, 4000, 14)
    assert n["colour_guess"] == "colour"
    p = rows[f"{trip}/layers.psd"]
    assert (p["src_kind"], p["width"], p["height"], p["status"]) == ("psd", 40, 30, "ok")
    png = rows["Screens/Screenshot 2021-03-04 at 10.11.12.png"]
    assert (png["src_kind"], png["date_kind"], png["date"]) == ("png", "filename", "2021-03-04T00:00:00")
    t = rows["Takeout/IMG_0003.jpg"]
    assert (t["date"], t["date_kind"]) == ("2019-07-04T12:34:56+00:00", "sidecar")
    assert rows["Takeout/IMG_0003.jpg.supplemental-metadata.json"]["kind"] == "sidecar"
    assert rows["Takeout/orphan.json"]["kind"] == "other"
    v = rows["Takeout/clip.mp4"]
    assert (v["kind"], v["src_kind"], v["width"]) == ("video", "video", None)
    pdf = rows["Docs/secret.pdf"]
    assert (pdf["kind"], pdf["status"], pdf["error"], pdf["bytes"]) == ("other", "stat_only", None, 15)

    # DNG+JPG pair is a capture group; the lone NEF is not
    dng, jpg = rows[f"{trip}/IMG_0002.dng"], rows[f"{trip}/IMG_0002.JPG"]
    assert dng["group_id"] == jpg["group_id"] == "g-" + min(dng["asset_id"], jpg["asset_id"])[:12]
    assert dng["group_rule"] == "raw_jpeg_basename"
    assert n["group_id"] is None
    arw, arw_jpg = rows[f"{trip}/IMG_0004.ARW"], rows[f"{trip}/IMG_0004.jpg"]
    assert arw["group_id"] == arw_jpg["group_id"] is not None and arw["group_rule"] == "raw_jpeg_basename"
    assert s["group_id"] == rows[f"{trip}/scan.jpg"]["group_id"] is not None
    assert s["group_rule"] == "scan_export_basename"


def test_skips_counted_in_listing_log(env):
    cfg, _src, out = env
    _run(cfg, out)
    log = json.loads((out / "listing-2026-10-10.json").read_text())
    s = log["sources"]["test"]
    assert s["skips"] == {"#recycle": 1, "*.lrdata": 1, ".DS_Store": 1, "._*": 1, "Thumbs.db": 1}
    assert s["skip_examples"]["*.lrdata"] == ["Lightroom/Cat Previews.lrdata"]
    assert all(d["listings"] >= 2 for d in s["dirs"])
    assert log["untouched"]["ok"] is True and log["untouched"]["sample_files"] > 0


def test_writable_synced_root_refused(env):
    cfg, src, out = env
    synced = Config(cfg.shoebox_root, {"test": Source("test", src, "owner", synced=True, hosted_consent=False)})
    with pytest.raises(SourceWritable):
        _run(synced, out)
    assert not out.exists()


def test_output_under_source_root_refused(env):
    cfg, src, _out = env
    with pytest.raises(ValueError, match="source root"):
        _run(cfg, src / "frame")


def test_existing_checkpoint_needs_resume(env):
    cfg, _src, out = env
    _run(cfg, out)
    with pytest.raises(FileExistsError):
        _run(cfg, out)


def test_resume_skips_recorded_files_and_redoes_a_torn_line(env, monkeypatch):
    cfg, _src, out = env
    _run(cfg, out)
    ckpt = out / "work-2026-10-10" / "records.jsonl"
    lines = ckpt.read_text().splitlines()
    torn = json.loads(lines[-1])["relpath"]
    ckpt.write_text("\n".join(lines[:-1]) + "\n" + lines[-1][: len(lines[-1]) // 2])
    probed = []
    real = inventory.probe

    def counting(cfg_, f, *a):
        probed.append(f.relpath)
        return real(cfg_, f, *a)

    monkeypatch.setattr(inventory, "probe", counting)
    _run(cfg, out, resume=True)
    assert probed == [torn]
    assert len(_rows(out)) == len(lines)


class _Flaky:
    """A lister that drops a different random part of large listings on its first few calls."""

    def __init__(self, flaky_calls: int = 2, seed: int = 1):
        self.rng = random.Random(seed)
        self.calls: dict[Path, int] = {}
        self.flaky_calls = flaky_calls

    def __call__(self, path: Path):
        entries = inventory.scandir_list(path)
        n = self.calls[path] = self.calls.get(path, 0) + 1
        if n <= self.flaky_calls and len(entries) > 10:
            return self.rng.sample(entries, len(entries) * 2 // 3)
        return entries


def test_flaky_lister_union_is_complete(tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(600):
        (big / f"f{i:04d}.txt").write_bytes(b"")
    lst = inventory.stable_listing(big, _Flaky(flaky_calls=2))
    assert len(lst.entries) == 600 and lst.differed
    assert len(lst.counts) >= 4  # two flaky, then two in a row that add nothing (>= 500 entries)
    res = inventory.walk_source("t", tmp_path, lister=_Flaky(flaky_calls=3, seed=2))
    assert len(res.files) == 600
    assert res.dir_log[1]["differed"] and res.dir_log[1]["count"] == 600


def test_union_completes_when_every_listing_is_partial(tmp_path):
    for i in range(30):
        (tmp_path / f"f{i:02d}").write_bytes(b"")
    calls = [0]

    def lister(p):  # each listing misses a different third
        calls[0] += 1
        return [e for i, e in enumerate(sorted(inventory.scandir_list(p), key=lambda e: e.name))
                if i % 3 != calls[0] % 3]

    lst = inventory.stable_listing(tmp_path, lister)
    assert len(lst.entries) == 30 and lst.differed and max(lst.counts) == 20
    assert len(inventory.walk_source("t", tmp_path, lister=lister).files) == 30


def test_stable_listing_minimums(tmp_path):
    (tmp_path / "a").write_bytes(b"")
    calls = []

    def lister(p):
        calls.append(p)
        return inventory.scandir_list(p)

    inventory.stable_listing(tmp_path, lister)
    assert len(calls) == 2
    many = [type("E", (), {"name": f"n{i}"})() for i in range(500)]
    seen = []
    inventory.stable_listing(tmp_path, lambda p: seen.append(1) or many)
    assert len(seen) == 3


def test_untouched_check_detects_planted_change(env):
    cfg, src, out = env

    def plant():
        (src / "Takeout" / "new.tmp").write_bytes(b"created during the run")
        with open(src / "Pictures" / "2004 Trip" / "exif.jpg", "ab") as f:
            f.write(b"!")

    with pytest.raises(inventory.SourcesTouched) as e:
        _run(cfg, out, sample_n=1000, after_run=plant)
    msg = str(e.value)
    assert "new.tmp" in msg and "exif.jpg" in msg
    log = json.loads((out / "listing-2026-10-10.json").read_text())
    assert log["untouched"]["ok"] is False


@pytest.mark.parametrize(("exif", "ts", "name", "want"), [
    ({"ExifIFD:DateTimeOriginal": "2004:05:06 07:08:09", "IFD0:ModifyDate": "2020:01:01 00:00:00"}, TAKEN_TS,
     "20190101_000000.jpg", ("2004-05-06T07:08:09", "exif")),
    ({"QuickTime:CreateDate": "2015:06:07 08:09:10"}, None, "x.mov", ("2015-06-07T08:09:10", "exif")),
    ({"ExifIFD:DateTimeOriginal": "0000:00:00 00:00:00"}, TAKEN_TS, "x.jpg", ("2019-07-04T12:34:56+00:00", "sidecar")),
    (None, None, "IMG-20190704-WA0001.jpg", ("2019-07-04T00:00:00", "filename")),
    (None, None, "20190704_123456.jpg", ("2019-07-04T12:34:56", "filename")),
    (None, None, "party 2019-07-04.jpg", ("2019-07-04T00:00:00", "filename")),
    (None, None, "DSC_0001.jpg", ("1970-01-02T00:00:00+00:00", "mtime")),
    ({"IFD0:Make": "Nikon", "IFD0:Model": "LS-5000", "IFD0:ModifyDate": "2011:02:03 04:05:06"}, None,
     "scan.tif", ("2011-02-03T04:05:06", "scan")),
    ({"IFD0:Software": "VueScan 9", "ExifIFD:DateTimeOriginal": "2012:01:01 00:00:00"}, None, "a.jpg",
     ("2012-01-01T00:00:00", "scan")),
    ({"IFD0:Software": "VueScan 9", "ExifIFD:DateTimeOriginal": "2012:01:01 00:00:00", "ExifIFD:FNumber": 2.8},
     None, "a.jpg", ("2012-01-01T00:00:00", "exif")),
])
def test_best_date_kinds(exif, ts, name, want):
    assert inventory.best_date(exif, ts, f"F/{name}", name.rsplit(".", 1)[1], 86400.0) == want


def test_classify_kind_sidecars():
    media = {"img.jpg", "dsc.nef"}
    assert inventory.classify_kind("IMG.jpg.json", media) == ("sidecar", "img.jpg")
    assert inventory.classify_kind("IMG.jpg.supplemental-metad.json", media) == ("sidecar", "img.jpg")
    assert inventory.classify_kind("metadata.json", media) == ("other", None)
    assert inventory.classify_kind("DSC.xmp", media) == ("sidecar", None)
    assert inventory.classify_kind("DSC.NEF.xmp", media) == ("sidecar", None)
    assert inventory.classify_kind("a.MOV", media) == ("video", None)
    assert inventory.classify_kind("a.pdf", media) == ("other", None)


def test_readme_summarises_without_file_names(env, tmp_path):
    cfg, _src, out = env
    _run(cfg, out)
    text = inventory_readme.render(out / "frame-2026-10-10.parquet")
    assert "Pictures/2004 Trip" in text and "raw_jpeg_basename" in text
    assert "Sources-untouched check: passed" in text
    assert "1 groups of identical bytes" in text
    for name in ("exif.jpg", "IMG_0002", "secret.pdf", "Screenshot"):
        assert name not in text


def test_cli_inventory_and_readme(env, tmp_path, monkeypatch, capsys):
    cfg, src, out = env
    toml = tmp_path / "shoebox.toml"
    toml.write_text(f'shoebox_root = "{cfg.shoebox_root}"\n[sources.test]\nroot = "{src}"\nperson = "owner"\n'
                    "synced = false\nhosted_consent = false\n")
    monkeypatch.setattr(config, "CONFIG_PATH", toml)
    assert main(["inventory", "--only", "Takeout", "--date", "2026-10-10", "--workers", "2"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["files"] == 5 and summary["untouched_ok"]
    readme = tmp_path / "README.md"
    assert main(["inventory", "readme", "--frame", str(out / "frame-2026-10-10.parquet"), "--out", str(readme)]) == 0
    assert "Takeout" in readme.read_text()


def test_exiftool_reads_local_copies_and_cleans_them(env, tmp_path, monkeypatch):
    """ExifTool runs on local copies made during the single read, which are deleted afterwards."""
    cfg, src, out = env
    trip = src / "Pictures" / "2004 Trip"
    scratch = tmp_path / "exif-scratch"
    seen = []
    real = inventory.exiftool_batch

    def spy(paths, exe="exiftool"):
        seen.extend(paths)
        return real(paths, exe)

    monkeypatch.setattr(inventory, "exiftool_batch", spy)
    _run(cfg, out, tmp_dir=scratch)
    assert seen and all(Path(p).parent == scratch for p in seen)
    assert list(scratch.iterdir()) == []
    row = _rows(out)[f"{trip.relative_to(src)}/exif.jpg"]
    exif = json.loads(row["exif"])
    assert exif["ExifIFD:DateTimeOriginal"] == "2004:05:06 07:08:09"
    assert not any(k.startswith("System:") for k in exif)
    assert row["asset_id"] == ids.asset_id(trip / "exif.jpg")


def test_local_copies_leave_no_open_descriptors(env, tmp_path):
    """A deleted local copy whose descriptor stays open keeps its disk space until the process exits: the
    2026-10-15 Takeout run filled the disk that way (ENOSPC on 13,959 files)."""
    cfg, _src, out = env
    fds = lambda: len(os.listdir("/dev/fd"))
    before = fds()
    _run(cfg, out, tmp_dir=tmp_path / "scratch")
    assert fds() == before


def test_jpeg_named_nef_probed_as_jpeg_and_retry_errors(env, monkeypatch):
    """A JPEG with a .NEF name decodes as JPEG; --retry-errors re-probes only error records."""
    cfg, src, out = env
    d = src / "Pictures" / "Google Photos" / "2005"
    d.mkdir(parents=True)
    _jpeg(d / "tmp.jpg")
    (d / "tmp.jpg").rename(d / "DSC_7123.NEF")
    real = inventory.decode_thumb
    monkeypatch.setattr(inventory, "decode_thumb", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    _run(cfg, out)
    rel = f"{d.relative_to(src)}/DSC_7123.NEF"
    first = _rows(out)
    errors_before = {p for p, r in first.items() if r["status"] == "error"}
    assert rel in errors_before
    monkeypatch.setattr(inventory, "decode_thumb", real)
    probed = []
    real_probe = inventory.probe
    monkeypatch.setattr(inventory, "probe", lambda cfg_, f, *a, **k: probed.append(f.relpath) or real_probe(cfg_, f, *a, **k))
    _run(cfg, out, resume=True, retry_errors=True)
    row = _rows(out)[rel]
    assert (row["status"], row["src_kind"]) == ("ok", "jpeg")
    assert set(probed) == errors_before  # only the error records were probed again


# --- derived-cache skip rules (add-pipeline-foundations 2.1) ---------------------------------------


def _touch(root: Path, rel: str, data: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.mark.parametrize("cache, reason", [
    ("DCIM/.thumbnails", ".thumbnails"),
    ("Pictures/iPod Photo Cache", "iPod Photo Cache"),
    ("Pictures/IPOD PHOTO CACHE", "iPod Photo Cache"),
    ("Lightroom/Cat Previews.lrdata", "*.lrdata"),
    ("Lightroom/Cat Smart Previews.lrdata", "*.lrdata"),
    ("photos/@SynoEAStream", "@SynoEAStream"),
    ("photos/@SynoResource", "@SynoResource"),
])
def test_derived_cache_folder_skipped_with_named_reason(tmp_path, cache, reason):
    _touch(tmp_path, f"{cache}/1234.jpg")
    _touch(tmp_path, f"{cache}/sub/5678.jpg")
    _touch(tmp_path, "keep/IMG_0001.jpg")
    w = inventory.walk_source("t", tmp_path)
    assert [f.relpath for f in w.files] == ["keep/IMG_0001.jpg"]
    assert w.skips == {reason: 1}
    assert w.skip_examples[reason] == [cache]


def test_owner_data_beside_caches_is_kept(tmp_path):
    _touch(tmp_path, "Lightroom/Cat.lrcat")
    _touch(tmp_path, "Lightroom/Cat Previews.lrdata/a/b.lrprev")
    _touch(tmp_path, "Trip/.picasa.ini")
    _touch(tmp_path, "Trip/Picasa.ini")
    _touch(tmp_path, "Trip/IMG_0001.jpg")
    _touch(tmp_path, "Trip/.thumbnails/IMG_0001.jpg")
    _touch(tmp_path, "Trip/thumbnails/IMG_0002.jpg")
    _touch(tmp_path, "Trip/my thumbnails/IMG_0003.jpg")
    _touch(tmp_path, "Trip/iPod Photos/IMG_0004.jpg")
    w = inventory.walk_source("t", tmp_path)
    assert [f.relpath for f in w.files] == [
        "Lightroom/Cat.lrcat", "Trip/.picasa.ini", "Trip/IMG_0001.jpg", "Trip/Picasa.ini",
        "Trip/iPod Photos/IMG_0004.jpg", "Trip/my thumbnails/IMG_0003.jpg", "Trip/thumbnails/IMG_0002.jpg"]
    assert w.skips == {"*.lrdata": 1, ".thumbnails": 1}


def test_readme_reports_cache_skip_counts(env):
    cfg, src, out = env
    _touch(src, "DCIM/.thumbnails/1.jpg")
    _touch(src, "Pictures/iPod Photo Cache/F00/1.jpg")
    _run(cfg, out)
    assert not [p for p in _rows(out) if ".thumbnails" in p or "iPod Photo Cache" in p]
    text = inventory_readme.render(out / "frame-2026-10-10.parquet")
    assert "| `.thumbnails` | 1 |" in text
    assert "| `iPod Photo Cache` | 1 |" in text


# --- quarantine `exclude` (add-takeout-landing 2.1c, design D9) ------------------------------------


def _verdicts(src: Path, by_rel: dict[str, str]) -> None:
    from shoebox import quarantine

    with quarantine.QUARANTINE_PATH.open("a") as f:
        for rel, verdict in by_rel.items():
            f.write(json.dumps({"asset_id": ids.asset_id(src / rel), "screen": None, "verdict": verdict}) + "\n")


def _spy_decode_and_exif(monkeypatch):
    decoded, exiffed = [], []
    real_decode, real_exif = inventory.decode_thumb, inventory.exiftool_batch

    def decode(data, ext, settings, path=None):
        decoded.append(ids.bytes_id(data) if data is not None else ids.asset_id(path))
        return real_decode(data, ext, settings, path)

    def exif(paths, exe="exiftool"):
        exiffed.extend(ids.asset_id(Path(p)) for p in paths)
        return real_exif(paths, exe)

    monkeypatch.setattr(inventory, "decode_thumb", decode)
    monkeypatch.setattr(inventory, "exiftool_batch", exif)
    return decoded, exiffed


@pytest.mark.parametrize("use_tmp", [True, False])
def test_excluded_asset_hashed_but_not_decoded_or_exiftooled(env, tmp_path, monkeypatch, use_tmp):
    cfg, src, out = env
    trip = "Pictures/2004 Trip"
    _verdicts(src, {f"{trip}/IMG_0002.JPG": "exclude", "Takeout/clip.mp4": "exclude",
                    f"{trip}/IMG_0004.jpg": "private", "Screens/Screenshot 2021-03-04 at 10.11.12.png": "include"})
    decoded, exiffed = _spy_decode_and_exif(monkeypatch)
    scratch = tmp_path / "scratch"
    _run(cfg, out, **({"tmp_dir": scratch} if use_tmp else {}))
    rows = _rows(out)
    excl_ids = {ids.asset_id(src / f"{trip}/IMG_0002.JPG"), ids.asset_id(src / "Takeout/clip.mp4")}
    for rel in (f"{trip}/IMG_0002.JPG", "Takeout/clip.mp4"):
        r = rows[rel]
        assert (r["status"], r["asset_id"], r["quarantine"]) == ("ok", ids.asset_id(src / rel), "exclude")
        assert (r["width"], r["chroma_mean"], r["colour_guess"], r["exif"], r["make"]) == (None,) * 5
    assert not excl_ids & set(decoded) and not excl_ids & set(exiffed)
    for rel in (f"{trip}/IMG_0004.jpg", f"{trip}/exif.jpg", "Screens/Screenshot 2021-03-04 at 10.11.12.png"):
        r = rows[rel]
        assert r["quarantine"] is None and r["width"] is not None and r["exif"] is not None
        assert r["asset_id"] in decoded and r["asset_id"] in exiffed
    assert ids.asset_id(src / "Takeout/clip.mp4") not in exiffed
    if use_tmp:
        assert list(scratch.iterdir()) == []


def test_excluded_kept_on_resume(env, monkeypatch):
    cfg, src, out = env
    rel = "Pictures/2004 Trip/IMG_0002.JPG"
    _verdicts(src, {rel: "exclude"})
    _run(cfg, out)
    ckpt = out / "work-2026-10-10" / "records.jsonl"
    lines = [ln for ln in ckpt.read_text().splitlines() if json.loads(ln)["relpath"] != rel]
    ckpt.write_text("\n".join(lines) + "\n")  # the excluded file is probed again on resume
    decoded, exiffed = _spy_decode_and_exif(monkeypatch)
    _run(cfg, out, resume=True)
    r = _rows(out)[rel]
    assert (r["quarantine"], r["width"], r["exif"]) == ("exclude", None, None)
    assert decoded == [] and exiffed == []
    assert "quarantine" in pq.read_table(out / "frame-2026-10-10.parquet").column_names
