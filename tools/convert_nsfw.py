# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "opennsfw2[tf-keras]==0.19.0",
#   "tensorflow==2.21.0",
#   "coremltools==9.0",
#   "numpy",
# ]
# ///
"""Convert OpenNSFW2 to Core ML for the local nudity screen (design D2, task 4.1).

Run once, in its own throwaway environment (TensorFlow never enters the project's deps):

    uv run tools/convert_nsfw.py --out ~/mnt/shoebox/models

It downloads the pre-trained weights, builds the Keras model, converts it to an ML Program
`.mlpackage`, checks the converted model against the Keras model on synthetic inputs, and
prints the package hash to pin in `config/candidates.d/screen.toml`.

The model takes one 224x224x3 float32 image in OpenNSFW's "Yahoo" preprocessing (BGR,
VGG means subtracted; `shoebox.runners.screen` does that with pyvips) and returns the
softmax `[sfw, nsfw]`. Only a yes/no derived from it is ever stored.

Licences (checked 2026-10-11):
- opennsfw2 code (github.com/bhky/opennsfw2): MIT, Copyright (c) 2021 Bosco Yung.
- The weights are Yahoo's open_nsfw model (github.com/yahoo/open_nsfw) ported to Keras:
  BSD 2-Clause, Copyright 2016 Yahoo Inc. Redistribution in binary form must keep the
  notice; the converted package is kept privately under <shoebox_root>/models/ and is
  not redistributed. A NOTICE file with the licence goes next to it anyway.
"""

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")  # opennsfw2's tf-keras path

import numpy as np  # noqa: E402

WEIGHTS_URL = "https://github.com/bhky/opennsfw2/releases/download/v0.1.0/open_nsfw_weights.h5"
NAME = "opennsfw2-v1"
TOLERANCE = 1e-3
NOTICE = """\
nsfw model: opennsfw2 (MIT, Copyright (c) 2021 Bosco Yung), weights from Yahoo open_nsfw.

Copyright 2016, Yahoo Inc. Redistribution and use in source and binary forms, with or
without modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this list of
conditions and the following disclaimer. 2. Redistributions in binary form must
reproduce the above copyright notice, this list of conditions and the following
disclaimer in the documentation and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY
EXPRESS OR IMPLIED WARRANTIES ARE DISCLAIMED.
"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package_hash(pkg: Path) -> str:
    """sha256 over every file in the package: relative path, NUL, bytes; sorted by path.

    Same function as `shoebox.runners.screen.package_hash`, which checks the pin at run time.
    """
    h = hashlib.sha256()
    for p in sorted(q for q in pkg.rglob("*") if q.is_file()):
        h.update(p.relative_to(pkg).as_posix().encode() + b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def synthetic_inputs(n: int = 5) -> np.ndarray:
    """Preprocessed-range inputs: noise, gradients and flat fields, BGR minus VGG means."""
    rng = np.random.default_rng(0)
    out = []
    for i in range(n):
        if i % 3 == 0:
            img = rng.uniform(0, 255, (224, 224, 3))
        elif i % 3 == 1:
            ramp = np.linspace(0, 255, 224)
            img = np.stack([np.add.outer(ramp, ramp) / 2] * 3, axis=-1)
            img[..., i % 3] = 255 - img[..., i % 3]
        else:
            img = np.full((224, 224, 3), rng.uniform(0, 255, 3))
        out.append(img - np.array([104, 117, 123]))
    return np.asarray(out, dtype=np.float32)


def raised_inputs(model, tf, targets=(0.2, 0.5, 0.9)) -> np.ndarray:
    """Synthetic inputs pushed by gradient ascent until the Keras nsfw score passes each
    target, so the check covers the threshold region, not just near-zero scores."""
    x = tf.Variable(synthetic_inputs(1))
    out = []
    for target in targets:
        for _ in range(300):
            with tf.GradientTape() as tape:
                p = model(x, training=False)[0, 1]
            if float(p) >= target:
                break
            g = tape.gradient(p, x)
            x.assign(tf.clip_by_value(x + 2.0 * tf.sign(g), -123.0, 151.0))
        out.append(x.numpy()[0].copy())
    return np.asarray(out, dtype=np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True, help="<shoebox_root>/models")
    ap.add_argument("--precision", choices=("float16", "float32"), default="float16")
    args = ap.parse_args()

    import coremltools as ct
    import opennsfw2

    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    weights = out / "src" / "open_nsfw_weights.h5"
    if not weights.exists():
        weights.parent.mkdir(parents=True, exist_ok=True)
        tmp = weights.with_suffix(".part")
        urllib.request.urlretrieve(WEIGHTS_URL, tmp)
        if tmp.read_bytes()[:8] != b"\x89HDF\r\n\x1a\n":
            tmp.unlink()
            sys.exit(f"download from {WEIGHTS_URL} is not an HDF5 file")
        tmp.rename(weights)
    print(f"weights sha256 {sha256_file(weights)}")

    model = opennsfw2.make_open_nsfw_model(weights_path=str(weights))
    precision = ct.precision.FLOAT16 if args.precision == "float16" else ct.precision.FLOAT32
    import tensorflow as tf

    # opennsfw2 builds a Keras 3 model, which coremltools 9 doesn't take directly; a
    # concrete function of the same graph converts.
    fn = tf.function(lambda x: model(x, training=False)).get_concrete_function(
        tf.TensorSpec((1, 224, 224, 3), tf.float32, name="input")
    )
    ml = ct.convert(
        [fn],
        source="tensorflow",
        convert_to="mlprogram",
        inputs=[ct.TensorType(name="input", shape=(1, 224, 224, 3), dtype=np.float32)],
        compute_precision=precision,
        minimum_deployment_target=ct.target.macOS13,
    )
    ml.short_description = "OpenNSFW2 (Yahoo open_nsfw weights): softmax [sfw, nsfw]"
    pkg = out / f"{NAME}-{args.precision}.mlpackage"
    if pkg.exists():
        sys.exit(f"{pkg} exists; remove it first to reconvert")
    ml.save(str(pkg))
    (out / f"{NAME}-NOTICE.txt").write_text(NOTICE)

    xs = np.concatenate([synthetic_inputs(), raised_inputs(model, tf)])
    want = model.predict(xs, verbose=0)[:, 1]
    if want.max() < max(0.5, TOLERANCE):
        sys.exit("synthetic inputs never reached a high nsfw score; check not meaningful")
    loaded = ct.models.MLModel(str(pkg), compute_units=ct.ComputeUnit.ALL)
    out_name = loaded.get_spec().description.output[0].name
    got = np.array([loaded.predict({"input": x[None]})[out_name].reshape(-1)[1] for x in xs])
    diff = float(np.max(np.abs(got - want)))
    print(f"keras nsfw  {np.round(want, 5).tolist()}")
    print(f"coreml nsfw {np.round(got, 5).tolist()}")
    print(f"max abs diff {diff:.2e} (tolerance {TOLERANCE:g}); output name {out_name!r}")
    if diff > TOLERANCE:
        print("FAIL: converted model does not match; try --precision float32")
        return 1
    print(f"package {pkg.name}")
    print(f"package sha256 {package_hash(pkg)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
