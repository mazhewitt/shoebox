# Design

## Context

See proposal.md for the motivation. Current state, from `add-eval-harness` and the `.scratch/` issues:

**ImportantFiles:**
- The frame (`frame-2026-10-10.parquet`) has 71,622 images and 52,481 unique `asset_id`s.
- Only 5,045 assets are derived (`infer-v1`, the pool5k set).

**Built:**
- **`privacy.filter_items(provider=)`** is the single hosted-send choke point: checklist, never-send, `held_back`, per-source consent. It judges each item by its own source only. Its `copy_restricted` reason sees copies only within the items passed in.
- **Runners** write to the keyed store (`results/<kind>/<model>/<params_hash>/part-*.jsonl`). `label_source` / `source_index` resolve one record per asset, with `~retry` substitution.
- **Grouping:** `pilot/select.py:assign_groups` has the `raw_jpeg_basename` and `scan_export_basename` rules, inside one inventory only.
- **The eval review page** (`evals/review.py`): an offline HTML page with localStorage and a "Download answers" ingest, checked by page id.

**Constraints:**
- The owner's language rule: Python for pipelines. Everything here is a batch CLI, so all of it is Python.
- No always-on worker (decision 13).
- Sources are read-only through `config.original_path`.
- No image bytes in the repo.
- The owner approves each new Gemini spend.

## Goals / Non-Goals

**Goals:**
- Every unique ImportantFiles image ends this change:
  - derived;
  - pixel-hashed;
  - screened;
  - vectorised;
  - placed in a capture;
  - with a hosted-eligibility verdict.
- The output is a caption-once worklist that `bulk-captioning/06` (the bulk run) and `add-eval-harness` 7.4 (pool5k) can consume unchanged.
- Each new stage is idempotent and resumable through the keyed store, so `shoebox ingest` (`ingest/01`) can later run the same stages.

**Non-Goals:**
- No DB import. Worklists and result kinds are files; `search/02` imports them later.
- No near-duplicate grouping (`dedup/03` is still needs-triage). The worklist allows for it: it errs toward captioning too much.
- No sources beyond ImportantFiles. Their rules are built and fixture-tested, and run when the sources land.

## Decisions

### D1. One decode per original: pixel hash computed in the derive pass
- `derive` already decodes every original (pyvips, LibRaw, PSD composite) and applies orientation. The pixel hash is a BLAKE3 over that orientation-normalised, full-resolution buffer, plus width, height and bands. It is taken before resize and ICC conversion, and stored as its own result kind (`pixelhash/<version>`).
- **Why:** reading about 600 GB over SMB twice is the dominant cost. A separate hashing pass was rejected.
- RAW and PSD hash their rendered image and are marked `rendered`.
- **The `infer-v1` profile and its `settings_hash` are unchanged,** so existing derived files stay valid. Assets derived before this change get their pixel hash in a backfill run, which decodes again.

### D2. Nudity screen: OpenNSFW2 converted to Core ML, run from Python
- Owner: "simple, fast, Apple", with no metadata.
- Apple SensitiveContentAnalysis was rejected: its entitlement is restricted. On 2026-10-11 an ad-hoc-signed CLI was killed at launch, and an unsigned one sees policy `.disabled`.
- **The model:**
  - `coremltools` runs the converted model on the Neural Engine in-process, with no Swift and no signing;
  - the conversion script is committed, the `.mlpackage` lives under `<shoebox_root>/models/`, and its hash is pinned in candidate `nsfw-coreml-v1`;
  - the threshold is a hashed candidate param, so a re-tune re-runs the cheap stage;
  - only the boolean is stored.
- **Fallback model:** a ViT classifier converted the same way, if OpenNSFW2 won't convert. Same contract.
- **The screen reads the `infer-v1` JPEG.** So no stage that sends or views an image can run before both derive and screen.

### D3. Quarantine as data, enforced at the existing choke points
- **The file:** `evals/quarantine.jsonl`, append-only, latest row per asset wins: `asset_id`, `screen` (`nudity` | `document`), `flagged_at`, and `verdict` / `verdict_by` / `verdict_at` once reviewed.
  - It lives in the repo because verdicts are owner labels, the most irreplaceable data, and the repo is backed up. It holds asset hashes only, never paths or images.
- **Enforcement:** `filter_items` gains a `quarantined` reason. The Claude packet builder, the contact-sheet builder and corpus draws call the same check.
- **Local captioning** is held for `pending` only. `private` allows local stages and refuses hosted ones.
- **Rejected:** folding quarantine into `never-send.txt`. That file is path globs, hand-edited and not viewable. Verdicts need states and provenance.

### D4. Owner review page reuses the eval review-page pattern
- **Format:** an offline HTML page with localStorage and a "Download answers" JSON. Ingest is checked by page id and completeness.
- **Location:** a non-synced local folder, `~/shoebox-review/screen/`. The page-map stays outside the page folder.
- **Thumbnails** are written by derive code and blurred until clicked. The builder never reads them back into the session, and prints only counts.
- Verdicts apply to every asset in the dedup group.
- **Shared with the document screen:** the same page and quarantine serve `bulk-captioning/08` later, via the `screen` column.

### D5. Hosted eligibility is decided over a cross-source copy index, failing closed
- **The copy index** maps `asset_id → [(source, relpath)]`, built from the latest frame of **every configured source**, not from the items passed in.
- **Fail closed:** if a configured source lacks a frame newer than its config entry, every hosted send is refused with a clear error, so a missing inventory can't pass as "no copies".
- **Capture level:** a capture is eligible only if every member asset is. Same-pixel groups count as copies.

### D6. Capture grouping: conservative rules, recorded with versions
- Each rule (`raw_jpeg_basename`, `scan_export_basename`, `live_photo`, `motion_photo`, `edited`, `burst`) emits groups with member roles and `rule_version` into `capture/<version>`.
- Ambiguity leaves files apart: a false merge loses a photo from search; a miss costs one extra caption.
- **Bursts** are one capture; only the cover is captioned (proposed in the issue, adopted).
- **`-edited`** is a linked asset, not a capture member for captioning: it inherits its original's label.
- **Motion Photo detection** reads the XMP tags already in the frame's `exif` column. If `-fast2` omits them, the rule falls back to the filename pattern and is marked `filename_only`, until `catalogue/02` lands.

### D7. Canonical member and worklist
- **Canonical member rule order:**
  1. a consented-source copy;
  2. richest metadata (in-file date and GPS);
  3. highest resolution;
  4. processed over raw for display;
  5. original over edited;
  6. smallest `asset_id`.
- **Worklist:** `<shoebox_root>/worklists/<name>.jsonl`, one row per capture, plus a header record with `dedup_version`, the source set, and the frame dates.
  - It sits outside the repo because it is about 50k rows; its summary (counts and USD) goes to `evals/runs/`.
- **Eligibility per capture** comes from D5. Ineligible captures are tagged for the local 12B queue.

### D8. Caption runs consume only worklists, and reuse before spend
- `label run` in bulk mode, and the batch runner, accept a worklist, never a frame or a raw item list. Eval sets keep their own path.
- **The guard refuses** a worklist whose source set lacks a configured source, unless `--partial-sources "<reason>"` is given; the reason is logged in the run record.
- **Reuse:** before any request, each capture's canonical asset (and every member) is looked up in the label index of the candidate and its fallback chain. Labelled captures are skipped and counted.
- **Staging is not decided here.** Whether to wait for all sources or caption by stages (`dedup/06`) is decided at bulk-run time. Both need only this guard.

### D9. Gemini batch runner is a distinct candidate
- `gemini-batch-v2` copies `gemini-medium-v2`'s prompt, schema, model, media resolution and thinking level, but hashes `runtime = "gemini-batch"` and its runner version.
- **Why:** a store key names how a record was made, and batch serving may differ.
- It reuses the sync runner's request building, parsing, normalisation and pricing functions, not copies of them.
- **Job ledger:** `results/batch-jobs/<candidate>/<job_id>.json` makes restarts poll instead of resubmitting.
- **Spend:**
  - pre-flight at the batch price (`price_batch_discount`, a `price_` key, so not hashed);
  - actual cost per job logged to `evals/spend.jsonl`;
  - jobs chunked to the Tier 1 queue limit, with the cap checked between jobs.

### D10. Refusal fallback via an ordered label-source list
- `label_source` takes an ordered list `[gemini-batch-v2, gemini-medium-v2, g12b-q4-v2]`. The resolved record keeps its own `model_id` / `params_hash`.
- The fallback runs only for final records that are `invalid` with reason `refused`, or `truncated` after retry. Other errors stay in the coverage report.

### D11. Tiny images and colour filter are decided by measurement on stored outputs
- **Tiny images:** compare stored captions of every arm on the eval-1k images under 256 px (and under 384 as a sensitivity band), with a blind Opus audit of at most 20 images. Then implement the chosen rule (do nothing / upscale / route / caption-skip) as a candidate or profile rule, not an ad-hoc filter.
- **Colour:** score the frame's existing `colour_guess` against the Opus reference, including a mono-stratified sample. Tune the thresholds on dev for mono precision ≥ 0.98 per stratum, and score test once. The filter source and the `toned` default are recorded in `evals/gates.toml` and RECOMMENDATION.md.

## Risks / Trade-offs

- **[Full derive over SMB takes days: 250 GB of TIFF, 106 GB of DNG]** → Run it resumable on the laptop, measuring img/h on the first 1k. It is the long pole, so it starts first. The screen and vectors pipeline behind it per asset.
- **[OpenNSFW2 misses some nudity (no score kept, single model)]** → Recall-first: the threshold starts at about 0.2. The owner reviews 200 unflagged items. A retro-check covers past sends. The threshold is set from the owner's first batch before the bulk worklist is screened.
- **[Quarantine file in a GitHub repo reveals that flagged assets exist]** → It holds hashes only, and the repo is private. If the owner prefers, move it under `<shoebox_root>` and back it up with the results store; the code takes the path from config.
- **[Copy-index fail-closed blocks pool5k until every configured source has a frame]** → Only ImportantFiles is configured today. New sources are added to config only together with their first inventory (`sources/06`).
- **[Capture rules merge wrongly (e.g. a reused file stem across trips)]** → Rules require the same folder, plus timestamp proximity where EXIF has it. A 50-group spot check must show zero false merges.
- **[Batch output differs from sync]** → It is a distinct candidate. Pool5k (7.4) compares them before the bulk run.

## Migration Plan

- Everything here adds to what exists:
  - existing store kinds and the `infer-v1` profile hash are unchanged;
  - `filter_items` gains reasons, but no existing refusal becomes an allow.
- **Backfills:**
  - pixel hashes for the 5,045 already-derived assets;
  - the screen over everything derived, including the retro-check sets.
- **Rollback:** the new result kinds and worklists are standalone files. Reverting the code leaves them inert.
