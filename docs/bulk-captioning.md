# Bulk captioning

How the library gets one caption per capture: from a dedup worklist, through a guard, reusing every label
that already exists, sent as Gemini batch jobs, with a local 12B fallback for images Gemini refuses.
Design: `openspec/changes/add-pipeline-foundations/design.md` D7–D10. Spec: `bulk-captioning`.

Every command below is run, in this order, by `tests/test_bulk_docs.py` against a fake batch transport
and a fake Ollama, on synthetic worklists and derived images. Keep them exact.

## 1. Worklists

A bulk run reads a **worklist**, never an inventory frame or an items list. Build one over the latest frame
of every configured source:

```sh
shoebox dedup worklist --name all-sources
```

It writes `<shoebox_root>/worklists/all-sources.jsonl` and a counts-only summary in `evals/runs/`.
(This one is not run by the docs test: it needs real frames and pixel hashes.)

The file is JSON lines:

- a **header** first: `type: "header"`, `sources` (the configured sources it was built over),
  `frame_dates`, `provider`, `profile`, `dedup_version`, `created_at`;
- then one **capture** row per capture: `capture_id`, `canonical_asset_id`, `members`, `linked`,
  `roles`, `eligible`, `queue`, `refusal`.

`queue` decides who may caption it:

| queue | goes to |
|---|---|
| `hosted` | a hosted candidate only (`gemini-batch`, `gemini` runtimes) |
| `local` | a local candidate only (`ollama` runtime, e.g. `g12b-q4-v2`) |
| `held` | nobody: quarantined, waits for a verdict |
| `not_derived` | nobody: no derived image yet |

`--worklist` takes the name (looked up in `<shoebox_root>/worklists/`) or a path.

## 2. The guard

Before any request, `shoebox label bulk` refuses:

- an **inventory frame** (a `.parquet` file, or any file starting with the parquet magic);
- anything **without the worklist header**, such as a raw items list;
- a worklist whose header `sources` **lacks a configured source**. The message names the missing source.

Check what a run would do, without sending anything:

```sh
shoebox label bulk --worklist all-sources --candidate gemini-batch-v2 --dry-run
```

To caption by stages (dedup/06 option B) while a source is not yet in the worklist, give the reason. It is
logged in the run record:

```sh
shoebox label bulk --worklist importantfiles-only --candidate gemini-batch-v2 --partial-sources "Takeout not landed yet; staging B (dedup/06)"
```

Eval sets are exempt: `shoebox label run --candidate <c> --items evals/sets/<set>/items.jsonl` is unchanged.

Every non-dry run writes a run record at `results/bulk-runs/<candidate>/<started>_<random>.json`: the worklist,
its sources and the configured ones, `missing_sources`, the `partial_sources` reason, the label-source
chain, the counts and the outcome. No asset ids.

## 3. Reuse before spend

Before any request, each capture of the candidate's queue is looked up in the candidate's **label-source
chain**: if its canonical asset, any member or any linked asset has an `ok` label there, the capture is
skipped and counted as `reused`. A new byte-identical copy in a new source therefore costs nothing, and on a
merge the existing label is kept.

The chain is an ordered list of candidates, resolved with `runners.source_index` (each record keeps its own
`model_id`/`params_hash`). It is, in order of precedence:

1. `--label-sources a,b,...` on the command line;
2. `label_sources = [...]` in the candidate's table (not hashed: it says where to look, not how to label);
3. the built-in default: `gemini-batch-v2` → `gemini-batch-v2, gemini-medium-v2`;
4. the candidate alone.

The candidate itself is always first. A refused or truncated-and-unrecovered record is not a usable label.

The summary line reconciles the counts:

```
gemini-batch-v2: assets A -> captures C -> eligible E -> hosted H -> reused R -> to send S  (queues: ...)
```

`R + S = H`, and the queues sum to `C`. File counts (before byte dedup) are in the worklist's summary in
`evals/runs/`.

## 4. Batch jobs

`gemini-batch-v2` sends the remaining captures' canonical images as Gemini Batch API jobs
(`runners/gemini_batch.py`), with every guard of the synchronous runner: privacy checklist, hosted
eligibility (`privacy.filter_items`, re-applied per item), derived-image checks, current price, per-run cap
and project budget, each job pre-flighted at the batch price.

```sh
shoebox label bulk --worklist all-sources --candidate gemini-batch-v2
```

- **Ledger.** Each submitted job is recorded at `results/batch-jobs/gemini-batch-v2/<job_id>.json` with
  its items and state, before anything else happens.
- **Spend.** A job's actual cost is appended to `evals/spend.jsonl` when its results are downloaded,
  before its labels are stored, once per job.
- **Resume.** Re-run the same command. Open jobs are polled, downloaded jobs are finished, and no item in an
  open or finished job is resubmitted. Items of expired or failed jobs are pending again. To return early
  and leave jobs open for the next run, add `--max-poll-rounds N`.

```sh
shoebox label bulk --worklist all-sources --candidate gemini-batch-v2
```

`local` captures go to the local candidate the same way (no batch, no spend):

```sh
shoebox label bulk --worklist all-sources --candidate g12b-q4-v2
```

## 5. Refusal fallback

Gemini refuses a few ordinary photos (`PROHIBITED_CONTENT`). The fallback captions them locally:

```sh
shoebox label fallback --candidate g12b-q4-v2 --primary gemini-batch-v2,gemini-medium-v2
```

- It runs on the **final** records of the primary chain that are `invalid` because they were **refused**,
  or **truncated** with no retry that recovered them. Schema-invalid records and errors are not routed;
  they stay in the coverage report with their reason.
- **Refusal rule** (`runners.refusal_reason`): the record's `reason` if it has one (batch and Ollama
  records). Synchronous `gemini` records have none, so it is read from what they stored: the raw
  `{finishReason, blockReason}` of a blocked or empty response, else `finishReason=`/`blockReason=` in the
  error text. A `blockReason`, or a `finishReason` of SAFETY, PROHIBITED_CONTENT, RECITATION, BLOCKLIST,
  SPII or IMAGE_SAFETY, is `refused`; MAX_TOKENS is `truncated`.
- Quarantined assets (`pending`/`exclude` for local stages) are skipped.
- It is idempotent: a second run finds the 12B records final and makes no requests.

Search then resolves labels through `[gemini-batch-v2, gemini-medium-v2, g12b-q4-v2]`: a refused primary
record falls through to the 12B label, which names its own model, and `fell_through` lists the sources
passed over.
