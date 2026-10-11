# Gate G: a fresh batch end to end, eval suites still pass

Status: blocked (on 01–04, and on `search/` and `web-app/` for the end-to-end check)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md Phase 7, Gate G: a small batch of fresh photos goes through end to end, and the eval suites still pass on the full ground truth. This catches drift between the bulk path (Gemini Batch) and the incremental path (local 12B), and between laptop and Studio.

## What

- **The batch.**
  - Take a small batch of genuinely new photos (about 50–100) from the first scheduled Takeout after the bulk run: phone photos plus a few new film scans from Drive.
  - None of them may be in eval-1k or pilot-v0. Check by `asset_id`.
- **The run.** Run `shoebox ingest` on the Studio: inventory → metadata → dedup → derive → 12B label → embed → import.
- **End-to-end checks:**
  - every new asset is in the DB with exactly one caption source (12B);
  - its stage ledger is complete;
  - it shows up in the UI;
  - a known-item query written for 10 of them (by Opus, query-writer-v1) finds each in the top 20.
- **Regression checks:**
  - re-run the eval-1k test queries through the **production** search path (`search/05`) and compare with `evals/runs/2026-10-11_eval-1k-test.md`;
  - nDCG@20 for the shipped arm must stay within the MDD.

## Done when

`evals/runs/<date>_gate-g.md` records:
- the batch size;
- the per-stage counts;
- the 10 known-item ranks;
- the production-path eval-1k nDCG@20 vs the frozen number, with a pass or fail.

## Depends on

- 01–04.
- `search/05` (production-path parity eval).
- `web-app/` (UI check).

## Comments
