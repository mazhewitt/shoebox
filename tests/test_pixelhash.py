# ruff: noqa: F811 - the `env` fixture is imported from test_derive and requested by name
"""Pixel hashes from the derive decode (design D1, spec asset-dedup). All images are synthetic."""

import io
import shutil

import numpy as np
import pytest
import pyvips
from PIL import Image
from test_derive import PROFILE, _item, _write_items, env  # noqa: F401 - fixture

from shoebox import derive, store
from shoebox.cli import main

INFER_V1_SETTINGS_HASH = "99b6818993fdd293"  # the hash of the 5,045 derived pool5k images


def _pix(seed: int = 0, h: int = 48, w: int = 64) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


def _jpeg(pix: np.ndarray, quality: int = 90, exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    kw = {"exif": exif} if exif is not None else {}
    Image.fromarray(pix).save(buf, "JPEG", quality=quality, **kw)
    return buf.getvalue()


def _exif(make: str) -> bytes:
    e = Image.Exif()
    e[0x010F] = make  # Make
    e[0x0132] = "2004:05:06 07:08:09"  # DateTime
    return e.tobytes()


def _tiff(img: pyvips.Image, orientation: int | None = None) -> bytes:
    if orientation is not None:
        img = img.copy()
        img.set_type(pyvips.GValue.gint_type, "orientation", orientation)
    return img.tiffsave_buffer()


def _pixels(cfg) -> dict[str, dict]:
    return derive.pixelhash_index(cfg, derive.load_profile(PROFILE))


def _derive(cfg, src, names: list[str], **kw) -> tuple[derive.Counts, dict[str, dict]]:
    items = [_item(src, n) for n in names]
    counts = derive.derive_items(cfg, PROFILE, items, **kw)
    px = _pixels(cfg)
    return counts, {n: px.get(i["asset_id"]) for n, i in zip(names, items)}


def test_infer_v1_settings_hash_unchanged():
    assert derive.settings_hash(derive.load_profile(PROFILE)) == INFER_V1_SETTINGS_HASH


def test_same_pixels_different_exif_same_hash(env):
    cfg, src = env
    pix = _pix(1)
    (src / "a.jpg").write_bytes(_jpeg(pix, exif=_exif("Canon")))
    (src / "b.jpg").write_bytes(_jpeg(pix, exif=_exif("Nikon")))
    (src / "c.jpg").write_bytes(_jpeg(pix))  # EXIF stripped
    _, px = _derive(cfg, src, ["a.jpg", "b.jpg", "c.jpg"])
    ids_ = {_item(src, n)["asset_id"] for n in ("a.jpg", "b.jpg", "c.jpg")}
    assert len(ids_) == 3  # three different files ...
    assert len({r["pixel_hash"] for r in px.values()}) == 1  # ... one picture
    assert all(r["status"] == "ok" and r["pixel_from"] == "decoded" for r in px.values())
    assert px["a.jpg"]["model_id"] == derive.PIXELHASH_VERSION


def test_orientation_flag_variant_same_hash(env):
    cfg, src = env
    upright = pyvips.Image.new_from_array(_pix(2)).copy(interpretation="srgb")
    # Orientation 6 = "rotate 90 cw to display", so the stored pixels are turned 90 ccw.
    (src / "upright.tif").write_bytes(_tiff(upright))
    (src / "flagged.tif").write_bytes(_tiff(upright.rot270(), orientation=6))
    _, px = _derive(cfg, src, ["upright.tif", "flagged.tif"])
    assert px["upright.tif"]["pixel_hash"] == px["flagged.tif"]["pixel_hash"]
    assert (px["flagged.tif"]["width"], px["flagged.tif"]["height"]) == (64, 48)


def test_reencoded_copy_differs(env):
    cfg, src = env
    pix = _pix(3)
    (src / "q90.jpg").write_bytes(_jpeg(pix, 90))
    (src / "q70.jpg").write_bytes(_jpeg(pix, 70))
    _, px = _derive(cfg, src, ["q90.jpg", "q70.jpg"])
    assert px["q90.jpg"]["pixel_hash"] != px["q70.jpg"]["pixel_hash"]


def test_one_changed_pixel_differs():
    a = pyvips.Image.new_from_array(_pix(4)).copy(interpretation="srgb")
    arr = _pix(4)
    arr[0, 0, 0] ^= 1
    b = pyvips.Image.new_from_array(arr).copy(interpretation="srgb")
    assert derive.pixel_hash(a, "decoded")["pixel_hash"] != derive.pixel_hash(b, "decoded")["pixel_hash"]


def test_shape_is_part_of_the_hash():
    flat = np.zeros((4, 6, 3), dtype=np.uint8)
    a = pyvips.Image.new_from_array(flat)
    b = pyvips.Image.new_from_array(flat.reshape(6, 4, 3))  # same bytes, other shape
    assert derive.pixel_hash(a, "decoded")["pixel_hash"] != derive.pixel_hash(b, "decoded")["pixel_hash"]


@pytest.mark.parametrize("src_from,want", [("raw", "rendered"), ("composite", "rendered"),
                                           ("decoded", "decoded"), ("libheif", "decoded")])
def test_raw_and_psd_marked_rendered(src_from, want):
    img = pyvips.Image.new_from_array(_pix(5))
    assert derive.pixel_hash(img, src_from)["pixel_from"] == want


def test_psd_hash_is_rendered(env):
    from psd_tools import PSDImage

    cfg, src = env
    PSDImage.frompil(Image.fromarray(_pix(6))).save(src / "layers.psd")
    _, px = _derive(cfg, src, ["layers.psd"])
    assert px["layers.psd"]["status"] == "ok" and px["layers.psd"]["pixel_from"] == "rendered"


def test_hashing_does_not_change_derived_bytes():
    settings = derive.load_profile(PROFILE)
    data = _jpeg(_pix(7), exif=_exif("Canon"))
    a = derive.render(data, "jpg", settings, hash_pixels=True)
    b = derive.render(data, "jpg", settings, hash_pixels=False)
    assert a.jpeg == b.jpeg and a.pixel and b.pixel is None


def test_decode_error_writes_no_pixel_record(env):
    cfg, src = env
    (src / "bad.jpg").write_bytes(b"\xff\xd8\xff" + b"\0" * 50)
    counts, px = _derive(cfg, src, ["bad.jpg"])
    assert counts.error == 1 and px["bad.jpg"] is None


def test_backfill_hashes_derived_assets_without_reencoding(env):
    cfg, src = env
    for n in range(3):
        (src / f"{n}.jpg").write_bytes(_jpeg(_pix(10 + n)))
    names = [f"{n}.jpg" for n in range(3)]
    _, before = _derive(cfg, src, names)
    # Simulate the pool5k images: derived before pixel hashes existed.
    shutil.rmtree(cfg.results_dir / derive.PIXELHASH_KIND)
    index_before = derive.index_path(cfg, PROFILE).read_bytes()
    jpgs = {p: p.stat().st_mtime_ns for p in derive.profile_dir(cfg, PROFILE).rglob("*.jpg")}

    counts, after = _derive(cfg, src, names, pixelhash_only=True)
    assert (counts.backfilled, counts.ok, counts.skipped, counts.error) == (3, 3, 0, 0)
    assert {n: r["pixel_hash"] for n, r in after.items()} == {
        n: r["pixel_hash"] for n, r in before.items()
    }
    assert derive.index_path(cfg, PROFILE).read_bytes() == index_before  # nothing re-derived
    assert {p: p.stat().st_mtime_ns for p in jpgs} == jpgs

    # Re-run: a no-op.
    counts, _ = _derive(cfg, src, names, pixelhash_only=True)
    assert (counts.backfilled, counts.skipped) == (0, 3)
    model_id, phash = derive.pixelhash_key(derive.load_profile(PROFILE))
    recs = list(store.iter_records(cfg, model_id, phash, kind=derive.PIXELHASH_KIND))
    assert len(recs) == 3


def test_plain_derive_backfills_too_and_pixelhash_only_skips_underived(env):
    cfg, src = env
    (src / "old.jpg").write_bytes(_jpeg(_pix(20)))
    (src / "new.jpg").write_bytes(_jpeg(_pix(21)))
    _derive(cfg, src, ["old.jpg"])
    shutil.rmtree(cfg.results_dir / derive.PIXELHASH_KIND)

    counts, px = _derive(cfg, src, ["old.jpg", "new.jpg"], pixelhash_only=True)
    assert (counts.backfilled, counts.skipped) == (1, 1)
    assert px["new.jpg"] is None
    assert _item(src, "new.jpg")["asset_id"] not in derive.load_index(cfg, PROFILE)

    counts, px = _derive(cfg, src, ["old.jpg", "new.jpg"])  # a normal derive run
    assert (counts.ok, counts.skipped, counts.backfilled) == (1, 1, 0)
    assert px["new.jpg"]["status"] == "ok"


def test_backfill_of_changed_original_is_an_error_record(env):
    cfg, src = env
    (src / "x.jpg").write_bytes(_jpeg(_pix(30)))
    item = _item(src, "x.jpg")
    derive.derive_items(cfg, PROFILE, [item])
    shutil.rmtree(cfg.results_dir / derive.PIXELHASH_KIND)
    (src / "x.jpg").write_bytes(_jpeg(_pix(31)))  # file changed under the same item
    counts = derive.derive_items(cfg, PROFILE, [item], pixelhash_only=True)
    assert (counts.backfilled, counts.error) == (1, 1)
    rec = _pixels(cfg)[item["asset_id"]]
    assert rec["status"] == "error" and "blake3" in rec["error"]


def test_cli_pixelhash_only(env, tmp_path, capsys):
    cfg, src = env
    (src / "a.jpg").write_bytes(_jpeg(_pix(40)))
    items = _write_items(tmp_path, [_item(src, "a.jpg")])
    assert main(["derive", "--items", str(items)]) == 0
    shutil.rmtree(cfg.results_dir / derive.PIXELHASH_KIND)
    assert main(["derive", "--items", str(items), "--pixelhash-only"]) == 0
    assert "pixel hashes backfilled 1" in capsys.readouterr().out
    assert len(_pixels(cfg)) == 1


def test_hash_is_of_full_resolution_pixels_not_the_resized_image(env):
    cfg, src = env
    big = _pix(50, h=40, w=2100)  # long edge above infer-v1's 2048, so the derive resizes
    img = pyvips.Image.new_from_array(big).copy(interpretation="srgb")
    (src / "wide.tif").write_bytes(_tiff(img))
    _, px = _derive(cfg, src, ["wide.tif"])
    rec = px["wide.tif"]
    idx = derive.load_index(cfg, PROFILE)[_item(src, "wide.tif")["asset_id"]]
    assert idx["width"] == 2048  # the derived image was resized ...
    assert (rec["width"], rec["height"]) == (2100, 40)  # ... the pixel hash was not
    assert rec["pixel_hash"] == derive.pixel_hash(img, "decoded")["pixel_hash"]
    resized = img.resize(2048 / 2100, kernel="lanczos3")
    assert rec["pixel_hash"] != derive.pixel_hash(resized, "decoded")["pixel_hash"]
