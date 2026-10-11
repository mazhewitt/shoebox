# Bulk captioning: the back catalogue through Gemini Batch

**Goal.** Caption every hosted-eligible capture in the back catalogue with Gemini 3.8 Flash `label-v2` through the Batch API (decision 41, `evals/RECOMMENDATION.md`). Do it inside the remaining hosted budget, with:
- the same privacy and spend guards as the sync runner;
- a recorded reason for every capture that ends without a Gemini label;
- local Gemma 4 12B `label-v2` as the fallback, so search has one caption space.

**Deadline pressure:** the introductory Gemini price ($0.75 / $3.75 per 1M) is valid until **2026-12-31** (`config/candidates.d/gemini.toml`, `price_valid_until`).
- From 2027-01-01 prices double. The bulk run would cost about $196 at batch price, more than the $139.96 left.
- So the bulk run (06) should finish in 2026, or be re-quoted and re-approved.

| # | Issue | Gate to pass before it lands |
|---|---|---|
| 01 | [Gemini Batch API runner](01-gemini-batch-runner.md) | Tests for every guard; a 20-image live batch whose labels match the sync runner's schema validation; spend logged |
| 02 | [Confirm on pilot-v0-pool5k (task 7.4)](02-pool5k-confirmation.md) | Owner approves the quote; route order on pool5k reported against eval-1k |
| 03 | [Refusal fallback to 12B](03-refusal-fallback-to-12b.md) | The 2 eval-1k refusals get 12B labels with provenance; search reads a mixed-source label set |
| 04 | [Tiny images](04-tiny-images.md) | Decision recorded from the 11 eval-1k images under 256 px, from existing outputs |
| 05 | [Colour-filter source](05-colour-filter-source.md) | Chosen source passes mono precision ≥ 0.98 |
| 06 | [The bulk run](06-bulk-run.md) | Prerequisites checklist all ticked; the owner approves the quote; coverage report complete |
| 07 | [Store `rotate_cw` per asset](07-orientation-store.md) | Stored with provenance; nothing auto-rotated |
| 08 | [Document screen before any bulk send](08-bulk-document-screen.md) | Method's recall measured on the known excludes; every worklist item has a verdict |

## Sequencing

1. **01** comes first and can start now. **02** is its first real use: about $7 on 5k photos, which tests the runner at scale and confirms the eval-1k result on a bigger corpus at the same time.
2. **03**, **04**, **05** and **07** are policy for the bulk run's outputs, and are cheap to settle now:
   - 04 needs no new spend.
   - 05 is a decision.
   - 03 and 07 are small.
3. **08**, the document screen, gates 06. Pick its method early; it runs on the worklist.
4. **06** waits on the whole of `sources/` and `dedup/04`. Its input is the caption-once worklist, not the raw file list.
