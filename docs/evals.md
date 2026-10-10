# Running evals with Vally

Shoebox uses [Vally](https://microsoft.github.io/vally/) to run and grade evals, one trial per image. Python handles everything statistical (design D1):

| Layer | What it does | Where |
|---|---|---|
| `shoebox eval gen` | turns a set into a Vally spec plus `candidates.json` | `src/shoebox/evals/gen.py` |
| Executor `shoebox-vlm` | replays the keyed label store (or fills it live) | `evals/vally/plugins/shoebox-vlm.js` |
| Graders `label-status`, `field-match`, `set-match` | grade one trial each; record predicted and expected values | `evals/vally/plugins/shoebox-graders.js` |
| `shoebox eval report` | per-candidate metrics, agreement, latency, cost → `evals/runs/` | `src/shoebox/evals/report.py` |
| `shoebox eval search-*`, `gen-search` | known-item search: queries, rankings per route, spec, report (see "Known-item search") | `src/shoebox/evals/search.py` |
| Executor `shoebox-search`, grader `retrieval` | replay stored rankings; grade one query | `evals/vally/plugins/shoebox-search.js`, `shoebox-graders.js` |

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

## Known-item search

An early slice of tasks 6.4 (the `retrieval` grader) and 7.2 (retrieval aggregation). The question it answers: given the owner's own short description of a photo, does each search route find that photo? Everything runs locally: query embeddings come from EG2 on the local Ollama, FTS is an in-memory SQLite database.

```sh
uv run shoebox eval search-queries --set pilot-v0         # evals/sets/pilot-v0/search-known-item.jsonl
uv run shoebox eval search-run --set pilot-v0 [--routes a,b] [--candidates c1,c2] [--no-sensitivity]
uv run shoebox eval gen-search --set pilot-v0 [--routes a,b]   # evals/vally/generated/pilot-v0-search/
cd evals/vally
npm run lint:search
npm run eval:search -- --model "$(python3 -c "import json;print(','.join(json.load(open('generated/pilot-v0-search/routes.json'))['routes']))")"
cd ../.. && uv run shoebox eval search-report --results ~/mnt/shoebox/results/vally/pilot-v0-search/<ts>/results.jsonl
```

### Queries

`search-queries` reads `evals/sets/<set>/labels.jsonl` and `items.jsonl` and writes `search-known-item.jsonl`, one line per query: `{query_id, text, relevant: [asset_id...], relevant_groups: [[asset_id...]...], source: "owner_notes"}`.

- **Text** is the owner's `notes` field, whitespace collapsed. An item with an empty note gives no query.
- **Relevant** is the noted item plus its capture-group siblings (`group_id`). **`relevant_groups`** splits it into targets, one per capture group (or ungrouped item), and the metrics score each target once, at its first-ranked member: a DNG+JPG pair is one target, so finding either file at rank 1 is a perfect result.
- **Deduplication:** one query per distinct note, compared after collapsing whitespace and case-folding. Items that share a note pool their relevant sets: a pair with one note is one query, and if two different groups (or ungrouped items) carry an identical note, the query's relevant set is their union. The command prints each such merge. Members of one group with different notes give separate queries, each with the whole group as relevant.
- **`query_id`** is `q-` + the first 12 hex of sha256 of the normalised note: stable, and it doesn't reveal the text.

### Routes

Every route ranks the **whole corpus** (the set's distinct asset_ids) for every query. The *stored* order breaks ties by `asset_id` ascending and puts items a route can't score last, by `asset_id`, but that order is only for determinism: the metrics treat ties symmetrically (see the grader below). EG2 vectors come from the `eg2-bf16` stores: image vectors from the image-only run, caption vectors from the run made with `shoebox embed run --caption-from <cand>`. Each query is embedded once with the EG2 query prompt (`task: search result | query: {text}`) at 768 d.

| Route | Score |
|---|---|
| `eg2-image@768`, `eg2-image@256` | cosine of query and image vector. At 256 both are truncated to the first 256 values and L2-normalised again (MRL). |
| `eg2-caption:<cand>` | cosine of query and the caption vector of `<cand>`'s caption (768 d). Items with no caption vector rank last. |
| `eg2-blend:<cand>` | `w·cos(image) + (1−w)·cos(caption)`, **w = 0.5, fixed**. Items with no caption vector get their image score alone. |
| `eg2-blend@w0.3:<cand>`, `eg2-blend@w0.7:<cand>` | the same at w = 0.3 / 0.7: a sensitivity table only, **not tuned on held-out data** (there are no dev queries yet). Don't pick w from it. |
| `fts:<cand>` | SQLite FTS5 BM25 over `<cand>`'s caption and tags (below). Unmatched items, and items with no caption, rank last. |
| `hybrid:<cand>` | reciprocal-rank fusion, k = 60, of `eg2-blend:<cand>` and `fts:<cand>`: `Σ 1/(60 + rank)`, rank 1-based. An item only counts for a route that scored it, so FTS's unmatched tail adds nothing. |

**FTS construction** (`FTS_VERSION = 1`, hashed):

- One in-memory table `fts5(asset_id UNINDEXED, caption, tags, tokenize='porter unicode61')`, one row per item whose `ok` label record has a non-empty caption; `tags` is the tag list joined by spaces. Never on disk, never on `~/mnt`.
- Query terms: the note case-folded, split into letter/digit runs (`[^\W_]+`, the same split as `unicode61`), English stopwords removed (a fixed function-word list in `search.STOPWORDS`, with no domain words such as "photo"), duplicates dropped, in order.
- MATCH expression: each term double-quoted, joined with ` OR `. For example "Night scene in Venice of bridge over canal" → `"night" OR "scene" OR "venice" OR "bridge" OR "canal"`. Porter stemming applies to the quoted terms too. No terms left → no matches.
- Score = `-bm25(docs)` with default column weights (higher is better).

**Missing captions.** `gemini-low` has one `invalid` record, so that item has no caption and no caption vector for that labeller: it gets its image score in `eg2-blend`, and ranks last in `eg2-caption`, `fts` and (if unmatched) `hybrid`. Each route's `coverage` records the counts.

### Stored rankings

`search-run` writes `<shoebox_root>/results/search/<set>/<route_dir>/<params_hash>.jsonl`, one line per query: `{query_id, route, ranking: [asset_id × corpus], scores: [float|null × corpus]}`. Next to it, `<params_hash>.params.json` holds the route definition and coverage. `route_dir` is the route name with `:` → `_` (the share is SMB).

- **`params_hash`** hashes the definition: route, set, the sha256 of the queries file and of the corpus, the EG2 `{candidate, model_id, params_hash, query_prompt}`, dim, the caption-embedding store, the label (caption) source `{candidate, model_id, params_hash}`, `w`, RRF `k`, the FTS settings and stopword-list hash, and the tie-break rule.
- Re-running writes byte-identical files (verified on pilot-v0, including the Ollama query embeddings).
- `results/search/<set>/index.json` records the latest rankings per route. `gen-search` reads it and refuses a route with no rankings or rankings made from another version of the queries file.
- A route whose inputs are missing (e.g. no caption run yet for that labeller) is skipped with a message and the command exits 1; the others are still written.

### Vally layer

- **Spec** (`gen-search`): `evals/vally/generated/<set>-search/eval.yaml`, one stimulus per query: `name` = `query_id`, `prompt` = the query text, tags `source`, `relevant_n` and `targets_n`, one grader `retrieval` with config `{relevant, k: [1, 5, 10, 20], groups}`. `routes.json` alongside maps each route to `{params_hash, path, coverage, ...}` and records the queries file and its sha256.
- **Executor `shoebox-search`** (replay only): `--model` is a route name. It returns the stored line for `(route, query_id)` as JSON. A route not in `routes.json`, or a query missing from the rankings, throws `shoebox-search replay: missing key (route=…, query_id=…, params_hash=…) … [set=…, routes_file=…]`. Its `custom` event is `data.shoebox = {kind: "search", set, routes_file, queries_sha256, mode, route, query_id, params_hash, n_ranked}`.
- **Grader `retrieval`** (config `{relevant, groups, k: [1, 5, 10, 20]}`): binary relevance, ranks 1-based. It reads the ranking and its scores from the trajectory.
  - **Ties.** Items with exactly equal scores form a tie block, and the unscored tail is one block. Every metric is its **expectation over random orders within tie blocks**, so the stored `asset_id` order decides nothing. This matters: RRF gives exact ties whenever two items swap ranks between blend and FTS (1/61 + 1/62 = 1/62 + 1/61), and FTS leaves many relevant items in the unscored tail. `rank_best` / `rank_worst` bound the first relevant item's rank over tie orders; `tie_decided` says they differ; `rank_of_first_relevant` is the stored-order rank, kept for tracing only.
  - **Targets.** `groups` (from `relevant_groups`) partitions `relevant`. rr, hit@k and rank use the first relevant item of any target. nDCG@k = Σ over targets of 1/log2(rank + 1) at the target's first member, divided by the ideal of min(#targets, k) hits. recall@k = targets in the top k / #targets (so for a one-target query recall@k = hit@k). judged@k = relevant items in the top k / min(k, n): the pool-coverage figure.
  - Metadata `{rank_of_first_relevant, rank_best, rank_worst, rank_expected, tie_decided, rr, rr_best, rr_worst, hit@k, recall@k, ndcg@k, judged@k (k in config.k and 5), relevant_n, targets_n, n_ranked, relevant, ranked}`.
  - **Pass iff hit@5 under every tie order**; score = expected hit@5. It's a convenience verdict; the metrics live in the report.
- Vally turns `:` in route names into `-` in its per-trial artifact folders; `results.jsonl` keeps the real names.
- `npm run grade:search < results.jsonl` re-grades with no executor (for example after editing the queries and regenerating the spec).

### Report

`search-report --results <results.jsonl or regraded.jsonl> [--routes-file F] [--date D]` writes `evals/runs/<date>_<set>-search.json` and `.md`. It recomputes every per-query metric in Python (`search.query_metrics`, the same tie- and target-aware definitions as the grader) from the trajectory's ranking and scores and the queries file, and checks rank, rr, hit@5 and nDCG@10 against the grader's metadata.

- **Per route:** n queries, MRR with a 95% percentile-bootstrap CI (1000 resamples of queries, seed 20261010), the **MRR tie range** (`mrr_worst`–`mrr_best`: every tie broken against / for the relevant item) and **`tie_decided_queries`**, hit@k, nDCG@k and recall@k for k = 1, 5, 10, 20 (nDCG@20 is the D11 gate metric), **pool coverage** `judged@k`, mean and median expected rank of the first relevant item, `params_hash`, coverage, and per query (keyed by `query_id`) the expected/best/worst/stored rank, `tie_decided`, rr and hit/recall/nDCG at every k.
- **Paired comparison:** each route vs `eg2-image@768` on reciprocal rank: wins, losses, ties, mean RR delta and a two-sided exact sign test over the non-tied queries. The **best caption-based route** (highest MRR among caption, blend at w = 0.5, fts and hybrid; ties go to the first name) is called out. It's chosen on the same queries, so its p-value is optimistic.
- **Per labeller:** MRR (and hit@5) of its caption, fts, blend and hybrid routes and their mean: whose captions make photos easiest to find. Plus the blend-weight sensitivity table, labelled as not tuned on held-out data.
- **`complete`** is false, with `blockers`, when a route lacks a usable trial for some query, a trial errored (missing key), the grader disagrees with the Python metrics, a trial replayed another `params_hash`, or the queries file changed since `gen-search`.
- **No text:** reports hold route names, `query_id`s and numbers only, never query or caption text. The query text stays in the set file in the repo; `results.jsonl` (which carries it in the prompt) stays under `shoebox_root`.

**Limits.**

- **Relevance is one known item, not a judged pool.** Each query was judged only against the photo(s) it was written for. Every other item is *assumed* non-relevant without a judgment, so a photo that also fits a generic note counts as a miss. `judged@k` is therefore about 1/k for k > 1. Read the numbers as known-item metrics (MRR, hit@k); nDCG and recall at large k only measure where the one known target landed, not how many good results a route found.
- **Ties** are scored symmetrically (above); the report's MRR tie range shows how much they could swing a route.
- **The corpus is tiny:** with 45 items most routes reach rank 1 for most queries. The test separates FTS and caption-only routes from the rest, but it can't separate the strong routes from each other. A larger corpus with distractors is needed before it can gate anything.
