# Dedup before captioning: no caption run sees a duplicate

Status: needs-triage (owner: choose the staging in "Open questions"; the guard itself is ready-for-agent once 04 exists)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Cost and the price deadline.**
  - 27% of ImportantFiles is byte-identical copies: 71,622 files, 52,481 unique asset_ids.
  - The Takeout will overlap heavily with ImportantFiles' `Pictures/Google Photos` tree (27,559 images) and with itself.
  - Every duplicate captioned is Gemini money at $0.00275 an image, and the budget only fits if the bulk run finishes before the introductory price ends on **2026-12-31** (`bulk-captioning/README.md`).
- **Consistency.** One capture should have one caption. If two copies get two different captions, search returns the same photo twice under different words.
- **Consent (decision 39).** A capture is hosted-eligible only if **no** copy sits in a non-consented source. That rule can only be applied once every source holding a copy is inventoried, including Jude's Takeout. Once a photo is sent it can't be unsent.
- **The gap.** 01–04 build the groups and the worklist, but nothing *enforces* that captioning reads the worklist. Nothing says what happens when a new source arrives after some captions exist.

## What

1. **The guard.**
   - Bulk and incremental caption commands (`bulk-captioning/01`'s batch runner, the `ingest/02` 12B path) take a **capture worklist** from `shoebox dedup worklist` (04) as input, never an inventory frame or a raw item list.
   - Each worklist records the dedup version and the source set it was built over.
   - The runner refuses a worklist whose source set is missing a configured source, unless `--partial-sources` is given and its reason is logged.
   - Eval sets (`evals/sets/*`) are exempt; they are deliberate samples.
2. **Caption reuse before spend.**
   - Before a caption run, every worklist capture is checked against existing labels by asset_id and by group membership. A capture that already has a label for that candidate is skipped and counted; it is not re-sent.
   - This is what makes staged sources cheap: a later source only costs its *new* captures.
3. **Propagation.**
   - A label attaches to the capture. Every member asset, including ones added by a later source, resolves to it at import (`search/02`).
   - When groups change, the capture keeps its existing label with a recorded reason: a new member joins, two groups merge via near-dup (03), or a threshold re-tune splits one. The cases:
     - **Merge:** the canonical member's label wins.
     - **Split:** the new capture gets queued.
4. **Savings report.** Each run's summary in `evals/runs/` gives counts and USD only: files → assets → captures → already labelled → to caption, with the quote at the day's batch price.

## Done when

- Tests:
  - the runner refuses an inventory frame;
  - it refuses a worklist missing a configured source;
  - a capture with an existing label is not re-sent;
  - a new byte-identical copy in a new source costs nothing and resolves to the existing label;
  - a merge keeps the canonical's label.
- The `bulk-captioning/02` pool5k run and the `06` bulk run both go through the guard. Their summaries show the dedup reduction.

## Depends on

- 01, 02 and 04. 03 is optional: it only lowers the count further.
- `sources/07` (the Takeout) for the full source set.

## Open questions

**Staging against the price deadline.** Recommendation: B, unless the Takeout lands by about mid-November, in which case A.

- **A. Wait.** Dedup over every source (ImportantFiles, the other shares, both Takeouts), then caption once.
  - The cheapest and cleanest.
  - Risk: Takeout delivery and landing slip past the 2026-12-31 price change.
- **B. Stage by consent.**
  1. Request the Takeouts now (`sources/07`).
  2. Caption ImportantFiles' unique captures as soon as **all** sources are at least inventoried, so decision 39 can be applied. Inventory needs only hashes, not dedup quality.
  3. Caption later sources' new captures through the reuse path (2).

  This costs the same in the end, because reuse makes repeats free, and keeps the deadline safe. The price is that near-duplicates across sources (e.g. Storage-saver recompressed copies) may each get a caption before 03 merges them. On a merge the canonical's label wins, so the waste is money, not consistency.
- **Whatever the choice: inventory Jude's library before any send** that could include a copy of a photo in it. Otherwise decision 39 can't be honoured.

## Comments
