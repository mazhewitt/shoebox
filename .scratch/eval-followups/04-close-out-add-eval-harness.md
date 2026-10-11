# Close out and archive `openspec/changes/add-eval-harness`

Status: blocked (on `bulk-captioning/02`, task 7.4: the pool5k confirmation; the bookkeeping can happen now)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

The eval change's decision is made (8.1, `evals/RECOMMENDATION.md`, commit 4af7038). Three things keep it open:
- **Task 4.3** (owner realism check) is still unticked, though it's done. `evals/sets/eval-1k/QUERIES.md:45` records "30 of 30 answered; would type 29". Task 6.4's done-note also says so.
- **Task 7.4** (confirm on `pilot-v0-pool5k`) is not done. It needs Gemini spend and is tracked in `bulk-captioning/02`.
- **Section 9** ("Deferred to before the bulk run") isn't eval work. Its items now live in `.scratch/`.

## What

- **Now:**
  - Tick 4.3 with a done-note pointing at `QUERIES.md` and the review page id `ca9391b30efc1e35`.
  - Make sure the result is recorded in the set's `README.md` (the task says "Record it in the set README"). If it's only in `QUERIES.md`, add one line to `evals/sets/eval-1k/README.md`.
  - Replace section 9's unticked items with pointers:
    - 9.1, 9.2 → `sources/`;
    - 9.3 → `sources/` (skip rules);
    - 9.4 → `ingest/04`;
    - 9.5 → `ops/04`, `eval-followups/02`, `eval-followups/03`.
  - Mark each one moved rather than done, so the archive doesn't claim work that hasn't happened.
  - Run `openspec validate add-eval-harness`.
- **After 7.4 lands:**
  - tick 7.4 with its run note;
  - sync the delta specs (`/opsx:sync`);
  - archive the change (`/opsx:archive`);
  - check that `openspec/specs/` now holds the eval-harness capability specs (it is empty today).

## Done when

- `openspec/changes/archive/<date>-add-eval-harness/` exists.
- `openspec/specs/` has the synced specs.
- No unticked task remains without a pointer.

## Depends on

- `bulk-captioning/02` (7.4).

## Comments
