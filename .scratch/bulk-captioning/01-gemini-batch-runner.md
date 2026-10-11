# Gemini Batch API runner with the sync runner's guards

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Decision 41: the back catalogue is captioned at batch price, 50% of standard. That is about $98 at the measured $0.00275/img for 71.6k images, against $139.96 left.

`src/shoebox/runners/gemini.py` is synchronous (`generateContent` per image). Its guards must carry over unchanged:
- the privacy checklist (`privacy.require_checklist`);
- never-send, `held_back` and per-provider consent via `privacy.filter_items(provider="gemini")`;
- the derived-JPEG check (`check_derived_jpeg`, `_check_image_path`);
- `max_usd_per_run`, the project budget, and fsynced lines in `evals/spend.jsonl`;
- `require_current_price`.

## What

- **Runtime.** A new runtime `gemini-batch` (`runners/gemini_batch.py`) that reuses `build_request`, `gemini_schema`, `parse_response`, `usage_of` and `usd_for` from `gemini.py`, not copies of them.
- **Flow:**
  1. Build a JSONL of requests, keyed by `asset_id`, from the filtered items. Inline the image as base64 `inlineData`, as sync does. Use the Files API only if the batch file size forces it; uploads are kept 48 h (research/mvp-models.md).
  2. Submit.
  3. Poll.
  4. Download the results.
  5. Parse each line with `parse_response` + `normalise_label` + label-v2 schema validation.
  6. Write to the keyed store via `store.append`, exactly like `run_and_store` records.
- **Job ledger.** `results/batch-jobs/<candidate>/<job_id>.json` holds the item IDs, the submit time, the state, the estimated USD and the actual USD.
  - Resumable: a restart polls open jobs and never resubmits an item that is in an open or finished job.
- **Spend:**
  - **Pre-flight** estimates with `privacy.estimate` at the batch rate. A new candidate key, `price_batch_discount = 0.5`, is excluded from the hash by the `price_` prefix. Refuse when the estimate would breach `max_usd_per_run` or the project budget.
  - **On download:** log the actual cost from `usageMetadata` to `evals/spend.jsonl`, one line per job with `n`.
  - **Chunking:** the Tier 1 queue is about 3M tokens, about 20 sequential jobs for the library (research/mvp-models.md). Chunk the jobs to fit, and check the cap between jobs.
- **Price.** Re-check the batch price and the discount on the pricing page on the day, and update `price_date`. The runner already refuses after `price_valid_until`.
- **Failures:**
  - **Truncation:** a response that hits `max_output_tokens` is `invalid` with reason `truncated`, then retried once via the candidate `retry` table (sync semantics).
  - **Refusals:** `blockReason`/`finishReason` `PROHIBITED_CONTENT`/`SAFETY` are recorded as `invalid`, reason `refused`, for 03.
  - **Expiry:** expired or failed jobs return their items to pending.

**Candidate identity (decided).** Add `gemini-batch-v2` as a **distinct candidate** with its own `params_hash`, not a reuse of `gemini-medium-v2`'s.
- The prompt, schema, model, media resolution and thinking level are copied unchanged.
- `runtime = "gemini-batch"` and the runner version are hashed.
- Reason: the store's contract is that a key names how a record was produced. Batch serving may differ (thinking budget, model snapshot), and must not be silently mixed into the eval arm's store.
- 02 measures whether batch output is equivalent. Search reads both through `label_source`, so one caption space still holds.

## Done when

- Unit tests with a fake transport:
  - every guard refuses: unconfirmed checklist, a never-send item, a `held_back` item, an over-cap estimate, an expired price;
  - resume after a crash mid-poll doesn't resubmit;
  - parse and store parity with the sync runner on canned responses;
  - refused and truncated items are recorded as such.
- A **live smoke batch of 20 eval-1k images** (needs owner OK, about $0.03):
  - every label validates;
  - spend is logged;
  - labels are compared field by field with `gemini-medium-v2` on the same IDs.
- `docs/evals.md` or a runner doc describes the job ledger and how to resume.

## Depends on

- Nothing in code. The live smoke batch needs the owner's spend approval (house rule: approve each new Gemini spend).

## Comments
