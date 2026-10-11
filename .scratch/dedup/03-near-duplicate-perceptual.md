# Near-duplicate detection (perceptual hash + EG2 confirmation), Gate C

Status: needs-triage
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Re-encodes, resizes, WhatsApp-received copies, Google Photos "storage saver" copies, and the same scan exported twice don't match by bytes or pixels (01). They should still be one capture for captioning and search.

PLAN.md Phase 3: two weak signals beat one.
- **pHash/dHash** finds the candidates.
- **The EmbeddingGemma 2 image vector** (already computed per asset for search) confirms them.

`pilot-v0-pool5k/near-dups.jsonl` already lists EG2-cosine pairs; one example is cosine 0.997 between `Google Photos/…/GRANDAD IN HELSINKI.jpg` and `Camera Roll/MAZDAS - GRANDAD IN HELSINKI.jpg`.

**Gate C (PLAN.md):** precision ≥ 0.99 at the chosen threshold, recall reported. A false merge hides a photo from search; a miss only costs one extra caption.

## What

- **Hashes:** pHash and dHash on a normalised decode (the 256 px thumbnail), stored per asset with a version.
- **Candidates:** Hamming distance ≤ k (tuned on dev), bucketed by multi-index hashing, so it isn't all-pairs over 70k+ assets.
- **Confirmation:** EG2 image cosine ≥ t on the candidate pair. Both k and t are chosen on dev.
- **Labelled pairs** (decision 40: Opus pre-labels, the owner spot-checks):
  - **Size:** about 600 pairs, stratified by distance band, source pair (Takeout↔share, share↔share) and medium (scan, digital, screenshot).
  - **Hard negatives:** burst neighbours, the same scene a second apart, and bracketed exposures. These are where false merges hide.
  - **Labels:** `same_picture` / `same_moment_different_frame` / `different`. Only `same_picture` merges; `same_moment_different_frame` may feed burst grouping (02).
  - **Where:** a dev/test split stored as `evals/sets/near-dup-v1/`, IDs and labels only.
- Report precision and recall on test with a bootstrap CI, per stratum (decision 18: per stratum, precision first).

## Done when

- `evals/runs/<date>_near-dup.md`:
  - test precision ≥ 0.99, overall and per stratum with ≥ 30 pairs;
  - recall reported;
  - chosen k and t frozen in `evals/gates.toml` (or its successor) before test scoring.
- Merges are stored as `near_dup` links with the scores kept, so a new threshold doesn't need recomputing (decision 14).

## Depends on

- 01 (exact and same-pixel matches are removed first).
- EG2 image vectors for every asset. These come from `catalogue/12`.
- Opus labelling time on the owner's subscription.

## Open questions

- Which is cheaper: EG2 vectors over the whole library first (local, free, needed for search anyway), or pHash candidates first so EG2 only runs on candidates? Lean: EG2 everything, since search needs it regardless.
- Should near-duplicates feed the caption-once worklist (04) for the bulk run, or only dedup the search results? Merging before captioning saves money but risks losing a caption if the precision is overstated.

## Comments
