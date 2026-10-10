# Search eval 2026-10-11 — pilot-v0-pool5k (known-item)

- Results: `/Users/mazhewitt/mnt/shoebox/results/vally/pilot-v0-pool5k-search/2026-10-10T22-13-06-897Z/results.jsonl`
- Routes file: `/Users/mazhewitt/projects/shoebox/evals/vally/generated/pilot-v0-pool5k-search/routes.json`
- Queries: 39 (owner_notes); complete: yes
- Corpus: 5042 items (45 targets, 4997 distractors); excluded: 3

## Routes

Metrics are expectations over random tie orders. MRR tie range = MRR if every tie broke against / for the relevant item; ties = queries whose first relevant rank depends on the tie order.

| route | n | MRR | MRR 95% CI | MRR tie range | ties | hit@1 | hit@5 | hit@10 | nDCG@10 | nDCG@20 | recall@20 | mean rank | median rank | params_hash |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| eg2-image@256 | 39 | 0.658 | 0.511–0.787 | 0.658–0.658 | 0 | 0.564 | 0.744 | 0.795 | 0.687 | 0.687 | 0.795 | 10.3 | 1.0 | `dedc7d7640b05fe3` |
| eg2-image@768 | 39 | 0.654 | 0.512–0.776 | 0.654–0.654 | 0 | 0.564 | 0.769 | 0.821 | 0.689 | 0.708 | 0.897 | 8.1 | 1.0 | `423bb421deff6da0` |

## Pool coverage

judged@k = the fraction of each route's top k that carries a relevance judgment (only the noted item(s) were judged; everything else is assumed non-relevant). Mean over queries.

| route | judged@1 | judged@5 | judged@10 | judged@20 |
|---|---|---|---|---|
| eg2-image@256 | 0.564 | 0.174 | 0.092 | 0.047 |
| eg2-image@768 | 0.564 | 0.179 | 0.095 | 0.051 |

## Best caption-based route vs eg2-image@768

Not available (baseline or caption-based routes missing).

All routes vs eg2-image@768 (reciprocal rank, paired):

| route | wins | losses | ties | mean RR delta | sign-test p |
|---|---|---|---|---|---|
| eg2-image@256 | 6 | 11 | 22 | 0.004 | 0.3323 |

## Per labeller: whose captions make photos easiest to find

MRR per route family (hit@5 in brackets); mean over the four when all four ran.

| labeller | caption | fts | blend (w=0.5) | hybrid | mean MRR | items w/o caption | w/o caption vector |
|---|---|---|---|---|---|---|---|

## Blend weight sensitivity (MRR) — not tuned on held-out data

Shown only to see how much w matters. w=0.5 is the fixed primary; do not pick w from this table.

| labeller | w=0.3 | w=0.5 | w=0.7 |
|---|---|---|---|

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
