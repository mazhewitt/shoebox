# The back-catalogue bulk run (Gemini Batch, `label-v2`)

Status: blocked (on sources/, dedup/04, 01, 02 and owner spend approval)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

This is the MVP's core deliverable: every photo described for search (decisions 35 and 41).

**Price deadline:** the introductory Gemini price is valid until **2026-12-31**. Past that the batch cost roughly doubles, to about $196, more than the remaining $139.96. Schedule the run to finish in 2026, or re-quote and re-approve.

## Prerequisites (all ticked before the quote goes to the owner)

- [ ] `sources/` complete: every share mounted read-only, Takeout landed and inventoried, skip rules applied (`.thumbnails`).
- [ ] Per-source consent recorded. `hosted_models` and `held_back` in `config/shoebox.toml` match the owner's latest word.
- [ ] `dedup/04` worklist built: captures, not files. Eligibility uses "no copy in a non-consented source".
- [ ] `shoebox privacy check --provider gemini --allowed-out` passes on the worklist. never-send is up to date (including the eval-1k screen additions).
- [ ] **Screen for documents** (`08`). Run the consent screen (`config/prompts/screen-v1.md`, Opus contact sheets) over everything not yet screened, to catch ID, medical and financial documents. For about 70k images this is a large Opus job: plan its subscription cost, or a rules + Gemini-free pre-filter first.
- [ ] Derived `infer-v1` JPEGs exist for every eligible capture. `derive` runs locally.
- [ ] 01 merged, 02 confirmed, and the 03, 04 and 05 policies decided.
- [ ] Batch price re-checked on the pricing page on the day. `price_date` updated.

## What

1. **Quote:** captures × measured cost per image at batch rate, plus the fallback count, expected from 0.2% refusals. The owner approves a figure and a `max_usd_per_run` cap.
2. **Submit** in chunks that fit the Tier 1 batch queue (about 20 jobs). Run `privacy.check_budget` before each chunk. Run the 03 fallback after each chunk.
3. **Checkpoint:** stop after the first chunk (about 3k images). Check the error, refusal and truncation rates and the actual against estimated cost before releasing the rest.
4. **Coverage report** `evals/runs/<date>_bulk-run.md`, counts only. Every eligible capture ends in exactly one state:
   - a Gemini label;
   - a 12B fallback label (with its reason);
   - `tiny` (per 04);
   - an error with a reason.

   Non-eligible captures (held back or non-consented) are listed as queued for the local 12B path (`ingest/`).
5. **Spend:** the total is logged in `evals/spend.jsonl`. The Spend table in `evals/RECOMMENDATION.md` is updated.

## Done when

The coverage report shows 100% of eligible captures accounted for, and the spend is within the approved cap.

## Depends on

- `sources/`, `dedup/04`, 01–05.
- The owner's approval of the quote.

## Comments
