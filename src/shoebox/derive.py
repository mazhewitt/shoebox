"""Derived inference images (design D6, docs/pilot.md section 2).

Turns an original into a profile's model-input JPEG: decoded with libvips (raws with
LibRaw via rawpy, PSDs from their flattened composite), orientation applied, ICC -> sRGB,
8-bit, long edge capped, every metadata block stripped. Originals are only ever read.
"""

import hashlib
import io
import json
import os
import threading
import time
import tomllib
import warnings
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyvips
import rawpy

from shoebox import ids
from shoebox.config import (
    REPO_ROOT,
    Config,
    check_source_root,
    check_write_location,
    original_path,
)

PROFILES_PATH = REPO_ROOT / "config" / "profiles.toml"

KIND_BY_EXT = {
    **dict.fromkeys(("jpg", "jpeg", "jpe", "jfif"), "jpeg"),
    "png": "png",
    **dict.fromkeys(("heic", "heif", "hif", "avif"), "heif"),
    "webp": "webp",
    **dict.fromkeys(("tif", "tiff"), "tiff"),
    **dict.fromkeys(
        (
            "nef",
            "nrw",
            "dng",
            "cr2",
            "cr3",
            "crw",
            "arw",
            "srf",
            "sr2",
            "orf",
            "rw2",
            "raf",
            "pef",
            "srw",
            "rwl",
            "3fr",
            "erf",
            "kdc",
            "mrw",
            "x3f",
            "iiq",
        ),
        "raw",
    ),
    **dict.fromkeys(("psd", "psb"), "psd"),
}
# libvips loaders we accept, and the src_kind each one means.
KIND_BY_LOADER = {
    "jpegload_buffer": "jpeg",
    "pngload_buffer": "png",
    "heifload_buffer": "heif",
    "webpload_buffer": "webp",
    "tiffload_buffer": "tiff",
}
_BITS_BY_FORMAT = {
    "uchar": 8,
    "char": 8,
    "ushort": 16,
    "short": 16,
    "uint": 32,
    "int": 32,
    "float": 32,
    "double": 64,
}
_WHITE_BY_FORMAT = {
    "uchar": 255,
    "char": 127,
    "ushort": 65535,
    "short": 32767,
    "float": 1.0,
    "double": 1.0,
}
_COLOUR_INTERPRETATIONS = {
    "srgb",
    "rgb16",
    "cmyk",
    "lab",
    "labs",
    "labq",
    "scrgb",
    "xyz",
    "yxy",
    "lch",
    "cmc",
    "hsv",
}

# Interpretations whose sample values aren't grey/RGB tone, so a source clip % means nothing.
_NON_TONAL = _COLOUR_INTERPRETATIONS - {"srgb", "rgb16"}


class DecodeError(Exception):
    """The original can't be turned into a derived image."""


@dataclass(frozen=True)
class Rendered:
    jpeg: bytes
    width: int
    height: int
    src_width: int
    src_height: int
    src_bits: int
    src_kind: str
    rendered_from: str  # decoded | libheif | raw | composite
    tone: dict  # clip check: see _tone_check


@dataclass
class Counts:
    ok: int = 0
    skipped: int = 0
    error: int = 0
    # ok, but clipped beyond the profile's tolerance (also counted in ok)
    flagged: int = 0

    @property
    def total(self) -> int:
        return self.ok + self.skipped + self.error


# --- profiles, paths, index -----------------------------------------------------


def load_profile(name: str, path: Path = PROFILES_PATH) -> dict:
    """The settings of profile `name` from config/profiles.toml (KeyError if absent)."""
    profiles = tomllib.loads(Path(path).read_text())
    if name not in profiles:
        raise KeyError(f"unknown image profile {name!r}")
    return profiles[name]


# Profile keys that don't change the derived bytes, so they're left out of settings_hash
# (and out of the label params_hash, which hashes `byte_settings`).
UNHASHED_KEYS = ("status", "check")


def byte_settings(settings: dict) -> dict:
    """The profile settings that change the derived bytes."""
    return {k: v for k, v in settings.items() if k not in UNHASHED_KEYS}


def settings_hash(settings: dict) -> str:
    """First 16 hex of sha256 over canonical JSON of the byte-affecting profile settings."""
    canon = json.dumps(byte_settings(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def tool_versions() -> dict:
    import psd_tools

    return {
        "libvips": f"{pyvips.version(0)}.{pyvips.version(1)}.{pyvips.version(2)}",
        "pyvips": pyvips.__version__,
        "rawpy": rawpy.__version__,
        "libraw": ".".join(str(v) for v in rawpy.libraw_version),
        "psd-tools": psd_tools.__version__,
        "numpy": np.__version__,
    }


def profile_dir(cfg: Config, profile: str) -> Path:
    return cfg.derived_dir / profile


def derived_path(cfg: Config, profile: str, asset_id: str) -> Path:
    return profile_dir(cfg, profile) / asset_id[:2] / f"{asset_id}.jpg"


def index_path(cfg: Config, profile: str) -> Path:
    return profile_dir(cfg, profile) / "index.jsonl"


def load_index(cfg: Config, profile: str) -> dict[str, dict]:
    """`{asset_id: record}` from the profile's index; the latest record per asset wins.

    A line that won't parse (an append cut short by a killed run or a dropped SMB link)
    is skipped with a warning, so that asset's previous record, if any, stands.
    """
    path = index_path(cfg, profile)
    out: dict[str, dict] = {}
    if path.exists():
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rec = None
            if not isinstance(rec, dict) or not isinstance(rec.get("asset_id"), str):
                warnings.warn(
                    f"{path}:{n}: skipping unreadable index line (interrupted append?)",
                    stacklevel=2,
                )
                continue
            out[rec["asset_id"]] = rec
    return out


# --- rendering ------------------------------------------------------------------


_HEIF_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis")


def content_kind(data: bytes) -> str | None:
    """The kind the bytes plainly are (JPEG, PNG, HEIF, WebP, PSD), else None.

    TIFF magic is deliberately not a kind here: most raws (NEF, DNG, ...) are TIFF containers.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "heif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"8BPS":
        return "psd"
    return None


def sniff_kind(data: bytes, ext: str) -> str | None:
    """Decode kind: what the bytes plainly are, else what the extension says.

    The archive holds JPEGs saved with .NEF/.DNG names (old Google Photos exports); by extension
    they went to LibRaw and failed.
    """
    return content_kind(data) or KIND_BY_EXT.get(ext.lower().lstrip("."))


def render(data: bytes, ext: str, settings: dict) -> Rendered:
    """Render an original's bytes to the profile's JPEG. Raises DecodeError or pyvips.Error."""
    kind = sniff_kind(data, ext)
    if kind is None:
        raise DecodeError(f"unsupported file type .{ext}")
    if kind == "raw":
        (img, bits), src_from = _decode_raw(data, settings["raw"]), "raw"
    elif kind == "psd":
        (img, bits), src_from = _decode_psd(data), "composite"
    else:
        try:
            img, kind, bits = _decode_vips(data, settings)
            src_from = "decoded"
        except pyvips.Error:
            if data[4:8] != b"ftyp":
                raise
            (img, bits), kind, src_from = _decode_heif_libheif(data), "heif", "libheif"
    src_w, src_h = img.width, img.height
    img = _flatten(img)
    tonal = img.bands in (1, 3) and img.interpretation not in _NON_TONAL
    src_clip = _clip_pct(img) if tonal else None
    img = _to_srgb8(img, settings)
    tone = _tone_check(src_clip, _clip_pct(img), settings.get("check", {}))
    long_edge = int(settings["long_edge"])
    if max(img.width, img.height) > long_edge:
        img = img.resize(
            long_edge / max(img.width, img.height), kernel=settings["resample_kernel"]
        )
    jpeg = img.jpegsave_buffer(
        Q=int(settings["jpeg_quality"]),
        subsample_mode=settings["jpeg_subsample"],
        optimize_coding=bool(settings["jpeg_optimize_coding"]),
        interlace=bool(settings["jpeg_interlace"]),
        keep=pyvips.enums.ForeignKeep.NONE,
    )
    return Rendered(
        jpeg, img.width, img.height, src_w, src_h, int(bits), kind, src_from, tone
    )


_TIFF_MAGIC = (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+")  # classic and BigTIFF


def _decode_vips(data: bytes, settings: dict) -> tuple[pyvips.Image, str, int]:
    # libvips caps libtiff's per-file metadata allocations at 50 MB unless `unlimited`.
    # Photoshop-saved scans carry their layers in tag 37724 (ImageSourceData), often
    # tens of MB, which trips that cap although the image itself is fine. The originals
    # are the owner's own files, so lift the cap for TIFF only; it doesn't change pixels.
    opts = {"unlimited": True} if data[:4] in _TIFF_MAGIC else {}
    img = pyvips.Image.new_from_buffer(
        data, "", fail_on=settings["decode_fail_on"], **opts
    )
    loader = img.get("vips-loader")
    if loader not in KIND_BY_LOADER:
        raise DecodeError(f"unsupported format (libvips loader {loader})")
    if loader == "heifload_buffer":
        img = img.copy_memory()  # an HEVC HEIC only fails here, when pixels are read
    if img.get_typeof("bits-per-sample"):
        bits = int(img.get("bits-per-sample"))
    else:
        bits = _BITS_BY_FORMAT.get(img.format, 8)
    return img.autorot(), KIND_BY_LOADER[loader], bits


def _decode_heif_libheif(data: bytes) -> tuple[pyvips.Image, int]:
    """HEIF/HEIC via pillow-heif's libheif: this libvips build has no HEVC decoder (iPhone HEICs).

    libheif applies the HEIF orientation transforms itself; the embedded ICC profile (often
    Display P3) is attached so `_to_srgb8` converts it like any other. Decoded at 8 bits;
    `bits` is the source's own bit depth.
    """
    import pillow_heif

    try:
        hf = pillow_heif.open_heif(data, convert_hdr_to_8bit=True)
    except Exception as e:  # libheif errors become a decode error
        raise DecodeError(f"libheif: {type(e).__name__}: {e}") from e
    mode = hf.mode
    arr = np.asarray(hf)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    img = pyvips.Image.new_from_array(np.ascontiguousarray(arr)).copy(
        interpretation="srgb" if arr.shape[2] >= 3 else "b-w"
    )
    icc = hf.info.get("icc_profile")
    if icc:
        img = img.copy()
        img.set_type(pyvips.GValue.blob_type, "icc-profile-data", bytes(icc))
    bits = int(hf.info.get("bit_depth") or (16 if mode.endswith(";16") else 8))
    return img, bits


def _decode_raw(data: bytes, raw_settings: dict) -> tuple[pyvips.Image, int]:
    """LibRaw render at the profile's fixed settings; orientation comes from the raw's flip tag."""
    s = raw_settings
    params = {
        "demosaic_algorithm": rawpy.DemosaicAlgorithm[s["demosaic_algorithm"]],
        "use_camera_wb": bool(s["use_camera_wb"]),
        "use_auto_wb": bool(s["use_auto_wb"]),
        "output_color": rawpy.ColorSpace[s["output_color"]],
        "output_bps": int(s["output_bps"]),
        "gamma": tuple(float(g) for g in s["gamma"]),
        "no_auto_bright": bool(s["no_auto_bright"]),
        "auto_bright_thr": float(s["auto_bright_thr"]),
        "bright": float(s["bright"]),
        "highlight_mode": rawpy.HighlightMode[s["highlight_mode"].capitalize()],
        "half_size": bool(s["half_size"]),
        "four_color_rgb": bool(s["four_color_rgb"]),
        "median_filter_passes": int(s["median_filter_passes"]),
        "fbdd_noise_reduction": rawpy.FBDDNoiseReductionMode[
            s["fbdd_noise_reduction"].capitalize()
        ],
    }
    try:
        with rawpy.imread(io.BytesIO(data)) as raw:
            bits = int(raw.white_level).bit_length()
            rgb = raw.postprocess(**params)
    except rawpy.LibRawError as e:
        raise DecodeError(f"LibRaw: {type(e).__name__}: {e}") from e
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise DecodeError(f"LibRaw returned shape {rgb.shape}")
    interp = "rgb16" if rgb.dtype == np.uint16 else "srgb"
    return pyvips.Image.new_from_array(np.ascontiguousarray(rgb)).copy(
        interpretation=interp
    ), bits


def _decode_psd(data: bytes) -> tuple[pyvips.Image, int]:
    """The PSD's merged image data (the composite Photoshop saved); layers are never re-rendered."""
    from psd_tools import PSDImage
    from psd_tools.constants import ColorMode, Resource

    psd = PSDImage.open(io.BytesIO(data))
    if not psd.has_preview():
        raise DecodeError(
            "PSD has no flattened composite (saved without maximise compatibility)"
        )
    w, h, depth = psd.width, psd.height, psd.depth
    n = {ColorMode.RGB: 3, ColorMode.GRAYSCALE: 1}.get(psd.color_mode)
    if n is not None and depth in (8, 16):
        planes = psd._record.image_data.get_data(psd._record.header)
        dtype = np.dtype(">u2") if depth == 16 else np.dtype("u1")
        arr = np.stack(
            [np.frombuffer(p, dtype).reshape(h, w) for p in planes[:n]], axis=-1
        )
        arr = arr.astype(arr.dtype.newbyteorder("=")) if depth == 16 else arr
        interp = {(3, 8): "srgb", (3, 16): "rgb16", (1, 8): "b-w", (1, 16): "grey16"}[
            (n, depth)
        ]
        img = pyvips.Image.new_from_array(np.ascontiguousarray(arr)).copy(
            interpretation=interp
        )
        if Resource.ICC_PROFILE in psd.image_resources:
            icc = psd.image_resources.get_data(Resource.ICC_PROFILE)
            img.set_type(pyvips.GValue.blob_type, "icc-profile-data", bytes(icc))
    else:
        # Other modes (CMYK, Lab, indexed, bitmap, 32-bit): psd-tools converts the same merged data.
        pil = psd.topil(apply_icc=True)
        if pil is None:
            raise DecodeError("PSD has no flattened composite")
        arr = np.asarray(pil.convert("RGB"))
        img = pyvips.Image.new_from_array(np.ascontiguousarray(arr)).copy(
            interpretation="srgb"
        )
    return img, depth


def _flatten(img: pyvips.Image) -> pyvips.Image:
    """Flatten alpha onto white."""
    if img.hasalpha():
        white = _WHITE_BY_FORMAT.get(img.format, 255)
        img = img.flatten(background=[white] * (img.bands - 1))
    return img


def _clip_pct(img: pyvips.Image) -> tuple[float, float]:
    """% of samples that are black / white at 8 bits (below 0.5/255 or above 254.5/255 of full scale)."""
    full = _WHITE_BY_FORMAT.get(img.format) or (2 ** _BITS_BY_FORMAT[img.format] - 1)
    masks = img.relational_const("less", [full * 0.5 / 255]).bandjoin(
        img.relational_const("more", [full * 254.5 / 255])
    )
    # one pass; row 1 + b is band b, column 4 its mean (masks are 0 or 255)
    st = masks.stats()
    n = img.bands
    means = [st(4, 1 + b)[0] / 2.55 for b in range(2 * n)]
    return sum(means[:n]) / n, sum(means[n:]) / n


def _tone_check(
    src: tuple[float, float] | None, out: tuple[float, float], check: dict
) -> dict:
    """Compare clipping before and after conversion against the profile's tolerance.

    `within_tolerance` is None when the source isn't grey or RGB (e.g. CMYK), or the
    profile sets no tolerance.
    """
    tol = check.get("max_clip_increase_pct")
    tone = {
        "src_black_pct": None if src is None else round(src[0], 3),
        "src_white_pct": None if src is None else round(src[1], 3),
        "out_black_pct": round(out[0], 3),
        "out_white_pct": round(out[1], 3),
        "max_clip_increase_pct": tol,
        "within_tolerance": None,
    }
    if src is not None and tol is not None:
        tone["within_tolerance"] = (
            out[0] - src[0] <= tol + 1e-9 and out[1] - src[1] <= tol + 1e-9
        )
    return tone


def _to_srgb8(img: pyvips.Image, settings: dict) -> pyvips.Image:
    """Alpha onto white, embedded ICC -> sRGB (else assume sRGB), 8-bit, 3 bands."""
    img = _flatten(img)
    if img.get_typeof("icc-profile-data"):
        img = img.icc_transform(
            "srgb", embedded=True, intent=settings["icc_intent"], depth=8
        )
    else:
        img = _fix_interpretation(img).colourspace("srgb")
    if img.bands > 3:
        img = img.extract_band(0, n=3)
    elif img.bands == 1:
        img = img.bandjoin([img, img])
    if img.format != "uchar":
        img = img.cast("uchar")
    return img.copy(interpretation="srgb")


def _fix_interpretation(img: pyvips.Image) -> pyvips.Image:
    """Make the interpretation match the band count and depth, so colourspace() maps 16 -> 8 bit."""
    sixteen = img.format in ("ushort", "short")
    if img.bands < 3:
        want = "grey16" if sixteen else "b-w"
        return img if img.interpretation == want else img.copy(interpretation=want)
    if (
        img.interpretation in ("srgb", "rgb16")
        or img.interpretation not in _COLOUR_INTERPRETATIONS
    ):
        want = "rgb16" if sixteen else "srgb"
        return img if img.interpretation == want else img.copy(interpretation=want)
    return img


# --- the derive run ---------------------------------------------------------------


def _check_locations(cfg: Config, profile: str) -> None:
    """Refuse /Volumes for reads and writes, and writes under any source root (docs/pilot.md)."""
    check_write_location(cfg, profile_dir(cfg, profile))
    for src in cfg.sources.values():
        check_source_root(src)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _append_index(cfg: Config, profile: str, rec: dict) -> None:
    path = index_path(cfg, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        # A cut-short last line gets its own line ended, so this record stays parseable.
        lead = b""
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            lead = b"" if f.read(1) == b"\n" else b"\n"
        f.write(lead + (json.dumps(rec, sort_keys=False) + "\n").encode())
        f.flush()
        os.fsync(f.fileno())


def _is_current(
    cfg: Config, profile: str, rec: dict | None, shash: str, tools: dict
) -> bool:
    """An ok record made with these settings and tool versions, whose file is intact."""
    if not rec or rec.get("status") != "ok":
        return False
    if rec.get("settings_hash") != shash or rec.get("tools") != tools:
        return False
    path = derived_path(cfg, profile, rec["asset_id"])
    return path.exists() and ids.bytes_id(path.read_bytes()) == rec.get(
        "derived_blake3"
    )


def _short(e: BaseException) -> str:
    return " ".join(f"{type(e).__name__}: {e}".split())[:500]


def _check_settings_unchanged(index: dict[str, dict], profile: str, shash: str) -> None:
    """A profile's settings may not change under the same name (spec inference-images)."""
    old = {
        r.get("settings_hash")
        for r in index.values()
        if r.get("status") == "ok" and r.get("settings_hash")
    } - {shash}
    if old:
        raise ValueError(
            f"profile {profile!r} settings changed since its images were derived "
            f"(settings_hash {sorted(old)} -> {shash}); give the changed profile a new "
            "name, or delete the profile's derived directory to rebuild a provisional one"
        )


def derive_one(
    cfg: Config, profile: str, settings: dict, item: dict, tools: dict
) -> dict:
    """Derive one item and return its index record (status ok or error); writes the image only on ok."""
    aid = item["asset_id"]
    ext = item.get("ext") or Path(item.get("relpath", "")).suffix.lstrip(".")
    rec = {
        "asset_id": aid,
        "profile": profile,
        "status": "error",
        "path": None,
        "derived_blake3": None,
        "width": None,
        "height": None,
        "src_width": None,
        "src_height": None,
        "src_bits": None,
        "src_kind": KIND_BY_EXT.get(ext.lower()),
        "rendered_from": None,
        "error": None,
        "tools": tools,
        "settings_hash": settings_hash(settings),
        "tone": None,
    }
    try:
        data = original_path(cfg, item).read_bytes()
        if ids.bytes_id(data) != aid:
            raise DecodeError(
                "original's blake3 does not match the item's asset_id (file changed?)"
            )
        r = render(data, ext, settings)
    except Exception as e:  # noqa: BLE001 - any decode failure becomes an error record
        rec["error"] = _short(e)
        return rec
    out = derived_path(cfg, profile, aid)
    _atomic_write(out, r.jpeg)
    rec.update(
        status="ok",
        path=out.relative_to(cfg.shoebox_root).as_posix(),
        derived_blake3=ids.bytes_id(r.jpeg),
        width=r.width,
        height=r.height,
        src_width=r.src_width,
        src_height=r.src_height,
        src_bits=r.src_bits,
        src_kind=r.src_kind,
        rendered_from=r.rendered_from,
        tone=r.tone,
    )
    return rec


def derive_items(
    cfg: Config,
    profile: str,
    items: Iterable[dict],
    force: bool = False,
    log: Callable[[str], None] | None = None,
    workers: int = 1,
) -> Counts:
    """Derive every item, appending one index record per attempt; skips current ok records unless `force`.

    `workers` > 1 derives that many items at once on a thread pool (libvips and LibRaw release the GIL).
    Index appends, counts and log lines are serialised by a lock, so each record stays one whole line.
    Each worker holds one original in memory at a time.
    """
    if workers < 1:
        raise ValueError(f"workers must be at least 1, not {workers}")
    _check_locations(cfg, profile)
    settings = load_profile(profile)
    shash = settings_hash(settings)
    tools = tool_versions()
    pyvips.cache_set_max(0)  # don't hold decoded originals between items
    index = load_index(cfg, profile)
    _check_settings_unchanged(index, profile, shash)
    counts = Counts()
    lock = threading.Lock()

    def one(item: dict) -> None:
        aid = item["asset_id"]
        if not force and _is_current(cfg, profile, index.get(aid), shash, tools):
            with lock:
                counts.skipped += 1
                if log:
                    log(f"skip  {aid[:12]}  {item.get('relpath', '')}")
            return
        t0 = time.monotonic()
        rec = derive_one(cfg, profile, settings, item, tools)
        with lock:
            _append_index(cfg, profile, rec)
            index[aid] = rec
            secs = time.monotonic() - t0
            if rec["status"] == "ok":
                counts.ok += 1
                msg = f"ok    {aid[:12]}  {item.get('relpath', '')}  {rec['width']}x{rec['height']}  {secs:.1f}s"
                if rec["tone"]["within_tolerance"] is False:
                    counts.flagged += 1
                    t = rec["tone"]
                    msg += (
                        f"  CLIP black {t['src_black_pct']}->{t['out_black_pct']}%"
                        f" white {t['src_white_pct']}->{t['out_white_pct']}%"
                    )
            else:
                counts.error += 1
                msg = f"ERROR {aid[:12]}  {item.get('relpath', '')}  {rec['error']}"
            if log:
                log(msg)

    if workers == 1:
        for item in items:
            one(item)
        return counts
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in [pool.submit(one, item) for item in items]:
            fut.result()
    return counts
