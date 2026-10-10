"""Rule baseline (`rules-v1`): medium, digitiser and colour class from metadata and pixel statistics.

Signals: the original's header only (EXIF Make/Model/Software, bands, XMP, size), its path, and colour
statistics of the derived infer-v1 image. Ambiguous rules abstain with `unknown`; caption is null, tags [].
"""

import hashlib
import json
import re
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pyvips
from jsonschema import Draft202012Validator

from shoebox.config import original_path

TIFF_EXTS = {"tif", "tiff", "nef", "nrw", "dng", "cr2", "arw", "srf", "sr2", "orf", "rw2", "pef", "srw", "erf",
             "3fr", "iiq", "mef", "mos", "kdc", "dcr"}
# Proprietary camera raws: a camera made the file even if its header is unreadable. DNG is excluded
# because scanner software (e.g. VueScan) can write DNG too.
CAMERA_RAW_EXTS = (TIFF_EXTS - {"tif", "tiff", "dng"}) | {"cr3", "crw", "raf", "x3f"}

# Nikon Coolscan "LS-5000" but not Panasonic "DMC-LS80"; Fuji Frontier "SP-3000"; Kodak "RFS 3600".
FILM_SCANNER = re.compile(
    r"(?<![-\w])ls-?\d{2,4}\b|coolscan|nikon scan|plustek|opticfilm|pacific image|primefilm|reflecta|flextight|"
    r"imacon|dimage scan|scan dual|scan elite|frontier|\bfuji.*\bsp-?\d{3,4}\b|noritsu|\bqss-?\d|pakon|"
    r"\brfs ?\d{4}|sprintscan|fs4000|film ?scan", re.IGNORECASE)
FLATBED = re.compile(
    r"perfection|expression ?\d|canoscan|scanjet|epson scan|scangear|hp scan|officejet|laserjet|deskjet|"
    r"pixma|scansnap|fi-\d|doxie|flatbed|\bmfc-|workforce|ecotank", re.IGNORECASE)
GENERIC_SCAN = re.compile(r"scanner|vuescan|silverfast|\bscan\b", re.IGNORECASE)
PHONE_MAKES = {"google", "huawei", "honor", "xiaomi", "redmi", "oneplus", "oppo", "vivo", "realme", "motorola",
               "htc", "lge", "lg electronics", "zte", "meizu", "nothing", "fairphone", "blackberry", "rim",
               "nokia", "hmd global", "sony ericsson", "sharp", "asus"}
CAMERA_MAKES = ("canon", "nikon", "leica", "fujifilm", "fuji photo", "olympus", "om digital", "panasonic",
                "pentax", "ricoh", "kodak", "eastman kodak", "hasselblad", "sigma", "minolta", "konica",
                "casio", "gopro", "dji", "phase one", "mamiya", "leaf", "sanyo", "sony")
# Makers of both cameras and film/lab scanners: their make alone does not say "camera".
MIXED_MAKES = ("kodak", "eastman kodak", "fuji photo", "fujifilm", "konica", "minolta")
MIXED_CAMERA_MODEL = re.compile(
    r"digital camera|easyshare|pixpro|\bdcs|\bd[cx] ?\d|\bcx ?\d|\b[zvcmp]\d{3,4}\b|finepix|^x-?[a-z]{0,3}\d|"
    r"^xq\d|^xf\d|^gfx|^ds-\d|^mx-\d|^s\dpro|dimage(?! ?scan)|dynax|maxxum|alpha|\bkd-\d|revio",
    re.IGNORECASE)
SONY_PHONE = re.compile(r"xperia|^so-|^xq-|^[cdefghj]\d{4}", re.IGNORECASE)
SAMSUNG_PHONE = re.compile(r"^(sm-|gt-|sgh-|sch-|sph-|galaxy)", re.IGNORECASE)
SCREENSHOT_NAME = re.compile(
    r"screen ?shot|screen_shot|screencap|bildschirmfoto|skjermbilde|sk.rmbillede|schermafbeelding|"
    r"capture d.?.cran|captura de pantalla", re.IGNORECASE)
# Common screen sizes (portrait and landscape both match).
SCREEN_SIZES = {(640, 1136), (750, 1334), (828, 1792), (1080, 1920), (1080, 2340), (1080, 2400),
                (1125, 2436), (1170, 2532), (1179, 2556), (1206, 2622), (1242, 2208), (1242, 2688),
                (1284, 2778), (1290, 2796), (1320, 2868), (1440, 3040), (1440, 3088), (1440, 3200),
                (1536, 2048), (1620, 2160), (1640, 2360), (1668, 2224), (1668, 2388), (2048, 2732),
                (1280, 800), (1366, 768), (1440, 900), (1600, 900), (1680, 1050), (1920, 1080),
                (1920, 1200), (2560, 1440), (2560, 1600), (2880, 1800), (3024, 1964), (3456, 2234),
                (3840, 2160), (5120, 2880)}
NEGATIVE_WORDS = {"negative", "negatives", "negs", "neg", "negativ", "negatieven"}
SLIDE_WORDS = {"slide", "slides", "transparency", "transparencies", "kodachrome", "ektachrome", "diapositive",
               "dias"}
DOCUMENT_WORDS = {"document", "documents", "letter", "letters", "certificate", "certificates", "passport",
                  "receipt", "receipts", "form", "forms"}

_TAGS = {256: "width", 257: "height", 262: "photometric", 271: "make", 272: "model", 277: "samples",
         305: "software"}


@dataclass
class Header:
    make: str = ""
    model: str = ""
    software: str = ""
    width: int = 0
    height: int = 0
    bands: int = 0
    grey: bool = False
    xmp: str = ""


def runtime_version(cand: dict) -> str:
    src = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    return (f"rules-v1 src:{src} libvips {pyvips.version(0)}.{pyvips.version(1)}.{pyvips.version(2)} "
            f"pyvips {version('pyvips')} numpy {np.__version__} pillow {version('pillow')}")


# --- original header ---------------------------------------------------------------------------

def tiff_ifd0(read: Callable[[int, int], bytes]) -> dict:
    """IFD0 tags of a TIFF-structured blob (TIFF, most raws, EXIF). Unknown byte order -> {}."""
    head = read(0, 8)
    if len(head) < 8 or head[:2] not in (b"II", b"MM"):
        return {}
    e = "<" if head[:2] == b"II" else ">"
    off = struct.unpack(e + "I", head[4:8])[0]
    raw_n = read(off, 2)
    if len(raw_n) < 2:
        return {}
    n = struct.unpack(e + "H", raw_n)[0]
    if not 0 < n < 1000:
        return {}
    entries = read(off + 2, 12 * n)
    out: dict = {}
    for i in range(len(entries) // 12):
        tag, typ, cnt = struct.unpack(e + "HHI", entries[12 * i: 12 * i + 8])
        if tag not in _TAGS or typ not in (2, 3, 4):
            continue
        val = entries[12 * i + 8: 12 * i + 12]
        nbytes = {2: 1, 3: 2, 4: 4}[typ] * cnt
        data = val[:nbytes] if nbytes <= 4 else read(struct.unpack(e + "I", val)[0], min(nbytes, 512))
        try:
            if typ == 2:
                out[_TAGS[tag]] = data.split(b"\0")[0].decode("latin-1").strip()
            else:
                out[_TAGS[tag]] = struct.unpack(e + ("H" if typ == 3 else "I"), data[: 2 if typ == 3 else 4])[0]
        except struct.error:
            continue
    return out


def _file_reader(f) -> Callable[[int, int], bytes]:
    def read(off: int, n: int) -> bytes:
        f.seek(off)
        return f.read(n)
    return read


def _from_tiff(path: Path) -> Header:
    with open(path, "rb") as f:
        t = tiff_ifd0(_file_reader(f))
    samples = t.get("samples", 0)
    return Header(make=t.get("make", ""), model=t.get("model", ""), software=t.get("software", ""),
                  width=t.get("width", 0), height=t.get("height", 0), bands=samples,
                  grey=t.get("photometric") in (0, 1) and samples in (0, 1))


def _from_vips(path: Path) -> Header:
    img = pyvips.Image.new_from_file(str(path), access="sequential")  # header only until pixels are asked for
    fields = set(img.get_fields())
    h = Header(width=img.width, height=img.height, bands=img.bands,
               grey=img.interpretation in ("b-w", "grey16") or img.bands in (1, 2))
    if "exif-data" in fields:
        blob = bytes(img.get("exif-data"))
        blob = blob[6:] if blob.startswith(b"Exif\0\0") else blob
        t = tiff_ifd0(lambda off, n: blob[off: off + n])
        h.make, h.model, h.software = t.get("make", ""), t.get("model", ""), t.get("software", "")
    if "xmp-data" in fields:
        h.xmp = bytes(img.get("xmp-data"))[:65536].decode("utf-8", "replace")
    return h


def _from_pil(path: Path) -> Header:
    from PIL import Image

    with Image.open(path) as im:
        ex = im.getexif()
        return Header(make=str(ex.get(271, "")).strip(), model=str(ex.get(272, "")).strip(),
                      software=str(ex.get(305, "")).strip(), width=im.width, height=im.height,
                      bands=len(im.getbands()), grey=im.mode in ("1", "L", "LA", "I", "I;16"))


def _from_psd(path: Path) -> Header:
    """PSD/PSB header plus the EXIF in image resource 1058 (Photoshop 'EXIF data 1')."""
    with open(path, "rb") as f:
        head = f.read(26)
        if head[:4] != b"8BPS":
            return Header()
        channels, height, width, _depth, mode = struct.unpack(">HIIHH", head[12:26])
        h = Header(width=width, height=height, bands=channels, grey=mode in (0, 1))
        (cm_len,) = struct.unpack(">I", f.read(4))
        f.seek(cm_len, 1)
        (res_len,) = struct.unpack(">I", f.read(4))
        res = f.read(min(res_len, 8 << 20))
    i = 0
    while i + 12 <= len(res) and res[i: i + 4] == b"8BIM":
        rid = struct.unpack(">H", res[i + 4: i + 6])[0]
        name_len = res[i + 6]
        i += 6 + ((name_len + 2) & ~1)  # Pascal name padded to an even length
        (size,) = struct.unpack(">I", res[i: i + 4])
        data = res[i + 4: i + 4 + size]
        i += 4 + size + (size & 1)
        if rid == 1058:
            t = tiff_ifd0(lambda off, n, b=data: b[off: off + n])
            h.make, h.model, h.software = t.get("make", ""), t.get("model", ""), t.get("software", "")
    return h


def read_header(path: Path, ext: str) -> Header:
    """Best-effort header of the original; an unreadable header gives an empty Header (no signals)."""
    readers = [_from_tiff] if ext in TIFF_EXTS else [_from_psd] if ext in ("psd", "psb") else []
    readers += [_from_vips, _from_pil] if ext not in CAMERA_RAW_EXTS | {"dng"} else []
    for reader in readers:
        try:
            h = reader(path)
        except Exception:  # noqa: BLE001, S112 - best effort over arbitrary files; next reader
            continue
        if h.make or h.model or h.width:
            return h
    return Header()


# --- rules -----------------------------------------------------------------------------------------

def _words(relpath: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", relpath.lower()))


def classify_origin(h: Header, relpath: str, ext: str) -> tuple[str, float, str, float]:
    """(medium, confidence, digitiser, confidence)."""
    make, model = h.make.lower(), h.model.lower()
    gear = f"{h.make} {h.model} {h.software}"
    words = _words(relpath)
    name = Path(relpath).name

    if SCREENSHOT_NAME.search(name) or "screenshot" in h.xmp.lower():
        return "screen", 0.9, "screenshot", 0.9
    if FILM_SCANNER.search(gear):
        digitiser, dconf = "film_scanner", 0.85
    elif FLATBED.search(gear):
        digitiser, dconf = "flatbed_scanner", 0.8
    elif GENERIC_SCAN.search(gear):
        digitiser, dconf = "unknown", 0.3  # a scan, but of what kind on what is unclear
    elif _is_phone(make, model):
        return "digital", 0.75, "phone", 0.85
    elif make.startswith(MIXED_MAKES) and not MIXED_CAMERA_MODEL.search(model) and ext not in CAMERA_RAW_EXTS:
        return "unknown", 0.3, "unknown", 0.3  # a camera or a scanner from the same maker: abstain
    elif make.startswith(CAMERA_MAKES) or (make == "seiko epson corp." and "r-d1" in model):
        return "digital", 0.75, "camera", 0.85
    elif not make and ext in CAMERA_RAW_EXTS:
        return "digital", 0.7, "camera", 0.75
    elif not make and ext == "png" and tuple(sorted((h.width, h.height))) in {tuple(sorted(s)) for s in SCREEN_SIZES}:
        return "screen", 0.6, "screenshot", 0.6
    else:
        return "unknown", 0.2, "unknown", 0.2

    # a scan: medium only from an explicit origin word in the path
    if words & NEGATIVE_WORDS:
        return "film_negative", 0.6, digitiser, dconf
    if words & SLIDE_WORDS:
        return "film_slide", 0.6, digitiser, dconf
    if digitiser == "flatbed_scanner" and words & DOCUMENT_WORDS:
        return "document", 0.5, digitiser, dconf
    return "unknown", 0.3, digitiser, dconf


def _is_phone(make: str, model: str) -> bool:
    if make.startswith("apple"):
        return any(k in model for k in ("iphone", "ipad", "ipod"))
    if make.startswith("samsung"):
        return bool(SAMSUNG_PHONE.search(model))
    if make.startswith("sony"):
        return bool(SONY_PHONE.search(model))
    return any(make.startswith(p) for p in PHONE_MAKES)


def colour_stats(image_path: Path) -> dict:
    """Chroma statistics in CIELAB over mid-tone pixels of a 256 px thumbnail of the derived image."""
    return chroma_stats(pyvips.Image.thumbnail(str(image_path), 256))


def chroma_stats(img: pyvips.Image) -> dict:
    """`colour_stats` maths over an already-thumbnailed image (also used by `shoebox inventory`)."""
    if img.bands > 3:
        img = img.extract_band(0, n=3)
    lab = img.colourspace("lab").cast("float")
    arr = np.ndarray(buffer=lab.write_to_memory(), dtype=np.float32, shape=(lab.height, lab.width, 3))
    L, a, b = arr[..., 0].ravel(), arr[..., 1].ravel(), arr[..., 2].ravel()
    mid = (L > 8) & (L < 95)
    if mid.sum() < 50:
        mid = np.ones_like(L, dtype=bool)
    a, b = a[mid], b[mid]
    c = np.hypot(a, b)
    chromatic = c > 3
    # hue concentration: length of the chroma-weighted mean hue vector (1 = one hue, 0 = spread)
    hue_r = float(np.hypot(a[chromatic].sum(), b[chromatic].sum()) / c[chromatic].sum()) if chromatic.any() else 0.0
    return {"cmean": float(c.mean()), "c50": float(np.percentile(c, 50)), "c95": float(np.percentile(c, 95)),
            "chromatic": float(chromatic.mean()), "hue_r": hue_r}


def classify_colour(stats: dict, h: Header) -> tuple[str, float]:
    if h.grey:
        return "mono", 0.95
    c50, c95, hue_r = stats["c50"], stats["c95"], stats["hue_r"]
    if c95 < 2:
        return "mono", 0.9
    if c95 < 3.5:
        return "mono", 0.75
    if c95 >= 30:
        return "colour", 0.85
    if hue_r >= 0.92 and c50 >= 6:
        return "toned", 0.7
    if c95 >= 15 and hue_r < 0.75:
        return "colour", 0.75
    return "unknown", 0.3  # e.g. faded colour vs toned vs mono with a scanner cast


def run_one(cand: dict, item: dict, image_path: Path, cfg) -> dict:
    result = {"status": "error", "output": None, "raw": None, "error": None,
              "usage": {"input_tokens": None, "output_tokens": None, "thinking_tokens": None},
              "latency_ms": 0, "usd": 0.0}
    t0 = time.monotonic()
    try:
        original = original_path(cfg, item)
    except ValueError as e:
        return {**result, "error": str(e)}
    if not original.is_file():
        return {**result, "error": f"original not readable: {original}"}
    ext = (item.get("ext") or original.suffix.lstrip(".")).lower()
    header = read_header(original, ext)
    medium, mconf, digitiser, dconf = classify_origin(header, item["relpath"], ext)
    colour, cconf = classify_colour(colour_stats(Path(image_path)), header)
    output = {"caption": None, "tags": [], "medium": medium, "digitiser": digitiser, "colour_class": colour,
              "confidence": {"medium": mconf, "digitiser": dconf, "colour_class": cconf}}
    latency = round((time.monotonic() - t0) * 1000)
    if cand.get("schema_text"):
        errors = sorted(_validator(cand["schema_text"]).iter_errors(output), key=str)
        if errors:
            where = "/".join(str(p) for p in errors[0].absolute_path) or "(root)"
            return {**result, "status": "invalid", "raw": json.dumps(output), "latency_ms": latency,
                    "error": f"schema: {where}: {errors[0].message}"[:500]}
    return {**result, "status": "ok", "output": output, "latency_ms": latency}


@cache
def _validator(schema_text: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_text))
