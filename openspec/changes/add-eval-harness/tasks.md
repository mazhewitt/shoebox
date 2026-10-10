# Tasks

## 1. Owner prerequisites and scaffolding

- [x] 1.1 Owner (DSM): create a non-synced `shoebox` shared folder and a `shoebox` NAS user with read-only access to `ImportantFiles` and read-write access to `shoebox`. Make sure the `shoebox` share isn't in any Cloud Sync task, and is in Hyper Backup except `derived/`. Verify in DSM, and record the setup in `docs/machines.md`.
- [ ] 1.2 Mount on the laptop as the `shoebox` user: `ImportantFiles` with `mount_smbfs -o rdonly` at a dedicated mount point, and the `shoebox` share read-write. Record the source roots and their synced flag in `config/sources.toml`. Verify that a write to the `ImportantFiles` mount fails, a write to `shoebox` succeeds, and the mount commands are in `docs/machines.md`.
- [ ] 1.3 Owner: request a Google Takeout of the owner's Photos library, land it in `shoebox/staging/takeout/<date>/`, extract it to `shoebox/originals/takeout/<date>/` and make that read-only for the `shoebox` user. Verify by file count/bytes reconciling against the export's archive listing.
- [ ] 1.4 Scaffold the `shoebox` Python package with uv (`shoebox` CLI entry point, pytest, ruff) and the repo layout `evals/{sets,vocab,runs,vally}`, `frame/`, `config/`. Verify `uv run shoebox --help` and `uv run pytest` pass on an empty test.
- [ ] 1.5 Owner: fill `evals/privacy-checklist.toml`: billing project ID, AI Studio logging off, retention 7 days, no datasets or feedback, confirmation date. Fill `evals/never-send.txt`, and record Jude's consent status. Verify the file parses with `shoebox privacy check` (added in 4.5).

## 2. Sampling frame (`eval-ground-truth`: Sampling frame)

- [ ] 2.1 Implement `shoebox inventory`: walk the configured sources, BLAKE3 each file, run batched ExifTool JSON, read dimensions and bit depth, pick the best date with its kind (EXIF, sidecar, filename, mtime, scan date), and compute 256 px chroma stats with libvips. Write `frame/frame-<date>.parquet`. Skip the recycle, sync-temp and Lightroom-preview folders, with counts. Form capture groups by same-folder base name across RAW/JPG/TIFF/PSD and record the rule. Refuse writable synced roots. Verify with unit tests on fixture files: JPEG with EXIF, HEIC, 16-bit grey TIFF, RGB TIFF, NEF, DNG+JPG pair, layered PSD, PNG screenshot, a file with a Takeout sidecar, and a writable "synced" root that must be refused.
- [ ] 2.2 Add a sources-untouched check: snapshot mtime/size/hash of a 200-file sample and the directory listing of each touched folder, before and after `inventory`. Verify nothing changed and no file was created, and that the check is part of the inventory test suite.
- [ ] 2.3 Implement the rule baseline for medium × digitiser and colour class (`label-runners`: Rule-based baseline), with explicit `unknown`. Verify with unit tests, including "no EXIF, no origin signal → digitiser unknown".
- [ ] 2.4 Run the inventory over `ImportantFiles` and the Takeout export. Write `frame/README.md` with:
  - counts per source, top-level folder, era, baseline medium/digitiser and colour class;
  - capture-group counts by rule;
  - exact-hash overlap between the `Google Photos` tree and Takeout;
  - decode failures.

  Verify every file appears once per path, and that failures and skips are listed rather than missing.

## 3. Inference images (`inference-images`)

- [ ] 3.1 Confirm the pixel ceiling per image-token budget from the Gemma 4 and EmbeddingGemma 2 processor configs and Gemini's `media_resolution` docs. Freeze profile `infer-v1` (long edge, quality, conversion) in `config/profiles.toml`. Verify by citing the config values in `docs/profiles.md`.
- [ ] 3.2 Implement `shoebox derive --profile infer-v1`: libvips decode, orientation, ICC → sRGB, 16→8-bit mapping, metadata strip, writing `derived/infer-v1/ab/<asset_id>.jpg` with a recorded content hash. Raws are rendered via rawpy at fixed recorded settings, and PSDs are flattened from their composite. Verify with tests: 16-bit grey TIFF output is 8-bit sRGB within tolerance; NEF and DNG render; a PSD without a composite is listed as an error; no EXIF/GPS/XMP in output; regenerating gives an identical hash. Also compare raw renderings with camera JPEG siblings on 30 pairs, and record the result in `docs/profiles.md`.

## 4. Label runners (`label-runners`)

- [ ] 4.1 Implement the runner framework: JSONL in/out, params hashing over prompt, schema, profile, budget, thinking, quantisation and runtime version, and the append-only bundle store at `results/labels/<model_id>/<params_hash>/` with cache hits on existing keys. Verify with tests: re-run is a cache hit with no model call; a param change gives a new key; a killed run resumes from the first missing key.
- [ ] 4.2 Write the label output JSON schema v1 (caption, vocab tags, free tags, medium, digitiser, colour class, per-field confidence/abstain) in `config/schemas/label-v1.json`. Implement storing invalid output as `status=invalid` with the raw text. Verify with a test feeding malformed JSON.
- [ ] 4.3 Implement the local VLM runner on mlx-vlm for Gemma 4 E4B and 12B (bf16, `max_soft_tokens`, thinking off, schema-constrained output). Verify on 5 fixture images per model that outputs validate and record tokens, latency, machine and runtime version.
- [ ] 4.4 Implement the EmbeddingGemma 2 runner (bf16 or fp32 only; refuses fp16; image and caption-text embeddings; 768 d stored, MRL truncation at query time). Verify: an fp16 config refuses to run; cosine ≥ 0.999 against the sentence-transformers fp32 reference on 20 images.
- [ ] 4.5 Implement the Gemini runner: `generateContent` and Batch API, `media_resolution`, thinking level, schema output, no grounding, no Interactions storage. Add `shoebox privacy check`, the pre-flight, the never-send/consent filter, the per-run USD cap with pre-flight estimate, spend logging to `evals/spend.jsonl`, 429 backoff, and deletion of batch jobs and uploads after storing. Verify with tests on a mocked API: missing or old checklist refuses to start; never-send assets are skipped and reported; an estimate over cap refuses; deletion calls are made. Then do one live 5-image call, with cost recorded.
- [ ] 4.6 Throughput benchmark: draw a 300-image sample from the frame (all strata). Run the rules baseline, E4B and 12B (280/560), EG2 and Gemini (sync and batch). Write `evals/runs/<date>_throughput.json` with images/hour, USD/1k and full-frame projections. Verify the report exists and is committed. Decide whether the 12B 8-bit arm is needed.

## 5. Ground truth (`eval-ground-truth`)

- [ ] 5.1 Implement `shoebox sample`: stratified 5× oversample per cell, derive + EG2-embed the oversample, k-means pick within cell, hard-case additions with flags, a group-aware 40/60 dev/test split from a seed, and a set README with cell counts. Verify with tests: minimum per cell met; duplicate/capture-group members share a split; same seed gives the same set.
- [ ] 5.2 Set up Label Studio locally with configs for capture/colour classification (pre-annotations shown or hidden per task), vocab multi-label tags, shuffled caption ranking and binary relevance. Implement export to Shoebox JSONL with provenance (`labelled_by`, `method`, pre-label model_id/params_hash). Verify a round trip on 5 items, and that a "confirmed" label without a source is rejected.
- [ ] 5.3 Draw `capture-colour-v1` (600 + 100 hard) and run Gemini pre-labels (high, thinking medium, batch) on non-blind items only. Verify the blind slice is ≥ 10%, chosen before pre-labelling, and that spend is logged.
- [ ] 5.4 Owner labels `capture-colour-v1`. Compute anchoring (agreement on shown vs blind) and flag if over the margin. Freeze the set and commit. Verify the README shows labels done vs budget and the anchoring figures.
- [ ] 5.5 Draft the scene vocabulary `evals/vocab/scene-v1.toml` (facets: setting, place type, event, subject) with 10 worked example images agreed with the owner. Verify the owner signs off in the vocab README.
- [ ] 5.6 Draw `scene-v1`/`captions-v1` (200), pre-label tags, owner labels tags, freeze and commit. Verify the anchoring report and README.
- [ ] 5.7 Owner writes 50 search queries for `search-v1`, including combined facet queries. Build the corpus (all set assets + 5k random frame assets) and commit both. Verify the query file and corpus manifest are committed.

## 6. Vally execution layer (`eval-execution`)

- [ ] 6.1 Add `evals/vally/package.json` pinning `@microsoft/vally-cli` and `@microsoft/vally` exactly (0.18.0 or newer if re-verified), with `VALLY_TELEMETRY_OPTOUT=1` set in the npm scripts. Verify `npx vally --version` matches the pin.
- [ ] 6.2 Implement `shoebox eval gen <set>`: generate `eval.yaml` per set version (stimulus name = asset ID, attachment = derived image, tags = strata/split/hard, expected values in grader config, a "do not edit" header). Verify with tests and with `vally lint` passing on the generated specs.
- [ ] 6.3 Implement the `shoebox-vlm` executor plugin: replay reads the bundle store and errors on a missing key; live calls the Python runner and writes through. It emits `token_usage` and a `custom` event with asset_id, model_id, params_hash, USD and latency. Verify: replay on a missing key errors; live then replay returns identical output.
- [ ] 6.4 Implement the `field-match` (with abstain), `set-match` (per-tag TP/FP/FN) and `retrieval` (ranked IDs, nDCG@k, recall@k) graders, writing predicted/expected into metadata. Verify with Vally runs over fixture specs, and that `vally grade` after a label fix changes the verdict with no model call.
- [ ] 6.5 Add a lint rule or gen check that any `prompt` judge grader has an explicit `judge_provider`. Verify a spec without one fails before any call.
- [ ] 6.6 Write `docs/evals.md`: how to generate, run (`--model a,b,c`, `--tag split=dev`), re-grade, serve the dashboard, and the replay-vs-live rule. Verify each documented command runs as written on the fixture set.

## 7. Reporting and gates (`eval-gates`)

- [ ] 7.1 Implement `shoebox eval report`: read `results.jsonl`, compute per-field precision/recall/coverage and confusion matrices, overall and per stratum with a size floor, plus the frame-weighted estimate. Write `evals/runs/<date>_<eval>_<config-hash>.json`. Verify with unit tests on synthetic results with known answers.
- [ ] 7.2 Add retrieval aggregation (recall@k, nDCG@k, pool coverage) and the guard rule (a field winner that drops nDCG beyond tolerance is not recommended). Verify with synthetic tests.
- [ ] 7.3 Add cost/throughput columns and full-frame projections with price date, McNemar paired comparisons with tie handling, and the mixed-model seam report (agreement + mixed-index nDCG). Verify with synthetic tests.
- [ ] 7.4 Add gates: read `evals/gates.toml`, record its hash, invalidate if it changed after run start, and apply the per-stratum precision rule with the dev-frozen abstain cut-off. Exit non-zero on failure. Verify with tests: one weak stratum fails; an edited gates file invalidates.

## 8. Head-to-head and recommendation

- [ ] 8.1 Run all candidates (D7 matrix) over the dev splits of `capture-colour-v1` and `scene-v1`, and generate captions for the `search-v1` corpus with each VLM candidate. Tune abstain cut-offs and search blend weights on dev. Verify the run records are committed and spend is within budget.
- [ ] 8.2 Owner does pooled relevance judgments (top-20 per route per query) and blind side-by-side caption ranking for `captions-v1`. Verify pool coverage ≥ the configured level for every route, and that rankings are committed with provenance.
- [ ] 8.3 Write and commit `evals/gates.toml` (D11 proposals, adjusted on dev evidence) **before** any test-split run. Verify the commit precedes the test-run records in git history.
- [ ] 8.4 Run the test-split gate runs (replay mode) for all candidates and the search eval. Verify the reports are produced, gate results are recorded, and the gates hash matches the committed file.
- [ ] 8.5 Write `evals/RECOMMENDATION.md`: bulk labeller and settings, search route, per-stratum results, cost and wall-clock projection for the full frame (with price date), the mixed-model seam if bulk ≠ incremental, privacy notes, and what failed and why. Verify the owner reviews it and records the decision in the change before archive.
