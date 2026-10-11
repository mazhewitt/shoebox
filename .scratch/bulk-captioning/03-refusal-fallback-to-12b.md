# Route Gemini refusals (and other permanent failures) to local 12B

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Gemini's safety filter refused 2 of 1,000 ordinary family photos in eval-1k (`PROHIBITED_CONTENT`; `evals/RECOMMENDATION.md`, "Known gaps"). At 0.2% of about 70k captures, that is roughly 140 photos with no caption, which makes them invisible to caption search.

Decision 41 already names g12b-q4-v2 as the local captioner. It shares the EG2 caption space, so a 12B caption is a drop-in.

## What

- **Selection.** A `shoebox label fallback` step (or a mode of `label run`) selects the final records of the primary candidate that are `invalid`, for a reason in a configured list:
  - `refused` (blockReason/finishReason `PROHIBITED_CONTENT`, `SAFETY`, `RECITATION`);
  - truncation that its retry didn't fix.
  It then runs `g12b-q4-v2` on exactly those assets.
- **Provenance.** Search must resolve one label per asset across two candidates. Extend `runners.label_source` / `source_index` to take an **ordered list** of sources: the primary first, then the fallback. Each resolved record keeps the `model_id` and `params_hash` it came from, so the index and UI can show "caption by 12B (Gemini refused)".
- **Other permanent failures.** Non-refusal errors, such as the derived image missing or a decode error, are not routed. They stay in the coverage report with their reason (06).
- **Verify on eval-1k.** Run the fallback for the 2 refused eval-1k images, then rebuild the `eg2-blend` index from the mixed source.
  - nDCG on the test split must not drop. It should rise slightly, since those 2 photos become findable.

## Done when

- Tests:
  - the ordered-source resolution;
  - the reason filter;
  - re-running is idempotent (assets with a fallback label aren't re-run).
- The eval-1k mixed-source search run is recorded in a short `evals/runs/` note.

## Depends on

- Nothing for eval-1k. In the bulk run (06) it runs after each batch chunk completes.

## Comments
