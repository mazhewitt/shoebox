# Proposal

## Why

`add-eval-harness` chose the MVP pipeline (decision 41, `evals/RECOMMENDATION.md`):
- Gemini 3.8 Flash `label-v2` through the Batch API for the back catalogue;
- Gemma 4 12B `label-v2` locally for new photos;
- `eg2-blend` search.

Nothing yet can run that pipeline safely over the library:
- **Coverage:** only 5,045 assets are derived.
- **No screen:** nothing screens out nude photos that got into the library by mistake.
- **Consent is checked per file,** not per copy.
- **No dedup:** duplicates would each be captioned. 27% of ImportantFiles is byte-identical copies.
- **No batch runner.**

**Why now:** the Gemini price that makes the bulk run fit the budget ends on **2026-12-31**. This change builds the foundations that every later stage reads, and that the bulk run can't start without.

Issues covered:
- `.scratch/sensitive-content-screen/01–03`;
- `sources/04–05`;
- `dedup/01, 02, 04, 06`;
- `bulk-captioning/01, 03, 04`;
- `catalogue/07, 12`.

## What Changes

- **Derive the whole of ImportantFiles** (`infer-v1`), computing a pixel hash from the same decode.
- **Nudity screen** (owner request, 2026-10-11).
  - A local Core ML classifier gives a yes/no per asset, with no other metadata kept.
  - Flagged assets are quarantined: no hosted send, no Claude viewing, no eval draws, no local captioning until reviewed, and later hidden from family viewers.
  - The owner rules on a local, offline review page: include, private or exclude.
  - A retro-check runs over everything already sent to Gemini or Claude.
- **Hosted eligibility per copy:** an asset is sent only if **no** copy of it sits in a non-consented source, under a `held_back` path, on never-send, or in quarantine. This fails closed when a configured source has no inventory.
- **Inventory skip rules** for derived caches: Android `.thumbnails`, iPod photo caches, Lightroom previews.
- **Dedup:**
  - exact and same-pixel groups across sources;
  - capture grouping (RAW+JPEG, scan exports, Live Photos, Motion Photos, `-edited`, bursts);
  - a canonical member per capture;
  - a **caption-once worklist** with a cost re-quote.
- **Caption runs read only a worklist,** and reuse existing labels instead of re-sending. They refuse an inventory frame, or a worklist missing a configured source, unless that's explicitly allowed.
- **Gemini Batch API runner** (`gemini-batch-v2`, a distinct candidate). It has the sync runner's guards, a resumable job ledger and per-job spend logging.
- **Refusal fallback:** Gemini safety refusals and unrecovered truncations get a local 12B caption, and search resolves one label per asset through an ordered source list.
- **Tiny images:** a measured decision, then a rule, for images under about 256 px.
- **Image features:**
  - an EG2 image vector for every derived asset;
  - the pixel colour/mono heuristic scored, tuned and gated as the candidate black-and-white filter source.
- **`openspec/config.yaml` Languages:** amended to the owner's rule. Python for scripts and pipelines; Rust only for complex logic, long-running services, or speed/concurrency requirements.

**Out of scope:**
- metadata merge (`catalogue/02–06, 08–10`);
- near-duplicates (`dedup/03`);
- the Takeout and other sources (owner actions);
- the bulk run itself, and the pool5k confirmation (`add-eval-harness` 7.4);
- the SQLite index, API and UI;
- storing `rotate_cw` (needs the index).

## Capabilities

### New Capabilities
- `content-screen`: local nudity screen, quarantine of flagged assets, and the owner's review verdicts.
- `hosted-eligibility`: which assets may go to a hosted model or be viewed by Claude, decided across every copy and capture member.
- `source-inventory`: what the inventory counts as a source file. Covers skip rules for derived caches.
- `asset-dedup`: pixel hashes, exact/same-pixel groups, capture grouping, canonical members and the caption-once worklist.
- `bulk-captioning`: worklist-driven caption runs, label reuse, the Gemini Batch runner, refusal fallback and tiny-image handling.
- `image-features`: library-wide EG2 image vectors and the gated colour/mono filter source.

### Modified Capabilities
None. `add-eval-harness` is not archived, so `openspec/specs/` is empty. Its `label-runners` and `inference-images` requirements still hold; the batch runner and the extended derive pass add to them under the new capabilities.

## Impact

- **Code** (Python, `src/shoebox/`):
  - extended: `derive.py`, `inventory.py`, `privacy.py`, `runners/__init__.py`;
  - new: `runners/gemini_batch.py`, `runners/screen.py`, `dedup/`, `screen/` (review page and ingest);
  - new commands: `shoebox screen …`, `shoebox dedup …`, `shoebox label fallback`.
- **Config:**
  - `config/candidates.d/gemini.toml` (`gemini-batch-v2`);
  - `config/candidates.d/screen.toml`;
  - `config/profiles.toml` (unchanged profile, with a pixel-hash record alongside);
  - `evals/gates.toml` (`[screen_nsfw]`, colour filter);
  - `openspec/config.yaml`.
- **Dependencies:**
  - `coremltools`;
  - OpenNSFW2 weights, converted once, kept outside the repo and pinned by hash.
- **Data:**
  - new keyed result kinds under `<shoebox_root>/results/`: `screen`, `pixelhash`, `capture`, `batch-jobs`;
  - worklists under `<shoebox_root>/worklists/`;
  - the quarantine verdicts file, backed up.
- **Spend:** a 20-image live batch smoke test, about $0.03, needs the owner's approval first. Nothing else in this change spends.
- **Owner time:** reviewing the first batch of nudity flags, plus 200 unflagged items.
- **Claude time:** a 50-group capture spot check, on contact sheets of eligible, non-quarantined assets only.
