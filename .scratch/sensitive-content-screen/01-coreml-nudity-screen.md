# Local nudity screen: one Core ML classifier, yes/no only

Status: ready-for-agent
Reported: 2026-10-11
Reporter: mazda (request), filed by claude

## Why

Owner request: filter out nude photos that got into the library by mistake, before they go into the pipeline. Requirements:
- **Simple, fast and Apple-native.** Shoebox always runs on Apple Silicon.
- **No metadata:** a yes/no flag is enough.

It runs locally: these images must never reach a hosted model or Claude.

**Apple's SensitiveContentAnalysis was the first choice** but was dropped on 2026-10-11 (owner: "fallback please"). Its entitlement is restricted. The probe:
- an unsigned CLI sees `analysisPolicy = .disabled`;
- an ad-hoc-signed CLI with `com.apple.developer.sensitivecontentanalysis.client` is killed at launch.

Using it would need a developer provisioning profile and a `.app` wrapper. **What would reopen it:** the owner wants Apple's own model badly enough to set up signing. The runner contract below doesn't change if it is swapped in later.

## What

- **One model, no bake-off.** Use OpenNSFW2: the Yahoo open_nsfw ResNet-50 retrained and ported to Keras, one "nsfw" probability.
  - Verify its licence (the upstream is BSD-2) and pin the weights by hash.
  - If it won't convert cleanly, use a ViT classifier (e.g. `Falconsai/nsfw_image_detection`, Apache-2.0), converted from PyTorch the same way.
- **Convert once to Core ML** with `coremltools`.
  - Commit only the conversion script (`tools/convert_nsfw.py`), not the weights.
  - The `.mlpackage` lives under `<shoebox_root>/models/`, pinned by hash in the candidate config.
- **The runner, `src/shoebox/runners/screen.py`,** is pure Python and needs no Swift and no signing:
  - `coremltools.models.MLModel(..., compute_units=ComputeUnit.ALL)`, so Core ML uses the Neural Engine;
  - input is the `infer-v1` JPEG, resized with pyvips to the model's input size;
  - no network;
  - output is `{asset_id, sensitive: bool}` in the keyed store `results/screen/<candidate>/<params_hash>/`;
  - the threshold is a candidate param, so it is hashed;
  - the score is not kept (owner: no metadata);
  - flags are appended to the quarantine (02).
- **Candidate** `nsfw-coreml-v1` in `config/candidates.d/screen.toml`: model hash, threshold, runner version. `coremltools` is added to `pyproject.toml`.
- **Threshold:**
  - Set it recall-first from the owner's first review batch (03): start low (about 0.2, the open_nsfw "probably safe" bound) and raise it only while no owner-confirmed positive is lost.
  - Record it in `evals/gates.toml` (`[screen_nsfw]`) before the bulk worklist is screened.
  - Because the threshold is hashed, a change re-runs the stage. Cheap: the model is fast.

## Done when

- Tests:
  - the runner on synthetic images, i.e. the plumbing: a stub model returning fixed scores gives the right booleans;
  - flags land in the quarantine;
  - no network access.
- A run over ImportantFiles records its img/h and flag rate here, as counts only.
- **Retro-check:** run over everything already sent to Gemini or Claude (pilot, eval-1k, pool5k). The count is reported to the owner.
- The owner reviews the first batch (03), plus a seeded random 200 unflagged items on the same page as a miss check. The threshold is recorded.

## Depends on

- `infer-v1` images.
- 02 and 03 for where flags go. Build those with a stub; this issue can run in parallel.

## Comments

- 2026-10-11 (owner): wants it simple, fast and Apple, with no metadata. The first draft's multi-model eval (NudeNet, OpenNSFW2 and others) was dropped.
- 2026-10-11 (owner): Apple SensitiveContentAnalysis needs restricted signing, so use the Core ML fallback.

**2026-10-11 (Studio, add-pipeline-foundations 4.4).** `shoebox screen run --items <first 500 pool5k items> --workers 4` with `nsfw-coreml-v1` (OpenNSFW2 FP32 Core ML, `ComputeUnit.ALL`, threshold 0.2):
- 500 screened in 224 s, about 8,000 img/h, including model load. The run is bound by reading the derived JPEGs from the NAS; the model takes 3.8 ms per image.
- 15 flagged (3%), now `pending` in `evals/quarantine.jsonl` (hashes only). Counts only; nobody has looked at them.
- At 3%, the whole of ImportantFiles would give about 1,500 flags for the owner to review. The threshold is set recall-first from the first review (9.4).

**2026-10-11 (9.3 retro-check).** 1,565 assets already sent to a hosted model or named in the shown eval sets were screened, and 39 were flagged: 27 had gone to Gemini, 26 had been seen by Claude, and 12 appear only in eval-set lists. All 39 are `pending` until the owner's review. Details are in `evals/runs/2026-10-11_screen-retro.md` (counts only).
