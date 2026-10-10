# Design

## Context

See proposal.md for the motivation. Nothing exists yet: no code, no catalogue, and Takeout has not been landed. The facts this design relies on come from three research notes:
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

## Goals / Non-Goals

**Goals:**
- A harness that the bulk job reuses unchanged, with the same runners and output keys, so "what was evaluated" is exactly "what ships".
- A recommendation for the bulk labeller and the search route, backed by per-stratum numbers, cost and throughput.
- Learning Vally on real work, without making the harness depend on Vally's weak spots.

**Non-Goals:**
- Production schema or API.
- Faces.
- Dedup eval.
- Scheduled Takeout.
- The bulk run.
- A general-purpose labelling UI. The labelling tool here is chosen for eval work only (D8).

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
- **What it does:** walks `ImportantFiles` and the landed Takeout export over read-only mounts.
- **What it skips:** `#recycle`, `$RECYCLE.BIN`, `.tmp.drivedownload`, Lightroom `.lrdata` previews and OS litter. Skipped paths are counted.
- **Sibling groups:** within a folder, files sharing a base name across RAW/JPG/TIFF/PSD form a capture group, and the rule that formed it is recorded.
- **What it records per file:** BLAKE3 (via the Rust-backed `blake3` package), ExifTool JSON in batches, dimensions and bit depth, the best date and its kind (EXIF, sidecar, filename, file mtime), and chroma statistics from a 256 px decode (libvips).
- **Output:** `frame/frame-<date>.parquet`.

**Why not wait for the Rust `shoebox-scan`:** it belongs to the catalogue change, whose schema waits on these evals. The asset IDs are compatible by construction, because both hash the same bytes with the same algorithm. The scanner supersedes the inventory later.

**Cost:** about 605 GB plus the Takeout over 1 GbE is roughly 1.5–2 hours of hashing I/O. Decoding for the chroma stats dominates, at hours not days. Raw files use their embedded preview for chroma stats only.

### D5. Sampling: stratified oversample, then diversity within each cell
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
| Orientation | EXIF orientation applied |
| Metadata | stripped |
| 16-bit TIFF | linear → sRGB with the embedded ICC if present, otherwise assumed sRGB/gamma 2.2; tonal range mapped via a percentile stretch only where flagged |
| Camera raw (NEF/DNG) | rendered with LibRaw (via rawpy) at fixed, recorded settings: camera white balance, default curve, sRGB. The raw rendering is used even when a camera JPEG sibling exists, so raw-only captures aren't treated differently; the JPEG sibling is its own asset in the same capture group |
| Layered PSD | flattened composite, via libvips/ImageMagick; PSDs saved without a composite are listed as decode errors |
| Tool | libvips (plus rawpy for raws) |

The 2048 px edge covers the largest budget under test: Gemma 4 / EG2 at 1120 soft tokens, and Gemini `high`. The exact pixel ceiling per budget is confirmed from the processor config in task 3.1 before the profile is frozen.

Per-budget downscaling is left to the model: mlx-vlm `max_soft_tokens`, Gemini `media_resolution`, EG2 `max_soft_tokens`. That's why **Ollama is used for exploration only**: it has no budget control and picks the budget from the input size.

Derived images go to `derived/infer-v1/ab/<asset_id>.jpg` on the dedicated `shoebox` share and are excluded from GCS.

### D7. Candidate matrix (kept small on purpose)
**Structured fields and captions:**

| Candidate | Settings |
|---|---|
| rules baseline | capture and colour only |
| Gemma 4 E4B | bf16, mlx-vlm, budget 280 and 560, thinking off |
| Gemma 4 12B | bf16, mlx-vlm, budget 280 and 560, thinking off |
| Gemini 3.8 Flash | `low` and `medium`, thinking `low` |

1120 is added only for the arm that wins at 560, to show whether more detail still pays. 12B 8-bit is added only if 12B bf16 throughput rules out the bulk run.

**Search:**

| Route | Settings |
|---|---|
| EG2 image-only | 768 d and 256 d |
| EG2 caption-text-only | per VLM candidate |
| EG2 image + caption, blended | blend weight tuned on dev queries |
| FTS over captions and tags | |
| FTS + EG2 blend | |

**Pre-labeller:** Gemini 3.8 Flash at `high` resolution, thinking `medium`, via Batch.

**Throughput benchmark:** the same 300-image sample through every candidate on the laptop, plus Gemini sync and batch, reporting images/hour.

### D8. Labelling tool: Label Studio (open source), run locally only during sessions
Label Studio (Apache 2.0) covers the needs out of the box:
- classification with pre-annotations, shown for some tasks and hidden for the blind slice;
- multi-label tags from a vocabulary;
- side-by-side ranking of shuffled captions;
- binary relevance for pooled search results.

Labels are exported to JSONL and committed to `evals/sets/`. Label Studio's own database is scratch.

*Alternative:* building the labelling screens in the Shoebox web UI now (PLAN §Phase 4). That's better long-term, but it would put UI and API work ahead of the evals the owner wants first. The exported JSONL format is ours, so the later UI writes the same files.

### D9. Hosted runs: Batch API, guarded
- **How Gemini is called:** `generateContent` (`store=false` by default) or the Batch API. Never Interactions with `store=true`, and never grounding.
- **Before a run:** a runner pre-flight reads `evals/privacy-checklist.toml`. The owner records there that AI Studio logging is off, retention is 7 days, there are no datasets and the billing project ID is set, together with a confirmation date.
- **After a run:** batch jobs and Files API uploads are deleted once the results are stored.
- **Spend:** each run has a cap, and cumulative spend is tracked in `evals/spend.jsonl` against $150.
- **Skipped assets:** anything on the never-send list (`evals/never-send.txt`, by asset ID or path glob) and Jude's library until consent is recorded.

Estimated hosted spend at the introductory price:

| Use | Basis | Cost |
|---|---|---|
| Pre-labels | ~950 items at `high`, ≈1.4k in / 1k out+thinking tokens | ≈ $3.50 |
| Candidate arms | 700 + 200 items, two settings | ≈ $3 |
| Search-corpus captions | 5k images at `low`, batch | ≈ $5 |
| Throughput sample | 300 images, sync and batch | < $1 |

The total is about $15. That leaves well over $100 for a bulk run if Gemini wins, though the bulk would cost ≈ $50+ at 100k images in batch and doubles after 2026-12-31.

### D10. Set sizes and owner budget for this change
| Set | Items | Owner effort |
|---|---|---|
| `capture-colour-v1` | 600 stratified + 100 hard | 700 labels, Gemini pre-labelled, blind slice ≥ 70 |
| `scene-v1` | 200 (from the capture set's dev+test) | 200 tag sets; vocabulary drafted first with 10 worked examples |
| `captions-v1` | the same 200 | 200 blind side-by-side rankings |
| `search-v1` | 50 queries, corpus = all set assets + 5k random frame assets | ~1,500 quick binary judgments from pools (top-20 per route) |

The first three total about 1,100 of the ~3,000 budget. Relevance judgments are tracked separately as quick clicks.

### D11. Gates are written before the test run, from the dev baseline
The dev-split run sets the abstain cut-offs and shows where thresholds are plausible. `gates.toml` is then committed, and only after that is the test split scored. Starting proposals:

| Field | Gate |
|---|---|
| colour class, medium, digitiser | precision ≥ 0.98 per stratum (stratum-size floor 20), coverage reported |
| scan vs not-scan | precision ≥ 0.99 |
| tag facet `setting` | precision ≥ 0.95 |
| other tags | precision ≥ 0.85 |
| search nDCG@20 | baseline-relative: the chosen route must beat FTS-only, and no candidate may drop it by more than 0.02 |

### D12. Machines, shares and mounts
Everything runs on the laptop.

**`ImportantFiles` is never written.** It syncs with Google Drive, and its direction isn't something to depend on, so anything written there could reach Drive. Today's `/Volumes` mounts are read-write SMB under the owner's account, so they aren't used for Shoebox. Instead:
- **NAS user:** a dedicated `shoebox` NAS user with **read-only** permission on `ImportantFiles`. This is enforced by the NAS, not by our code.
- **Separate mount:** that user mounts `ImportantFiles` at its own mount point (`mount_smbfs -o rdonly`).
- **Check in code:** every command also refuses a source root it can write to (spec: "Synced sources are never written").
- **Finder:** browsing source mounts in Finder is avoided, because Finder writes `.DS_Store`.

**A new, non-synced `shoebox` share** holds `staging/`, `originals/takeout/` (read-only after extraction), `derived/`, `results/` and `frame/`. The `shoebox` user has read-write access to it.

*Alternative:* using the owner's existing read-write mounts with care. Rejected, because one bug could push files to Drive. Python runs with uv; Vally runs from a pinned local `node_modules` with `VALLY_TELEMETRY_OPTOUT=1`.

## Risks / Trade-offs

- **[Vally churn: pre-1.0, minor releases every 1–3 weeks, no public repo]** → Pin exact versions, keep the TypeScript minimal, and hold all state outside Vally. Upgrades are deliberate and re-run one known eval to compare.
- **[`vally experiment` can't load plugins in 0.18.0]** → Use `vally eval --model a,b,c --param …` for sweeps.
- **[Python graders need `--workspace` when re-grading]** → Graders are TypeScript; Python is only used in the report step.
- **[Anchoring on Gemini pre-labels inflates Gemini's score]** → Blind slice ≥ 10%, with Gemini's score reported on the blind slice separately. If anchoring shows, label the rest blind.
- **[Gemini price doubles on 2027-01-01]** → Reports print the price date. If Gemini wins, decide on the bulk run before year end, or re-cost it.
- **[Local throughput unknown; 12B could take days]** → The 300-image benchmark comes first. The 12B 8-bit arm is held in reserve.
- **[The frame misses the second library until Jude's Takeout lands]** → The frame records the libraries present. Sets can be topped up with a new version when the second library lands, and the strata README says what was missing.
- **[Heavy overlap between `ImportantFiles/Pictures/Google Photos` and Takeout]** → Exact-hash duplicates are grouped in the frame, and their members share a split. Re-encoded copies are left for the dedup change. The frame README reports overlap by hash.
- **[Raw rendering differs from the owner's processed look]** → Labels are about content, not rendering. Raw-vs-JPEG agreement is checked on 30 RAW+JPEG pairs in task 3.2.
- **[Label Studio is throwaway]** → Export format is owned by Shoebox, so nothing is locked in.
- **[EG2 is 4 days old; fp16 silently breaks it]** → Use bf16 or fp32 only, enforced by the runner. Check against the sentence-transformers reference on 20 images before use. SigLIP 2 is held as a fallback arm.
- **[Mixed bulk/incremental models leave a seam]** → Measured explicitly (eval-gates spec, "Mixed-model seam is measured").

## Migration Plan

This is greenfield; nothing is migrated.

Rollback means deleting `derived/infer-v1/` and `results/labels/`. Ground truth and run records in git are never rolled back, only versioned.

## Open Questions

- The exact Gemini 3.8 Flash RPM and batch limits for the owner's tier. These are read in AI Studio and only change run scheduling, not design.
- Whether Vally publishes release notes anywhere. This affects upgrade hygiene only.
- The final thresholds in `gates.toml`. By design they are set after the dev baseline and before the test run (D11).
