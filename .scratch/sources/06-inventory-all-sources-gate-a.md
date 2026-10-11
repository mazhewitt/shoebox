# Inventory over every source, and the Gate A report

Status: blocked (on 01, 02, 03, 05)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- PLAN.md Phase 1 Gate A: completeness, with no ML.
- tasks.md 9.2 remainder: "run the inventory over all sources".
- Every later stage reads the frame:
  - the catalogue merge;
  - dedup;
  - the bulk caption set;
  - the hosted-eligibility rule (04).
- **Cost:** the bulk-run quote ($98 for 71,600 images) comes from one source's image count. It must be re-quoted from the full, deduplicated frame.

## What

- Run `uv run shoebox inventory --workers 8` across all configured sources: ImportantFiles, the new NAS shares (01), both Takeout trees (02) and the scan mirror (03). Use the existing listing cross-check and sources-untouched check.
- **Extend the frame README** (`shoebox inventory readme`) into the Gate A report:
  - per source: files listed / inventoried / skipped (by reason) / errored, and bytes;
  - per Takeout: zip entries vs extracted vs inventoried; JSON sidecars matched / unmatched, with the unmatched rate and up to 20 examples;
  - per source: person, `hosted_consent`, `hosted_models`, `held_back`;
  - cross-source exact duplicates: unique asset_ids vs files. ImportantFiles alone: 71,622 images, 52,481 unique (27% byte-identical copies).
  - a hosted-eligible unique image count per provider (needs 04).
- Write the Gate A verdict into `evals/runs/<date>_gate-a.md`, with each check pass or fail.

## Done when

- `evals/runs/<date>_gate-a.md` exists, with every check in the `sources/README.md` Gate A list passing, or failing with a named follow-up issue.
- The bulk-run re-quote input (unique hosted-eligible images) is posted as a comment on `bulk-captioning/`.

## Depends on

- 01, 02 and 03: the sources exist.
- 05: the skip rules.
- 04: the eligibility counts.
- `catalogue/03` does the sidecar *matching*. Gate A only needs the matched/unmatched count, so a minimal matcher here is fine if 03 isn't done.

## Comments
