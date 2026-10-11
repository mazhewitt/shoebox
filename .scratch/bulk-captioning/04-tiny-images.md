# Tiny images (< about 256 px): upscale, route, or caption-skip?

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

The owner caught photo "4a" on the review page: E4B described a 72×72 app icon as people in a park.
- **Re-checked:** re-running reproduced it.
- **How many:** 11 eval-1k images are under 256 px on the long side (1.1%).
- **Scale:** across about 70k captures, that is roughly 800 images. Icons, thumbnails, WhatsApp previews and Android `.thumbnails` cache leftovers make up a large share (`sources/` adds a skip rule for the last).
- **The gap:** `evals/RECOMMENDATION.md` asks how 12B and Gemini did on the same images before choosing a fix. Nothing has measured that yet.

## What

1. **Identify the images.** From `derived/infer-v1/index.jsonl` (`src_width`, `src_height`), list the eval-1k asset IDs with max side < 256, and also < 384 as a sensitivity band.
2. **Compare stored outputs,** with no new inference and no spend. Compare the captions of the stored arms on those IDs:
   - `e4b-bf16-v2`, `g12b-q4-v2`, `gemini-medium-v2` and `opus-v2`;
   - with the Opus/Gemini caption-audit verdicts where the IDs are in the 200-photo audit sample;
   - otherwise a fresh blind Opus audit of just these ≤ 20 images (subscription, small).
3. **Decide,** per model, between:
   - **(a) Do nothing:** if Gemini and 12B are fine, only E4B is affected, and E4B is not in the pipeline.
   - **(b) Upscale:** to the `infer-v1` minimum before inference. Test on the same images with 12B: free and local.
   - **(c) Route:** sub-threshold images go to Gemini even in the local path (`ingest/`).
   - **(d) Caption-skip:** mark them `tiny`, with search relying on folder, date and the image vector. Only if all models invent things.
4. **Record** the decision, and the threshold, in this file and in `evals/RECOMMENDATION.md` "Known gaps". Implement it as a candidate or profile rule, not an ad-hoc filter.

## Done when

- A short `evals/runs/<date>_tiny-images.md` with a per-model correct/invented count on the tiny set, and the decision.
- The chosen rule is implemented and tested, if it is (b), (c) or (d).

## Depends on

- Nothing. Stored outputs already exist for every arm.

## Comments
