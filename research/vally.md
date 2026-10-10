# Vally as the eval harness for Shoebox

Research date: 2026-10-10. Sources are primary only:
- the Vally docs at https://microsoft.github.io/vally/;
- the published npm packages `@microsoft/vally@0.18.0` and `@microsoft/vally-cli@0.18.0` (compiled JS plus `.d.ts`). These stand in for the source repo, which isn't public (see Q1);
- a few throwaway experiments run against those packages in a scratch directory.

File paths written as `core:dist/...` are inside `@microsoft/vally@0.18.0`. Paths written as `cli:dist/...` are inside `@microsoft/vally-cli@0.18.0`. Anything not confirmed from docs, package code or an experiment is marked **UNVERIFIED**.

The question is "how do we use Vally well here, and where does it not fit?", not "should we use it". The owner wants to learn Vally while building Shoebox's evals.

---

## Summary

- **What it is.** Vally is Microsoft's eval platform for **AI agents**, written in TypeScript on Node ≥ 22.14.
  - It is distributed on npm: `@microsoft/vally-cli`, `@microsoft/vally` and `@microsoft/vally-server`. The licence is MIT.
  - It is pre-1.0. It went from 0.3.0 (2026-04-29) to 0.18.0 (2026-10-06), with a minor release every 1–3 weeks, and the docs show breaking changes and deprecations between releases.
  - The docs link to `github.com/microsoft/vally`, but that repo returns 404 today, so the npm tarballs are the only source available.
- **The pipeline** is: stimulus (a prompt plus graders) → executor → trajectory (an event log plus metrics) → graders → score.
  - There are five plugin seams: executors, graders, eval providers, reporters and lint.
  - Graders score **one trial at a time** (pass/fail plus a 0–1 score). The eval-level score is the **mean of per-stimulus scores**, plus pass@k and flakiness across repeated trials. There is no notion of dataset-level metrics such as precision, recall or confusion matrices.
- **Copilot is only the default, not a requirement.** The built-in executor and the default LLM judge use GitHub Copilot. A **custom executor plus a BYOK judge endpoint runs with no GitHub/Copilot credentials at all.** I verified this in a clean `HOME` with no tokens (see the experiments section). The Copilot SDK is still installed as a dependency, and LLM judges still run inside its runtime.
- **Images:** a stimulus can carry images via `attachments: [path, ...]`. The executor receives absolute file paths and does the encoding itself. Verified end to end: local **Gemma 4 E4B via Ollama** got a real image and returned schema-constrained JSON for colour/mono, which a custom grader scored against the label.
- **Datasets:**
  - Stimuli are YAML. An **eval-provider plugin** can turn a JSONL/CSV file into stimuli (verified), but `vally grade` can't load that plugin. So **generate `eval.yaml` from the ground-truth JSONL** with a small Python step. 1,000 stimuli loaded and ran fine.
  - **Re-grading without re-inference works** (verified): `vally grade --eval-spec eval.yaml < results.jsonl`. Fixing a label in the spec and re-grading changes the verdict.
- **The LLM judge (`prompt` grader) is text-only.** It reads the output, timeline and metrics as text files. It never sees the image, so it can't judge whether a caption matches the photo. It can be pointed at any OpenAI-compatible endpoint (`judge_provider`). This worked against local Ollama with no Copilot.
- **Gaps that matter for Shoebox:**
  - No per-stratum precision/recall and no retrieval metrics across the set.
  - Cost is only represented as Copilot "nano-AIU", so USD cost isn't native.
  - No inference cache keyed by params.
  - No resume after a crash.
  - `vally compare` always uses an LLM judge.
  - **`vally experiment run` can't load custom executor or grader plugins in 0.18.0.**
  - Vally's own docs say: "Running generic LLM benchmarks… Other frameworks may be simpler."

## Verdict

**Use Vally as the per-image run, grade and report layer. Keep metrics, gates and the keyed output store in Python.** Concretely:

1. **Python (`shoebox eval gen`)** turns a frozen ground-truth set (`evals/sets/*.jsonl`) into a generated `eval.yaml`.
   - One stimulus per asset.
   - Attachment = the JPEG derivative.
   - Tags = `split`, `era`, `source`, `colour_gt` and hard-case flags.
   - The label goes in the grader config.
2. **A Vally executor plugin (`shoebox-vlm`, TypeScript)** runs or replays the model call.
   - **Recommended for gate runs: `replay` mode.** It reads the output that Shoebox's own Python runner already wrote, keyed by `(asset_id, model_id, params_hash)`. This keeps one inference path, shared with the bulk job (PLAN §1 and Gate G), and the keyed store doubles as the cache.
   - **`live` mode** calls the Gemini API, Ollama or an OpenAI-compatible server directly. It's good for learning Vally and for quick dev-split runs.
3. **A Vally grader plugin (`field-match`, TypeScript)** compares one JSON field with the label. It writes `{predicted, expected}` into `GraderResult.metadata`, so every trial record carries what the aggregator needs. Re-grading after a label fix needs no re-inference.
4. **Python (`shoebox eval report`)** reads `results.jsonl` and does the rest:
   - per-stratum confusion matrices, precision/recall and re-weighted estimates;
   - USD cost and latency;
   - head-to-head deltas (McNemar);
   - `evals/gates.toml` checks;
   - writing `evals/runs/<date>_<stage>_<config-hash>.json`.

   Vally's `--require-pass` is not the gate.
5. **Vally-native extras that are worth learning:**
   - `--model a,b,c` multi-model runs and `--param` overrides;
   - `--tag split=test` selection and `vally lint`;
   - `vally serve` for the dashboard and Results API (judge-free within-run model comparison);
   - the `prompt` judge for text-only caption criteria, pointed at Gemini or local via `judge_provider`.

| Shoebox need | Fit |
|---|---|
| Image + prompt → VLM → structured JSON (Gemini API, Gemma via Ollama/MLX) | **Custom executor.** Verified with Ollama. |
| Embedding route scored by retrieval | **Custom executor and grader, one stimulus per query.** Corpus embedding happens outside Vally. |
| Ground-truth sets of 300–3,000, stratified, frozen dev/test | **Outside** (sampling). Represented in Vally as generated YAML plus tags. Split selection with `--tag` is native. |
| Per-field accuracy | **Near-native.** The eval score is the mean of 0/1 per-stimulus scores. Per stratum via `--tag`. |
| Per-field precision/recall and confusion per stratum | **Outside** (Python on `results.jsonl`). |
| Retrieval recall@k, nDCG | **Custom grader.** Score = nDCG per query; the eval score is the mean. Recall@k goes in metadata and is aggregated outside. |
| LLM judge for caption quality | **Text-only judge native** (`prompt`, BYOK). **Judging against the image needs a custom grader or happens outside.** |
| Tokens / latency | **Native** (`token_usage` events, `wallTimeMs`, Results API means). |
| USD cost, throughput | **Executor emits it; aggregation outside.** |
| Gates from `gates.toml`, precision ≥ 0.98 per stratum | **Outside.** |
| Re-run on model/param change; compare runs head to head | Re-run: **native** (`--model`, `--param`, executor config). Head-to-head on fields: **outside**, or `vally serve` within-run compare. `vally compare` is LLM-judge only. |
| Outputs keyed by `(asset_id, model_id, params_hash)`, re-gradeable | Re-grade: **native**. Keyed store and cache: **outside** (Shoebox store, read by the executor). |

---

## Q1. What Vally is

- **Purpose.** "An extensible eval platform for making AI projects more reliable, efficient, and capable", built around agent trajectories (https://microsoft.github.io/vally/). Its own positioning (https://microsoft.github.io/vally/concepts/why-vally/):
  - agent behaviour such as tool calls, multi-turn and trajectories → "Use Vally — built for this";
  - "Running generic LLM benchmarks (MMLU, HumanEval, etc.) → Other frameworks may be simpler".
- **Language and runtime.** TypeScript compiled to ESM. `"engines": {"node": ">=22.14.0"}` (core and cli `package.json`).
  - Core dependencies include **`@github/copilot-sdk` pinned at `1.0.14`**, plus `@modelcontextprotocol/sdk`, `zod`, `yaml` and `js-tiktoken` (core `package.json`).
  - The CLI also pulls in OpenTelemetry, `@azure/monitor-opentelemetry-exporter` and `@vscode/deviceid` (cli `package.json`).
- **Install.** The docs say `npm install -g @microsoft/vally-cli`. There's an optional library `npm install @microsoft/vally`, and a server package `@microsoft/vally-server` for the dashboard and REST API (https://microsoft.github.io/vally/get-started/install/).
  - A project-local install (`npm install @microsoft/vally-cli` then `npx vally`) works fine. I used that for the experiments.
  - Plugins must be ESM packages with `@microsoft/vally` as a peer dependency (https://microsoft.github.io/vally/guides/writing-custom-executors/).
- **Licence.** MIT, "Copyright (c) Microsoft Corporation" (`LICENSE` and `"license": "MIT"` in all three packages).
- **Versions and activity.** From `npm view @microsoft/vally time`:
  - 0.3.0 2026-04-29, 0.4.0 05-04, 0.5.0 05-22, 0.6.0 06-13, 0.7.0 07-02, 0.8.0 07-09, 0.9.0 07-14, 0.10.0 07-17, 0.11.0 07-23, 0.12.0 07-31, 0.13.0 08-07, 0.14.0 08-19, 0.15.0 09-01, 0.16.0 09-09, 0.17.0 09-24, **0.18.0 2026-10-06** (`latest`), plus `next` prereleases.
  - Very active, but pre-1.0. The docs carry "Behavioral change" and "Backend migration" notes and deprecated aliases (e.g. `environment` → `agent_environment`, `output_delivery`) (https://microsoft.github.io/vally/reference/cli/eval/, https://microsoft.github.io/vally/guides/writing-custom-executors/, https://microsoft.github.io/vally/reference/graders/prompt/). **Pin the version.**
- **Source repo.**
  - The docs link to `https://github.com/microsoft/vally/tree/main/samples/multi-turn-memory` (https://microsoft.github.io/vally/reference/eval-spec/). But `https://github.com/microsoft/vally` returns **404** (unauthenticated `curl`, and `gh api repos/microsoft/vally` → "Not Found") on 2026-10-10.
  - The npm metadata has no `repository` field, and `https://aka.ms/vally` redirects to the docs site. The published source maps reference `../src/*.ts` but don't embed `sourcesContent`.
  - So the readable source is the compiled `dist/` JS and `.d.ts` in the tarballs. Whether a public repo is planned is **UNVERIFIED**.
  - GitHub code search shows it in use in Microsoft repos, e.g. `microsoft/mcp` `eng/tools/VallyEvaluator/README.md`, `dotnet/skills` `eng/evaluation-tools/vally.mjs`, and `Azure/azure-sdk-tools` `evals/README.md`.
- **Does it need Copilot?**
  - The docs' prerequisites say yes for the *default* setup: "Make sure your GitHub account has an active Copilot subscription for the default runtime" (https://microsoft.github.io/vally/get-started/install/). Runtime setup authenticates via a Copilot CLI login or a PAT with "Copilot Requests". That token "authenticates both the default executor and LLM judge features such as prompt, panel, vally compare, and experiment --compare" (https://microsoft.github.io/vally/get-started/runtime-setup/).
  - **With a custom executor and either no LLM grader or a `judge_provider` BYOK endpoint, no Copilot account is needed.** Verified: `env -i PATH=… HOME=<empty dir>` with no `GITHUB_TOKEN`, `GH_TOKEN` or `COPILOT_GITHUB_TOKEN`. Both a custom-executor eval and a `prompt` judge pointed at local Ollama ran.
  - The judge still runs inside the Copilot SDK runtime: `createCopilotLlmClient()` always starts a `CopilotClient` and passes the provider per session (`core:dist/graders/llm/llm-client.js` lines 23–37).
  - **Telemetry** is on by default: command name, version, outcome, duration and a pseudonymous device ID. No prompts, datasets or paths are sent. Opt out with `VALLY_TELEMETRY_OPTOUT=1` or `DO_NOT_TRACK=1` (https://microsoft.github.io/vally/reference/telemetry/). Set it in the Shoebox env.

## Q2. Eval spec schema

From https://microsoft.github.io/vally/reference/eval-spec/:

- **Top level.**
  - `name` (required), `description`, `version`, `type: capability|regression`.
  - `agent_environment` (staged files, commands, skills, MCP servers, env, git).
  - `grading_environment`: grader-only files hidden from the agent.
  - `defaults`, `stimuli` (required, ≥ 1), `scoring`.
  - Unknown fields are rejected.
- **`defaults`.**
  - `runs` (trials per stimulus, default 1) and `timeout` (default `2m`, unit suffix required).
  - `model` and `reasoning_effort`.
  - `judge_model` and `judge_reasoning_effort`.
  - `executor`: a name, or `{name, config}`. The config is passed opaquely to the executor and validated by its `validateConfig`.
  - `judge_provider`: a BYOK OpenAI-compatible endpoint for judges, with fields `baseUrl`, `type`, `apiKeyEnv`/`bearerTokenEnv` (env-var *names*), `headers` and `wireModel`.
- **`stimuli[]`.**
  - `name` (unique) and `prompt`, or `turns` for multi-turn.
  - **`attachments: string[]`**: "File paths (binary images or native documents) attached to the agent's initial prompt, resolved relative to the eval file (absolute paths allowed)… existence is checked at validation time. Only attachment-capable executors (e.g. copilot-sdk) accept them; others reject the stimulus."
  - `graders[]`, `rubric[]` (criteria for LLM judges), `constraints`, `tags` (string or string-array values, for filtering), `supported_executors`, golden-reference fields for `vally oracle`, and `simulation`.
- **Graders.** Each entry is `{type, name?, turn?, scope?, config}`.
  - `name` must be **unique across the whole eval file** (lint error `duplicate-grader-name`, hit in the experiment below). For a generated spec, omit `name` or make it per-stimulus.
- **Scoring.** `scoring: {weights: {<grader type>: w}, threshold}`.
  - Without a threshold, the verdict is "all graders passed".
  - With one, a trial passes if its aggregate score ≥ threshold, and the eval passes if the mean stimulus score ≥ threshold (https://microsoft.github.io/vally/concepts/scoring/).
- **Params.**
  - `${KEY}` and `${KEY=fallback}` placeholders anywhere in the spec, set by `--param KEY=value`, `eval.params.yaml` or `.vally.yaml params:` (https://microsoft.github.io/vally/reference/cli/eval/).
  - Verified: `params_hash: "${PARAMS=p-default}"` inside `defaults.executor.config` reached the executor as `p-042` with `--param PARAMS=p-042`.

**Can a stimulus carry an image?** Yes, as a **file path** in `attachments`.

- There's no data-URI or inline-bytes field.
- `agent_environment.files` (with `dest_root: workdir|assets`) can also stage files into the trial workspace, but that's for agent tools. For a VLM call, `attachments` is the right channel.
- The executor must set `supportsAttachments = true` (`core:dist/executor/types.d.ts` line 48).
- It receives `stimulus.attachments` as **absolute paths** ("during planning they are resolved to absolute paths", `core:dist/eval/types.d.ts`, `Stimulus.attachments`). Verified: the executor got `/…/images/a001.png`.
- Encoding (base64 / `inline_data` / `image_url` data URI) is the executor's job.
- The built-in copilot-sdk executor forwards them as `{type: "file", path}` on the first turn (`core:dist/executor/copilot-sdk-executor.js` ~line 725).

**Can stimuli come from a dataset?** Two ways:

1. **An eval-provider plugin** (`--eval-plugin`). It exports `registerEvalProviders(registry)`. Its `provide({path, tags, tagsExclude})` returns `RawEvalSchema[]`, and "the file need not be YAML" (`core:dist/eval/plugin/types.d.ts`; results are validated in `core:dist/eval/plugin/discovery.js`).
   - Verified: a 25-line plugin read `gt.jsonl` and produced stimuli with attachments, tags and per-row labels.
   - **Limits:**
     - only one provider per run;
     - `--eval-plugin requires --eval-spec` and can't be combined with `--suite` (`cli:dist/commands/eval.js` lines 138–151);
     - **`vally grade` has no `--eval-plugin` option** (`cli:dist/commands/grade.js` lines 515–529);
     - `experiment` ignores `eval_plugin` (https://microsoft.github.io/vally/reference/experiment-file/, "Reserved fields").
2. **Generate `eval.yaml`** from the JSONL with a script. This is **recommended**, because re-grading needs a real spec. Verified that 1,000 generated stimuli lint, load and run.

## Q3. Executors

**Interface** (https://microsoft.github.io/vally/guides/writing-custom-executors/):

- Required: `name`, `execute(stimulus, options): Promise<Trajectory>` and `shutdown()`.
- Optional capability flags: `supportsAttachments`, `supportsMultiTurn`, `supportsEnvVars`, `supportsSimulation`, `supportsPreparedWorkspace`, `supportsTurnCompletion`.
- Optional `validateConfig(config)`. If a spec supplies `config` and the executor has no `validateConfig`, planning **fails closed**.
- `ExecutorOptions` include `model`, `timeout`, `workDir`, `executorConfig` (from `defaults.executor.config`), `reasoningEffort`, `env`, `registerCleanup` and `sessionLog`.
- Shipping: a plugin package exports `registerExecutors(registry)` and is loaded with `vally eval --executor-plugin <pkg-or-path>` (repeatable).
- `execute()` "may be called many times concurrently (controlled by --workers)". Initialise lazily and clean up in `shutdown()`.

**Raw model APIs are fine.** The executor is just code that returns a trajectory. Vally doesn't care whether an agent ran.

- Verified with a ~40-line executor that POSTs to Ollama `/api/chat`: `model: gemma4:e4b`, the image as base64 in `images`, a JSON schema in `format`, `temperature: 0`.
- Three images ran in 6–14 s each with 314–379 total tokens (4×4 px test PNGs, so the token counts aren't representative). The `field-match` grader scored them correctly.
- The same pattern applies to:
  - the Gemini API (`generateContent` with `inline_data`);
  - Gemini's OpenAI-compatible endpoint `https://generativelanguage.googleapis.com/v1beta/openai/` (image via `image_url` data URI; https://ai.google.dev/gemini-api/docs/openai, last updated 2026-09-02);
  - any OpenAI-compatible local server.

  These weren't run here. **UNVERIFIED** in this harness: the Gemini call, and an mlx-vlm server's OpenAI compatibility.

**Built-in alternative: `copilot-sdk` executor with BYOK.** It accepts `provider: {baseUrl: http://localhost:11434/v1, …}` for Ollama or vLLM (https://microsoft.github.io/vally/reference/eval-spec/, "Executor config & BYOK") and supports attachments. But it wraps the model in the Copilot agent runtime (agent system prompt, tools, workspace skill discovery; https://microsoft.github.io/vally/reference/cli/eval/, "Skills"). That isn't a clean measurement of a VLM prompt. **Not recommended. Not tested here.**

**What it must emit** (https://microsoft.github.io/vally/reference/trajectory-format/):

- `{id, stimulus, events[], metrics, output, workDir, metadata{startedAt, completedAt, model, executor, skillsLoaded, sessionID}}`.
- Events are typed: `user_message`, `assistant_message`, `token_usage {inputTokens, outputTokens, model}`, `error`, `custom {data: {...}}`, `turn_start`/`turn_end`, tool events and so on.
- `computeMetrics(events)` (exported by `@microsoft/vally`) builds `tokenUsage` (with a `byModel` breakdown), `errorCount` and `turnCount`. Set `wallTimeMs` yourself.
- `output` is what output graders and judges see, so put the model's raw JSON there.
- **Gotchas:**
  - **Vally doesn't record `defaults.executor.config` in `results.jsonl`** (checked: `params_hash` appeared only in my own `custom` event). The executor should emit a `custom` event carrying `params_hash`, `model_id`, USD cost and the provider latency.
  - `tokenUsage.cost` is typed `{provider: "github-copilot", unit: "nano-aiu"}` only (`core:dist/trajectory/types.d.ts` line 316), so USD has to go in a `custom` event.
- **Retries.** A thrown error whose message contains `timeout`, `rate limit`, `too many requests` or `429`, or that carries `retryable: true`, is retried. That's up to `--max-retries` (default 2) with exponential backoff and a fresh workspace (`core:dist/utils/error-classifier.js`; https://microsoft.github.io/vally/reference/cli/eval/). Retries are ignored when `runs ≥ 2`.

## Q4. Graders

- **Built-ins** (https://microsoft.github.io/vally/reference/graders/):
  - output/transcript contains and matches checks (and their negations);
  - file and diff checks;
  - `run-command` and `program`;
  - `tool-calls`, `skill-invocation`, `system-event`, `completed`;
  - loop detection;
  - metric budgets: `token-budget`, `tool-call-count`, `turn-count`, `step-count`, `error-count`, `wall-time`;
  - `custom-metrics` (assertions on a JSON file the run writes);
  - LLM judges: `prompt` and `panel`.

  The catalogue says: "All graders are reference-free… and trajectory-level". **No built-in compares output with a per-stimulus label.** `output-matches` with a regex per stimulus could do a crude version.
- **Custom graders, in-process TypeScript/JS.** Implement `{metadata, grade(input): Promise<GraderResult>}`, export `registerGraders(registry)`, and load with `--grader-plugin` (works for `eval`, `grade`, `lint`, `oracle` and `export`) (https://microsoft.github.io/vally/guides/writing-custom-graders/).
  - `GraderInput` = `{trajectory, stimulus, config, …}`.
  - `GraderResult` = `{name, kind, passed, score 0–1, evidence, label?, details?, metadata?}` (`core:dist/graders/types.d.ts` lines 53–95).
  - `metadata` round-trips into `results.jsonl`. Verified: `{"field":"colour","predicted":"mono","expected":"colour","tags":{…}}` came out under `gradeResult.details[0].metadata`.
- **Custom graders in any language via `program`.** Vally runs the program with `EVALUATE_GRADER_INPUT` (a JSON copy of `GraderInput`), and the program prints a `GraderResult` JSON on stdout (https://microsoft.github.io/vally/reference/graders/program/). Verified with a Python grader.
  - **Caveat:** it "fails when its working directory is missing, so it cannot re-grade a saved results.jsonl once the workspace is gone". Verified: re-grade → `Grader error: Directory '/tmp/vally-eval-…' does not exist`.
  - **Workaround (verified):** `vally grade … --workspace <any existing dir>` overrides `workDir`, and the Python grader then re-grades fine. `grade` warns when one `--workspace` is applied to multi-record input (https://microsoft.github.io/vally/reference/cli/grade/).
- **Comparing structured JSON with ground truth per stimulus: yes, via a custom grader.**
  - Put the label in the grader's `config` (e.g. `{field: colour, expected: mono}`), not in a workspace file. The spec is re-read at grade time, so a corrected label re-grades without re-inference (verified).
  - `grading_environment.files` (hidden answer keys) also exists, but that path is workspace-bound.
- **LLM judge (`prompt`)** (https://microsoft.github.io/vally/reference/graders/prompt/):
  - It scores the stimulus `rubric` on `binary`, `scale_1_5` (default) or `scale_1_10`, normalised to 0–1, with a default pass threshold of 0.5. The judge must call a `submit_grade` tool.
  - **Model resolution:** grader `config.model` → `--judge-model` → `defaults.judge_model` → `EVAL_JUDGE_MODEL` → default **`gpt-5.6-terra`** via Copilot.
  - `judge_provider` repoints every judge call at an OpenAI-compatible endpoint. Verified: `judge_model: qwen3:8b` and `judge_provider.baseUrl: http://localhost:11434/v1` with no GitHub credentials → valid verdict.
  - **Evidence is text files only**: `agent-output.txt`, `timeline.jsonl`, `metrics.json`, optional diff/patch/repo snapshot. Limits are 12 read calls, 16k characters per read and 64k characters in total. **No path for images.** The `core:dist/graders/llm/` code has no attachment or image handling.
  - Cost per call is non-trivial: one binary criterion on a 40-character output used **5,469 input / 941 output tokens and 37 s** on local qwen3:8b. That's the agentic judge loop with its file-read tool calls.
  - `panel` runs N judges with consensus aggregation.
  - Retries: 2, with backoff (5 s → 10 s → 20 s), within a 10-minute budget.

## Q5. Scoring, reporting, re-grading, comparison

- **Scoring is per trial, then the mean over trials and over stimuli** (https://microsoft.github.io/vally/concepts/scoring/, https://microsoft.github.io/vally/reference/scoring-functions/):
  - trial verdict: all graders pass, or score ≥ threshold;
  - stimulus: mean of trial scores, plus pass@k, pass^k and flakiness;
  - eval: `overallScore` = mean of stimulus scores, `passed = overallScore ≥ threshold`.
  - So **accuracy** (with 0/1 graders) is the eval score. Per-stratum accuracy = a run filtered with `--tag source=scan`, and `--threshold 0.98 --require-pass` would gate it.
  - **Precision, recall, F1, confusion matrices, re-weighted estimates and recall@k across a query set aren't computable** in Vally's scoring. Its `computeSkillScore` only averages.
- **Reporters.**
  - Built-ins: console, Markdown (`eval-results.md`), JUnit and JSONL.
  - **Custom reporter plugins** (`--reporter-plugin`) get `onRunStart`, `onTrialResult({item, result: {trajectory, grade}})` and `onRunComplete(summary, artifacts, status)` (https://microsoft.github.io/vally/guides/writing-custom-reporters/). A TypeScript reporter *could* compute per-stratum metrics live. It's simpler to do that in Python over `results.jsonl`, which has the same data.
- **Output layout** (https://microsoft.github.io/vally/reference/cli/eval/): `<output-dir>/<timestamp>/results.jsonl` (one `trial-result` per trial, then a `run-summary`), `eval-results.md`, `otel-spans.jsonl`, and `<eval>/<stimulus>/<model>/<trial>/metadata.json` plus `workspace.patch`.
  - A `trial-result` carries `stimulus`, `model`, `variant`, `status`, `durationMs`, and `trajectory` (with `stimulus.tags`, `stimulus.attachments`, `output`, `events` and `metrics`) plus `gradeResult.details[]` (verified).
- **Re-grade without re-running: yes, native.** `vally grade --eval-spec eval.yaml [--grader-plugin …] < results.jsonl` "matches each trajectory's stimulus to the graders defined in the eval spec" (https://microsoft.github.io/vally/reference/cli/grade/).
  - Output and trajectory graders, `prompt` included, need nothing on disk. Workspace graders, `program` included, need `--workspace`.
  - `vally eval --skip-grade --output jsonl` captures without grading.
  - Verified: changing `a003`'s label from `colour` to `mono` in the spec and re-grading flipped its verdict.
- **Run-vs-run comparison.**
  - **`vally compare --baseline A --treatment B`** pairs trials by stimulus name and trial index. It runs the **LLM judge twice per pair** (position-swapped) against the rubric, and reports win rate, mean signed score with a 95% CI, **McNemar on pass/fail flips** and metric deltas. `--fail-on-regression` gates on it (https://microsoft.github.io/vally/reference/cli/compare/).
    - It's always LLM-judged. For 3,000 images that means 6,000 judge calls. It's useful for caption A/B on a small set, not for field accuracy.
  - **`vally serve`** (dashboard plus REST, optional SQLite history via `vally ingest`) has `GET /api/runs/:id/compare?modelA=&modelB=` (per-stimulus agreement, no judge), a stimulus × model matrix, a model ranking by pass rate and a per-grader failure analysis (https://microsoft.github.io/vally/reference/results-api/, https://microsoft.github.io/vally/reference/cli/serve/). It has no per-tag (stratum) aggregation.
  - **`vally experiment run`** sweeps variants (model × params matrix, up to 256 cells) with drift detection and `--compare` (https://microsoft.github.io/vally/reference/cli/experiment/). But **it can't load custom executor or grader plugins in 0.18.0**:
    - its options have no `--executor-plugin` or `--grader-plugin` (`cli:dist/commands/experiment.js` lines 73–89);
    - the manifest's `executor_plugins` and `grader_plugins` are "accepted by the file parser but are not wired into execution" (https://microsoft.github.io/vally/reference/experiment-file/; `core:dist/experiment/loader.js` lines 24, 377–395).

    For Shoebox, use `vally eval --model a,b,c` (verified with a custom executor) or separate runs.

## Q6. Scale and cost

- **Concurrency.** `--workers` (default 5) sets concurrent stimulus sessions. Trials of one stimulus run sequentially (https://microsoft.github.io/vally/reference/cli/eval/). There are no per-model or per-endpoint limits, so a mixed Gemini + local run shares one worker pool. Run local MLX/Ollama at `--workers 1–2` in its own invocation, and Gemini higher.
- **Overhead (measured).** 1,000 stimuli × 2 models = **2,000 trials in 40.5 s wall** at `--workers 16`, using a no-op executor (120 s user, 369 s sys CPU). That's about 20 ms of wall time per trial, negligible next to seconds-per-image inference.
  - Output: 5.5 MB `results.jsonl` (~2.7 KB per trial), 3.3 MB OTel spans and 4,003 files (per-trial `metadata.json` + `workspace.patch`).
  - The console printed 36k lines, so redirect it to a file.
  - Per-trial temp workspaces under `/tmp` were cleaned up.
- **Caching.**
  - The only caching is **workspace** caching across trials when `runs ≥ 2` (https://microsoft.github.io/vally/reference/cli/eval/).
  - There's **no inference or result cache** keyed by model or params, and no resume of a partial run. (The docs have nothing on resuming `eval`. `experiment --variant` and `--shard` are the only partial-rerun tools.)
  - So the `(asset_id, model_id, params_hash)` store has to live in Shoebox, and the executor checks it first. That makes a crashed 3,000-image run cheap to repeat.
- **Judge cost.** "Cost ≈ (num_stimuli × runs × prompt_graders_per_stimulus) × per-call cost" (https://microsoft.github.io/vally/reference/graders/prompt/). Measured ~6.4k tokens per trivial judgement locally. Re-grading re-calls the judge, because judge verdicts aren't cached.

## Q7. Fit and proposed split

**Split.**

```
evals/sets/colour-v1.jsonl          (Python: stratified sampling, labels; in git)
   │ shoebox eval gen colour
   ▼
evals/colour/eval.yaml              (generated; one stimulus per asset; tags = strata + split)
   │ vally eval -e … --executor-plugin evals/vally/shoebox-vlm --grader-plugin evals/vally/shoebox-graders
   │            --model gemma4:e4b,gemma4:12b,gemini-3.8-flash --param PARAMS=params/caption-v3.toml --tag split=test
   │   shoebox-vlm: replay from results store (or live call) → trajectory + custom{params_hash, usd, latency}
   │   field-match: predicted vs expected → GraderResult.metadata
   ▼
vally-results/<ts>/results.jsonl    (re-gradeable: vally grade -e … < results.jsonl)
   │ shoebox eval report colour --run vally-results/<ts>
   ▼
per-stratum P/R + confusion + re-weighted estimate, cost/latency, McNemar vs baseline,
gates.toml check (exit code) → evals/runs/<date>_colour_<config-hash>.json
```

**The other evals.**
- **Capture source** is the same shape: `field-match` on `source`, with `positive: scan` for scan precision.
- **Scene tags** use a `set-match` grader that outputs per-tag TP/FP/FN in metadata.
- **Search** is one stimulus per query, with the label being the judged relevant asset IDs. The executor queries the index, which is built outside Vally from cached embeddings keyed the same way. A `retrieval` grader sets `score = nDCG@20` and `metadata = {recall@20, ranked ids}`.
- **Captions:**
  - the text-only `prompt` judge (BYOK to Gemini or local) checks rubric items that don't need the image (format, vocabulary, length, no hallucinated names);
  - judging the caption against the image needs a custom grader or a Python step that sends the image plus caption to a VLM judge;
  - PLAN's blind side-by-side human rating stays a labelling-UI job.

**Sketch: generated `evals/colour/eval.yaml`.**

```yaml
# GENERATED by `shoebox eval gen colour --set colour-v1`. Do not edit; edit evals/sets/colour-v1.jsonl.
name: colour-mono
description: Colour vs not-colour (mono/sepia/toned), set colour-v1 (300 stratified + 50 hard)
version: "colour-v1"
type: capability
defaults:
  runs: 1
  timeout: 3m
  model: "${MODEL=gemma4:e4b}"
  executor:
    name: shoebox-vlm
    config:
      mode: "${MODE=replay}"                     # replay | live
      params_file: "${PARAMS=params/caption-v3.toml}"
      store: "${STORE=/Volumes/shoebox/results/eval-outputs.sqlite}"
      task: colour
stimuli:
  - name: a-01HZX3K9QW                           # asset_id; stable, so re-grade and compare pair by it
    prompt: "Classify the photo as colour or mono. Return JSON {\"colour\": \"colour\"|\"mono\"}."
    attachments: [/Volumes/shoebox/derived/eval-jpeg/01HZX3K9QW.jpg]
    tags: { set: colour-v1, split: test, era: "1985-1989", source: scan, colour_gt: mono, hard: toned-print }
    graders:
      - type: field-match                        # no `name:`; names must be unique per file
        config: { field: colour, expected: mono }
  # … one entry per asset
```

**Sketch: the `shoebox-vlm` executor (TypeScript, `evals/vally/shoebox-vlm/src/index.ts`).** The model calls are outlines. The Ollama shape was verified here; the Gemini one wasn't.

```ts
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { computeMetrics, type Executor, type ExecutorOptions, type Stimulus, type Trajectory } from "@microsoft/vally";

type Cfg = { mode: "replay" | "live"; params_file: string; store: string; task: string };

class ShoeboxVlm implements Executor {
  name = "shoebox-vlm";
  supportsAttachments = true;
  validateConfig(c: unknown) {
    const cfg = c as Cfg;
    if (!cfg?.params_file || !["replay", "live"].includes(cfg.mode)) throw new Error("shoebox-vlm: mode and params_file required");
  }
  async execute(stimulus: Stimulus, o: ExecutorOptions): Promise<Trajectory> {
    const cfg = o.executorConfig as Cfg;
    const params = readFileSync(cfg.params_file, "utf8");
    const paramsHash = createHash("sha256").update(params + stimulus.prompt).digest("hex").slice(0, 12);
    const assetId = stimulus.name.replace(/^a-/, "");
    const t0 = new Date();
    // replay: read (asset_id, model_id, params_hash) from the Shoebox results store written by the Python runner;
    //         a missing row throws (fail loud). live: call the model directly.
    const r = cfg.mode === "replay"
      ? await readStore(cfg.store, assetId, o.model!, paramsHash)          // {text, inTok, outTok, usd, latencyMs}
      : o.model!.startsWith("gemini-")
        ? await callGemini(o.model!, stimulus.prompt, stimulus.attachments![0], params)   // generateContent + inline_data + responseSchema
        : await callOllama(o.model!, stimulus.prompt, stimulus.attachments![0], params);  // /api/chat, images:[b64], format: schema
    // HTTP 429 → throw Object.assign(new Error("429 rate limit"), { retryable: true }) so Vally retries with backoff
    const t1 = new Date();
    const events = [
      { type: "user_message", timestamp: t0, data: { content: stimulus.prompt } },
      { type: "token_usage", timestamp: t1, data: { model: o.model!, inputTokens: r.inTok, outputTokens: r.outTok } },
      { type: "custom", timestamp: t1, data: { shoebox: { asset_id: assetId, model_id: o.model, params_hash: paramsHash,
                                                         mode: cfg.mode, usd: r.usd, provider_latency_ms: r.latencyMs } } },
      { type: "assistant_message", timestamp: t1, data: { content: r.text } },
    ] as Trajectory["events"];
    return { id: crypto.randomUUID(), stimulus, events, output: r.text, workDir: o.workDir,
      metadata: { startedAt: t0, completedAt: t1, model: o.model!, executor: this.name, skillsLoaded: [], sessionID: crypto.randomUUID() },
      metrics: { ...computeMetrics(events), wallTimeMs: r.latencyMs ?? t1.getTime() - t0.getTime() } };
  }
  async shutdown() {}
}
export function registerExecutors(registry: { register(e: Executor): void }) { registry.register(new ShoeboxVlm()); }
```

The matching `field-match` grader is about 20 lines. It parses `trajectory.output`, compares `config.field` with `config.expected`, and returns `{passed, score: 0|1, evidence, metadata: {field, predicted, expected, parse_error}}`. A copy was exercised in the experiments. The Python report reads `stimulus.tags` and `gradeResult.details[].metadata` from each `trial-result` line.

**Design notes and risks.**
- **Two languages.** Executors, in-process graders and reporters are TypeScript (Node ≥ 22.14), while PLAN fixes Python for the pipeline and eval metrics. Keep the TypeScript thin: one executor and two or three graders. Python can still be used for graders through `program`, with the `--workspace` workaround when re-grading.
- **Replay vs live.** Replay keeps the eval on the same runner the bulk job uses, which matters for Gate G. Live is the faster way to learn Vally and to try prompts on the dev split. With live mode, the executor should still write to the keyed store so later runs and the backfill can reuse it.
- **Version churn.** Pin `@microsoft/vally-cli` and `@microsoft/vally` exactly, and re-read the release notes before upgrading. That's harder without a public repo or changelog. Whether release notes exist anywhere is **UNVERIFIED**.
- **Privacy.**
  - Set `VALLY_TELEMETRY_OPTOUT=1`.
  - Keep `judge_provider` set, so captions aren't sent to Copilot's default judge by accident.
  - `results.jsonl` holds absolute image paths and model outputs, so keep runs under `evals/runs/` rules (git or not) deliberately.

---

## Experiments run (throwaway, scratchpad only)

Vally 0.18.0 was installed project-locally in the session scratchpad. Every command ran with `env -i PATH=… HOME=<empty> VALLY_TELEMETRY_OPTOUT=1`, so no GitHub or Copilot credentials were present.

1. A fake executor plugin (`supportsAttachments`, `validateConfig`, `token_usage` + `custom` events) and a `field-match` grader plugin ran over 3 stimuli with PNG attachments and strata tags. Each trial produced a result. The executor received absolute attachment paths and its `executorConfig`. Grader metadata and tags were persisted in `results.jsonl`.
2. `vally grade` with a corrected label re-graded with no executor call, and the verdict changed.
3. A generated spec of 1,000 stimuli × 2 models (`--model a,b`) at 16 workers ran 2,000 trials in 40.5 s.
4. An eval-provider plugin turned `gt.jsonl` into stimuli and the run passed.
5. A live Ollama executor (`gemma4:e4b`, base64 image, JSON-schema `format`) produced correct `colour`/`mono` predictions on 3 synthetic images.
6. A `prompt` judge with `judge_provider` → Ollama `qwen3:8b` returned a valid binary verdict (5,469/941 tokens, 37 s).
7. A Python `program` grader worked live. Re-grading failed without `--workspace` and passed with it.
8. `--param` interpolated into `defaults.executor.config`. Executor config is not persisted in `results.jsonl`.
9. The lint error `duplicate-grader-name` fired when the same grader `name` was repeated across stimuli.

---

## Sources

Vally docs (https://microsoft.github.io/vally/, read 2026-10-10; pages carry no dates):
- Home: https://microsoft.github.io/vally/
- Install: https://microsoft.github.io/vally/get-started/install/
- Runtime setup: https://microsoft.github.io/vally/get-started/runtime-setup/
- How it works: https://microsoft.github.io/vally/concepts/how-it-works/
- Why Vally: https://microsoft.github.io/vally/concepts/why-vally/
- Scoring: https://microsoft.github.io/vally/concepts/scoring/
- Scoring functions: https://microsoft.github.io/vally/reference/scoring-functions/
- Eval spec reference: https://microsoft.github.io/vally/reference/eval-spec/
- Writing eval specs: https://microsoft.github.io/vally/guides/writing-eval-specs/
- Writing custom executors: https://microsoft.github.io/vally/guides/writing-custom-executors/
- Writing custom graders: https://microsoft.github.io/vally/guides/writing-custom-graders/
- Writing custom reporters: https://microsoft.github.io/vally/guides/writing-custom-reporters/
- Trajectory format: https://microsoft.github.io/vally/reference/trajectory-format/
- Grader catalogue: https://microsoft.github.io/vally/reference/graders/
- `prompt` grader: https://microsoft.github.io/vally/reference/graders/prompt/
- `program` grader: https://microsoft.github.io/vally/reference/graders/program/
- `custom-metrics` grader: https://microsoft.github.io/vally/reference/graders/custom-metrics/
- Metric-threshold graders: https://microsoft.github.io/vally/reference/graders/metric-thresholds/
- CLI `eval`: https://microsoft.github.io/vally/reference/cli/eval/
- CLI `grade`: https://microsoft.github.io/vally/reference/cli/grade/
- CLI `compare`: https://microsoft.github.io/vally/reference/cli/compare/
- CLI `experiment`: https://microsoft.github.io/vally/reference/cli/experiment/
- Experiment file schema: https://microsoft.github.io/vally/reference/experiment-file/
- CLI `serve`: https://microsoft.github.io/vally/reference/cli/serve/
- CLI `ingest`: https://microsoft.github.io/vally/reference/cli/ingest/
- Results API: https://microsoft.github.io/vally/reference/results-api/
- Telemetry: https://microsoft.github.io/vally/reference/telemetry/

npm packages (source of record in the absence of a public repo):
- `@microsoft/vally@0.18.0` (https://www.npmjs.com/package/@microsoft/vally): `package.json`, `LICENSE`, `README.md`, `dist/executor/types.d.ts`, `dist/executor/copilot-sdk-executor.js`, `dist/eval/types.d.ts`, `dist/eval/plugin/{types.d.ts,discovery.js}`, `dist/graders/types.d.ts`, `dist/graders/llm/llm-client.js`, `dist/trajectory/types.d.ts`, `dist/utils/error-classifier.js`, `dist/experiment/loader.js`
- `@microsoft/vally-cli@0.18.0` (https://www.npmjs.com/package/@microsoft/vally-cli): `package.json`, `dist/commands/{eval,grade,experiment}.js`
- `@microsoft/vally-server@0.18.0` (https://www.npmjs.com/package/@microsoft/vally-server): `package.json`
- Release history: `npm view @microsoft/vally time` (2026-10-10)

GitHub (repo status and adoption):
- https://github.com/microsoft/vally → 404 (2026-10-10), also via `gh api repos/microsoft/vally`
- Code-search hits: `microsoft/mcp` `eng/tools/VallyEvaluator/README.md`; `dotnet/skills` `eng/evaluation-tools/vally.mjs`; `Azure/azure-sdk-tools` `evals/README.md`

Other:
- Gemini OpenAI compatibility: https://ai.google.dev/gemini-api/docs/openai (last updated 2026-09-02)
