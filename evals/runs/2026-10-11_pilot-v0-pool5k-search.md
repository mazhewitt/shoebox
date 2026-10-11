# Search eval 2026-10-11 — pilot-v0-pool5k (known-item)

- Results: `/Users/mazhewitt/mnt/shoebox/results/vally/pilot-v0-pool5k-search/2026-10-11T07-31-03-800Z/results.jsonl`
- Routes file: `/Users/mazhewitt/projects/shoebox/evals/vally/generated/pilot-v0-pool5k-search/routes.json`
- Queries: 39 (owner_notes); complete: yes
- Corpus: 5042 items (45 targets, 4997 distractors); excluded: 3

## Routes

Metrics are expectations over random tie orders. MRR tie range = MRR if every tie broke against / for the relevant item; ties = queries whose first relevant rank depends on the tie order.

| route | n | MRR | MRR 95% CI | MRR tie range | ties | hit@1 | hit@5 | hit@10 | nDCG@10 | nDCG@20 | recall@20 | mean rank | median rank | params_hash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| eg2-image@256 | 39 | 0.658 | 0.511–0.787 | 0.658–0.658 | 0 | 0.564 | 0.744 | 0.795 | 0.687 | 0.687 | 0.795 | 10.3 | 1.0 | `dedc7d7640b05fe3` |
| eg2-image@768 | 39 | 0.654 | 0.512–0.776 | 0.654–0.654 | 0 | 0.564 | 0.769 | 0.821 | 0.689 | 0.708 | 0.897 | 8.1 | 1.0 | `423bb421deff6da0` |
| eg2-blend:e4b-bf16 | 39 | 0.549 | 0.412–0.669 | 0.549–0.549 | 0 | 0.410 | 0.744 | 0.795 | 0.602 | 0.621 | 0.872 | 8.9 | 2.0 | `c3e10277e725110b` |
| eg2-caption:e4b-bf16 | 39 | 0.409 | 0.280–0.541 | 0.409–0.409 | 0 | 0.308 | 0.538 | 0.615 | 0.447 | 0.479 | 0.744 | 115.9 | 4.0 | `54c6704f4bee4aed` |
| hybrid:e4b-bf16 | 39 | 0.294 | 0.186–0.414 | 0.292–0.296 | 2 | 0.205 | 0.333 | 0.538 | 0.336 | 0.386 | 0.744 | 21.3 | 9.0 | `c678f8b1ca97592a` |
| fts:e4b-bf16 | 39 | 0.168 | 0.088–0.253 | 0.168–0.169 | 17 | 0.077 | 0.282 | 0.333 | 0.197 | 0.220 | 0.431 | 305.3 | 24.0 | `3f74abf04bb7146b` |
| eg2-blend@w0.7:e4b-bf16 (sensitivity) | 39 | 0.612 | 0.477–0.736 | 0.612–0.612 | 0 | 0.487 | 0.744 | 0.821 | 0.657 | 0.676 | 0.897 | 7.2 | 2.0 | `0683eeb88ab9f100` |
| eg2-blend@w0.3:e4b-bf16 (sensitivity) | 39 | 0.462 | 0.338–0.587 | 0.462–0.462 | 0 | 0.333 | 0.615 | 0.769 | 0.528 | 0.541 | 0.821 | 16.3 | 4.0 | `94f5fd211546821b` |

## Pool coverage

judged@k = the fraction of each route's top k that carries a relevance judgment (only the noted item(s) were judged; everything else is assumed non-relevant). Mean over queries.

| route | judged@1 | judged@5 | judged@10 | judged@20 |
|---|---|---|---|---|
| eg2-image@256 | 0.564 | 0.174 | 0.092 | 0.047 |
| eg2-image@768 | 0.564 | 0.179 | 0.095 | 0.051 |
| eg2-blend:e4b-bf16 | 0.410 | 0.174 | 0.092 | 0.051 |
| eg2-caption:e4b-bf16 | 0.308 | 0.128 | 0.072 | 0.042 |
| hybrid:e4b-bf16 | 0.205 | 0.067 | 0.062 | 0.044 |
| fts:e4b-bf16 | 0.077 | 0.056 | 0.033 | 0.022 |
| eg2-blend@w0.7:e4b-bf16 | 0.487 | 0.174 | 0.095 | 0.053 |
| eg2-blend@w0.3:e4b-bf16 | 0.333 | 0.144 | 0.090 | 0.047 |

## Best caption-based route vs eg2-image@768

`eg2-blend:e4b-bf16` vs `eg2-image@768` on reciprocal rank over 39 queries: 9 wins, 11 losses, 19 ties; mean RR delta -0.105; two-sided sign test p = 0.8238. Chosen on the same queries: the p-value is optimistic.

All routes vs eg2-image@768 (reciprocal rank, paired):

| route | wins | losses | ties | mean RR delta | sign-test p |
|---|---|---|---|---|---|
| eg2-image@256 | 6 | 11 | 22 | 0.004 | 0.3323 |
| eg2-blend:e4b-bf16 | 9 | 11 | 19 | -0.105 | 0.8238 |
| eg2-caption:e4b-bf16 | 7 | 18 | 14 | -0.244 | 0.0433 |
| hybrid:e4b-bf16 | 4 | 29 | 6 | -0.360 | 0.0000 |
| fts:e4b-bf16 | 3 | 33 | 3 | -0.486 | 0.0000 |
| eg2-blend@w0.7:e4b-bf16 | 9 | 8 | 22 | -0.042 | 1.0000 |
| eg2-blend@w0.3:e4b-bf16 | 7 | 16 | 16 | -0.192 | 0.0931 |

## Per labeller: whose captions make photos easiest to find

MRR per route family (hit@5 in brackets); mean over the four when all four ran.

| labeller | caption | fts | blend (w=0.5) | hybrid | mean MRR | items w/o caption | w/o caption vector |
|---|---|---|---|---|---|---|---|
| e4b-bf16 | 0.409 (0.54) | 0.168 (0.28) | 0.549 (0.74) | 0.294 (0.33) | 0.355 | 57 | 57 |

## Blend weight sensitivity (MRR) — not tuned on held-out data

Shown only to see how much w matters. w=0.5 is the fixed primary; do not pick w from this table.

| labeller | w=0.3 | w=0.5 | w=0.7 |
|---|---|---|---|
| e4b-bf16 | 0.462 | 0.549 | 0.612 |

## Settings

- Blend w = 0.5; RRF k = 60; FTS5 tokenizer `porter unicode61`.
- Bootstrap: 1000 resamples, seed 20261010.

## Notes

- Known-item search: each query is an owner note; relevant = the noted item(s) and capture-group siblings, one target per capture group (a DNG+JPG pair is one target). Only those items were judged for the query: every other item is ASSUMED non-relevant without a judgment, so a photo that also fits a generic note counts as a miss. Pool coverage (judged@k: the judged fraction of the top k) is therefore about 1/k; the metrics are known-item metrics, not judged-pool metrics.
- Ties: every metric is its expectation over random orders within blocks of exactly equal score (the unscored tail is one block), so the stored asset_id tie-break decides nothing. mrr_best/mrr_worst bound MRR over tie orders; tie_decided_queries counts queries whose rank of the first relevant item depends on the tie order (common for hybrid RRF and FTS).
- Blend weight w=0.5 is fixed, not tuned. The w=0.3/0.7 sensitivity table is NOT tuned on held-out data and must not be used to pick w from these queries.
- The best caption-based route is chosen on these same queries, so its sign-test p-value vs eg2-image@768 is optimistic (selection bias). n is small: treat all differences as indicative.
- MRR CI: percentile bootstrap over queries, 1000 resamples, seed 20261010.
- Items with no caption: blend uses their image score; caption/FTS routes rank them last.
- Vally's pass = hit@5 under every tie order (score = expected hit@5); its overall score is not the metric.
