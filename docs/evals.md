# Running evals with Vally

Shoebox uses [Vally](https://microsoft.github.io/vally/) to run and grade evals, one trial per image. Python handles everything statistical (design D1):

| Layer | What it does | Where |
|---|---|---|
| `shoebox eval gen` | turns a set into a Vally spec plus `candidates.json` | `src/shoebox/evals/gen.py` |
| Executor `shoebox-vlm` | replays the keyed label store (or fills it live) | `evals/vally/plugins/shoebox-vlm.js` |
| Graders `label-status`, `field-match`, `set-match` | grade one trial each; record predicted and expected values | `evals/vally/plugins/shoebox-graders.js` |
| `shoebox eval report` | per-candidate metrics, agreement, latency, cost → `evals/runs/` | `src/shoebox/evals/report.py` |

Vally's own score is a mean of per-trial pass/fail. **It isn't the metric.** Read the report instead.

## Install

```sh
cd evals/vally
npm ci            # Node 24; installs the pinned @microsoft/vally-cli and @microsoft/vally 0.18.0
npm run vally -- --version   # 0.18.0
```

- **Versions.** Both packages are pinned exactly to `0.18.0`, which was npm `latest` on 2026-10-10 (no newer release). See `"vally"` in `package.json`. Before upgrading, re-run `tests/test_eval_vally_e2e.py` and `tests/test_eval_plugins.py`.
- **Install scripts.** npm 11 doesn't run the install scripts of `koffi` and `@vscode/deviceid` (it warns about `allow-scripts`). Shoebox doesn't need them: the custom executor never starts Copilot, and `deviceid` exists only for telemetry. Leave them unapproved.
- **Telemetry.** Every npm script sets `VALLY_TELEMETRY_OPTOUT=1`. If you call `npx vally` directly, export it first.
- **Plugins** are plain ESM JavaScript, with no build step. They import `computeMetrics` from `@microsoft/vally`.

## 1. Fill the store

Inference happens in Python, never in Vally (design D2):

```sh
uv run shoebox label run --candidate rules-v1 --items evals/sets/pilot-v0/items.jsonl
uv run shoebox label run --candidate e4b-bf16 --items evals/sets/pilot-v0/items.jsonl
```

## 2. Generate the spec

```sh
uv run shoebox eval gen --set pilot-v0 --candidates rules-v1,e4b-bf16,g12b-q4 [--mode replay|live]
```

This writes `evals/vally/generated/pilot-v0/eval.yaml` and `candidates.json`. Both start with "generated — do not edit".

- **Inputs:**
  - `evals/sets/<set>/items.jsonl`;
  - the `infer-v1` derived index;
  - `evals/sets/<set>/labels.jsonl`, if present.
- **Stimuli:**
  - There's one stimulus per item that has an `ok` derived image whose file exists. Other items are skipped, with a message on stderr. They're also recorded in `candidates.json` (`skipped_items`) and counted in the spec's description, so the report can see them.
  - Each stimulus is named by its `asset_id`, and its attachment is the absolute derived JPEG path.
  - Its tags are `bucket`, `ext`, `group_rule` (`none` when null) and `labelled` (`yes`/`no`).
- **Graders:**
  - Every stimulus gets `label-status` and `field-match` for `medium`, `digitiser` and `colour_class`.
  - A field gets `expected` only when the owner labelled it. An empty label value counts as unlabelled. An owner label of `unknown` (the owner couldn't tell) is passed through; `field-match` treats it as unlabelled and records `owner_unknown: true`.
  - `set-match` on `tags` is added only when the label has tags.
- **`candidates.json`** maps each candidate name, which is what Vally calls the model, to `{model_id, params_hash, store_dir}`. It also records `set`, `mode`, every set item's `asset_id` in set order (`items`, with `items_sha256`) and `skipped_items` (`{asset_id: reason}`).
  - Each candidate is resolved with `shoebox label resolve --candidate X`, run in a child process.
  - `--resolutions FILE`, a JSON object `{name: {model_id, params_hash, store_dir}}`, replaces that lookup. Tests use it to run offline.
  - Candidates whose runtime is `embed` (from `config/candidates.toml` and `config/candidates.d/*.toml`) aren't label candidates, so they're skipped with a message.
- **Always regenerate** after the store's params change (a new `params_hash`) or after labels change.

## 3. Lint and run

Run these from `evals/vally`:

```sh
npm run lint                                  # vally lint -e generated/$SET/eval.yaml --grader-plugin … --strict
npm run eval -- --model rules-v1,e4b-bf16     # replay; output under $VALLY_OUT or ~/mnt/shoebox/results/vally/$SET
```

Both scripts default to `SET=pilot-v0`. The second one is equivalent to:

```sh
export VALLY_TELEMETRY_OPTOUT=1
npx vally eval --eval-spec generated/pilot-v0/eval.yaml \
  --executor-plugin "$PWD/plugins/shoebox-vlm.js" \
  --grader-plugin   "$PWD/plugins/shoebox-graders.js" \
  --model rules-v1,e4b-bf16 \
  --output-dir ~/mnt/shoebox/results/vally/pilot-v0 --workers 1
```

Vally 0.18.0 facts that this depends on, all verified:

- **Plugin paths must be absolute.** Relative `--executor-plugin` and `--grader-plugin` paths resolve against the **eval spec's directory**, not the shell's working directory. That's why the scripts use `$PWD/plugins/…`.
- **The executor is in the spec**, under `defaults.executor: {name: shoebox-vlm, config: {mode, candidates_file}}`, so `--executor` isn't needed. `vally lint` has no `--executor-plugin` option and doesn't need one.
- **`--model a,b,c`** runs every stimulus once per candidate. In each trial, `trajectory.metadata.model` and the line's `model` field hold the candidate name.
- **Output location.** There's no `--results` flag. `--output-dir D` creates `D/<UTC timestamp>/`, which holds:
  - `results.jsonl`: one `trial-result` line per trial, then a `run-summary` line;
  - `eval-results.md`;
  - `otel-spans.jsonl`;
  - `<eval>/<asset_id>/<candidate>/<trial>/metadata.json`.

  Without `--output-dir`, the output goes to `./vally-results`, which is gitignored in `evals/vally`.
- **Filtering by tag.** `--tag labelled=yes` or `--tag bucket=leica-dng-jpg-pair` selects stimuli. `--workers` defaults to 5. Use 1 for live runs against local models.
- **Exit codes.** `vally eval` exits 0 when every trial ran, even if some graders failed, unless you pass `--require-pass`. Any trial that throws makes it exit 1.
- **Scoring.** The spec sets `scoring.threshold: 1.0`, so a trial passes only if all its graders pass. It also keeps `vally lint --strict` quiet: without it, lint warns `scoring-defaults-applied`.

### What the executor emits

- **Replay** reads `store_dir/part-*.jsonl` once per process and picks the record for `(asset_id, model_id, params_hash)` by the same rule as `shoebox.store.index`: the **first** `ok` or `invalid` record, else the latest `error` record. So live and replay return the same output.
  - **Torn lines** (an unparseable line left by a killed writer) are skipped, as `store.iter_records` does. The count goes to stderr and into any missing-key message.
  - **A missing key throws** `shoebox-vlm replay: missing key (candidate=…, asset_id=…, model_id=…, params_hash=…) in <store_dir> [set=…, candidates_file=…]`. The report parses these pairs back out, because an errored trial has no trajectory.
  - Vally records that trial as `status: "error"` with no trajectory, and the run exits 1.
  - The message contains no retry keywords (timeout, 429, rate limit), so Vally doesn't retry it.
- **Live** runs `uv run shoebox label one --candidate <name> --asset-id <id>` in the repo root and parses the last JSON line on stdout. The label runner writes through to the store, so a later replay returns the same output. Live also refuses a record whose key doesn't match `candidates.json`.
- **Output** is the label JSON for `ok`. For the other statuses it's `[shoebox:invalid] <raw>` or `[shoebox:error] <error>`.
- **Events:**
  - `user_message`;
  - `token_usage` (the `model_id` plus input and output tokens);
  - `custom` with `data.shoebox = {set, candidates_file, items_sha256, mode, candidate, asset_id, model_id, params_hash, status, usd, latency_ms, input_tokens, output_tokens, thinking_tokens, machine, runtime, record_ts}`;
  - an `error` event for `status: error`;
  - `assistant_message`.

  `metrics.wallTimeMs` is the stored provider latency.

  Vally doesn't persist executor config in results, so the `custom` event is how each trial is attributed to its `model_id` and `params_hash`.

### Graders and "unlabelled"

| Grader | Config | Passes when | `metadata` |
|---|---|---|---|
| `label-status` | none | the record's status is `ok` (`invalid` and `error` count as wrong) | `{status}` |
| `field-match` | `{field, expected?}` | status is `ok` and predicted equals expected | `{field, status, predicted, expected, abstained, unlabelled[, owner_unknown]}` |
| `set-match` | `{field: "tags", expected?}` | there are no FP or FN (score = F1) | `{field, status, predicted, expected, unlabelled, tp, fp, fn}` |

- **Abstaining:** a prediction of `unknown` is an abstention. It's recorded with `abstained: true` and fails when a label exists. The report counts it against coverage, not precision.
- **Owner `unknown`:** an expected value of `unknown` means the owner couldn't tell, so there's nothing to score against. The grader passes it as unlabelled with `owner_unknown: true`, and the report counts it in `owner_unknown_n`, not in accuracy.
- **Vally 0.18.0 has no skip or not-applicable verdict.**
  - `GraderResult` has only `passed`, `score` and `status: success|error`, and `error` means the grader itself broke.
  - Weights don't help either: a grader type with weight 0 still runs.
  - So a grader with no `expected` value **passes, with `metadata.unlabelled: true`**, and the report leaves it out of accuracy. Vally's own pass rate therefore over-counts on unlabelled items.

## 4. Re-grade with no model call

After labels are added or corrected:

```sh
uv run shoebox eval gen --set pilot-v0 --candidates rules-v1,e4b-bf16      # regenerate with the new expected values
cd evals/vally
npm run -s grade < ~/mnt/shoebox/results/vally/pilot-v0/<ts>/results.jsonl > ~/mnt/shoebox/results/vally/pilot-v0/<ts>/regraded.jsonl
```

- **What it does.** `vally grade -e … --grader-plugin … --output jsonl` re-reads the spec and matches each trajectory to its stimulus's graders. It loads no executor, so no model is called. The tests prove this by moving the store away before grading.
- **Output streams.** The JSONL goes to stdout and the human-readable summary to stderr.
- **Line format.** Re-graded lines have only `{runId, status, trajectory, gradeResult[, error]}`, with no `type`, `stimulus` or `model` fields. The candidate and asset come from `trajectory.metadata.model` and `trajectory.stimulus.name`, and for errored trials from the error message. The report handles both formats.
- **Exit code.** `vally grade` exits 1 when the input contains errored trials, such as missing keys.

## 5. Report

```sh
uv run shoebox eval report --results <results.jsonl or regraded.jsonl> [--set S] [--candidates-file F] [--date YYYY-MM-DD]
```

This writes `evals/runs/<date>_<set>.json` and `.md`.

- **The set** comes from the trials' executor events (or missing-key messages). If trials name more than one set, or `--set` disagrees with them, the command exits 2. `--set` is only needed when no trial records a set.
- **`candidates.json`** is found through the path the trials record; `--candidates-file` overrides it. The report uses its `items` and `skipped_items` to list items without a stimulus and, per candidate, `not_run` (set items with no trial).

For each candidate the report gives:
- `model_id` and `params_hash`;
- n, plus status counts (`ok`, `invalid`, `error`, `missing`, `exec_error`) and the validity rate;
- for each field, the value distribution over `ok` outputs and the abstain rate;
- accuracy, coverage and precision **over labelled items only** (with n, and `owner_unknown_n`). Accuracy = correct / labelled; coverage = answered (status `ok`, not `unknown`) / labelled; precision = correct answers / answered;
- a pairwise agreement matrix between candidates, per field, over assets where both are `ok`;
- latency p50/p90 and images/hour (3.6e6 / mean provider latency, sequential);
- tokens and USD.

`gate_eligible` is true only when `gate_blockers` is empty. Each of these adds a blocker:
- any mode other than replay;
- a trial with a missing replay key, or one where the executor failed;
- a replayed stored `error` record (a transport failure, not an output; retry with `shoebox label run`);
- a set item that `shoebox eval gen` skipped, or that a candidate has no trial for;
- no readable `candidates.json` with the set's items (completeness can't be checked), or one regenerated with different items since the run.

Run records hold aggregates, asset IDs and skip reasons only, never captions, so they can be committed.

## Dashboard

```sh
cd evals/vally && npm run serve -- ~/mnt/shoebox/results/vally/pilot-v0    # http://127.0.0.1:3200
```

The dashboard binds to 127.0.0.1. Its per-model compare (`/api/runs/:id/compare?modelA=&modelB=`) needs no judge.

## Rules

- **Gate runs are replay only.** Live mode is for dev-split exploration and for learning Vally. The report marks any live or broken run as not gate-eligible.
- **No LLM judges** (`prompt`, `panel`, `vally compare`) unless `judge_provider` is set explicitly. Their default is Copilot. The pilot uses none.
- **Keep run output under `shoebox_root`.** `results.jsonl` holds model outputs, including captions of private photos, and absolute image paths, so it stays out of git.
- **Only localhost models.** The executor never calls a model itself. Live mode goes through `shoebox label`, whose runners enforce the privacy pre-flight.
