# Live smoke batch: `gemini-batch-v2` on 20 eval-1k photos (task 8.6), 2026-10-12

Owner-approved spend. `tools/smoke_batch.py` (seed 20261012): 20 eval-1k photos with an ok `gemini-medium-v2`
record that pass the privacy filter, through the real batch runner and `HttpTransport`. Counts only.

## Attempts

| # | Schema field sent | Result | USD |
|---|---|---|---|
| 1 | `responseJsonSchema` (as the sync runner) | 2 jobs succeeded; 20 of 20 outputs ignored the schema's shape (e.g. `confidence` a number, not an object), stored `invalid` under key `dcfeb24f48fd37ec` | 0.0261 |
| 2 | `responseSchema` (OpenAPI subset) | HTTP 400 at submit: `enum` takes strings only (`rotate_cw` is an integer enum); no job created, intent marked `failed` after the provider lookup | 0 |
| 3 | `responseSchema`, non-string enums dropped (local validation keeps them exact) | 2 jobs succeeded; **20 of 20 ok** under key `1fd09dd11411cd4a` | 0.0275 |

So the Batch API ignores `responseJsonSchema`; `gemini-batch-v2` now sends `responseSchema`. Every job's spend
was in `evals/spend.jsonl` before its labels were stored. Total smoke spend $0.0536.

## Batch vs sync (`gemini-medium-v2`) on the same 20 photos

| Field | Same value |
|---|---|
| medium | 19 of 20 |
| digitiser | 18 of 20 |
| colour_class | 20 of 20 |
| rotate_cw | 20 of 20 |

- Tags: mean Jaccard overlap 0.54 (min 0.24); 12.1 vs 12.4 tags per photo.
- Caption length: 310 vs 308 characters on average.
- Tokens per photo: input 2,522 vs 2,522, output 228 vs 230.
- Cost: $0.0275 at batch price vs $0.0551 sync for the same 20 (the 50% batch discount, as quoted).

Batch labels look like the sync labels: same structured fields almost everywhere and captions of the same length.
The batch path is ready for the pool5k confirmation.
