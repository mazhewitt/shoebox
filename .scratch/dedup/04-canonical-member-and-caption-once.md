# Canonical member per group, and the caption-once bulk worklist

Status: blocked (on 01 and 02; 03 optional)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Cost.** The bulk Gemini run was quoted at about $98 for 71.6k images (`evals/RECOMMENDATION.md`, $0.00275/img at batch price). That counts files. Captioning once per capture is cheaper, and gives one consistent caption per thing the owner searches for.
- **Search** shows the canonical member and keeps the others reachable (PLAN.md Phase 3).

## What

**Canonical member** per capture/duplicate group. The rule is deterministic, with a reason recorded:
1. a consented-source copy over a non-consented one, since the hosted run can only send consented copies;
2. richest metadata: in-file EXIF date and GPS over a stripped copy (decision 9 precedence: the owner's copy by camera wins across libraries);
3. highest resolution;
4. processed over raw for display (a JPEG/export over NEF/DNG/TIFF in RAW+JPEG and scan-export groups). Raws stay the master (decision 25);
5. original over `-edited` for captioning. Edits are linked assets and inherit the caption unless the owner asks otherwise;
6. tie-break: the smallest `asset_id`.

**Worklist** `shoebox dedup worklist`:
- **Contents:** one row per capture, giving the canonical `asset_id`, its source, its hosted eligibility and the group size.
- **Eligibility:** taken from `privacy.filter_items(provider="gemini")` (never-send, `held_back`, per-source consent). Decision 39: a capture is hosted-eligible only if **no** copy sits in a non-consented source. Non-eligible captures go to the local 12B queue (`ingest/`).
- **Summary:** total files → assets → captures → hosted-eligible captures, with each step's reduction. Also re-quote the bulk cost at the day's batch price via `privacy.estimate`.
- **Propagation:** captions attach to the capture. Every member asset resolves to it in the search index.

## Done when

- Unit tests for each rule step, plus the "any copy in a non-consented source" rule.
- The worklist builds over every inventoried source. Its summary is committed to `evals/runs/` (counts and USD only, no paths).
- `bulk-captioning/06` takes its input from this worklist, not from the raw inventory.

## Depends on

- 01 and 02.
- 03 (optional; adds near-duplicate merges).
- `sources/` complete, for an honest count.

## Comments
