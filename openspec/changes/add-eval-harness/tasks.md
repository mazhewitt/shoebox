# Tasks

Replanned on 2026-10-11 (see design "Replan"). Section 0 records what was built under the first plan. Sections 1–8 are the replan. Section 9 is deferred to before the bulk run.

## 0. Built under the first plan (kept)

- [x] 0.1 NAS `shoebox` share and user, `ImportantFiles` mounted read-only at `~/mnt/importantfiles-ro` (old 1.1, part of 1.2).
- [x] 0.2 `shoebox` package scaffold: uv, CLI, pytest, ruff (old 1.4; `c21d888`).
- [x] 0.3 Pilot pipeline: pick, `derive --profile infer-v1`, Ollama/Gemini/rules runners, the EG2 embedder, keyed result store, `privacy check`, never-send, spend log, and the Vally layer with executor and graders (old 3.2, 4.1–4.5, 6.1–6.4 in pilot form; `9547882`, `a593ea1`).
- [x] 0.4 pilot-v0: 45 photos through rules, E4B (bf16/q4), 12B, 31B, Gemini (low/medium) and EG2; owner blind labels; re-grade without inference (`8d65ef8`, `ad8e117`).
- [x] 0.5 Known-item search eval: routes, ties, bootstrap, report, Vally `eval:search` (`7f2f85d`).
- [x] 0.6 `shoebox inventory` over `ImportantFiles`, with a resumable checkpoint, the untouched check, content sniffing and the frame README (old 2.1, 2.2, and 2.4 for this source; `d7709b3`, `bdf427f`, `e3afe91`, `b1c5d57`).
- [x] 0.7 Distractor pool `pilot-v0-pool5k`, near-dup review and exclusions, image routes and E4B caption routes (`c5f6f91`, `746b6b6`).

**Dropped by the replan:**
- old 5.2–5.6: Label Studio, `capture-colour-v1`, the scene vocabulary, `scene-v1`/`captions-v1`;
- old 5.7: 50 owner queries;
- old 8.2: owner pooled judging;
- the 12B/31B pool runs.

## 1. Fixes before more model runs

- [ ] 1.1 Tags: lowercase tags in the runner before validation, and keep the schema pattern. Re-validate the stored invalid records as a replay. Verify with a test that a tag "Helsinki" is stored as "helsinki", and that the normalisation is part of the hashed runner version, so normalised and raw outputs never share a key.
- [ ] 1.2 Truncation: mark outputs that hit `num_predict` as `invalid` with reason `truncated`, then retry once at `num_predict = 300` with a repeat penalty, under a new params_hash. Run it over the 57 E4B and the 17 12B invalids. Verify with a test feeding a capped output, and report how many retries recovered.
- [ ] 1.3 Orientation (D6a): build `infer-v2` with an orientation pass for untagged scans. Compare the three methods on 50 scans with Opus as the reference, record the result in `docs/profiles.md`, and choose one. Re-derive the scan items of `eval-1k` and the 45 targets, then re-embed. Verify with tests: the rotation is recorded, `infer-v1` images are unchanged, and a rotated fixture comes out upright.
- [ ] 1.4 Search report: add a paired bootstrap (one resample index for both routes) on the RR and nDCG deltas with 95% CIs, and the minimum detectable difference. Verify with a synthetic test where a known delta is recovered.
- [ ] 1.5 Re-run pool5k image routes on `infer-v2` for the 45 targets, and report how much of the 0.98→0.65 drop came from orientation.

## 2. Eval corpus `eval-1k`

- [ ] 2.1 Config: add `held_back` path globs (`Pictures/toren phone/**`, `Pictures/Jude Profile/**`, `documents/**`), and record Claude as an approved hosted model. Update `privacy check` to report both. Verify with tests: a held-back path is skipped and reported.
- [ ] 2.2 Draw `eval-1k`: the 45 targets plus 955 of the 1,966 12B-processed non-target pool items, by a seeded draw. Run the hosted-eligibility screen: never-send, held-back paths, consent, and an Opus pre-screen for medical, ID and financial images. Swap exclusions from the rest of the pool. Write the README. Verify the draw is reproducible and every substitution is listed.

## 3. Captions

- [ ] 3.1 Write `config/prompts/label-v2.md` and the schema (D7). Iterate on 20 dev photos with Opus reading the outputs. Verify that E4B and Gemini outputs validate on those 20.
- [ ] 3.2 Run Gemini 3.8 Flash `label-v2` (`medium`, thinking `low`) on `eval-1k` with a $5 cap. Verify the spend is logged and the skipped items are reported.
- [ ] 3.3 Run E4B `label-v2` (about 1 h) and 12B `label-v2` (about 2 h) on `eval-1k`, and top up E4B and 12B `label-v1` where they are missing. Caption-embed all of them with EG2. Verify every arm covers all 1,000 photos or lists its invalids.

## 4. Queries (Opus)

- [ ] 4.1 Write `config/prompts/query-writer-v1.md` with the style mix (D9). Opus subagents write about 200 known-item queries from a stratified draw of `eval-1k`, and about 80 category queries from the corpus summary. Store them keyed in the results store, and write `evals/sets/eval-1k/queries-v1.jsonl` with kind, style, source and provenance. Add the 39 owner notes as subset `owner_notes`.
- [ ] 4.2 Split dev/test about 1:2 by source capture group, from a seed. Verify there is no capture group in both splits.
- [ ] 4.3 Owner realism check on 30 random queries, using the local review page. Record it in the set README.

## 5. Search routes

- [ ] 5.1 Metadata route: FTS over path, folder and filename tokens, plus EXIF date and year. Also attribute filters (mono/colour, film-scan bucket, year range) taken from the labels.
- [ ] 5.2 Query expansion: a small local model (E4B text-only) rewrites a query into keywords and synonyms for FTS and a second embedding query. The prompt is versioned and expansions are cached per query.
- [ ] 5.3 Run every route over `eval-1k`, for each captioner arm and with no captioner: image@768, caption, FTS, hybrid, blend, metadata, expansion variants and filters. Verify the rankings are written and indexed per route, and that the params_hash covers the expansion prompt and the filters.

## 6. Judging

- [ ] 6.1 Gemini grid judge: query plus up to 10 numbered thumbnails, returning grades 0/1/2 as JSON, keyed `(query_id, asset_id, …)`, with a USD cap. Verify on a 5-query dry run with a mocked API, then live on 10 dev queries.
- [ ] 6.2 Pool the top-10 of every route per query, plus the targets, and judge them all ($25 cap). Verify pool coverage is 1.0 for every route.
- [ ] 6.3 Opus audit: a stratified 10% plus all target disagreements. Compute weighted kappa overall and per route, and re-judge the test pool with Opus if kappa < 0.6.
- [ ] 6.4 Local review page (static HTML on the laptop, derived JPEGs): the owner judges 100 audited items and rates 30 caption sets blind. Store the results with provenance.
- [ ] 6.5 Opus caption audit: 200 photos, every arm's captions shown blind, counting wrong or invented facts and missing searchable facts. Structured-field reference: metadata rules where unambiguous, otherwise Gemini; Opus audits 200, and the owner checks the disagreements.

## 7. Decision rule and reports

- [ ] 7.1 Tune on dev only: blend weight, expansion prompt, filters. Freeze them.
- [ ] 7.2 Write and commit `evals/gates.toml` (D11) before any test-split scoring. Verify the commit precedes the test run records.
- [ ] 7.3 Score test: graded nDCG@20 and paired-bootstrap deltas against the reference, per kind, style and the owner-notes subset; caption error rates; structured-field precision per stratum. Add cost and time projections for 71.6k images (USD at the stated price date, local hours on the M5 Max).
- [ ] 7.4 Confirm the chosen pipeline on `pilot-v0-pool5k` (owner notes plus the subset of queries whose source photos are in it). Report any change in route order.

## 8. Recommendation

- [ ] 8.1 Write `evals/RECOMMENDATION.md` covering:
  - captioner, prompt and search method;
  - per-kind results and the reference's own audited error rate;
  - the cost and time projection;
  - the seam if bulk ≠ incremental;
  - privacy notes;
  - what failed.

  The owner records the decision.

## 9. Deferred to before the bulk run

- [ ] 9.1 Owner (DSM): read-only access for the `shoebox` user to `Home Folders`, `Backup` and `Volume2`; mount them read-only (old 1.1b, 1.2).
- [ ] 9.2 Owner: land a Google Takeout (old 1.3), then run the inventory over all sources (old 2.4 remainder).
- [ ] 9.3 Inventory skip rule for Android `DCIM/.thumbnails` caches.
- [ ] 9.4 Re-measure throughput on the M5 Ultra Mac Studio when it arrives, and re-project.
- [ ] 9.5 Older open issues: Ollama listening on all interfaces; the agreement matrix counting unknown–unknown as agreement; g31b enum leakage into tags.
