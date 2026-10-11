"""Screen runtime: the local nudity screen (design D2, spec content-screen).

Reads each asset's `infer-v1` derived JPEG, preprocesses it with pyvips the way OpenNSFW's "Yahoo"
pipeline does, scores it with the pinned Core ML package (`ComputeUnit.ALL`, in-process, no network),
and stores only `{asset_id, model_id, params_hash, status, sensitive}` under
`results/screen/<model_id>/<params_hash>/`. The score is compared with the threshold and dropped: it is
never stored, returned or logged. `sensitive` is `score >= threshold` (the boundary errs towards
quarantine). Flagged assets are queued in the quarantine (`quarantine.flag(..., "nudity")`).

An asset with no ok derived image, or one made with other profile settings/tools, gets an `error` record
(`sensitive: null`), retried on the next run. A final `ok` record is a cache hit: no model load, no call.
"""

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

import numpy as np

from shoebox import quarantine, store
from shoebox.config import Config

VGG_MEANS_BGR = np.array([104.0, 117.0, 123.0], dtype=np.float32)
INPUT_NAME = "input"
OUTPUT_NAME = "Identity"


class Model(Protocol):
    def predict(self, x: np.ndarray) -> float:
        """The nsfw probability for one preprocessed (1, 224, 224, 3) float32 image."""


def runtime_version(cand: dict) -> str:
    return f"coremltools {version('coremltools')}"


def package_hash(pkg: Path) -> str:
    """sha256 over every file in the package: relative path, NUL, bytes; sorted by path.
    Same function as `tools/convert_nsfw.py:package_hash`, which printed the pinned value."""
    h = hashlib.sha256()
    for p in sorted(q for q in pkg.rglob("*") if q.is_file()):
        h.update(p.relative_to(pkg).as_posix().encode() + b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


@dataclass
class CoreMLModel:
    ml: object

    @classmethod
    def load(cls, pkg: Path, pinned_sha256: str, compute_units: str = "ALL") -> "CoreMLModel":
        got = package_hash(pkg)
        if got != pinned_sha256:
            raise RuntimeError(f"{pkg.name}: package hash {got[:12]} does not match the pinned "
                               f"{pinned_sha256[:12]}; refusing to load")
        import coremltools as ct

        return cls(ct.models.MLModel(str(pkg), compute_units=getattr(ct.ComputeUnit, compute_units)))

    def predict(self, x: np.ndarray) -> float:
        return float(np.asarray(self.ml.predict({INPUT_NAME: x})[OUTPUT_NAME]).reshape(-1)[1])


def load_model(cand: dict, cfg: Config) -> Model:
    """The candidate's Core ML package from `<shoebox_root>/models/`, hash-checked."""
    return CoreMLModel.load(cfg.shoebox_root / "models" / cand["package"], cand["package_sha256"],
                            cand.get("compute_units", "ALL"))


def preprocess(path: Path) -> np.ndarray:
    """OpenNSFW "Yahoo" preprocessing: RGB, 256x256 bilinear, JPEG re-encode (q75, as PIL's default),
    centre 224 crop, RGB->BGR, minus VGG means. Returns (1, 224, 224, 3) float32."""
    import pyvips

    img = pyvips.Image.new_from_file(str(path), access="sequential")
    if img.hasalpha():
        img = img.flatten(background=[255, 255, 255])
    img = img.colourspace("srgb")
    if img.bands == 1:
        img = img.bandjoin([img, img])
    img = img[:3]
    img = img.resize(256 / img.width, vscale=256 / img.height, kernel="linear")
    img = pyvips.Image.new_from_buffer(img.jpegsave_buffer(Q=75), "")
    img = img.crop(16, 16, 224, 224)
    arr = np.ndarray(buffer=img.write_to_memory(), dtype=np.uint8, shape=(224, 224, img.bands))[..., :3]
    x = arr[..., ::-1].astype(np.float32) - VGG_MEANS_BGR
    return x[None]


@dataclass
class ScreenTally:
    screened: int = 0   # records written this run (ok or error)
    flagged: int = 0    # of those, sensitive
    cached: int = 0     # already final in the store
    errors: int = 0     # error records written this run
    queued: int = 0     # pending rows appended (an asset already in the quarantine, or a same-pixel copy of a
                        # ruled one, is not re-queued)


def _record(res, asset_id: str, sensitive: bool | None, error: str | None = None) -> dict:
    rec = {"asset_id": asset_id, "model_id": res.model_id, "params_hash": res.params_hash,
           "status": "error" if error else "ok", "sensitive": sensitive}
    if error:
        rec["error"] = error
    return rec


def screen_items(res, items: list[dict], cfg: Config, model: Model | None = None, workers: int = 1,
                 quarantine_path: Path | None = None, groups: quarantine.Groups | None = None,
                 progress: Callable[[ScreenTally], None] | None = None) -> ScreenTally:
    """Screen every item not already final under `res`. `model` defaults to `load_model` (loaded only if
    something needs screening). `workers` threads read and preprocess images; scoring is sequential.
    `groups` (default: same-pixel copies from the pixel-hash store, loaded on the first flag) lets a flagged
    copy of an already-ruled asset inherit that verdict instead of being re-queued (`quarantine.flag`)."""
    from shoebox import derive
    from shoebox.runners import _derived_problem, _params_doc, derived_image

    tally = ScreenTally()
    done = store.index(cfg, res.model_id, res.params_hash, res.kind)
    profile = res.cand.get("profile", "infer-v1")
    derived = derive.load_index(cfg, profile)
    threshold = float(res.cand["threshold"])
    pending: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for it in items:
        aid = it["asset_id"]
        if aid in seen:
            continue
        seen.add(aid)
        if store.is_final(done.get(aid)):
            tally.cached += 1
        else:
            pending.append((aid, derived_image(cfg, profile, aid)))
    if not pending:
        return tally
    store.write_params(cfg, res.model_id, res.params_hash, _params_doc(res), res.kind)
    if model is None:
        model = load_model(res.cand, cfg)

    def prepare(job: tuple[str, Path]) -> tuple[str, np.ndarray | None, str | None]:
        aid, image = job
        why = _derived_problem(res, derived.get(aid), image)
        if why:
            return aid, None, why
        try:
            return aid, preprocess(image), None
        except Exception as e:  # noqa: BLE001 - an unreadable image is a retryable error record
            return aid, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for aid, x, err in pool.map(prepare, pending):
            if err is None:
                try:
                    sensitive = model.predict(x) >= threshold  # the score goes no further than this line
                except Exception as e:  # noqa: BLE001 - a model failure is retryable, never a verdict
                    err = f"{type(e).__name__}: {e}"
            if err is not None:
                store.append(cfg, _record(res, aid, None, err), res.kind)
                tally.errors += 1
            else:
                if sensitive:  # quarantine before the record, so a crash between them can't lose a flag
                    if groups is None:
                        from shoebox import copies

                        groups = copies.CopyIndex.build([], copies.load_pixel_keys(cfg)).pixel_group
                    tally.queued += quarantine.flag([aid], "nudity", quarantine_path, groups=groups)
                    tally.flagged += 1
                store.append(cfg, _record(res, aid, bool(sensitive)), res.kind)
            tally.screened += 1
            if progress:
                progress(tally)
    return tally
