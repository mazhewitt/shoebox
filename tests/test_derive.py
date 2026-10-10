"""Tests for `shoebox derive` (profile infer-v1). All images are synthetic and made here."""

import io
import json
import struct
from pathlib import Path

import numpy as np
import pytest
import pyvips
from PIL import Image, ImageCms

from shoebox import config, derive, ids
from shoebox.cli import main

PROFILE = "infer-v1"
META_FIELDS = ("exif-data", "xmp-data", "icc-profile-data", "iptc-data", "jpeg-comment")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp shoebox_root and source root, plus a config that points at them."""
    src = tmp_path / "src"
    src.mkdir()
    root = tmp_path / "shoebox"
    toml = tmp_path / "shoebox.toml"
    toml.write_text(
        f'shoebox_root = "{root}"\n[sources.test]\nroot = "{src}"\nperson = "owner"\n'
        "synced = false\nhosted_consent = false\n"
    )
    monkeypatch.setattr(config, "CONFIG_PATH", toml)
    return config.load(toml), src


def _item(src: Path, name: str) -> dict:
    p = src / name
    return {
        "asset_id": ids.asset_id(p),
        "source": "test",
        "relpath": name,
        "ext": p.suffix.lstrip(".").lower(),
        "bytes": p.stat().st_size,
    }


def _write_items(tmp: Path, items: list[dict]) -> Path:
    f = tmp / "items.jsonl"
    f.write_text("".join(json.dumps(i) + "\n" for i in items))
    return f


def _vips(arr: np.ndarray, interpretation: str) -> pyvips.Image:
    return pyvips.Image.new_from_array(arr).copy(interpretation=interpretation)


def _out(cfg, rec: dict) -> tuple[pyvips.Image, np.ndarray, bytes]:
    data = (cfg.shoebox_root / rec["path"]).read_bytes()
    img = pyvips.Image.new_from_buffer(data, "")
    arr = np.ndarray(
        buffer=img.write_to_memory(),
        dtype=np.uint8,
        shape=(img.height, img.width, img.bands),
    )
    return img, arr, data


def _derive_one(cfg, src, name, **kw) -> dict:
    item = _item(src, name)
    counts = derive.derive_items(cfg, PROFILE, [item], **kw)
    assert counts.total == 1
    return derive.load_index(cfg, PROFILE)[item["asset_id"]]


def _s15(x: float) -> bytes:
    return struct.pack(">i", round(x * 65536))


def _grey_icc(gamma: float) -> bytes:
    """A minimal ICC v2 grey display profile with a pure-gamma kTRC (like scanners embed)."""
    d50 = _s15(0.9642) + _s15(1.0) + _s15(0.8249)
    desc = b"grey gamma"
    tags = [
        (
            b"desc",
            b"desc\0\0\0\0"
            + struct.pack(">I", len(desc) + 1)
            + desc
            + b"\0"
            + b"\0" * 78,
        ),
        (b"cprt", b"text\0\0\0\0none\0"),
        (b"wtpt", b"XYZ \0\0\0\0" + d50),
        (
            b"kTRC",
            b"curv\0\0\0\0" + struct.pack(">IH", 1, round(gamma * 256)) + b"\0\0",
        ),
    ]
    off = 128 + 4 + 12 * len(tags)
    table, body = struct.pack(">I", len(tags)), b""
    for sig, data in tags:
        data += b"\0" * (-len(data) % 4)
        table += sig + struct.pack(">II", off + len(body), len(data))
        body += data
    header = (
        struct.pack(">I", off + len(body))
        + b"none\x02\x10\x00\x00mntrGRAYXYZ "
        + b"\0" * 12
        + b"acspAPPL"
        + b"\0" * 24
        + d50
        + b"none"
        + b"\0" * 44
    )
    assert len(header) == 128
    return header + table + body


def _p3(rgb8: np.ndarray) -> pyvips.Image:
    """sRGB pixels re-encoded as Display P3, tagged with libvips' built-in P3 profile."""
    return _vips(rgb8, "srgb").icc_export(output_profile="p3", depth=8)


def _p3_to_srgb_by_pil(p3: pyvips.Image) -> np.ndarray:
    """The expected sRGB pixels, from an independent CMM path (PIL ImageCms)."""
    icc = ImageCms.ImageCmsProfile(io.BytesIO(p3.get("icc-profile-data")))
    out = ImageCms.profileToProfile(
        Image.fromarray(p3.numpy()),
        icc,
        ImageCms.createProfile("sRGB"),
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
    )
    return np.asarray(out).astype(int)


def _assert_clean_srgb8(img: pyvips.Image, data: bytes) -> None:
    assert img.get("vips-loader") == "jpegload_buffer"
    assert img.bands == 3 and img.format == "uchar" and img.interpretation == "srgb"
    for field in META_FIELDS:
        assert img.get_typeof(field) == 0, field
    assert (
        b"Exif" not in data
        and b"ICC_PROFILE" not in data
        and b"http://ns.adobe.com/xap" not in data
    )


# --- paths and index ---------------------------------------------------------


def test_derived_path_layout(env):
    cfg, _ = env
    aid = "ab" + "0" * 62
    assert (
        derive.derived_path(cfg, PROFILE, aid)
        == cfg.shoebox_root / "derived" / PROFILE / "ab" / f"{aid}.jpg"
    )
    assert (
        derive.index_path(cfg, PROFILE)
        == cfg.shoebox_root / "derived" / PROFILE / "index.jsonl"
    )


def test_load_index_latest_wins(env):
    cfg, _ = env
    assert derive.load_index(cfg, PROFILE) == {}
    idx = derive.index_path(cfg, PROFILE)
    idx.parent.mkdir(parents=True)
    idx.write_text(
        json.dumps({"asset_id": "a", "status": "error"})
        + "\n"
        + json.dumps({"asset_id": "b", "status": "ok"})
        + "\n"
        + json.dumps({"asset_id": "a", "status": "ok"})
        + "\n"
    )
    got = derive.load_index(cfg, PROFILE)
    assert got["a"]["status"] == "ok" and set(got) == {"a", "b"}


def test_profile_settings_loaded():
    s = derive.load_profile(PROFILE)
    assert s["long_edge"] == 2048 and s["jpeg_quality"] == 92 and s["upscale"] is False
    assert s["raw"]["use_camera_wb"] is True and s["raw"]["output_color"] == "sRGB"
    with pytest.raises(KeyError):
        derive.load_profile("no-such-profile")


# --- formats ----------------------------------------------------------------


def test_grey16_tiff_becomes_8bit_srgb_without_clipping(env):
    cfg, src = env
    ramp = np.tile(np.linspace(0, 65535, 640), (480, 1)).round().astype(np.uint16)
    _vips(ramp, "grey16").tiffsave(str(src / "scan.tif"))
    rec = _derive_one(cfg, src, "scan.tif")
    assert rec["status"] == "ok" and rec["error"] is None
    assert (rec["src_kind"], rec["src_bits"], rec["rendered_from"]) == (
        "tiff",
        16,
        "decoded",
    )
    assert (rec["width"], rec["height"], rec["src_width"], rec["src_height"]) == (
        640,
        480,
        640,
        480,
    )
    img, arr, data = _out(cfg, rec)
    _assert_clean_srgb8(img, data)
    assert (
        np.abs(arr[..., 0].astype(int) - arr[..., 2].astype(int)).max() <= 2
    )  # still neutral grey
    expected = ramp.astype(float) / 257.0
    assert abs(arr[..., 1].mean() - expected.mean()) < 1.5
    assert np.abs(arr[:, 8:-8, 1].astype(float) - expected[:, 8:-8]).max() < 4
    assert arr.min() <= 2 and arr.max() >= 253
    tone = rec["tone"]
    assert tone["within_tolerance"] is True and tone["max_clip_increase_pct"] == 1.0
    assert tone["out_black_pct"] - tone["src_black_pct"] < 0.5


def test_grey16_with_grey_icc_is_colour_managed(env):
    cfg, src = env
    flat = np.full((40, 60), 32768, np.uint16)  # 0.5 in a gamma-1.0 (linear) grey space
    img = _vips(flat, "grey16")
    img.set_type(pyvips.GValue.blob_type, "icc-profile-data", _grey_icc(1.0))
    img.tiffsave(str(src / "lin.tif"))
    rec = _derive_one(cfg, src, "lin.tif")
    assert rec["status"] == "ok", rec["error"]
    img, arr, data = _out(cfg, rec)
    _assert_clean_srgb8(img, data)
    # linear 0.5 is sRGB ~188; read as untagged sRGB it would stay at 128
    assert abs(arr.mean() - 188) < 2


def test_grey_icc_shadow_clipping_is_flagged(env, capsys):
    """A dark gamma-2.2 grey scan loses its deepest shadows in sRGB: recorded and reported."""
    cfg, src = env
    # a dense negative: all tones in the bottom eighth, like the real archive scans
    ramp = np.tile(np.linspace(0, 8191, 2000), (50, 1)).round().astype(np.uint16)
    img = _vips(ramp, "grey16")
    img.set_type(pyvips.GValue.blob_type, "icc-profile-data", _grey_icc(2.2))
    img.tiffsave(str(src / "g22.tif"))
    item = _item(src, "g22.tif")
    counts = derive.derive_items(cfg, PROFILE, [item], log=print)
    assert (counts.ok, counts.flagged) == (1, 1)
    rec = derive.load_index(cfg, PROFILE)[item["asset_id"]]
    tone = rec["tone"]
    assert rec["status"] == "ok" and tone["within_tolerance"] is False
    assert tone["src_black_pct"] < 2  # values below 0.5/255 of full scale
    assert tone["out_black_pct"] - tone["src_black_pct"] > 3  # up to ~1/100 of it -> 0
    assert tone["src_white_pct"] == tone["out_white_pct"] == 0
    assert "CLIP black" in capsys.readouterr().out


def test_exif_orientation_applied(env):
    cfg, src = env
    rgb = np.zeros((200, 300, 3), np.uint8)
    rgb[:50, :50] = (255, 0, 0)  # red block top-left of the stored pixels
    img = _vips(rgb, "srgb")
    img.set_type(
        pyvips.GValue.gint_type, "orientation", 6
    )  # display = stored rotated 90 deg clockwise
    img.tiffsave(str(src / "rot.tif"))
    rec = _derive_one(cfg, src, "rot.tif")
    assert (rec["width"], rec["height"]) == (200, 300)
    assert (rec["src_width"], rec["src_height"]) == (200, 300)
    _, arr, _ = _out(cfg, rec)
    assert arr[10, -10, 0] > 200 and arr[10, -10, 1] < 60  # red now top-right
    assert arr[10, 10, 0] < 60


def test_png_with_alpha_flattened_on_white(env):
    cfg, src = env
    rgba = np.zeros((100, 120, 4), np.uint8)
    rgba[..., 2] = 255
    rgba[:, 60:, 3] = 255  # left half transparent, right half opaque blue
    Image.fromarray(rgba, "RGBA").save(src / "a.png")
    rec = _derive_one(cfg, src, "a.png")
    assert rec["src_kind"] == "png" and rec["src_bits"] == 8
    img, arr, data = _out(cfg, rec)
    _assert_clean_srgb8(img, data)
    assert arr[50, 10].min() > 245  # white
    assert arr[50, 110, 2] > 240 and arr[50, 110, 0] < 15  # blue


def test_jpeg_metadata_stripped(env):
    cfg, src = env
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    exif[0x0112] = 1
    exif.get_ifd(0x8825).update(
        {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}
    )  # GPS
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>'
    pix = np.random.default_rng(1).integers(0, 255, (300, 400, 3), dtype=np.uint8)
    Image.fromarray(pix).save(
        src / "p.jpg", quality=90, exif=exif.tobytes(), icc_profile=icc, xmp=xmp
    )
    raw = (src / "p.jpg").read_bytes()
    assert (
        b"SecretCam" in raw and b"ICC_PROFILE" in raw and b"xmpmeta" in raw
    )  # fixture really has them
    rec = _derive_one(cfg, src, "p.jpg")
    assert rec["status"] == "ok" and rec["src_kind"] == "jpeg"
    img, _, data = _out(cfg, rec)
    _assert_clean_srgb8(img, data)
    assert b"SecretCam" not in data and b"xmpmeta" not in data
    assert (
        data[2:4] == b"\xff\xdb"
    )  # no APP segments at all: straight to the quantisation table


def test_heif_avif_decoded(env):
    cfg, src = env
    rgb = np.full((64, 96, 3), (30, 120, 220), np.uint8)
    (src / "h.avif").write_bytes(
        _vips(rgb, "srgb").heifsave_buffer(compression="av1", lossless=True)
    )
    rec = _derive_one(cfg, src, "h.avif")
    assert (
        rec["status"] == "ok"
        and rec["src_kind"] == "heif"
        and (rec["width"], rec["height"]) == (96, 64)
    )


def test_long_edge_capped_and_never_upscaled(env):
    cfg, src = env
    big = np.random.default_rng(2).integers(0, 255, (3000, 5000, 3), dtype=np.uint8)
    _vips(big, "srgb").pngsave(str(src / "big.png"), compression=1)
    small = np.random.default_rng(3).integers(0, 255, (600, 800, 3), dtype=np.uint8)
    _vips(small, "srgb").pngsave(str(src / "small.png"))
    tall = np.random.default_rng(4).integers(0, 255, (4097, 1999, 3), dtype=np.uint8)
    _vips(tall, "srgb").pngsave(str(src / "tall.png"), compression=1)
    items = [_item(src, n) for n in ("big.png", "small.png", "tall.png")]
    derive.derive_items(cfg, PROFILE, items)
    idx = derive.load_index(cfg, PROFILE)
    assert (
        idx[items[0]["asset_id"]]["width"],
        idx[items[0]["asset_id"]]["height"],
    ) == (2048, 1229)
    assert (
        idx[items[1]["asset_id"]]["width"],
        idx[items[1]["asset_id"]]["height"],
    ) == (800, 600)
    assert (
        idx[items[2]["asset_id"]]["width"],
        idx[items[2]["asset_id"]]["height"],
    ) == (999, 2048)
    for it in items:
        img, _, _ = _out(cfg, idx[it["asset_id"]])
        assert (img.width, img.height) == (
            idx[it["asset_id"]]["width"],
            idx[it["asset_id"]]["height"],
        )


def test_embedded_icc_is_applied(env):
    cfg, src = env
    # A 16-bit RGB TIFF in Display P3: converting with its profile gets the sRGB colour back.
    want = np.zeros((32, 48, 3), np.uint8)
    want[:, :16] = (225, 60, 50)
    want[:, 16:32] = (60, 190, 90)
    want[:, 32:] = (128, 128, 128)
    p3 = _p3(want)
    p3_8 = p3.numpy()
    # the P3 numbers really differ from the sRGB ones
    assert np.abs(p3_8.astype(int) - want).max() > 15
    img = (p3.cast("ushort") * 257).cast("ushort")
    img = img.copy(interpretation="rgb16")
    img.set_type(
        pyvips.GValue.blob_type, "icc-profile-data", p3.get("icc-profile-data")
    )
    img.tiffsave(str(src / "icc.tif"))
    rec = _derive_one(cfg, src, "icc.tif")
    assert rec["status"] == "ok" and rec["src_bits"] == 16
    out, arr, data = _out(cfg, rec)
    _assert_clean_srgb8(out, data)
    cols = [8, 24, 40]
    centre = arr[8:-8, cols].astype(int)
    exp = _p3_to_srgb_by_pil(p3)[8:-8, cols]
    assert np.abs(centre - want[8:-8, cols]).mean() < 5  # close to the sRGB colour
    assert np.abs(centre - exp).max() <= 3  # and what an independent CMM gives
    assert np.abs(centre - p3_8[8:-8, cols]).max() > 15  # not the untagged P3 numbers


def test_psd_uses_merged_composite(env):
    from psd_tools import PSDImage

    cfg, src = env
    pix = np.zeros((90, 120, 3), np.uint8)
    pix[..., 1] = 200
    PSDImage.frompil(Image.fromarray(pix)).save(src / "layers.psd")
    rec = _derive_one(cfg, src, "layers.psd")
    assert rec["status"] == "ok", rec["error"]
    assert (rec["src_kind"], rec["rendered_from"], rec["src_bits"]) == (
        "psd",
        "composite",
        8,
    )
    _, arr, _ = _out(cfg, rec)
    assert abs(arr[..., 1].mean() - 200) < 3 and arr[..., 0].mean() < 5


def test_psd_embedded_icc_is_applied(env):
    from psd_tools import PSDImage
    from psd_tools.constants import Resource
    from psd_tools.psd.image_resources import ImageResource

    cfg, src = env
    want = np.full((30, 40, 3), (225, 60, 50), np.uint8)
    p3 = _p3(want)
    psd = PSDImage.frompil(Image.fromarray(p3.numpy()))
    icc = p3.get("icc-profile-data")
    psd.image_resources[Resource.ICC_PROFILE] = ImageResource(
        key=Resource.ICC_PROFILE, data=icc
    )
    psd.save(src / "p3.psd")
    rec = _derive_one(cfg, src, "p3.psd")
    assert rec["status"] == "ok", rec["error"]
    _, arr, _ = _out(cfg, rec)
    exp = _p3_to_srgb_by_pil(p3)[5:-5, 5:-5]
    assert np.abs(arr[5:-5, 5:-5].astype(int) - exp).max() <= 3
    assert np.abs(arr[5:-5, 5:-5].astype(int) - want[5:-5, 5:-5]).max() <= 6
    assert np.abs(arr[5:-5, 5:-5].astype(int) - p3.numpy()[5:-5, 5:-5]).max() > 15


def test_psd_without_composite_is_error(env, monkeypatch):
    from psd_tools import PSDImage

    cfg, src = env
    PSDImage.frompil(Image.new("RGB", (40, 30), (1, 2, 3))).save(src / "nocomp.psd")
    monkeypatch.setattr(PSDImage, "has_preview", lambda self: False)
    rec = _derive_one(cfg, src, "nocomp.psd")
    assert rec["status"] == "error" and "composite" in rec["error"]
    assert rec["path"] is None


def test_raw_rendered_with_recorded_settings(env, monkeypatch):
    """No synthetic NEF/DNG is possible, so a fake LibRaw stands in; real raws are checked by hand."""
    import rawpy

    cfg, src = env
    (src / "r.nef").write_bytes(b"fake raw bytes")
    seen = {}

    class FakeRaw:
        white_level = 4095

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def postprocess(self, **kw):
            seen.update(kw)
            out = np.zeros((400, 600, 3), np.uint16)
            out[..., 0] = 65535
            return out

    def fake_imread(f):
        seen["read"] = f.read()
        return FakeRaw()

    monkeypatch.setattr(rawpy, "imread", fake_imread)
    rec = _derive_one(cfg, src, "r.nef")
    assert rec["status"] == "ok", rec["error"]
    assert (rec["src_kind"], rec["rendered_from"], rec["src_bits"]) == (
        "raw",
        "raw",
        12,
    )
    assert (rec["width"], rec["height"]) == (600, 400)
    assert seen["read"] == b"fake raw bytes"
    assert seen["use_camera_wb"] is True and seen["use_auto_wb"] is False
    assert seen["output_color"] == rawpy.ColorSpace.sRGB and seen["output_bps"] == 16
    assert seen["gamma"] == (2.222, 4.5) and seen["no_auto_bright"] is False
    assert seen["demosaic_algorithm"] == rawpy.DemosaicAlgorithm.AHD
    _, arr, _ = _out(cfg, rec)
    assert arr[..., 0].mean() > 250 and arr[..., 1].mean() < 5


def _tiff_with_big_tag(rgb8: np.ndarray, blob_len: int) -> bytes:
    """A little-endian strip TIFF plus a large tag 37724 (Photoshop ImageSourceData)."""
    h, w, _ = rgb8.shape
    pixels = rgb8.tobytes()
    n_tags = 11
    ifd_off = 8
    bps_off = ifd_off + 2 + 12 * n_tags + 4
    pix_off = bps_off + 6
    blob_off = pix_off + len(pixels)

    def ent(tag, typ, count, value):
        return struct.pack("<HHI", tag, typ, count) + value

    short = lambda v: struct.pack("<HH", v, 0)  # noqa: E731
    long_ = lambda v: struct.pack("<I", v)  # noqa: E731
    entries = [
        ent(256, 3, 1, short(w)),
        ent(257, 3, 1, short(h)),
        ent(258, 3, 3, long_(bps_off)),
        ent(259, 3, 1, short(1)),
        ent(262, 3, 1, short(2)),
        ent(273, 4, 1, long_(pix_off)),
        ent(277, 3, 1, short(3)),
        ent(278, 3, 1, short(h)),
        ent(279, 4, 1, long_(len(pixels))),
        ent(284, 3, 1, short(1)),
        ent(37724, 7, blob_len, long_(blob_off)),
    ]
    assert len(entries) == n_tags
    return (
        b"II*\0"
        + long_(ifd_off)
        + struct.pack("<H", n_tags)
        + b"".join(entries)
        + long_(0)
        + struct.pack("<HHH", 8, 8, 8)
        + pixels
        + b"\x5a" * blob_len
    )


def test_tiff_with_large_photoshop_tag_decodes(env):
    # Regression: a real Photoshop-saved scan with a ~36 MB tag 37724 failed with
    # libtiff's "cumulated byte limit" because libvips caps TIFF metadata at 50 MB.
    cfg, src = env
    ramp = np.linspace(0, 255, 64).round().astype(np.uint8)
    pix = np.stack([np.tile(ramp, (48, 1))] * 3, axis=-1)
    (src / "layered.tif").write_bytes(_tiff_with_big_tag(pix, 32 * 1024 * 1024))
    rec = _derive_one(cfg, src, "layered.tif")
    assert rec["status"] == "ok", rec["error"]
    assert (rec["src_kind"], rec["src_width"], rec["src_height"]) == ("tiff", 64, 48)
    img, arr, data = _out(cfg, rec)
    _assert_clean_srgb8(img, data)
    assert np.abs(arr.astype(int) - pix.astype(int)).mean() < 2


# --- determinism, skipping, errors -------------------------------------------


def test_output_is_byte_deterministic(env):
    cfg, src = env
    pix = np.random.default_rng(5).integers(0, 65535, (2500, 1800, 3), dtype=np.uint16)
    _vips(pix, "rgb16").tiffsave(str(src / "d.tif"))
    rec1 = _derive_one(cfg, src, "d.tif")
    out = cfg.shoebox_root / rec1["path"]
    assert ids.bytes_id(out.read_bytes()) == rec1["derived_blake3"]
    out.unlink()
    rec2 = _derive_one(cfg, src, "d.tif")  # file gone, so it is regenerated
    assert rec2["derived_blake3"] == rec1["derived_blake3"]
    assert ids.bytes_id(out.read_bytes()) == rec1["derived_blake3"]
    settings = derive.load_profile(PROFILE)
    data = (src / "d.tif").read_bytes()
    assert (
        derive.render(data, "tif", settings).jpeg
        == derive.render(data, "tif", settings).jpeg
    )


def test_skip_existing_unless_forced(env):
    cfg, src = env
    _vips(np.zeros((20, 30, 3), np.uint8), "srgb").pngsave(str(src / "s.png"))
    item = _item(src, "s.png")
    c1 = derive.derive_items(cfg, PROFILE, [item])
    c2 = derive.derive_items(cfg, PROFILE, [item])
    assert (c1.ok, c1.skipped) == (1, 0) and (c2.ok, c2.skipped) == (0, 1)
    lines = derive.index_path(cfg, PROFILE).read_text().splitlines()
    assert len(lines) == 1
    c3 = derive.derive_items(cfg, PROFILE, [item], force=True)
    assert (
        c3.ok == 1
        and len(derive.index_path(cfg, PROFILE).read_text().splitlines()) == 2
    )


def test_record_format(env):
    cfg, src = env
    _vips(np.zeros((20, 30, 3), np.uint8), "srgb").pngsave(str(src / "f.png"))
    rec = _derive_one(cfg, src, "f.png")
    assert set(rec) == {
        "asset_id",
        "profile",
        "status",
        "path",
        "derived_blake3",
        "width",
        "height",
        "src_width",
        "src_height",
        "src_bits",
        "src_kind",
        "rendered_from",
        "error",
        "tools",
        "settings_hash",
        "tone",
    }
    assert rec["profile"] == PROFILE
    assert rec["settings_hash"] == derive.settings_hash(derive.load_profile(PROFILE))
    assert set(rec["tone"]) == {
        "src_black_pct",
        "src_white_pct",
        "out_black_pct",
        "out_white_pct",
        "max_clip_increase_pct",
        "within_tolerance",
    }
    assert (
        rec["path"] == f"derived/{PROFILE}/{rec['asset_id'][:2]}/{rec['asset_id']}.jpg"
    )
    assert {"libvips", "rawpy", "libraw"} <= set(rec["tools"])
    leftovers = [
        p
        for p in (cfg.shoebox_root / "derived").rglob("*")
        if p.is_file() and "tmp" in p.name
    ]
    assert leftovers == []


def test_unreadable_file_gets_error_record(env):
    cfg, src = env
    (src / "bad.jpg").write_bytes(b"\xff\xd8 this is not a jpeg")
    rec = _derive_one(cfg, src, "bad.jpg")
    assert rec["status"] == "error" and rec["error"]
    assert (
        rec["path"] is None and rec["derived_blake3"] is None and rec["width"] is None
    )
    assert not derive.derived_path(cfg, PROFILE, rec["asset_id"]).exists()


def test_missing_original_and_hash_mismatch_are_errors(env):
    cfg, src = env
    _vips(np.zeros((20, 30, 3), np.uint8), "srgb").pngsave(str(src / "m.png"))
    item = _item(src, "m.png")
    gone = dict(item, relpath="nope.png", asset_id="cd" + "1" * 62)
    wrong = dict(item, asset_id="ef" + "2" * 62)
    c = derive.derive_items(cfg, PROFILE, [gone, wrong])
    assert c.error == 2
    idx = derive.load_index(cfg, PROFILE)
    assert idx[gone["asset_id"]]["status"] == "error"
    assert "asset_id" in idx[wrong["asset_id"]]["error"]


def test_truncated_index_line_is_skipped_and_run_recovers(env):
    cfg, src = env
    for v, n in enumerate(("t1.png", "t2.png")):
        _vips(np.full((20, 30, 3), v, np.uint8), "srgb").pngsave(str(src / n))
    a, b = _item(src, "t1.png"), _item(src, "t2.png")
    derive.derive_items(cfg, PROFILE, [a])
    idx = derive.index_path(cfg, PROFILE)
    with open(idx, "a") as f:  # an append cut short by a killed run
        f.write('{"asset_id": "' + b["asset_id"] + '", "profile": "inf')
    with pytest.warns(UserWarning, match="unreadable index line"):
        assert set(derive.load_index(cfg, PROFILE)) == {a["asset_id"]}
    with pytest.warns(UserWarning):
        c = derive.derive_items(cfg, PROFILE, [a, b])
    assert (c.ok, c.skipped, c.error) == (1, 1, 0)
    lines = idx.read_text().splitlines()
    assert json.loads(lines[-1])["asset_id"] == b["asset_id"]  # on its own line
    with pytest.warns(UserWarning):
        got = derive.load_index(cfg, PROFILE)
    assert got[b["asset_id"]]["status"] == "ok"


def test_settings_change_under_same_name_is_refused(env, monkeypatch):
    cfg, src = env
    _vips(np.zeros((20, 30, 3), np.uint8), "srgb").pngsave(str(src / "c.png"))
    item = _item(src, "c.png")
    derive.derive_items(cfg, PROFILE, [item])
    real = derive.load_profile

    def changed(name, *a, **kw):
        return dict(real(name, *a, **kw), jpeg_quality=80)

    monkeypatch.setattr(derive, "load_profile", changed)
    with pytest.raises(ValueError, match="settings changed"):
        derive.derive_items(cfg, PROFILE, [item], force=True)
    assert len(derive.index_path(cfg, PROFILE).read_text().splitlines()) == 1


def test_status_and_check_do_not_change_settings_hash():
    s = derive.load_profile(PROFILE)
    h = derive.settings_hash(s)
    assert derive.settings_hash(dict(s, status="frozen")) == h
    assert derive.settings_hash(dict(s, check={"max_clip_increase_pct": 5})) == h
    assert derive.settings_hash(dict(s, long_edge=1536)) != h


def test_stale_tools_or_missing_settings_hash_rederives(env, monkeypatch):
    cfg, src = env
    _vips(np.zeros((20, 30, 3), np.uint8), "srgb").pngsave(str(src / "v.png"))
    item = _item(src, "v.png")
    derive.derive_items(cfg, PROFILE, [item])
    real = derive.tool_versions
    monkeypatch.setattr(derive, "tool_versions", lambda: dict(real(), libvips="9.0.0"))
    c = derive.derive_items(cfg, PROFILE, [item])
    assert (c.ok, c.skipped) == (1, 0)
    assert derive.derive_items(cfg, PROFILE, [item]).skipped == 1
    # a record without settings_hash (older format) isn't trusted either
    idx = derive.index_path(cfg, PROFILE)
    rec = derive.load_index(cfg, PROFILE)[item["asset_id"]]
    rec.pop("settings_hash")
    with open(idx, "a") as f:
        f.write(json.dumps(rec) + "\n")
    assert derive.derive_items(cfg, PROFILE, [item]).ok == 1


def test_refuses_to_write_under_volumes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    cfg = config.Config(
        shoebox_root=Path("/Volumes/shoebox-test-never-exists"),
        sources={"t": config.Source("t", src, "owner", True, False)},
    )
    with pytest.raises(ValueError, match="/Volumes"):
        derive.derive_items(cfg, PROFILE, [])


def test_refuses_to_read_sources_under_volumes(tmp_path):
    cfg = config.Config(
        shoebox_root=tmp_path / "shoebox",
        sources={
            "t": config.Source(
                "t", Path("/Volumes/ImportantFiles-never-exists"), "owner", True, False
            )
        },
    )
    item = {"asset_id": "ab" * 32, "source": "t", "relpath": "x.jpg", "ext": "jpg"}
    with pytest.raises(ValueError, match="/Volumes"):
        derive.derive_items(cfg, PROFILE, [item])
    assert not (tmp_path / "shoebox").exists()


def test_symlink_out_of_source_root_is_an_error(env, tmp_path):
    cfg, src = env
    outside = tmp_path / "elsewhere.png"
    _vips(np.zeros((20, 30, 3), np.uint8), "srgb").pngsave(str(outside))
    (src / "link.png").symlink_to(outside)
    item = dict(_item(src, "link.png"))
    rec = _derive_one(cfg, src, "link.png")
    assert rec["status"] == "error" and "outside its source root" in rec["error"]
    assert not derive.derived_path(cfg, PROFILE, item["asset_id"]).exists()


def test_refuses_to_write_under_a_source_root(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    cfg = config.Config(
        shoebox_root=src / "inside",
        sources={"t": config.Source("t", src, "owner", True, False)},
    )
    with pytest.raises(ValueError):
        derive.derive_items(cfg, PROFILE, [])


def test_cli_exit_codes(env, tmp_path, capsys):
    _, src = env
    (src / "bad.png").write_bytes(b"nope")
    _vips(np.zeros((20, 30, 3), np.uint8), "srgb").pngsave(str(src / "good.png"))
    bad = _write_items(tmp_path, [_item(src, "bad.png")])
    assert main(["derive", "--profile", PROFILE, "--items", str(bad)]) == 1
    mixed = _write_items(tmp_path, [_item(src, "bad.png"), _item(src, "good.png")])
    assert main(["derive", "--profile", PROFILE, "--items", str(mixed), "--force"]) == 0
    out = capsys.readouterr().out
    assert "ok 1" in out and "error 1" in out and "clipped 0" in out


def test_render_is_pure_bytes_in_bytes_out():
    pix = np.zeros((10, 10, 3), np.uint8)
    buf = io.BytesIO()
    Image.fromarray(pix).save(buf, "PNG")
    r = derive.render(buf.getvalue(), "png", derive.load_profile(PROFILE))
    assert r.jpeg[:2] == b"\xff\xd8" and (r.width, r.height) == (10, 10)
