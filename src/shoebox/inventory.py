"""The sampling frame (design D4): `shoebox inventory` walks the sources read-only, one row per file.

Reading rules (docs/pilot.md): originals are read only through the configured roots, resolved with
`config.original_path`; outputs go through `config.check_write_location`. Only image and video files,
and Takeout JSON sidecars next to media, are opened; every other file is recorded from `stat` alone.
ExifTool is run read-only (`-j -n -G1 -struct -fast2`, never a write option).

Listing cross-check: the macOS SMB client returns truncated listings that vary from run to run for
folders with more than about 1000 entries. Each folder is listed repeatedly and the union taken,
until a listing adds nothing new (two in a row when any listing had >= 500 entries), capped at 8.

Best date and its kind, in order:
1. `scan`: the file looks like a scan (film/flatbed scanner per `rules.classify_origin`, or a generic
   scanner Make/Model/Software with no camera exposure tags) and has an EXIF DateTimeOriginal,
   CreateDate or ModifyDate (the TIFF DateTime tag). That is when it was scanned, not taken.
2. `exif`: DateTimeOriginal, else CreationDate/CreateDate (images and videos; QuickTime is UTC).
3. `sidecar`: a Google Takeout JSON sidecar's `photoTakenTime` (UTC).
4. `filename`: a date in the file name (20190704_123456, IMG-20190704-WA0001, 2019-07-04).
5. `mtime`: the file's modification time (UTC).
EXIF and file-name dates are local wall-clock times without a zone, as recorded.
"""

import io
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import blake3
import pyvips

from shoebox import derive, ids
from shoebox.config import (
    Config,
    Source,
    check_source_root,
    check_write_location,
    original_path,
)
from shoebox.pilot.select import assign_groups
from shoebox.pilot.untouched import check_read_only, diff_snapshots, measure, remeasure
from shoebox.runners.rules import (
    GENERIC_SCAN,
    Header,
    chroma_stats,
    classify_colour,
    classify_origin,
)

IMAGE_EXTS = frozenset(
    ["jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "dng", "nef", "cr2", "cr3", "arw", "orf", "rw2", "raf", "psd", "gif", "bmp"]
)
VIDEO_EXTS = frozenset(["mp4", "mov", "m4v", "avi", "mts", "3gp", "mkv", "wmv", "mpg"])
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
RAW_EXTS = frozenset(e for e, k in derive.KIND_BY_EXT.items() if k == "raw")
SRC_KIND = {**derive.KIND_BY_EXT, "gif": "gif", "bmp": "bmp", **dict.fromkeys(VIDEO_EXTS, "video")}

SKIP_DIRS = frozenset({"#recycle", "$RECYCLE.BIN", ".tmp.drivedownload", "@eaDir", ".Spotlight-V100",
                       ".Trashes", ".fseventsd"})
SKIP_FILES = {".ds_store": ".DS_Store", "thumbs.db": "Thumbs.db", "desktop.ini": "desktop.ini"}
SKIP_EXAMPLES = 5

LIST_CAP = 8
LIST_BIG = 500
EXIF_BATCH = 200
SAMPLE_N = 200
SIDECAR_MAX_BYTES = 4 << 20
THUMB = 256
# Dropped from the stored ExifTool record: where the file is (already columns) and tool noise.
# ExifTool reads a local temporary copy (see `probe`), so every System: tag describes that copy,
# not the original; the inventory records the original's own name, size and mtime itself.
EXIF_DROP = {"SourceFile", "ExifTool:ExifToolVersion"}
EXIF_DROP_GROUPS = ("System:",)
TMP_CAP_BYTES = 8 << 30  # stop starting probes while this much copy data awaits ExifTool
_CHUNK = 8 << 20

Log = Callable[[str], None]
Lister = Callable[[Path], list[os.DirEntry]]


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --- listing ---------------------------------------------------------------------------------------

def scandir_list(path: Path) -> list[os.DirEntry]:
    with os.scandir(path) as it:
        return list(it)


@dataclass
class DirListing:
    entries: dict[str, os.DirEntry]  # union of every listing
    counts: list[int]  # entries in each listing taken
    differed: bool  # some listing differed from the first


def stable_listing(path: Path, lister: Lister = scandir_list, cap: int = LIST_CAP, big: int = LIST_BIG) -> DirListing:
    """List `path` until a listing adds nothing new (two in a row once any listing had >= `big` entries)."""
    union: dict[str, os.DirEntry] = {}
    counts: list[int] = []
    first: set[str] | None = None
    differed = False
    quiet = 0
    while len(counts) < cap:
        entries = lister(path)
        names = {e.name for e in entries}
        counts.append(len(names))
        if first is None:
            first = names
        elif names != first:
            differed = True
        new = [e for e in entries if e.name not in union]
        for e in new:
            union[e.name] = e
        if len(counts) > 1:
            quiet = 0 if new else quiet + 1
        if quiet >= (2 if max(counts) >= big else 1):
            break
    return DirListing(union, counts, differed)


# --- walk ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FileStat:
    source: str
    relpath: str
    bytes: int
    mtime: float
    kind: str  # image | video | sidecar | other
    sidecar_for: str | None = None  # the media file name a Takeout JSON belongs to

    @property
    def ext(self) -> str:
        return PurePosixPath(self.relpath).suffix.lower().lstrip(".")

    @property
    def key(self) -> tuple:
        return (self.source, self.relpath, self.bytes, self.mtime)


@dataclass
class WalkResult:
    files: list[FileStat] = field(default_factory=list)
    dirs: dict[str, list[str]] = field(default_factory=dict)  # rel -> sorted union of names
    dir_log: list[dict] = field(default_factory=list)
    skips: Counter = field(default_factory=Counter)
    skip_examples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    errors: list[str] = field(default_factory=list)


def skip_reason(name: str, is_dir: bool) -> str | None:
    if is_dir:
        if name in SKIP_DIRS:
            return name
        if name.lower().endswith(".lrdata"):
            return "*.lrdata"
        return None
    if name.startswith("._"):
        return "._*"
    return SKIP_FILES.get(name.lower())


def classify_kind(name: str, media_names: set[str]) -> tuple[str, str | None]:
    """(kind, sidecar_for) by extension; `media_names` are the folder's media file names, lowercased."""
    ext = PurePosixPath(name).suffix.lower().lstrip(".")
    if ext in IMAGE_EXTS:
        return "image", None
    if ext in VIDEO_EXTS:
        return "video", None
    low = name.lower()
    if ext == "json":
        # IMG.jpg.json, IMG.jpg.supplemental-metadata.json (and Takeout's truncated variants)
        base = low[:-5]
        for i, ch in enumerate(base):
            if ch == "." and base[:i] in media_names:
                return "sidecar", base[:i]
        if base in media_names:
            return "sidecar", base
    if ext == "xmp":
        stem = low[:-4]
        stems = {PurePosixPath(m).stem for m in media_names}
        if stem in media_names or stem in stems:
            return "sidecar", None
    return "other", None


def walk_source(source: str, root: Path, starts: Iterable[str] = ("",), lister: Lister = scandir_list,
                log: Log | None = None) -> WalkResult:
    """Every file under `root/start` (symlinks are not followed), with listing stability per folder."""
    res = WalkResult()
    stack = sorted({s.strip("/") for s in starts}, reverse=True)
    while stack:
        rel = stack.pop()
        try:
            lst = stable_listing(root / rel, lister)
        except (FileNotFoundError, NotADirectoryError):
            res.errors.append(f"folder missing: {rel or '.'}")
            continue
        except PermissionError as e:
            res.errors.append(f"folder unreadable: {rel or '.'} ({e.strerror})")
            continue
        res.dirs[rel] = sorted(lst.entries)
        res.dir_log.append({"dir": rel, "listings": len(lst.counts), "count": len(lst.entries),
                            "differed": lst.differed, "counts": lst.counts})
        if lst.differed and log:
            log(f"listing unstable: {rel or '.'} counts {lst.counts} -> union {len(lst.entries)}")
        entries = [lst.entries[n] for n in sorted(lst.entries)]
        media = {e.name.lower() for e in entries
                 if PurePosixPath(e.name).suffix.lower().lstrip(".") in MEDIA_EXTS}
        subdirs = []
        for e in entries:
            child = f"{rel}/{e.name}" if rel else e.name
            try:
                is_link = e.is_symlink()
                is_dir = not is_link and e.is_dir(follow_symlinks=False)
            except OSError as err:
                res.errors.append(f"entry unreadable: {child} ({err.strerror})")
                continue
            reason = "symlink" if is_link else skip_reason(e.name, is_dir)
            if reason:
                res.skips[reason] += 1
                if len(res.skip_examples[reason]) < SKIP_EXAMPLES:
                    res.skip_examples[reason].append(child)
                continue
            if is_dir:
                subdirs.append(child)
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as err:
                res.errors.append(f"file unreadable: {child} ({err.strerror})")
                continue
            kind, side = classify_kind(e.name, media)
            res.files.append(FileStat(source, child, st.st_size, st.st_mtime, kind, side))
        stack.extend(sorted(subdirs, reverse=True))
    res.files.sort(key=lambda f: f.relpath)
    return res


# --- per-file probe ----------------------------------------------------------------------------------

def _vips_bits(img: pyvips.Image) -> int:
    if img.get_typeof("bits-per-sample"):
        return int(img.get("bits-per-sample"))
    return derive._BITS_BY_FORMAT.get(img.format, 8)


def _is_grey(img: pyvips.Image) -> bool:
    return img.interpretation in ("b-w", "grey16") or img.bands in (1, 2)


def _to_thumb(img: pyvips.Image, icc_intent: str) -> pyvips.Image:
    """A <= 256 px 8-bit sRGB image (alpha on white, ICC applied) from a decoded image."""
    return derive._to_srgb8(img.thumbnail_image(THUMB, size="down"), {"icc_intent": icc_intent})


def _thumb_raw(data: bytes, settings: dict) -> dict:
    import rawpy

    try:
        with rawpy.imread(io.BytesIO(data)) as raw:
            out = {"width": int(raw.sizes.width), "height": int(raw.sizes.height),
                   "bits": int(raw.white_level).bit_length(), "grey": False}
            try:
                th = raw.extract_thumb()
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                th = None
            if th is not None and th.format == rawpy.ThumbFormat.JPEG:
                out["thumb"] = pyvips.Image.thumbnail_buffer(bytes(th.data), THUMB, size="down")
            elif th is not None and th.format == rawpy.ThumbFormat.BITMAP:
                out["thumb"] = _to_thumb(pyvips.Image.new_from_array(th.data).copy(interpretation="srgb"),
                                         settings["icc_intent"])
            else:
                rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
                out["thumb"] = _to_thumb(pyvips.Image.new_from_array(rgb).copy(interpretation="srgb"),
                                         settings["icc_intent"])
    except rawpy.LibRawError as e:
        raise derive.DecodeError(f"LibRaw: {type(e).__name__}: {e}") from e
    return out


def _thumb_pil(data: bytes) -> dict:
    import numpy as np
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    with Image.open(io.BytesIO(data)) as im:
        grey = im.mode in ("1", "L", "LA", "I", "I;16")
        w, h = im.width, im.height
        im.thumbnail((THUMB, THUMB))
        arr = np.asarray(im.convert("RGB"))
    thumb = pyvips.Image.new_from_array(np.ascontiguousarray(arr)).copy(interpretation="srgb")
    return {"width": w, "height": h, "bits": 8, "grey": grey, "thumb": thumb}


def decode_thumb(data: bytes | None, ext: str, settings: dict, path: Path | None = None) -> dict:
    """Header facts (width, height, bits, grey, src_kind) and a <= 256 px thumbnail of an image.

    `data` is the file's bytes; `None` means too large to hold, so libvips reads `path` instead.
    Raws use their embedded preview (else a half-size LibRaw render); PSDs their merged composite.
    """
    kind = SRC_KIND.get(ext)
    if data is not None and kind == "raw":
        return {**_thumb_raw(data, settings), "src_kind": "raw"}
    if data is not None and kind == "psd":
        img, bits = derive._decode_psd(data)
        return {"width": img.width, "height": img.height, "bits": int(bits), "grey": _is_grey(img),
                "src_kind": "psd", "thumb": _to_thumb(img, settings["icc_intent"])}
    tiff = (data[:4] if data is not None else b"") in derive._TIFF_MAGIC or kind == "tiff"
    opts = {"unlimited": True} if tiff else {}
    try:
        if data is not None:
            head = pyvips.Image.new_from_buffer(data, "", access="sequential", **opts)
            thumb = pyvips.Image.thumbnail_buffer(data, THUMB, size="down",
                                                  option_string="unlimited" if tiff else "")
        else:
            head = pyvips.Image.new_from_file(str(path), access="sequential", **opts)
            thumb = pyvips.Image.thumbnail(str(path), THUMB, size="down")
        thumb = thumb.copy_memory()  # decode now: libvips reports some failures only when pixels are read
    except pyvips.Error:
        # This libvips has no HEVC decoder, so iPhone HEICs go through pillow-heif's libheif.
        if data is None or kind not in ("heif", "gif", "bmp", "webp", "png"):
            raise
        return {**_thumb_pil(data), "src_kind": kind}
    loader = str(head.get("vips-loader")).replace("load_buffer", "").replace("load", "")
    src_kind = {"jpeg": "jpeg", "png": "png", "heif": "heif", "webp": "webp", "tiff": "tiff", "gif": "gif",
                "bmp": "bmp", "magick": kind}.get(loader, kind)
    return {"width": head.width, "height": head.height, "bits": _vips_bits(head), "grey": _is_grey(head),
            "src_kind": src_kind, "thumb": thumb}


def _short(e: BaseException) -> str:
    return " ".join(f"{type(e).__name__}: {e}".split())[:300]


def blank_record(f: FileStat) -> dict:
    return {"source": f.source, "relpath": f.relpath, "bytes": f.bytes, "mtime": f.mtime, "kind": f.kind,
            "ext": f.ext, "sidecar_for": f.sidecar_for, "status": "stat_only", "error": None, "asset_id": None,
            "width": None, "height": None, "bits": None, "src_kind": SRC_KIND.get(f.ext) if f.kind in
            ("image", "video") else None, "grey": None, "chroma_mean": None, "chroma_median": None,
            "chroma_p95": None, "chromatic": None, "hue_r": None, "colour_guess": None, "taken_ts": None,
            "exif": None}


def _copy_and_hash(path: Path, dst: Path, keep_max: int) -> tuple[str, bytes | None]:
    """Stream `path` once: BLAKE3 it and write a local copy at `dst` (for ExifTool).

    Returns the hash and, when the file is at most `keep_max` bytes, its bytes for decoding.
    """
    h = blake3.blake3()
    keep: list[bytes] | None = []
    size = 0
    with open(path, "rb") as src, open(dst, "wb") as out:
        while chunk := src.read(_CHUNK):
            h.update(chunk)
            out.write(chunk)
            size += len(chunk)
            if keep is not None:
                keep.append(chunk)
                if size > keep_max:
                    keep = None
    return h.hexdigest(), (b"".join(keep) if keep is not None else None)


def probe(cfg: Config, f: FileStat, settings: dict, max_read: int, tmp_dir: Path | None = None) -> dict:
    """Read, hash and decode one file (media and Takeout sidecars only; everything else is stat only).

    Media is read from the source exactly once. With `tmp_dir`, that one read also writes a local
    copy (`rec["_tmp"]`) that ExifTool reads instead of the SMB original: ExifTool's many small
    reads over SMB queue behind the probes' bulk reads and ran at about one file a second.
    """
    rec = blank_record(f)
    if f.kind not in ("image", "video") and not (f.kind == "sidecar" and f.ext == "json"):
        return rec
    try:
        path = original_path(cfg, {"source": f.source, "relpath": f.relpath})
        if f.kind == "sidecar":
            if f.bytes > SIDECAR_MAX_BYTES:
                return rec
            rec["status"] = "ok"
            taken = json.loads(path.read_bytes()).get("photoTakenTime") or {}
            ts = taken.get("timestamp") if isinstance(taken, dict) else None
            rec["taken_ts"] = int(ts) if ts not in (None, "") else None
            return rec
        if tmp_dir is not None:
            tmp = Path(tempfile.mkstemp(dir=tmp_dir, suffix="." + (f.ext or "bin"))[1])
            rec["_tmp"] = str(tmp)
            keep = -1 if f.kind == "video" else max_read
            rec["asset_id"], data = _copy_and_hash(path, tmp, keep)
            local = tmp
        elif f.kind == "video" or f.bytes > max_read:
            data, local = None, path
            rec["asset_id"] = ids.asset_id(path)
        else:
            data, local = path.read_bytes(), path  # read once: hash and decode from memory
            rec["asset_id"] = ids.bytes_id(data)
        rec["status"] = "ok"
        if f.kind == "video":
            return rec
        d = decode_thumb(data, f.ext, settings, local)
        stats = chroma_stats(d["thumb"])
        rec.update(width=d["width"], height=d["height"], bits=d["bits"], grey=d["grey"], src_kind=d["src_kind"],
                   chroma_mean=stats["cmean"], chroma_median=stats["c50"], chroma_p95=stats["c95"],
                   chromatic=stats["chromatic"], hue_r=stats["hue_r"],
                   colour_guess=classify_colour(stats, Header(grey=bool(d["grey"])))[0])
    except Exception as e:  # noqa: BLE001 - any read or decode failure becomes an error record
        rec["status"] = "error"
        rec["error"] = _short(e)
    return rec


# --- ExifTool ----------------------------------------------------------------------------------------

def clean_exif(d: dict) -> dict:
    """Drop binary blobs, thumbnails and location fields already held in columns."""
    return {k: v for k, v in d.items()
            if k not in EXIF_DROP and not k.startswith(EXIF_DROP_GROUPS) and not (isinstance(v, str) and v.startswith("(Binary data"))
            and not k.endswith(("ThumbnailImage", "PreviewImage", "JpgFromRaw", "OtherImage"))}


def exiftool_batch(paths: list[Path], exe: str = "exiftool") -> dict[str, dict]:
    """ExifTool JSON per path (read-only options only). Paths are absolute, so none reads as an option."""
    if not paths:
        return {}
    cmd = [exe, "-j", "-n", "-G1", "-struct", "-q", "-q", "-fast2", "-charset", "filename=utf8",
           *[str(p) for p in paths]]
    r = subprocess.run(cmd, capture_output=True, timeout=1800, check=False, stdin=subprocess.DEVNULL)
    if not r.stdout.strip():
        return {}
    return {d.get("SourceFile"): clean_exif(d) for d in json.loads(r.stdout)}


def attach_exif(cfg: Config, recs: list[dict], exe: str) -> list[dict]:
    """ExifTool each record's local copy (`_tmp`, deleted afterwards), else its original."""
    paths = {}
    for r in recs:
        if r.get("_tmp"):
            paths[r["_tmp"]] = r
            continue
        try:
            paths[str(original_path(cfg, r))] = r
        except ValueError as e:
            r["error"] = r["error"] or _short(e)
    try:
        got = exiftool_batch([Path(p) for p in paths], exe)
    except Exception as e:  # noqa: BLE001 - a failed batch leaves exif null and says why
        got = {}
        for r in paths.values():
            r["error"] = r["error"] or f"exiftool: {_short(e)}"
    for p, r in paths.items():
        r["exif"] = got.get(p)
    for r in recs:
        if tmp := r.pop("_tmp", None):
            Path(tmp).unlink(missing_ok=True)
    return recs


# --- dates -------------------------------------------------------------------------------------------

EXIF_DATE = re.compile(r"^(\d{4})[:-](\d{2})[:-](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
FILENAME_DATE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})([-_.]?)(0[1-9]|1[0-2])\2(0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T.]?([01]\d|2[0-3])[-_.]?([0-5]\d)[-_.]?([0-5]\d))?(?!\d)")
MIN_YEAR = 1826


def _iso(parts: tuple, now_year: int) -> str | None:
    y, mo, d, hh, mm, ss = (int(p) if p else 0 for p in parts)
    if not MIN_YEAR <= y <= now_year + 1:
        return None
    try:
        return datetime(y, mo, d, hh, mm, ss).isoformat()
    except ValueError:
        return None


def _tag(exif: dict, name: str, prefer: tuple[str, ...] = ()) -> object:
    for g in prefer:
        if f"{g}:{name}" in exif:
            return exif[f"{g}:{name}"]
    for k, v in exif.items():
        if k.rsplit(":", 1)[-1] == name:
            return v
    return None


def exif_date(exif: dict, names: tuple[str, ...]) -> str | None:
    now_year = datetime.now().year
    for name in names:
        v = _tag(exif, name, ("ExifIFD", "IFD0", "Keys", "QuickTime"))
        m = EXIF_DATE.match(v.strip()) if isinstance(v, str) else None
        if m and (iso := _iso(m.groups(), now_year)):
            return iso
    return None


def filename_date(name: str) -> str | None:
    now_year = datetime.now().year
    for m in FILENAME_DATE.finditer(name):
        g = m.groups()
        if iso := _iso((g[0], g[2], g[3], g[4], g[5], g[6]), now_year):
            return iso
    return None


def gear(exif: dict | None) -> tuple[str, str, str]:
    exif = exif or {}
    return tuple(str(_tag(exif, n, ("IFD0",)) or "").strip() for n in ("Make", "Model", "Software"))


def is_scan(exif: dict | None, relpath: str, ext: str) -> bool:
    """A film/flatbed scanner per the rule baseline, or a scanner tag with no camera exposure tags."""
    if not exif:
        return False
    make, model, software = gear(exif)
    _, _, digitiser, _ = classify_origin(Header(make=make, model=model, software=software), relpath, ext)
    if digitiser in ("film_scanner", "flatbed_scanner"):
        return True
    camera = any(_tag(exif, n) is not None for n in ("ExposureTime", "FNumber", "ISO"))
    return bool(GENERIC_SCAN.search(f"{make} {model} {software}")) and not camera


def best_date(exif: dict | None, sidecar_ts: int | None, relpath: str, ext: str, mtime: float) -> tuple[str, str]:
    """(ISO 8601 date, kind); see the module docstring for the precedence."""
    scan = bool(exif) and is_scan(exif, relpath, ext)
    if scan and (d := exif_date(exif, ("DateTimeOriginal", "CreateDate", "ModifyDate"))):
        return d, "scan"
    if exif and (d := exif_date(exif, ("DateTimeOriginal", "CreationDate", "CreateDate"))):
        return d, "exif"
    if sidecar_ts is not None:
        return datetime.fromtimestamp(sidecar_ts, UTC).isoformat(timespec="seconds"), "sidecar"
    if d := filename_date(PurePosixPath(relpath).name):
        return d, "filename"
    return datetime.fromtimestamp(mtime, UTC).isoformat(timespec="seconds"), "mtime"


# --- frame rows --------------------------------------------------------------------------------------

COLUMNS = ["source", "person", "synced", "relpath", "folder", "name", "ext", "kind", "bytes", "mtime",
           "asset_id", "status", "error", "width", "height", "bits", "src_kind", "date", "date_kind", "make",
           "model", "software", "exif", "chroma_mean", "chroma_median", "chroma_p95", "chromatic", "hue_r",
           "colour_guess", "group_id", "group_rule", "dup_n"]


def schema():
    import pyarrow as pa

    s, i, f = pa.string(), pa.int64(), pa.float64()
    types = {"synced": pa.bool_(), "bytes": i, "mtime": f, "width": i, "height": i, "bits": i, "chroma_mean": f,
             "chroma_median": f, "chroma_p95": f, "chromatic": f, "hue_r": f, "dup_n": i}
    return pa.schema([(c, types.get(c, s)) for c in COLUMNS])


def build_rows(records: list[dict], sources: dict[str, Source]) -> list[dict]:
    """Frame rows from probe records: best date, gear, capture groups and duplicate counts."""
    sidecar_ts = {}
    for r in records:
        if r["kind"] == "sidecar" and r.get("taken_ts") is not None and r.get("sidecar_for"):
            folder = str(PurePosixPath(r["relpath"]).parent)
            sidecar_ts.setdefault((r["source"], folder, r["sidecar_for"]), r["taken_ts"])
    dup = Counter(r["asset_id"] for r in records if r.get("asset_id"))
    rows = []
    for r in records:
        p = PurePosixPath(r["relpath"])
        folder = "" if str(p.parent) == "." else str(p.parent)
        exif = r.get("exif")
        make, model, software = gear(exif)
        if r["kind"] in ("image", "video"):
            date, date_kind = best_date(exif, sidecar_ts.get((r["source"], str(p.parent), p.name.lower())),
                                        r["relpath"], r["ext"], r["mtime"])
        else:
            date, date_kind = None, None
        src = sources[r["source"]]
        rows.append({
            "source": r["source"], "person": src.person, "synced": src.synced, "relpath": r["relpath"],
            "folder": folder, "name": p.name, "ext": r["ext"], "kind": r["kind"], "bytes": r["bytes"],
            "mtime": r["mtime"], "asset_id": r.get("asset_id"), "status": r["status"], "error": r.get("error"),
            "width": r.get("width"), "height": r.get("height"), "bits": r.get("bits"),
            "src_kind": r.get("src_kind"), "date": date, "date_kind": date_kind, "make": make or None,
            "model": model or None, "software": software or None,
            "exif": json.dumps(exif, ensure_ascii=False) if exif is not None else None,
            "chroma_mean": r.get("chroma_mean"), "chroma_median": r.get("chroma_median"),
            "chroma_p95": r.get("chroma_p95"), "chromatic": r.get("chromatic"), "hue_r": r.get("hue_r"),
            "colour_guess": r.get("colour_guess"), "group_id": None, "group_rule": None,
            "dup_n": dup[r["asset_id"]] if r.get("asset_id") else None,
        })
    by_source = defaultdict(list)
    for row in rows:
        if row["asset_id"] and row["kind"] == "image":
            by_source[row["source"]].append(row)
    for members in by_source.values():
        assign_groups(members, raw_exts=RAW_EXTS)
    return rows


def write_parquet(rows: list[dict], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows, schema=schema())
    tmp = path.parent / f".{path.name}.tmp"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


# --- the run -----------------------------------------------------------------------------------------

class SourcesTouched(RuntimeError):
    """The sources-untouched check found a change."""


@dataclass
class Options:
    sources: list[str] | None = None
    only: list[str] = field(default_factory=list)
    workers: int = 8
    date: str = field(default_factory=lambda: datetime.now().date().isoformat())
    out_dir: Path | None = None
    resume: bool = False
    lister: Lister = scandir_list
    exiftool: str = "exiftool"
    # Local scratch for ExifTool's copies (default: a fresh dir under the system temp dir).
    tmp_dir: Path | None = None
    tmp_cap_bytes: int = TMP_CAP_BYTES
    progress_every: int = 500
    sample_n: int = SAMPLE_N
    max_read_bytes: int = 1 << 30
    # Called after the run, before the untouched verify (tests use it to plant a change).
    after_run: Callable[[], None] | None = None


def load_checkpoint(path: Path) -> dict[tuple, dict]:
    """Records by (source, relpath, bytes, mtime); an unparseable (torn) line is skipped."""
    out: dict[tuple, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                out[(r["source"], r["relpath"], r["bytes"], r["mtime"])] = r
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return out


def _open_checkpoint(path: Path):
    f = open(path, "a+b")  # noqa: SIM115 - closed by the caller
    if f.seek(0, os.SEEK_END) > 0:
        f.seek(-1, os.SEEK_END)
        if f.read(1) != b"\n":
            f.write(b"\n")  # end a torn last line so the next record parses
    return f


def _eta(secs: float) -> str:
    secs = int(secs)
    return f"{secs // 3600}h{secs % 3600 // 60:02d}m" if secs >= 3600 else f"{secs // 60}m{secs % 60:02d}s"


def run(cfg: Config, opts: Options, log: Log = _stderr) -> dict:
    """Walk, snapshot, probe, verify untouched; writes the frame parquet and the listing log."""
    names = opts.sources or sorted(cfg.sources)
    unknown = [n for n in names if n not in cfg.sources]
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(unknown)}")
    sel = Config(shoebox_root=cfg.shoebox_root, sources={n: cfg.sources[n] for n in names})
    roots = {}
    for n in names:
        roots[n] = check_source_root(sel.sources[n])
        check_read_only(sel.sources[n])
    only = [o.strip("/") for o in opts.only] or [""]
    for o in only:
        if o.startswith("/") or ".." in PurePosixPath(o).parts:
            raise ValueError(f"--only must be a relative path inside the source: {o}")
    out_dir = Path(opts.out_dir or cfg.shoebox_root / "frame").expanduser()
    check_write_location(sel, out_dir)
    work = out_dir / f"work-{opts.date}"
    check_write_location(sel, work)
    ckpt = work / "records.jsonl"
    if ckpt.exists() and not opts.resume:
        raise FileExistsError(f"{ckpt} exists: pass --resume to continue it, or move it away")
    work.mkdir(parents=True, exist_ok=True)
    frame_path = out_dir / f"frame-{opts.date}.parquet"
    listing_path = out_dir / f"listing-{opts.date}.json"

    t_start = time.monotonic()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    walks: dict[str, WalkResult] = {}
    for n in names:
        log(f"walking {n}: {', '.join(o or '.' for o in only)}")
        walks[n] = walk_source(n, roots[n], only, opts.lister, log)
        log(f"  {len(walks[n].files)} files in {len(walks[n].dirs)} folders, "
            f"{sum(walks[n].skips.values())} skipped entries")
    files = [f for n in names for f in walks[n].files]

    # Sources-untouched snapshot: a seeded sample of media files (hashed) and every listed folder.
    media = sorted((f for f in files if f.kind in ("image", "video")), key=lambda f: (f.source, f.relpath))
    sample = random.Random(f"shoebox-inventory-{opts.date}").sample(media, min(opts.sample_n, len(media)))
    before = measure(sel, (), (), [f"{f.source}:{f.relpath}" for f in sample])
    before["started"] = started
    before["dirs"] = {f"{n}:{rel}": listing for n in names for rel, listing in walks[n].dirs.items()}

    done = load_checkpoint(ckpt) if opts.resume else {}
    todo = [f for f in files if f.key not in done]
    log(f"{len(files)} files, {len(files) - len(todo)} already recorded, {len(todo)} to do")
    settings = derive.load_profile("infer-v1")
    pyvips.cache_set_max(0)
    total_bytes = sum(f.bytes for f in todo if f.kind in ("image", "video"))
    stats = {"files": 0, "bytes": 0, "t0": time.monotonic(), "last": time.monotonic()}

    own_tmp = opts.tmp_dir is None
    tmp_dir = Path(tempfile.mkdtemp(prefix="shoebox-inventory-")) if own_tmp else Path(opts.tmp_dir)
    check_write_location(sel, tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    with _open_checkpoint(ckpt) as out:
        def write(rec: dict) -> None:
            out.write((json.dumps(rec, ensure_ascii=False) + "\n").encode())
            out.flush()
            done[(rec["source"], rec["relpath"], rec["bytes"], rec["mtime"])] = rec
            stats["files"] += 1
            if rec["kind"] in ("image", "video"):
                stats["bytes"] += rec["bytes"]
            now = time.monotonic()
            if stats["files"] % opts.progress_every == 0 or now - stats["last"] > 30:
                stats["last"] = now
                el = max(now - stats["t0"], 1e-6)
                rate = stats["bytes"] / el
                eta = (total_bytes - stats["bytes"]) / rate if rate else 0
                log(f"  {stats['files']}/{len(todo)} files, {stats['bytes'] / 1e9:.1f}/{total_bytes / 1e9:.1f} GB, "
                    f"{rate / 1e6:.1f} MB/s, ETA {_eta(eta)}")

        with ThreadPoolExecutor(max(1, opts.workers)) as pool, ThreadPoolExecutor(2) as xpool:
            it = iter(todo)
            inflight: set[Future] = set()
            exif_jobs: dict[Future, int] = {}  # job -> bytes of local copies it holds
            pending: list[dict] = []
            exhausted = False
            while True:
                held = sum(exif_jobs.values()) + sum(r["bytes"] for r in pending)
                while not exhausted and len(inflight) < opts.workers * 3 and held < opts.tmp_cap_bytes:
                    f = next(it, None)
                    if f is None:
                        exhausted = True
                        break
                    inflight.add(pool.submit(probe, sel, f, settings, opts.max_read_bytes, tmp_dir))
                if pending and (len(pending) >= EXIF_BATCH or not inflight):
                    job = xpool.submit(attach_exif, sel, pending, opts.exiftool)
                    exif_jobs[job] = sum(r["bytes"] for r in pending)
                    pending = []
                if not inflight and not exif_jobs:
                    break
                finished, _ = wait(inflight | set(exif_jobs), return_when=FIRST_COMPLETED)
                for fut in finished:
                    if fut in inflight:
                        inflight.discard(fut)
                        rec = fut.result()
                        if rec["kind"] in ("image", "video") and rec["asset_id"]:
                            pending.append(rec)
                        else:
                            if tmp := rec.pop("_tmp", None):
                                Path(tmp).unlink(missing_ok=True)
                            write(rec)
                    else:
                        exif_jobs.pop(fut)
                        for rec in fut.result():
                            write(rec)

    if own_tmp:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if opts.after_run:
        opts.after_run()
    after = remeasure(sel, before)
    problems = diff_snapshots(before, after)

    records = [done[f.key] for f in files if f.key in done]
    rows = build_rows(records, sel.sources)
    write_parquet(rows, frame_path)
    elapsed = time.monotonic() - t_start
    summary = summarise(rows, walks, elapsed, stats["bytes"])
    listing = {
        "date": opts.date, "started": started, "finished": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": socket.gethostname(), "only": only,
        "sources": {n: {
            "root": str(sel.sources[n].root), "folders": len(w.dirs),
            "unstable_folders": sum(d["differed"] for d in w.dir_log),
            "listings": sum(d["listings"] for d in w.dir_log),
            "skips": dict(w.skips), "skip_examples": dict(w.skip_examples), "errors": w.errors,
            "dirs": w.dir_log,
        } for n, w in walks.items()},
        "untouched": {"sample_files": len(before["files"]), "folders": len(before["dirs"]), "ok": not problems,
                      "problems": problems[:200]},
        "summary": summary,
    }
    tmp = listing_path.parent / f".{listing_path.name}.tmp"
    tmp.write_text(json.dumps(listing, indent=1, ensure_ascii=False))
    os.replace(tmp, listing_path)
    summary.update(frame=str(frame_path), listing=str(listing_path), untouched_ok=not problems)
    if problems:
        raise SourcesTouched("sources-untouched check FAILED:\n  " + "\n  ".join(problems[:50]))
    return summary


def summarise(rows: list[dict], walks: dict[str, WalkResult], elapsed: float, read_bytes: int) -> dict:
    return {
        "files": len(rows),
        "bytes": sum(r["bytes"] for r in rows),
        "by_kind": dict(Counter(r["kind"] for r in rows)),
        "by_status": dict(Counter(r["status"] for r in rows)),
        "decode_errors": sum(r["status"] == "error" for r in rows),
        "skips": dict(sum((w.skips for w in walks.values()), Counter())),
        "walk_errors": sum(len(w.errors) for w in walks.values()),
        "unstable_folders": sum(d["differed"] for w in walks.values() for d in w.dir_log),
        "folders": sum(len(w.dirs) for w in walks.values()),
        "elapsed_s": round(elapsed, 1),
        "read_mb_per_s": round(read_bytes / 1e6 / elapsed, 1) if elapsed else None,
    }
