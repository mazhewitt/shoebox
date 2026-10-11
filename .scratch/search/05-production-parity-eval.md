# Production parity eval: the product ranks what the eval scored

Status: blocked (on 03, 04)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Decision 41 and `evals/RECOMMENDATION.md` rest on `eg2-blend:gemini-medium-v2` scoring nDCG@20 = 0.794 on the eval-1k test split (Opus qrels; 0.832 on dev with Gemini qrels, per `evals/sets/eval-1k/frozen-routes.json`). That number is about `evals/search.py` running in memory. The product's path is a different one: SQLite, an import, a mask, captures and a served encoder. Any drift between the two, from tie-breaks, a different digest, a wrong active label or caption-vector misalignment, would silently void the eval. PLAN.md Phase 7 / Gate G names the same risk for bulk versus incremental.

## What

A command, `shoebox search parity --set eval-1k --split test`, that:
- builds or reuses a DB holding only eval-1k's assets (imported via 02 from the same stores the harness used: the `params_hash` values in `frozen-routes.json`);
- runs every test query through the **production** query path (03, default mode, no filters);
- scores the rankings with `evals/graded.py` (`score_queries`, K = 20, the same qrels: `judgments-opus.jsonl` primary, `judgments-v1.jsonl` secondary);
- compares them per query with the harness's stored rankings for the frozen route.

It reports:
- the mean nDCG@20 for each path;
- the per-query ranking agreement (identical top-20 / Kendall τ);
- the queries that differ, by query_id only (no text, no asset bytes).

It also runs on dev with the Gemini qrels, so it can be repeated freely without touching test. Test is the final check only.

## Done when

- **Test:** |production − harness| nDCG@20 ≤ 0.002 for `gemini-medium-v2`, and identical top-20 on ≥ 99% of queries. The tolerance covers float32 BLOB round-trip and ordering noise; anything more is a bug to find, not to accept.
- **Dev:** the same, for the `g12b-q4-v2` route too, since that is the incremental captioner.
- The report is written to `evals/runs/<date>_search-parity.md`.
- The command runs in CI-able form against a fixture set, with a synthetic corpus test in `tests/`.

## Depends on

03, 04, and 02's import of the eval-1k stores.

## Open questions

- Re-run parity after the bulk run, on `pilot-v0-pool5k` as well (with `bulk-captioning/02`, task 7.4)? Proposed: yes, as Gate G's search half.

## Comments
