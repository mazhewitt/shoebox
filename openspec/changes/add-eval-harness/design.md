# Design

## Context

See proposal.md for the motivation. The facts this design relies on come from three research notes:
- `research/mvp-models.md`: models, prices, privacy terms, runtimes;
- `research/vally.md`: Vally's fit, verified by experiment;
- `research/hosted-gemma.md`.

Settled decisions that shape it:
- Owner decisions 2026-10-03/10: compute starts on the **M5 Max laptop** (128 GB). The M1 Studio is busy, and the M5 Ultra Studio arrives late October.
- There is **no always-on worker**.
- Gates are **precision-first** (≈0.98–0.99 per stratum), with abstention and coverage reported.
- Capture is **medium × digitiser**.
- Gemini 3.8 Flash may **pre-label and compete**, under the paid-tier terms, with its 55-day abuse log accepted.
- The NAS copy of Drive is the **`ImportantFiles`** share, synced by Synology Cloud Sync. It was surveyed read-only on 2026-10-10: about 605 GB and 83k files. The images are TIFF 250 GB, DNG 106 GB, JPG 101 GB, NEF 51 GB and PSD 26 GB, and there's about 65 GB of video. It includes `Saved Pictures/Scanned Photos` (≈3.9k raw film-scan TIFFs), an old `Google Photos` year tree (2004–2019), RAW+JPEG pairs from Leica and Nikon, and 13 Lightroom catalogues. That's far more than the original "~210 GB".

`openspec/config.yaml` was updated on 2026-10-10 to reflect these decisions.

**Replan, 2026-10-11.** By then these were built and run:
- the frame over `ImportantFiles` (80,497 files, 71,622 images; `frame/README.md`);
- derive, the runners, the Vally layer and the search eval;
- pilot-v0: 45 photos through rules, E4B, 12B, 31B, Gemini and EG2, with the owner's blind labels and 39 search notes;
- `pilot-v0-pool5k`: those 45 targets among 5,000 random distractors.

What the pilots showed:
- With single-relevant judging, generic owner notes made most of the 45→5k drop a measurement artefact: image-only MRR went from 0.98 to 0.65.
- 39 queries cannot separate captioners. The minimum detectable difference is about 0.13–0.15 MRR, while the observed gaps were 0.01–0.05.
- Owner labels are sound for provenance but too few per stratum to gate on.

An adversarial review (2026-10-11) confirmed these findings. It also found:
- E4B outputs truncated at the 1,024-token cap (57 of 5,045 lost);
- a tag pattern that rejects proper nouns;
- sideways film scans: the `nikon-film-scan` bucket's image-route MRR was 0.09.

The owner then chose frontier models as the yardstick, and widened consent (D14). D1–D4, D6 and D12 stand; D5 is deferred; D7–D11 and D13–D14 are the replan.

## Goals / Non-Goals

**Goals:**
- A harness that the bulk job reuses unchanged, with the same runners and output keys, so "what was evaluated" is exactly "what ships".
- A recommendation for the bulk captioner, prompt and search method that is closest to frontier quality at the lowest cost and time, backed by paired statistics, per-stratum numbers and full-library cost and time projections.
- Learning Vally on real work, without making the harness depend on Vally's weak spots.

**Non-Goals:**
- Production schema or API.
- Faces.
- Dedup eval.
- Scheduled Takeout.
- The bulk run.
- A labelling UI. Owner spot-checks use a static local review page (replan: Label Studio dropped).
- Owner-authored ground truth at scale: the replan replaces it with frontier-model references plus audits.

## Decisions

### D1. Vally runs and grades; Python owns frame, sampling, metrics and gates
Vally scores per trial and averages the results. It has no dataset-level precision/recall, no retrieval aggregates, no USD costs and no inference cache (`research/vally.md`). So:
- **Vally** does spec loading, attachments, the multi-model `--model a,b,c` runs, per-trial grading, re-grading (`vally grade`) and the dashboard (`vally serve`).
- **Python** does everything statistical, and the gate decision.

The TypeScript surface is one executor (`shoebox-vlm`) and three graders (`field-match`, `set-match`, `retrieval`), each tens of lines.

*Alternatives:*
- **A pure-Python harness:** simpler, one language, but it loses the learning goal.
- **Inspect / promptfoo:** both are good fits, but the owner asked for Vally.

Because Vally's role is limited to replaying a keyed store, it can be swapped out later without touching runners or ground truth.

### D2. Replay-first: inference lives in the Python runners, not in Vally
Gate runs use `mode: replay`, which reads outputs that the label runners wrote. `mode: live` exists for dev-split exploration and for learning Vally; it calls through the same runner and writes through to the store. This keeps a single inference code path, shared with the bulk job, and makes the store the cache.

*Alternative:* inference inside the Vally executor. That would mean a second copy of every model call in TypeScript, plus Vally's lack of resume and cache.

### D3. Output store: JSONL bundles keyed by `(asset_id, model_id, params_hash)`
Each run writes `results/labels/<model_id>/<params_hash>/part-NNNN.jsonl` on the NAS. Each line is `{asset_id, model_id, params_hash, status, output, usage, latency_ms, usd, machine, runtime, ts}`, and `params.toml` is stored alongside. Bundles are append-only, and a key already present is a cache hit.

JSONL rather than Parquet, for now: the TypeScript replay executor reads it with no dependencies, and the catalogue change can convert it mechanically when it settles the Parquet layout.

*Alternative:* a SQLite store on the laptop. Rejected: it's a second database outside the NAS backup path, and config.yaml says model outputs land as result bundles in `results/`.

### D4. Sampling frame: `shoebox inventory`, a read-only Python pass
- **What it does:** walks every source in `config/sources.toml` over read-only mounts.
  - **Sources:** `ImportantFiles`; `Home Folders` (per-person subfolders); `Backup` (`mazda/DESKTOP-EIBCH27`, `Backup/Jude`); `Volume2/damaged-backup`; and the landed Takeout exports.
  - **Each source records:** its person and whether it's cloud-synced.
  - **Owner decision (2026-10-10):** everything is in scope eventually, and duplicates are cleaned up later from a dedup report.
- **What it skips:** `#recycle`, `$RECYCLE.BIN`, `.tmp.drivedownload`, Lightroom `.lrdata` previews and OS litter. Skipped paths are counted.
- **Sibling groups:** within a folder, files sharing a base name across RAW/JPG/TIFF/PSD form a capture group, and the rule that formed it is recorded.
- **What it records per file:** BLAKE3 (via the Rust-backed `blake3` package), ExifTool JSON in batches, dimensions and bit depth, the best date and its kind (EXIF, sidecar, filename, file mtime), and chroma statistics from a 256 px decode (libvips).
- **Output:** `frame/frame-<date>.parquet`.

**Why not wait for the Rust `shoebox-scan`:** it belongs to the catalogue change, whose schema waits on these evals. The asset IDs are compatible by construction, because both hash the same bytes with the same algorithm. The scanner supersedes the inventory later.

**Cost:** about 880 GB plus the Takeout over 1 GbE is roughly 2.5–3 hours of hashing I/O. Decoding for the chroma stats dominates, at hours not days. Raw files use their embedded preview for chroma stats only.

### D5. Sampling: stratified oversample, then diversity within each cell
*Deferred (replan 2026-10-11): the replan's corpora are random draws from the frame (D8). This method returns if per-stratum gates need more items in small cells.*

1. Strata are era (5-year buckets, plus a separate "scan-dated" bucket), medium/digitiser guess from the rule baseline, and colour class guess from chroma.
2. Draw 5× the target per cell.
3. Derive inference images for the oversample only, embed them with EmbeddingGemma 2, and k-means within each cell to pick the final members.

This avoids deriving and embedding the whole archive just to sample it. Using EG2 for diversity only spreads the draw; it doesn't touch labels or the search queries, so the bias toward EG2 is negligible.

Dev/test is a 40/60 split, by capture/duplicate group (the exact-hash group in the frame, plus filename burst/edit patterns), with a fixed seed.

### D6. Image profile `infer-v1`
| Setting | Value |
|---|---|
| Long edge | 2048 px, never upscaled |
| Format | 8-bit sRGB JPEG, q92 |
| Orientation | EXIF orientation applied. Scans with no orientation tag get an orientation pass (D6a) from profile `infer-v2` on |
| Metadata | stripped |
| 16-bit TIFF | linear → sRGB with the embedded ICC if present, otherwise assumed sRGB/gamma 2.2; tonal range mapped via a percentile stretch only where flagged |
| Camera raw (NEF/DNG) | rendered with LibRaw (via rawpy) at fixed, recorded settings: camera white balance, default curve, sRGB. The raw rendering is used even when a camera JPEG sibling exists, so raw-only captures aren't treated differently; the JPEG sibling is its own asset in the same capture group |
| Layered PSD | flattened composite, via libvips/ImageMagick; PSDs saved without a composite are listed as decode errors |
| Tool | libvips (plus rawpy for raws) |

The 2048 px edge covers the largest budget under test: Gemma 4 / EG2 at 1120 soft tokens, and Gemini `high`. The exact pixel ceiling per budget is confirmed from the processor config in task 3.1 before the profile is frozen.

Per-budget downscaling is left to the model: Gemini `media_resolution` and, under mlx-vlm, `max_soft_tokens`. **As built, the local runners use Ollama** (image budget recorded as `"auto"`, chosen by Ollama from the input size). mlx-vlm was not needed for the pilots; it returns only if budget control is wanted for the bulk run.

### D6a. Orientation of scans
Film and print scans carry no EXIF orientation, and some are stored sideways. That hurts every route.

The fix:
1. **Method.** Choose one on 50 scans, with Opus judging upright as the reference. Candidates:
   - an E4B orientation question over the 4 rotations;
   - the EG2 image–text score for "an upright photograph" over the 4 rotations;
   - Gemini on the eval corpus only.
2. **Bulk.** The chosen local method runs over scan buckets only, at derive time.
3. **Recording.** The rotation is recorded per asset in the derive index. The rotated images form profile `infer-v2`, so outputs keyed to `infer-v1` images never mix silently.

Derived images go to `derived/infer-v1/ab/<asset_id>.jpg` on the dedicated `shoebox` share and are excluded from GCS.

### D7. Who does what: frontier models as the yardstick, cheap models as candidates (replan 2026-10-11)
| Role | Model | Notes |
|---|---|---|
| Query writer | Claude Opus 5.5 | Writes the synthetic queries (D9). It captions nothing, so the queries don't favour any captioner. |
| Reference captioner | Gemini 3.8 Flash, `medium`, thinking `low` | Captions `eval-1k` with `label-v2`. It is both the quality bar and a candidate for the bulk run. |
| Bulk relevance judge | Gemini 3.8 Flash | Sees the query and a grid of candidate images, never the captions (D10). |
| Source of truth | Claude Opus 5.5 | Audits about 10% of judgments, a sample of captions, and the structured-field reference. |
| Candidate captioners | Gemma 4 E4B (`e4b-bf16`) and 12B (`g12b-q4`) on Ollama | Each with `label-v1` and `label-v2`. 31B is dropped: about 230 img/h, or about 310 h for the library. |
| Embedder | EmbeddingGemma 2 (`eg2-bf16`) | Image and caption-text vectors. |
| Owner | | About 45 minutes: about 100 judgments, 30 captions, and whether a sample of 30 queries reads like real searches. |

**How Opus runs.** There is no Anthropic API key, so Opus runs as Claude Code subagents. They view derived infer-v1 JPEGs and write JSONL, which is stored in the results store keyed `(asset_id or query_id, model_id = "claude/opus-5.5", params_hash)` like any runner, so audits are reproducible as records. The prompt files are versioned in `config/prompts/`. If an API key is added later, an API runner replaces the subagents behind the same keys.

**Prompt `label-v2`.** Search-oriented: a 2–4 sentence caption plus lowercase tags. It covers the setting, subjects and activity, the number of people and their apparent roles (no identification), any visible text, the medium cues (black-and-white, film grain, scan borders, print fading) and era cues. The structured fields stay as in `label-v1`. Tags may name places when they're visible (signs, landmarks), and are lowercased by the runner, not rejected.

### D8. Eval corpora and splits
- **`eval-1k`:** 1,000 photos from `pilot-v0-pool5k`, including its 45 targets. The other 955 come from the 1,966 non-target pool photos that 12B (`label-v1`) already processed before the run stopped (1,949 valid; the 17 invalid are retried after the tag fix). The runner processes in asset_id order and BLAKE3 ids are uniform, so that prefix is a random sample. That means 12B `label-v1` costs nothing, and E4B `label-v1` and the EG2 image vectors already exist.
- **Hosted-eligibility screen**, before anything is sent. Excluded:
  - `evals/never-send.txt`;
  - paths under `documents/` (passports, IDs, visas);
  - `Pictures/toren phone` and `Pictures/Jude Profile`, which look like other people's libraries and stay out until the owner confirms;
  - anything an Opus pre-screen flags as medical, ID or financial.

  Excluded photos are swapped for eligible ones from the same pool so the corpus stays at 1,000. The screen and the swaps are recorded in the set README.
- **`pilot-v0-pool5k` (5,042)** stays the confirmation corpus. The chosen pipeline is re-run there, and its image-only and E4B routes already exist.
- **Splits.** Queries split dev/test about 1:2, by source photo's capture group, from a recorded seed. The dev split is for tuning (blend weight, expansion prompt, filters). The test split is scored once, after `gates.toml` is committed (D11).

### D9. Synthetic queries (Opus)
- **Known-item queries (~200):** one per source photo, from a stratified draw across `eval-1k` by bucket, era and colour class. Opus sees the derived image plus its date, folder path and filename, and writes one query in a style assigned at random from a fixed mix:
  - vague recall ("that snowy valley walk");
  - visual description;
  - attribute-led ("black and white portrait of an old woman");
  - place/time from metadata ("Greece 2018 ruins");
  - people by role, from folder or filename only, never from faces ("grandad in helsinki");
  - text in the photo.
- **Category queries (~80):** written from a summary of the corpus (folder names, eras, bucket counts) without looking at any photo, e.g. "black and white film photos of mountains", "beach holidays with the kids", "scanned prints from the 70s". They have many right answers.
- **Owner notes:** the 39 pilot notes are kept as an owner-voice check, reported separately.
- **Owner realism check:** the owner reads 30 random queries and marks each "I'd search like this / I wouldn't". The style mix is adjusted on dev if needed.
- **Circularity guard:** queries come from Opus, captions from Gemini/Gemma, and judgments from Gemini audited by Opus. No model grades its own output.

### D10. Pooled, graded relevance judging
- **The pool:** the union of every route's top 10 per query (at about 10 routes × 3 captioners, typically 30–50 unique images), plus the known-item target.
- **The judge:** Gemini sees the query and a grid of up to 10 numbered thumbnails (derived images at `low`), and returns a grade per image: 0 not relevant, 1 partly, 2 fits. Judgments are stored keyed `(query_id, asset_id, model_id, params_hash)`.
- **Audit:** Opus re-judges a stratified 10% (by route, grade and query kind) and all disagreements with the known-item target. The owner judges 100 drawn from the audited set.
- **Reported:**
  - Gemini–Opus agreement (weighted kappa), per route, to detect a judge that favours one route;
  - owner–Opus agreement.
- **If Gemini–Opus agreement is below 0.6**, Opus re-judges the whole test pool.
- **Unjudged results** (from a route added later) are queued for judging before that route can pass; pool coverage is reported.

### D11. Metrics, statistics and the pre-registered decision rule
- **Primary metric:** nDCG@20 with graded relevance over the test queries. Also MRR and hit@k for known-item queries, recall@20 for category queries, and the owner-notes check.
- **Statistics:** route comparisons use a paired bootstrap over queries (one resample index for both routes) on the metric delta, with 95% CIs. A sign test is shown for information only.
- **Sample size:** with about 200 test queries and paired deltas whose sd is about 0.3, the minimum detectable difference is about 0.06. Differences below that are reported as ties, decided on cost.
- **Decision rule** in `evals/gates.toml`, committed before the test split is scored:
  1. **Reference:** the Gemini `label-v2` captions with the best route on dev.
  2. **Candidates:** every (captioner, prompt, route) triple, including "no captioner" (image + metadata routes only).
  3. **Pass:**
     - search non-inferiority: the lower 95% bound of (candidate − reference) test nDCG@20 is above −0.03;
     - the Opus-audited caption error rate (wrong or invented facts) is at most the reference's + 2 points;
     - colour/mono precision is at least 0.98 against the audited reference.
  4. **Choose:** the passing candidate with the lowest projected full-library cost, with hosted dollars and local hours both shown. A tie in cost goes to local.
  5. **No local pass:** recommend Gemini for the bulk run, with its cost re-quoted at batch price.
- **Structured fields** (medium, digitiser, colour/mono) are reported per stratum, scored against the reference:
  - metadata rules where they are unambiguous (scanner EXIF, film-scan folders, camera EXIF);
  - otherwise Gemini `label-v2`.

  Opus audits 200 reference labels, and the owner checks Opus–reference disagreements.

### D12. Machines, shares and mounts
Everything runs on the laptop.

**`ImportantFiles` is never written.** It syncs with Google Drive, and its direction isn't something to depend on, so anything written there could reach Drive. Today's `/Volumes` mounts are read-write SMB under the owner's account, so they aren't used for Shoebox. Instead:
- **NAS user:** a dedicated `shoebox` NAS user with **read-only** permission on `ImportantFiles`. This is enforced by the NAS, not by our code.
- **Separate mount:** that user mounts `ImportantFiles` at its own mount point (`mount_smbfs -o rdonly`).
- **Check in code:** every command also refuses a source root it can write to (spec: "Synced sources are never written").
- **Finder:** browsing source mounts in Finder is avoided, because Finder writes `.DS_Store`.

The `shoebox` user also gets **read-only** access to `Home Folders`, `Backup` and `Volume2`. They aren't synced, but they're originals all the same.

**A new, non-synced `shoebox` share** holds `staging/`, `originals/takeout/` (read-only after extraction), `derived/`, `results/` and `frame/`. The `shoebox` user has read-write access to it.

*Alternative:* using the owner's existing read-write mounts with care. Rejected, because one bug could push files to Drive. Python runs with uv; Vally runs from a pinned local `node_modules` with `VALLY_TELEMETRY_OPTOUT=1`.

### D13. Hosted budget for the replan
| Use | Basis | Cost |
|---|---|---|
| Gemini reference captions | `eval-1k` at `medium` | ≈ $2–3 |
| Gemini judging | ~300 queries × ~40 pooled images, in grids of 10 at `low` | ≈ $15–25 |
| Confirmation on the 5k pool | Gemini captions, only if Gemini is in the final comparison | ≈ $10 |
| Retries and dev iterations | | ≈ $5 |

The total is about $35–45. $0.18 has been spent so far, which leaves about $100 for a Gemini bulk run (about $145 at sync price for 71.6k images, less at batch price, re-quoted before deciding). Opus runs through the owner's Claude subscription, at no API cost.

### D14. Hosted runs: Gemini and Claude, guarded
- **Approved hosted models:** Gemini 3.8 Flash on the owner's paid project, and Claude Opus 5.5 via Claude Code. The owner approved both on 2026-10-11 for their own library, including photos of family and friends.
- **How Gemini is called:** `generateContent` (`store=false`) or the Batch API. Never Interactions with `store=true`, and never grounding.
- **How Claude is called:** Claude Code subagents read derived JPEGs from `~/mnt/shoebox/derived/`, never originals.
- **Before a Gemini run:** the runner pre-flight reads `evals/privacy-checklist.toml`.
- **After a run:** batch jobs and uploads are deleted.
- **Spend:** each run has a USD cap, and spend is tracked in `evals/spend.jsonl` against $150.
- **Consent is per library (source):**
  - **Sendable:** the owner's library (`ImportantFiles`, `hosted_consent = true`).
  - **Not sendable:** other people's own libraries (Home Folders, `Backup/Jude`, Jude's Takeout) until their person's consent is recorded.
  - **Held back pending owner confirmation:** folders inside the owner's share that look like someone else's library (`Pictures/toren phone`, `Pictures/Jude Profile`).
- **Never sent:**
  - `evals/never-send.txt`;
  - ID, medical and financial documents (the D8 screen);
  - any asset with a copy in a non-consented source.
- **Faces/identity:** no hosted model is asked to recognise or name people. Names in queries come only from folder and file names.

## Risks / Trade-offs

- **[Vally churn: pre-1.0, minor releases every 1–3 weeks, no public repo]** → Pin exact versions, keep the TypeScript minimal, and hold all state outside Vally. Upgrades are deliberate and re-run one known eval to compare.
- **[`vally experiment` can't load plugins in 0.18.0]** → Use `vally eval --model a,b,c --param …` for sweeps.
- **[Python graders need `--workspace` when re-grading]** → Graders are TypeScript; Python is only used in the report step.
- **[LLM judge bias: Gemini judging could favour what Gemini sees]** → The judge sees images and the query, never captions. Opus audits a stratified 10% and every target disagreement. Gemini–Opus agreement is reported per route, and Opus re-judges the whole test pool if kappa < 0.6.
- **[Synthetic queries may not match how the owner will search]** → Mixed styles (D9), category queries written without seeing photos, the owner's realism check on 30, and the owner's 39 notes reported separately. When real search logs exist, a later eval replaces the synthetic set.
- **[Opus writes queries while looking at the photo: visual-description bias, as with the owner notes]** → Only some of the style mix is visual description; the rest is vague recall, attributes, place/time and roles from metadata. Results are reported per style.
- **[The reference is a model, not the truth]** → The Opus and owner audits measure how often the reference is wrong. The gate is non-inferiority to the reference, not agreement with it. A captioner that is "better than Gemini" shows up as a non-negative delta, not a failure.
- **[Opus via subagents is slower and less reproducible than an API]** → Prompts are versioned and outputs are stored keyed by params_hash. Volumes are kept to hundreds of items (about 300 queries, about 10% audits).
- **[Gemini price doubles on 2027-01-01]** → Reports print the price date. If Gemini wins, decide on the bulk run before year end, or re-cost it.
- **[Local throughput: 12B ≈ 470 img/h (≈150 h for the library), E4B ≈ 900–1,050 img/h (≈70 h)]** → The library projection is part of the decision rule. A Mac Studio arriving in late October may change the hours and is re-measured then.
- **[The frame misses the second library until Jude's Takeout lands]** → The frame records the libraries present. Sets can be topped up with a new version when the second library lands, and the strata README says what was missing.
- **[Gemini and Claude never see non-consented libraries]** → The replan's corpora come from the owner's library only. A Gemini bulk recommendation covers consented sources only; other libraries use the chosen local captioner, and the mixed-model seam is measured.
- **[Heavy overlap between `ImportantFiles/Pictures/Google Photos` and Takeout]** → Exact-hash duplicates are grouped in the frame, and their members share a split. Re-encoded copies are left for the dedup change. The frame README reports overlap by hash.
- **[Raw rendering differs from the owner's processed look]** → Labels are about content, not rendering. Raw-vs-JPEG agreement is checked on 30 RAW+JPEG pairs in task 3.2.
- **[EG2 is 4 days old; fp16 silently breaks it]** → Use bf16 or fp32 only, enforced by the runner. Check against the sentence-transformers reference on 20 images before use. SigLIP 2 is held as a fallback arm.
- **[Mixed bulk/incremental models leave a seam]** → Measured explicitly (eval-gates spec, "Mixed-model seam is measured").

## Migration Plan

This is greenfield; nothing is migrated.

Rollback means deleting `derived/infer-v1/` and `results/labels/`. Ground truth and run records in git are never rolled back, only versioned.

## Open Questions

- The exact Gemini 3.8 Flash RPM and batch limits for the owner's tier. These are read in AI Studio and only change run scheduling, not design.
- Whether Vally publishes release notes anywhere. This affects upgrade hygiene only.
- The final thresholds in `gates.toml`. By design they are set after the dev baseline and before the test run (D11).
- Whether `Pictures/toren phone` and `Pictures/Jude Profile` are the owner's or someone else's library. They are held back from hosted sends until the owner says.
- Gemini batch pricing for the bulk projection, re-quoted before the decision.
