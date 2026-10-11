# Tasks

Order: group 1 starts first, because the full derive is the long pole. Groups 2–6 are code that can be built in parallel with it. Groups 7–9 consume the derive.

**Before any work:** sources are read-only through `config.original_path`; no image bytes in the repo; no new Gemini spend without the owner's approval.

## 1. Config and the full derive pass

- [x] 1.1 Amend `openspec/config.yaml` "Languages" to the owner's rule: Python for scripts and pipelines; Rust only for complex logic, long-running services, or speed/concurrency requirements. API including ranking = Rust; all batch stages = Python. Verify with `openspec validate add-pipeline-foundations`.
- [x] 1.2 Add the pixel hash to the derive decode (design D1).
  - BLAKE3 over the orientation-normalised, full-resolution buffer, plus width/height/bands, taken before resize/ICC. Written to result kind `pixelhash/<version>` with `decoded|rendered`. The `infer-v1` `settings_hash` is unchanged.
  - Verify with tests on synthetic fixtures:
    - same pixels, different EXIF → same hash;
    - orientation-flag variant → same hash;
    - re-encoded → different hash;
    - the existing derive tests and the `infer-v1` settings hash are unchanged.
- [x] 1.3 Add a pixel-hash backfill mode for already-derived assets (decode only, no re-encode), resumable by `(asset_id, version)`. Verify it covers the 5,045 pool5k assets and is a no-op on re-run.
- [ ] 1.4 Run `derive` over every unique ImportantFiles image asset (resumable, on the Mac Studio, wired to the NAS; see `docs/handoff-studio.md`).
  - Record img/h after the first 1k, total coverage and errors by kind in `evals/runs/<date>_derive-importantfiles.md` (counts only).
  - Verify that every unique image asset_id has an `ok` record and a pixel hash, or a listed error.

## 2. Inventory skip rules (sources/05)

- [x] 2.1 Add named skip reasons to `inventory.py`:
  - `.thumbnails`;
  - iPod/iTunes photo-cache folders;
  - `*.lrdata`;
  - Synology `@SynoEAStream`/`@SynoResource`, only if found in the frame.

  Keep `.lrcat` and Picasa `.ini` recorded. Verify with unit tests on synthetic trees, one per reason, including the keep cases.
- [ ] 2.2 Re-run `shoebox inventory --resume` on ImportantFiles.
  - Verify the new frame differs from `frame-2026-10-10.parquet` only by newly skipped paths (a diff script prints any other change), and that the frame README shows the skip counts.
  - Verify that no `eval-1k` item is under a newly skipped path. If one is, stop and report it.

## 3. Hosted eligibility across copies (sources/04)

- [x] 3.1 Build the cross-source copy index from the latest frame of every configured source, adding same-pixel links from `pixelhash`. Verify with unit tests on synthetic frames.
- [x] 3.2 Extend `filter_items`:
  - **`copy_restricted`** when any copy is in a non-consented source, under `held_back`, or on never-send;
  - **`quarantined`** from the quarantine file (group 5);
  - **fail closed** when a configured source has no frame newer than its config entry.

  Verify with tests:
  - an asset with copies only in `importantfiles` passes;
  - a copy in a `person = "jude"` source refuses it for gemini and claude;
  - a `held_back` copy refuses the other copy;
  - a missing frame refuses everything and names the source;
  - existing privacy tests still pass.
- [x] 3.3 Extend `shoebox privacy check --provider <p>` to report refused counts by reason and restricting source, with no paths. Verify by running it over the real frame and recording the counts in `.scratch/sources/04` Comments.

## 4. Nudity screen runner (sensitive-content-screen/01)

- [x] 4.1 Write `tools/convert_nsfw.py`.
  - It converts OpenNSFW2 to Core ML into `<shoebox_root>/models/`, printing the `.mlpackage` hash. If that fails, convert the ViT fallback instead.
  - Check the licence and record it in the script header.
  - Verify the conversion runs, and that the converted model's outputs on 5 synthetic inputs match the source model within 1e-3.
- [x] 4.2 Add `coremltools` to `pyproject.toml`. Add candidate `nsfw-coreml-v1` in `config/candidates.d/screen.toml`: model hash, threshold 0.2, runner version; the threshold is hashed. Verify that `uv sync` succeeds and the candidate resolves.
- [x] 4.3 Implement `runners/screen.py`.
  - Input: the `infer-v1` JPEG, resized with pyvips.
  - `ComputeUnit.ALL`, no network.
  - Writes `{asset_id, sensitive}` to `results/screen/<candidate>/<params_hash>/`, with no score kept, and appends flags to the quarantine.
  - Verify with tests using a stub model that returns fixed scores: booleans at the threshold boundary; no network (socket blocked); flags appended once; re-run is a cache hit.
- [x] 4.4 Add a `shoebox screen run` command over a derived set. Verify with a CLI test, and by measuring img/h on 500 pool5k assets (recorded in `.scratch/sensitive-content-screen/01` Comments).

## 5. Quarantine and owner review (sensitive-content-screen/02, 03)

- [x] 5.1 Implement the quarantine file `evals/quarantine.jsonl` (latest row per asset wins). Verdicts are `pending|include|private|exclude`, with `verdict_by`/`verdict_at`, propagated to the asset's dedup group.
  - Verify with tests: a `pending` asset is refused by:
    - the Gemini sync runner;
    - the Gemini batch runner (group 8);
    - the external/Claude packet builder;
    - the contact-sheet builder;
    - `corpus-draw`;
    - local caption runs.
  - Verify that a `private` asset is refused by hosted runners only, and an `exclude` asset is never re-queued.
- [x] 5.2 Implement `shoebox screen review-page`.
  - Offline HTML with blurred thumbnails (click to reveal), keyboard shortcuts, a grid, "mark page as include", and a `screen` column.
  - Output goes to `~/shoebox-review/screen/`, with the page-map outside the page folder. The build refuses repo, Desktop and Documents paths, and prints counts only.
  - Verify with tests on a stub flag list, including the refused output locations.
- [x] 5.3 Implement `shoebox screen ingest <answers.json>`. It checks the page id and completeness, writes verdicts as human labels (`labelled_by = owner`), and offers to delete the review thumbnails. Verify with tests: page-id mismatch refused; incomplete answers refused; verdicts applied to group members.
- [x] 5.4 Document the screen, quarantine and review workflow in `docs/screen.md`. Verify the documented commands run as written on the stub set.

## 6. Dedup: groups, captures, canonical, worklist (dedup/01, 02, 04)

- [ ] 6.1 Build the exact and `same_pixels` groups from the frames and `pixelhash`. Group id = `g-` + the first 12 hex of the smallest member id. Write to the keyed store.
  - Verify with fixture tests (byte-identical; same pixels; re-encoded not grouped).
  - Verify with an ImportantFiles count by source/folder pair in `evals/runs/` once 1.4 is done.
- [x] 6.2 Implement the capture rules as `capture/<version>`:
  - `raw_jpeg_basename` and `scan_export_basename`, generalised from `pilot/select.py`;
  - `live_photo`;
  - `motion_photo` (XMP from the frame's `exif` column, else filename-only, marked);
  - `edited`, as a linked asset;
  - `burst`, with the cover primary.

  Verify with fixtures for each rule and its near misses (same stem in another folder; `-edited` without its original).
- [x] 6.3 Implement canonical selection in design D7's order, with the reason recorded. Verify with a unit test per rule step and a determinism test (two runs, identical output).
- [x] 6.4 Implement `shoebox dedup worklist`.
  - One row per capture, plus a header (dedup version, source set, frame dates).
  - Eligibility per capture: every member must be eligible; ineligible captures are tagged local.
  - Output goes to `<shoebox_root>/worklists/`, with a summary (files → assets → captures → eligible; re-quoted USD at batch price) in `evals/runs/`.
  - Verify with tests on synthetic groups and a summary check that the counts add up.
- [ ] 6.5 After 1.4: run 6.1–6.4 over ImportantFiles. A Claude 50-group stratified capture spot check on contact sheets (eligible, non-quarantined only) must find zero false merges, recorded in `evals/runs/`. Update `.scratch/dedup/README.md` with the counts.

## 7. Library image vectors and colour filter (catalogue/12, 07)

- [ ] 7.1 Run `shoebox embed run` with `eg2-bf16` over every derived, non-`pending` image asset, one vector per asset. Record img/h and coverage in `.scratch/catalogue/12` Comments. Verify every derived asset has a vector passing `embed.check_vector`, or a listed error.
- [ ] 7.2 Score the frame's `colour_guess` against the Opus reference.
  - First on the eval-1k 200-photo audit sample.
  - Then on a mono-stratified sample: about 150 `mono`/`toned` plus 150 near-threshold, labelled by Opus blind, eligible and non-quarantined only.
  - Verify the report gives mono precision and recall per stratum.
- [ ] 7.3 Tune the `classify_colour` thresholds on a dev half for mono precision ≥ 0.98 per stratum, freeze them, and score the test half once.
  - Record the filter source (or "unavailable" per stratum) and the `toned` default in `evals/gates.toml` and `evals/RECOMMENDATION.md`.
  - Verify with `evals/runs/<date>_colour-heuristic.md` and with tests for the frozen thresholds.

## 8. Worklist-driven caption runs and the batch runner (dedup/06, bulk-captioning/01, 03)

- [x] 8.1 Add the worklist guard to bulk caption runs.
  - Refuse frames and raw item lists.
  - Refuse a worklist missing a configured source unless `--partial-sources "<reason>"` is given (logged); eval sets are exempt.
  - Verify with tests for each refusal and for the eval-set exemption.
- [x] 8.2 Add label reuse before spend: skip captures whose canonical asset or any member already has a resolved label in the candidate's source chain; report the reused count. Verify with tests:
  - a new byte-identical copy costs nothing;
  - a merge keeps the canonical's label;
  - the summary counts reused captures.
- [x] 8.3 Extend `label_source`/`source_index` to an ordered source list. Each resolved record keeps its own `model_id`/`params_hash`. Verify with tests for ordering, `~retry` substitution inside the list, and unchanged single-source behaviour (existing tests pass).
- [x] 8.4 Implement `runners/gemini_batch.py` and candidate `gemini-batch-v2`.
  - Reuse the sync runner's build/parse/normalise/price functions.
  - Job ledger in `results/batch-jobs/`.
  - Pre-flight at the batch price (`price_batch_discount = 0.5`, not hashed).
  - Chunking to the queue limit, with the cap checked between jobs.
  - Per-job spend logging; `refused`/`truncated` reasons; expired jobs back to pending.
  - Verify with fake-transport tests:
    - each guard refuses: checklist, never-send, `held_back`, quarantine, over-cap, expired price;
    - crash mid-poll → no resubmit;
    - parse parity with sync on canned responses.
- [x] 8.5 Re-check the batch price and discount on the Gemini pricing page, and update `price_date` in the candidate. Verify `require_current_price` passes.
- [ ] 8.6 **Owner approval required (about $0.03).** Run the live smoke batch: 20 eval-1k images through `gemini-batch-v2`. Verify every label validates, spend is logged, and a field-by-field comparison with `gemini-medium-v2` on the same ids is in `evals/runs/`.
- [ ] 8.7 Implement `shoebox label fallback`. It runs `g12b-q4-v2` on final `refused` or retry-failed `truncated` records of the primary chain, and is idempotent.
  - Verify with tests for the reason filter and the idempotence.
  - Verify by running it on the 2 refused eval-1k images, rebuilding `eg2-blend` from the ordered source list, and checking test nDCG@20 doesn't drop (a short `evals/runs/` note).
- [x] 8.8 Document worklists, the guard, reuse, batch jobs and resume in `docs/bulk-captioning.md`. Verify the documented commands run against the fake transport.

## 9. Tiny images and the retro-check (bulk-captioning/04, sensitive-content-screen/01)

- [ ] 9.1 List eval-1k assets under 256 px and under 384 px from the derive index.
  - Compare the stored captions of `e4b-bf16-v2`, `g12b-q4-v2`, `gemini-medium-v2` and `opus-v2`, using existing audit verdicts, plus a blind Opus audit of at most 20 images (non-quarantined only).
  - Decide the rule per model.
  - Verify with `evals/runs/<date>_tiny-images.md`, and with RECOMMENDATION.md "Known gaps" updated.
- [ ] 9.2 Implement the chosen tiny-image rule as a candidate or profile rule, unless it is "do nothing". Verify with a test on a synthetic 72 px image.
- [x] 9.3 Run the screen over every asset already sent to a hosted model or shown to Claude: the pilot-v0, pool5k and eval-1k label/judge/audit stores and Opus packets. Report the flagged count to the owner. Verify the union size and the flagged count are recorded (counts only).
- [ ] 9.4 Run the screen over all of ImportantFiles, and build the first review page (flags plus a seeded random 200 unflagged). **Owner reviews it.**
  - Ingest the verdicts.
  - Set the threshold recall-first from the verdicts, recorded in `evals/gates.toml` `[screen_nsfw]`. If it changed, re-run the screen.
  - Verify that `shoebox privacy check --provider gemini` reports zero `pending`.

## 10. Integration check

- [ ] 10.1 End to end on ImportantFiles: build the worklist and run the bulk caption command against the fake batch transport.
  - Verify that no request contains a quarantined, ineligible or already-labelled capture.
  - Verify that the summary's counts reconcile: files → assets → captures → eligible → reused → to send.
  - Verify the re-quoted USD for the real run is recorded in `evals/runs/`.
- [ ] 10.2 Update the `.scratch/` issues this change closes (status `done` with commit refs) and the `.scratch/README.md` queues. Verify `openspec validate add-pipeline-foundations --strict` passes.
