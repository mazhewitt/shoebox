# Owner-rephrase subset: a small robustness check on the frozen routes

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Where the rephrasings come from.** In the review page's Searches tab (task 6.4), the owner judged 30 Opus-written queries for realism: 29 of 30 "would type" (`evals/sets/eval-1k/QUERIES.md`). For some, the owner also wrote how they would actually phrase the search.
- **The answers file** is `~/Downloads/answers-ca9391b30efc1e35-2.json`. It has 30 search entries, each with a verdict, and **7** have a non-empty `rephrase`. (Earlier in the session this was called "8 rephrasings"; the file has 7.)
- **What they test.** These are the only queries in the set written by the person who will search. The question: does the chosen route (`eg2-blend`) rank the same targets as well under the owner's own wording as under Opus's?

## What

- **Ingest the 7 rephrasings** as a subset `owner_rephrase` in a new `queries-v1-rephrase.jsonl`.
  - `queries-v1.jsonl` is not touched.
  - Each one is linked to its original `query_id`, inherits its targets and split, and carries provenance `owner (review page ca9391b30efc1e35)`.
  - Keep the answers file out of the repo. Copy only the rephrased text and the link.
- **Score both versions of each query** on the frozen routes (`evals/sets/eval-1k/frozen-routes.json`) for the shipped arms (`gemini-medium-v2`, `g12b-q4-v2`) and image-only:
  - the target's rank;
  - nDCG@20 on the existing pool.
- **Unjudged pairs.** A rephrasing may retrieve items outside the pool; list how many. Any new pool pairs would need judging, which needs owner approval for Gemini spend. Opus is the default judge here, so no USD.
- **Report** a short section in a new run note. With n = 7 it is descriptive only, not gated.

## Done when

- `evals/runs/<date>_eval-1k-owner-rephrase.md` shows each pair's target rank (original vs rephrase) per arm, plus the count of unjudged new results.
- A unit test covers the subset loader linking each rephrase to its source query.

## Depends on

Nothing. It uses frozen artefacts.

## Comments
