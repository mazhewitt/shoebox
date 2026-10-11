# Confirm the chosen pipeline on pilot-v0-pool5k (task 7.4)

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

`openspec/changes/add-eval-harness/tasks.md` 7.4 is the last open eval task.
- **Why it matters:** eval-1k decided the pipeline. pool5k (5,045 items, `evals/sets/pilot-v0-pool5k/`) is a bigger, differently drawn corpus. It checks that the route order (Gemini ≥ 12B-v2 ≥ image-only, `eg2-blend` best) isn't an artefact of eval-1k.
- **Second purpose:** it is the first real-scale use of the Batch runner (01).

## What

1. **Quote.** Run `privacy.estimate` for `gemini-batch-v2` on the pool5k items after `filter_items(provider="gemini")`, at the day's batch price. Expect about $7. The owner approves before submission.
2. **Captions.**
   - Gemini batch on the eligible pool5k items.
   - g12b-q4-v2 locally on the same items. This is free, at about 560 img/h, so about 9 h on the M5 Max.
   - EG2 caption and image embeddings for both arms.
3. **Search.** Score the frozen routes (`evals/sets/eval-1k/frozen-routes.json`) on the pool5k queries:
   - the owner notes;
   - the queries in `search-known-item.jsonl`;
   - any eval-1k queries whose source photos are in pool5k.
4. **Judging.**
   - New pool pairs are judged with judge-v2 (Gemini; the cost is in the quote), or by Opus on the subscription if the owner prefers.
   - Unjudged pairs count as non-relevant, and the coverage is reported.
5. **Report.** `evals/runs/<date>_pool5k-confirm.md`:
   - nDCG@20 per arm with paired bootstrap CIs;
   - route order against eval-1k;
   - any reversal flagged;
   - batch-vs-sync label agreement on the overlap with eval-1k, if any.
6. **Close-out.** Tick 7.4 in tasks.md. If the order changes, say so in `evals/RECOMMENDATION.md`, and don't silently re-decide.

## Done when

The report is committed, 7.4 is ticked, and its spend is logged in `evals/spend.jsonl`.

## Depends on

- 01.
- Owner spend approval.

## Comments
Mazda has given permission to spend the money
