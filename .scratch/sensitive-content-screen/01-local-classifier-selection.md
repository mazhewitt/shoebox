# Choose and gate a local nudity/explicit-content classifier

Status: ready-for-agent
Reported: 2026-10-11
Reporter: mazda (request), filed by claude

## Why

Owner request: filter out naughty or nude photos that got into the library by mistake, before they go into the pipeline. The classifier must run **locally**. The whole point is that these images never reach a hosted model or Claude.

## What

**Candidates to evaluate** (check the current version, licence and Apple Silicon support of each before use):
- **NudeNet:** a detector with body-part labels. Its labels let the review page say *why*, without anyone else seeing the image.
- **OpenNSFW2:** a port of Yahoo's open_nsfw. ResNet-50, one score.
- **GantMan `nsfw_model`:** MobileNet/Inception, 5 classes (drawings, hentai, neutral, porn, sexy).
- **A ViT classifier,** e.g. `Falconsai/nsfw_image_detection`.
- **Apple's SensitiveContentAnalysis framework** (macOS, on-device). Check whether a CLI tool can use it: it needs an entitlement and the user's Sensitive Content Warning setting.

**Method:**
- **Input:** run on the `infer-v1` JPEG, or a smaller derived size if the model wants 224–320 px. Locally, with no network: same guard style as the Ollama runner, pinned weights with recorded hashes.
- **Store:** the keyed store (`results/screen-nsfw/<model>/<params_hash>/`), with the score and the per-class or per-part detail. It plugs into the ingest ledger as a stage (`ingest/01`).
- **Recall-first gate:** a miss sends an intimate image to a hosted model; a false flag costs the owner a glance. Prefer a **union of two detectors at low thresholds** over one tuned model.
- **Ground truth comes only from the owner.** Claude must not view candidates. The protocol:
  1. Run every candidate over ImportantFiles.
  2. The union of everything any candidate flags at a generous threshold goes to the owner's review page (03).
  3. Owner verdicts give precision per model and *relative* recall: the share of all confirmed positives each model caught.
  4. Add a seeded random slice of, say, 200 low-scoring items for the owner to page through quickly, as a sanity check on misses.
  5. Gemini's PROHIBITED_CONTENT refusals from eval-1k are extra known-sensitive items. Check them by score only; don't view them.
- **Pick** the set of models and thresholds that catches every confirmed positive with the lowest flag rate. Record it in `evals/gates.toml` under a new `[screen_nsfw]` section **before** running it on the bulk worklist.

## Done when

- Every candidate is run over ImportantFiles, with img/h and flag rate recorded. Counts only in the repo; no paths of flagged items.
- The owner has reviewed the union of flags. The chosen configuration catches all confirmed positives, and its flag rate is recorded.
- **Retro-check:** the chosen screen is run over every asset already sent to Gemini or Claude (pilot, eval-1k, pool5k sends), and the result (count only) is reported to the owner.

## Depends on

- 03 (review page) for owner verdicts.
- `infer-v1` derived images.

## Comments
