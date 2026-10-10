import json

import numpy as np
import pytest
import pyvips
from jsonschema import Draft202012Validator
from PIL import Image

from shoebox import runners
from shoebox.config import REPO_ROOT, Config, Source
from shoebox.runners import rules

SCHEMA = Draft202012Validator(json.loads((REPO_ROOT / "config" / "schemas" / "label-v1.json").read_text()))
CAND = {"name": "rules-v1", "runtime": "rules", "model": "rules-v1", "profile": "infer-v1"}


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "src").mkdir()
    return Config(shoebox_root=tmp_path / "shoebox",
                  sources={"t": Source("t", tmp_path / "src", "owner", False, True)})


def exif(make=None, model=None, software=None):
    ex = Image.Exif()
    if make:
        ex[0x010F] = make
    if model:
        ex[0x0110] = model
    if software:
        ex[0x0131] = software
    return ex


def grey_rgb(w=96, h=64):
    ramp = np.tile(np.linspace(20, 235, w, dtype=np.uint8), (h, 1))
    return Image.fromarray(np.stack([ramp] * 3, axis=-1))


def lab_image(a, b, w=128, h=96):
    """A lightness ramp with a constant (a, b): toned when (a, b) != 0."""
    L = np.tile(np.linspace(20, 90, w, dtype=np.float32), (h, 1))
    arr = np.stack([L, np.full_like(L, a), np.full_like(L, b)], axis=-1)
    img = pyvips.Image.new_from_memory(arr.tobytes(), w, h, 3, "float")
    rgb = img.copy(interpretation="lab").colourspace("srgb").cast("uchar")
    return Image.fromarray(np.ndarray(buffer=rgb.write_to_memory(), dtype=np.uint8, shape=(h, w, 3)))


def colourful(w=128, h=96):
    arr = np.zeros((h, w, 3), np.uint8)
    arr[: h // 2, : w // 2] = (220, 40, 40)
    arr[: h // 2, w // 2:] = (40, 200, 60)
    arr[h // 2:, : w // 2] = (40, 70, 220)
    arr[h // 2:, w // 2:] = (230, 210, 40)
    return Image.fromarray(arr)


def add(cfg, relpath, original: Image.Image | bytes, derived: Image.Image | None = None, **save):
    p = cfg.sources["t"].root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(original, bytes):
        p.write_bytes(original)
    else:
        original.save(p, **save)
    aid = f"{abs(hash(relpath)):064x}"[-64:]
    d = runners.derived_image(cfg, "infer-v1", aid)
    d.parent.mkdir(parents=True, exist_ok=True)
    (derived or (original if isinstance(original, Image.Image) else grey_rgb())).convert("RGB").save(d, quality=92)
    item = {"asset_id": aid, "source": "t", "relpath": relpath, "ext": p.suffix.lstrip(".").lower()}
    return item, d


def label(cfg, item, image):
    out = rules.run_one(CAND, item, image, cfg)
    assert out["status"] == "ok", out
    assert SCHEMA.is_valid(out["output"]), list(SCHEMA.iter_errors(out["output"]))
    o = out["output"]
    assert o["caption"] is None and o["tags"] == []
    return o


def tiff_bytes(tags: dict[int, str], magic=42) -> bytes:
    """A minimal little-endian TIFF/raw header carrying ASCII IFD0 tags (no image data needed)."""
    import struct

    entries, blob = [], b""
    data_off = 8 + 2 + 12 * len(tags) + 4
    for tag, text in sorted(tags.items()):
        val = text.encode() + b"\0"
        entries.append(struct.pack("<HHII", tag, 2, len(val), data_off + len(blob)))
        blob += val
    return b"II" + struct.pack("<HI", magic, 8) + struct.pack("<H", len(tags)) + b"".join(entries) + b"\0\0\0\0" + blob


def test_film_scanner_exif_is_film_scanner_with_unknown_medium(cfg):
    item, d = add(cfg, "Scans/roll1/img001.jpg", grey_rgb(), exif=exif("Nikon", "LS-50 ED", "Nikon Scan 4.0.2 W"))
    o = label(cfg, item, d)
    assert o["digitiser"] == "film_scanner" and o["medium"] == "unknown"


def test_film_scanner_with_negatives_folder_is_film_negative(cfg):
    item, d = add(cfg, "Negatives/1985/img001.jpg", grey_rgb(), exif=exif("NIKON", "SUPER COOLSCAN 5000 ED"))
    o = label(cfg, item, d)
    assert (o["medium"], o["digitiser"]) == ("film_negative", "film_scanner")


def test_flatbed_tiff_header(cfg):
    item, d = add(cfg, "scan0001.tif", tiff_bytes({271: "EPSON", 272: "Perfection V700", 305: "EPSON Scan"}),
                  derived=colourful())
    o = label(cfg, item, d)
    assert o["digitiser"] == "flatbed_scanner"


def test_nef_header_is_camera(cfg):
    item, d = add(cfg, "DSC_0001.NEF", tiff_bytes({271: "NIKON CORPORATION", 272: "NIKON D700"}), derived=colourful())
    o = label(cfg, item, d)
    assert (o["medium"], o["digitiser"]) == ("digital", "camera")


def test_orf_magic_header_is_read(cfg):
    item, d = add(cfg, "P1010001.ORF", tiff_bytes({271: "OLYMPUS IMAGING CORP.", 272: "E-M5"}, magic=0x4F52),
                  derived=colourful())
    assert label(cfg, item, d)["digitiser"] == "camera"


def test_iphone_is_phone(cfg):
    item, d = add(cfg, "IMG_0001.JPG", colourful(), exif=exif("Apple", "iPhone 12 Pro"))
    o = label(cfg, item, d)
    assert (o["medium"], o["digitiser"]) == ("digital", "phone")


def test_heic_iphone_is_phone(cfg):
    import pillow_heif

    pillow_heif.register_heif_opener()
    item, d = add(cfg, "IMG_0002.HEIC", colourful(), exif=exif("Apple", "iPhone 15"), format="HEIF")
    assert label(cfg, item, d)["digitiser"] == "phone"


def test_canon_camera_vs_canoscan(cfg):
    item, d = add(cfg, "IMG_1.jpg", colourful(), exif=exif("Canon", "Canon EOS 5D Mark II"))
    assert label(cfg, item, d)["digitiser"] == "camera"
    item, d = add(cfg, "scan1.jpg", colourful(), exif=exif("Canon", "CanoScan LiDE 220"))
    assert label(cfg, item, d)["digitiser"] == "flatbed_scanner"


def test_samsung_phone_and_sony_camera(cfg):
    item, d = add(cfg, "a.jpg", colourful(), exif=exif("samsung", "SM-G991B"))
    assert label(cfg, item, d)["digitiser"] == "phone"
    item, d = add(cfg, "b.jpg", colourful(), exif=exif("SONY", "ILCE-7M3"))
    assert label(cfg, item, d)["digitiser"] == "camera"


def test_no_exif_no_origin_signal_abstains(cfg):
    item, d = add(cfg, "Pictures/old/photo12.jpg", colourful())
    o = label(cfg, item, d)
    assert o["digitiser"] == "unknown" and o["medium"] == "unknown"
    assert o["confidence"]["digitiser"] <= 0.3


def test_screenshot_by_filename(cfg):
    item, d = add(cfg, "Desktop/Screenshot 2020-03-01 at 10.00.00.png", colourful())
    o = label(cfg, item, d)
    assert (o["medium"], o["digitiser"]) == ("screen", "screenshot")


def test_png_with_phone_screen_size_is_screenshot(cfg):
    item, d = add(cfg, "IMG_4410.PNG", Image.new("RGB", (1170, 2532), (250, 250, 250)), derived=colourful())
    o = label(cfg, item, d)
    assert o["digitiser"] == "screenshot" and o["confidence"]["digitiser"] < 0.9


def test_png_of_other_size_is_not_screenshot(cfg):
    item, d = add(cfg, "drawing.png", Image.new("RGB", (1000, 700), (250, 250, 250)), derived=colourful())
    assert label(cfg, item, d)["digitiser"] == "unknown"


def test_proprietary_raw_without_readable_header_is_camera(cfg):
    item, d = add(cfg, "DSC_9.NEF", b"not a tiff at all", derived=colourful())
    assert label(cfg, item, d)["digitiser"] == "camera"


def test_dng_without_header_abstains(cfg):
    item, d = add(cfg, "scan.dng", b"garbage", derived=colourful())
    assert label(cfg, item, d)["digitiser"] == "unknown"


@pytest.mark.parametrize("derived,expected", [
    (grey_rgb(), "mono"),
    (lab_image(4, 16), "toned"),         # sepia
    (lab_image(2, -14), "toned"),        # cyanotype-like blue
    (colourful(), "colour"),
])
def test_colour_class_from_derived_pixels(cfg, derived, expected):
    item, d = add(cfg, f"c_{expected}_{id(derived)}.jpg", grey_rgb(), derived=derived)
    assert label(cfg, item, d)["colour_class"] == expected


def test_single_band_original_is_mono(cfg):
    item, d = add(cfg, "bw.jpg", grey_rgb().convert("L"), derived=lab_image(1, 3))
    o = label(cfg, item, d)
    assert o["colour_class"] == "mono" and o["confidence"]["colour_class"] >= 0.9


def test_missing_original_is_error(cfg):
    item, d = add(cfg, "x.jpg", colourful())
    (cfg.sources["t"].root / "x.jpg").unlink()
    out = rules.run_one(CAND, item, d, cfg)
    assert out["status"] == "error" and "x.jpg" in out["error"]


def test_runtime_version_tracks_rules_source():
    v = rules.runtime_version(CAND)
    assert v.startswith("rules-v1 src:") and "libvips" in v


def test_faint_uniform_cast_abstains_rather_than_toned(cfg):
    item, d = add(cfg, "faint.jpg", grey_rgb(), derived=lab_image(1, 3))
    assert label(cfg, item, d)["colour_class"] == "unknown"


def psd_bytes(mode: int, channels: int, exif_blob: bytes = b"") -> bytes:
    import struct

    head = b"8BPS" + struct.pack(">H6xHIIHH", 1, channels, 40, 60, 8, mode)
    res = b""
    if exif_blob:
        res += b"8BIM" + struct.pack(">H", 1028) + b"\0\0" + struct.pack(">I", 3) + b"abc\0"  # odd size, padded
        res += b"8BIM" + struct.pack(">H", 1058) + b"\0\0" + struct.pack(">I", len(exif_blob)) + exif_blob
    return head + struct.pack(">I", 0) + struct.pack(">I", len(res)) + res + b"\0" * 16


def test_psd_header_and_embedded_exif(cfg):
    blob = tiff_bytes({271: "Leica Camera AG", 272: "LEICA M (Typ 262)"})
    item, d = add(cfg, "L1002656.psd", psd_bytes(3, 4, blob), derived=colourful())
    o = label(cfg, item, d)
    assert (o["medium"], o["digitiser"]) == ("digital", "camera")
    h = rules.read_header(cfg.sources["t"].root / "L1002656.psd", "psd")
    assert (h.width, h.height, h.bands, h.grey) == (60, 40, 4, False)


def test_greyscale_psd_is_mono(cfg):
    item, d = add(cfg, "bw.psd", psd_bytes(1, 1), derived=lab_image(4, 16))
    assert label(cfg, item, d)["colour_class"] == "mono"


# --- review fixes: scanner gear from camera makers, path guards, schema --------------------------

@pytest.mark.parametrize("make,model,expected", [
    ("Panasonic", "DMC-LS80", ("digital", "camera")),                       # a compact, not a Coolscan
    ("Nikon", "LS-5000", ("unknown", "film_scanner")),
    ("Nikon", "LS-50 ED", ("unknown", "film_scanner")),
    ("FUJI PHOTO FILM CO., LTD.", "SP-3000", ("unknown", "film_scanner")),  # Frontier lab scanner
    ("NORITSU KOKI", "QSS-32", ("unknown", "film_scanner")),
    ("Eastman Kodak Company", "KODAK RFS 3600", ("unknown", "film_scanner")),
    ("Eastman Kodak Company", "KODAK EASYSHARE C875 ZOOM DIGITAL CAMERA", ("digital", "camera")),
    ("Eastman Kodak Company", "DCS Pro 14N", ("digital", "camera")),
    ("FUJIFILM", "X-T3", ("digital", "camera")),
    ("FUJIFILM", "FinePix S5Pro", ("digital", "camera")),
    ("KONICA MINOLTA", "DYNAX 7D", ("digital", "camera")),
    ("Minolta Co., Ltd.", "DiMAGE 7Hi", ("digital", "camera")),
])
def test_origin_of_camera_maker_gear(make, model, expected):
    medium, _, digitiser, _ = rules.classify_origin(rules.Header(make=make, model=model), "a/b.jpg", "jpg")
    assert (medium, digitiser) == expected


@pytest.mark.parametrize("make,model", [
    ("Eastman Kodak Company", "KODAK PROFESSIONAL XYZ 99"),  # unknown model from a camera-and-scanner maker
    ("FUJIFILM", "Model 3"),
    ("KONICA MINOLTA", "Thing 42"),
])
def test_unrecognised_model_from_a_scanner_making_camera_maker_abstains(make, model):
    medium, mconf, digitiser, dconf = rules.classify_origin(rules.Header(make=make, model=model), "a/b.jpg", "jpg")
    assert (medium, digitiser) == ("unknown", "unknown") and mconf <= 0.3 and dconf <= 0.3


def test_relpath_escaping_the_source_root_is_refused(cfg, tmp_path):
    outside = tmp_path / "outside.jpg"
    colourful().save(outside, exif=exif("Apple", "iPhone 12"))
    item, d = add(cfg, "x.jpg", colourful())
    out = rules.run_one(CAND, {**item, "relpath": "../outside.jpg"}, d, cfg)
    assert out["status"] == "error" and "source root" in out["error"]


def test_symlink_out_of_the_source_root_is_refused(cfg, tmp_path):
    outside = tmp_path / "outside.jpg"
    colourful().save(outside)
    item, d = add(cfg, "x.jpg", colourful())
    (cfg.sources["t"].root / "link.jpg").symlink_to(outside)
    out = rules.run_one(CAND, {**item, "relpath": "link.jpg"}, d, cfg)
    assert out["status"] == "error" and "source root" in out["error"]


def test_source_under_volumes_is_refused(tmp_path):
    from pathlib import Path

    cfg = Config(shoebox_root=tmp_path / "shoebox",
                 sources={"t": Source("t", Path("/Volumes/no-such-share"), "owner", False, True)})
    out = rules.run_one(CAND, {"asset_id": "a" * 64, "source": "t", "relpath": "x.jpg", "ext": "jpg"},
                        tmp_path / "d.jpg", cfg)
    assert out["status"] == "error" and "refusing" in out["error"] and "/Volumes" in out["error"]


def test_output_is_validated_against_the_hashed_schema(cfg):
    item, d = add(cfg, "x.jpg", colourful())
    narrow = json.loads((REPO_ROOT / "config" / "schemas" / "label-v1.json").read_text())
    narrow["properties"]["medium"] = {"enum": ["film_print"]}  # a schema the rules output cannot meet
    out = rules.run_one({**CAND, "schema_text": json.dumps(narrow)}, item, d, cfg)
    assert out["status"] == "invalid" and "medium" in out["error"]
    ok = rules.run_one({**CAND, "schema_text": (REPO_ROOT / "config" / "schemas" / "label-v1.json").read_text()},
                       item, d, cfg)
    assert ok["status"] == "ok"
