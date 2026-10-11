# Quarantine stage: flagged assets go nowhere until the owner rules

Status: ready-for-agent
Reported: 2026-10-11
Reporter: mazda (request), filed by claude

## Why

A flag must stop the asset **everywhere downstream**, not just in the bulk run:
- hosted caption runners (Gemini sync and batch, Claude/external packets);
- Opus contact sheets (`screen-v1`) and any Claude review packet;
- eval-set draws;
- the family view in the UI.

`privacy.filter_items` is already the single choke point for hosted sends (never-send, `held_back`, consent). Quarantine belongs there too.

## What

- **`evals/quarantine.jsonl`** (or under `<shoebox_root>` if asset_ids alone are too revealing; decide): one row per flagged asset, with `asset_id`, screen (`nudity` | `document`), flagged-at, and verdict (`pending | include | private | exclude`) once reviewed.
  - Rows only, no paths, no images.
  - Appended by the screen stage (01). Verdicts are written by the review page's ingest (03).
- **Verdict effects:**
  - **`pending`:** refused by every hosted provider and every Claude-viewing path; local stages (EG2 embedding, 12B caption) are held too, until reviewed.
  - **`include`:** a false positive; the asset proceeds normally.
  - **`private`:**
    - stays in the catalogue for the owner;
    - local processing only (12B caption, EG2), never hosted;
    - hidden from family viewers (04).
  - **`exclude`:** not catalogued for search at all. It is recorded so it is never re-flagged or re-sent. Originals are untouched (Shoebox never deletes).
- **Enforcement:**
  - `filter_items(provider=...)` returns reason `quarantined` for `pending`, `private` and `exclude`.
  - The external/Claude packet builder and the contact-sheet builder call the same check.
  - Corpus draws (`evals/corpus.py`) skip quarantined assets.
- **Ingest ordering:** the screen runs right after `derive`, before any other stage, in the ingest pipeline config (`ingest/01`).
- **Duplicates:** quarantine applies to the whole dedup group. If any copy is flagged, every copy is (`dedup/01`).

## Done when

- Tests prove a `pending` asset is refused by:
  - each hosted runner, sync and batch;
  - the external packet builder;
  - the contact-sheet builder;
  - corpus-draw.
- Tests prove a `private` asset is refused by hosted runners but accepted by the local 12B runner.
- A byte-identical copy of a flagged asset in another source is refused too.
- `shoebox privacy check --provider gemini --allowed-out` reports the quarantined count.

## Depends on

- None to build: test with a stub flag list.
- 01 for real flags.
- `dedup/01` for group propagation; until it lands, match by asset_id only.

## Comments
