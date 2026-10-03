# Shoebox — Plan

**Status:** Plan for review. Nothing is built, downloaded or moved until this is signed off.
**Input:** `handoff.md`, plus the language steer: *pipeline in Python or TypeScript; anything that needs to be fast or long-running in Rust.*

---

## 0. Summary of the key decisions

| Question | Recommendation |
| --- | --- |
| Pipeline language | **Python** (not TS). The models, ExifTool wrappers, clustering and eval metrics all live there; one language for pipeline + inference + evals. Managed with `uv`, every stage a CLI command, no Python daemons. |
| Rust scope | Everything fast or long-running: the **API** (Axum, NAS), the **scanner** (hashing, decode, thumbnails, colour metric — the passes over all 210 GB), and the **worker daemon** on the Mac Studio. |
| Web UI | TypeScript, thin client over the HTTP API. |
| Source of truth | Originals are immutable. All metadata — including Takeout sidecar data — goes into the **database with provenance**, not written back into files. (Deviation from handoff step 2; see §8.) |
| Identity | Every asset is keyed by **BLAKE3 of the original bytes**. Paths are just locations; model outputs are keyed by hash + model version, so they survive schema changes. |
| Vector store | **SQLite, embeddings as BLOBs, filtered brute force** (sqlite-vec or plain Rust). No Qdrant. At ~100k images × 768 d, a scan of the filtered set is milliseconds, and pre-filtering by date/person/B&W is exactly what ANN indexes are bad at. |
| CLIP vs VLM | **Both, with different jobs.** VLM → a *controlled tag vocabulary* + short caption (filterable, explainable, FTS5). CLIP-style embeddings → free-text semantic ranking and near-duplicate support. Search eval decides how they're weighted. |
| Job queue | **Lives in the NAS SQLite, exposed over the API.** Workers lease jobs over HTTP. Nothing ever opens the SQLite file over SMB/NFS. |
| NAS inference | Assume **none**, except possibly a CLIP *text* encoder for query-time search (small, CPU, ONNX via `ort` in Rust). Phase 0 checks RAM/CPU and decides. |
| Thumbnails | **Pre-generate** during the bulk pass on the M5 (two sizes, WebP/AVIF); NAS does on-the-fly only as fallback with cache. An old Intel NAS decoding 40 MB TIFF scans on request will be slow. |

---

## 1. Components and language split

```
                    Cloudflare Access (Google auth) ──► tunnel
                                                         │
┌──────────────────────── Synology NAS (Docker) ─────────▼───────────────────┐
│  shoebox-api   (Rust, Axum)                                                │
│    • search API, image + thumbnail serving (range requests), JWT check    │
│    • job queue endpoints: /jobs/lease, /jobs/{id}/heartbeat, /complete     │
│    • result import (single writer to SQLite)                               │
│  shoebox.sqlite  (FTS5 + embedding BLOBs + job table), Litestream → GCS    │
│  originals/ (read-only)   derived/   staging/   results/   evals/          │
└──────────────▲──────────────────────────────────────▲──────────────────────┘
               │ HTTP (LAN): lease jobs, post results │ NFS read-only mount
┌──────────────┴───────────── M1 Mac Studio ──────────┴──────────────────────┐
│  shoebox-worker (Rust daemon, launchd)                                     │
│    • polls/leases jobs, heartbeats, retries, backpressure                  │
│    • supervises persistent Python runners (models stay warm)               │
│  runners (Python): faces | embed | caption — JSONL over stdin/stdout       │
└────────────────────────────────────────────────────────────────────────────┘
┌───────────────────────────── M5 Max (one-off) ─────────────────────────────┐
│  shoebox-scan (Rust CLI) + same Python runners, driven by the pipeline CLI │
│  writes result bundles (Parquet, keyed by asset hash + model id) → NAS     │
└────────────────────────────────────────────────────────────────────────────┘
```

**Rust crates (one workspace):**
- `shoebox-core` — asset id, hashing, EXIF/metadata types, shared DB access.
- `shoebox-scan` — CLI. Walk a tree; BLAKE3 the file bytes; decode once and from that decode produce: a pixel hash, perceptual hashes, thumbnails, colour statistics. Parallel (rayon), resumable, emits Parquet.
- `shoebox-api` — Axum service. Search, serving, job queue, result import, Cloudflare Access JWT validation (verify against the team's JWKS; trust nothing else).
- `shoebox-worker` — Mac Studio daemon. Leases jobs, spawns and supervises the Python runners, handles crashes/timeouts, posts results.

**Python package (`shoebox-pipeline`, run with `uv`):**
- `acquire` — Takeout zip verify + extract, rclone wrappers, manifest.
- `metadata` — ExifTool (`-stay_open` batch mode) + Takeout sidecar parsing + reconciliation into one record with provenance.
- `runners/` — `faces`, `embed`, `caption`. Each is a process that loads a model once, reads `{asset_id, path}` lines on stdin, writes results on stdout. Same runner is used by the bulk job on the M5, the worker on the Mac Studio, and the eval harness. **That is the Python boundary**: Rust never imports Python, Python never owns a long-lived service.
- `cluster` — face clustering (batch).
- `evals` — the eval harness (§5).
- `import` — loads result bundles into SQLite via the API (or directly on the NAS as a one-shot container job).

**Why results go through bundles:** the schema is deliberately undecided. If bulk inference writes Parquet keyed by `(asset_id, model_id, params_hash)`, then the schema can change and be re-imported without re-running 70 hours of inference.

---

## 2. Storage layout (NAS)

```
/volume1/shoebox/
  staging/            Takeout zips, rclone landing area. Deleted only after a verified extract.
    takeout/2026-10-xx/*.zip
    drive/
  originals/          Read-only after acquisition (chmod + a read-only Docker bind mount).
    takeout/<export-date>/...     original Takeout tree, untouched (sidecars kept alongside)
    drive/<rclone tree>/...
  derived/            Regenerable. Excluded from GCS backup.
    thumbs/ab/abcdef…_256.webp, …_1024.webp
  results/            Parquet result bundles from scan / inference runs. Backed up (cheap, expensive to recreate).
    scan/<run-id>/   faces/<model>/<run-id>/   embed/…   caption/…
  db/
    shoebox.sqlite    Single writer: shoebox-api. Litestream replication to GCS.
  evals/              Ground-truth sets + run reports. Also in git.
```

Backup priorities: **human labels** (face names, eval ground truth) are the most irreplaceable data in the system — they are the only thing that costs human hours. DB + results + evals go to GCS; `derived/` does not.

Disk budget to confirm in Phase 0: ~210 GB zips + ~210 GB extracted + Drive + ~5–10% derived. Zips can go after verification.

---

## 3. Phases and gates

Each phase ends in a gate. A gate is a written eval report against a frozen ground-truth set, compared with thresholds set *before* the run. No stage runs over the full archive until its gate passes on the sample.

### Phase 0 — Recon (no data moves)

- NAS: model, CPU, **RAM**, DSM version, Docker version, free space, filesystem (Btrfs → snapshots available?), NFS support.
- Network: NAS ↔ Mac throughput (decides read-over-NFS vs copy-to-local for the M5 bulk run).
- Inventory **without downloading**: `rclone lsjson -R` on Drive (counts, sizes, extensions, mime types, mtimes). For Photos, the Takeout item count/size shown at export time.
- Check whether Photos was backed up in **Original** or **Storage saver** quality. If storage saver, Takeout delivers recompressed files and the EXIF situation is different.
- Pick 5 throwaway sample files of each kind (phone JPEG, HEIC, scanned TIFF, scanned JPEG, screenshot, WhatsApp-received image, video) for smoke tests.
- Decide the incremental ingest path (see Risk R1) — it shapes Phase 7.

**Exit:** a one-page recon note with the numbers, and the open questions in §7 answered or explicitly deferred.

### Phase 1 — Acquire

- Request Takeout (Photos only, 50 GB zips — fewer files to babysit). Download to `staging/takeout/`.
- `unzip -t` every archive; extract to `originals/takeout/<date>/`; build a manifest (path, size, BLAKE3).
- Drive: `rclone copy` (never `sync`) with a **read-only** OAuth scope (`drive.readonly`), `--checksum`. Then `rclone check` against the remote.
- Make `originals/` read-only.

**Gate A (completeness, no ML):** file counts and bytes reconcile with the Phase 0 inventory; zero failed archive tests; `rclone check` clean; each Takeout media file is matched to a sidecar or listed as unmatched. Unmatched rate is reported, not hidden.

### Phase 2 — Catalogue (deterministic + computed metadata)

- `shoebox-scan` over `originals/`: BLAKE3, pixel hash, perceptual hashes, thumbnails, colour stats. Run on the M5 for speed (reads over NFS, or from a local copy if Phase 0 says NFS is slow).
- `metadata`: ExifTool batch + sidecar merge → one metadata record per asset with **per-field provenance** (`exif`, `takeout_sidecar`, `filename`, `file_mtime`, `human`).
- Timestamps get a **date kind**: `exif_original`, `sidecar_taken`, `filename_pattern`, `scan_date` (time the scan was made — *not* when the photo was taken), `estimated`, `unknown`. Search for "2008" uses capture dates only, and says so when a hit's date is estimated.
- GPS → place names with an **offline** reverse geocoder (GeoNames cities dataset). Place names become searchable text.
- **Colour vs mono** is computed from pixels, not EXIF (B&W negatives are usually scanned as RGB): chroma statistics on the 256-px thumbnail. Three classes: `colour`, `mono`, `toned` (sepia/cyanotype/colour-cast mono).
- **Capture source** is a rule-based classifier over EXIF Make/Model/Software, resolution/DPI, file type, aspect ratio, Drive vs Takeout origin. Classes: `phone`, `digital_camera` (2002–~2012 compacts are a big group), `scan_negative`, `scan_print`, `screenshot`, `received` (messaging apps strip EXIF), `unknown`. A photo with no EXIF is **not** assumed to be a scan.

**Gate B:** capture-source and colour/mono evals pass (§5.2); timestamp spot-check passes; GPS coverage reported.

### Phase 3 — Dedup and triage

- Exact duplicates: same BLAKE3. Same pixels, different metadata: same pixel hash. Near duplicates: perceptual-hash distance below a threshold, confirmed by CLIP similarity once embeddings exist (Phase 6) — two weak signals beat one.
- **Nothing is deleted.** Duplicates form groups with a chosen canonical member (rule: richest metadata, then highest resolution, then Photos over Drive). Search shows the canonical one; the others stay reachable.
- Each group keeps member-level features (resolution, bit depth, file type, Software tag, levels/histogram stats, sharpness) so the **parked raw-vs-processed scan sort** is a later query over existing data, not a re-scan.

**Gate C:** near-dup precision on labelled pairs ≥ 0.99 at the chosen threshold (false merges are worse than misses); recall reported.

### Phase 4 — Index, API and minimal UI (first usable thing)

- SQLite schema v1, designed now that the Phase 2/3 outputs and eval needs are known.
- `shoebox-api`: faceted search (date range, place, B&W, source, has-GPS), image + thumbnail serving with range requests, Cloudflare Access JWT validation. Docker on the NAS.
- Web UI (TS): search, grid, detail view, and — importantly — the **labelling screens** the later evals need (pair judgments, tag judgments, face naming). Building these as part of the real UI means they aren't throwaway.

**Gate D:** an end-to-end query suite over deterministic facets returns the expected results; p95 latency on the NAS measured; JWT rejection tested (missing, expired, wrong audience).

This phase delivers value early: browse by date, place, B&W and source before any ML runs.

### Phase 5 — Faces

1. Build the face ground-truth set (§5.3) *before* running anything over the archive.
2. Head-to-head on the ground-truth set: detector + embedding candidates (§4).
3. Tune detection-quality filters (minimum face size, detection score, blur) and clustering parameters on the **dev** split; report on the **test** split.
4. **Gate E** passes → bulk on the M5 → cluster → naming UI.
5. Naming workflow: human names **clusters**; can merge clusters (one person across decades will usually be several clusters — expected, not a failure) and split/eject faces. Names propagate to photos. Small/low-quality faces go to an "unassigned" bucket instead of being forced into clusters.
6. Incremental: new faces are assigned to a named person only if close to that person's faces *and* clearly closer than the next person; otherwise they wait in the unassigned pool for the next periodic re-cluster. Human-confirmed assignments are never overwritten by a re-cluster.

### Phase 6 — Scene content

1. Define the **tag vocabulary** first (~100–200 tags in a few facets: setting [indoor/outdoor], place type [beach, snow, city…], event [wedding, birthday…], subject [group, portrait, landscape, pet, food, document…]). Write down what a good tag set looks like for 10 example images before scoring anything.
2. Build the scene eval set and query eval set (§5.4).
3. Head-to-head: embedding models for retrieval; VLMs (2–3 sizes, not just the biggest) for tags + captions. Measure speed per image on the M5 too.
4. **Gate F** → bulk run on the M5. Bulk VLM captioning at a few seconds per image is likely days, not hours: run it resumable, in the background, idempotent by `(asset_id, model_id)`.
5. Search ranking: structured filters first (date, person, B&W, tags) → rank the filtered set by text match (FTS5 over captions/tags/places) blended with CLIP similarity. Blend weights are tuned on the query eval set.

### Phase 7 — Incremental (Mac Studio)

- New files land on the NAS (via whatever R1 decides) → `shoebox-api` sees them (periodic scan of an `inbox/` dir) → enqueues jobs per stage.
- `shoebox-worker` leases jobs, runs scan → faces → embed → caption, posts results. Leases expire so a crashed worker's jobs are retried; jobs are idempotent.
- The same model versions as the bulk run; a model change means a new `model_id`, a re-eval, and an explicit backfill job, never silent mixing.

**Gate G:** a small batch of fresh photos goes through end to end and the eval suites still pass on the full ground truth (catches drift between bulk and incremental paths).

---

## 4. Tools per stage

> Model and library versions are being verified separately; the shortlist below is what goes into the head-to-heads, not the decision. The evals choose.

| Stage | Recommended | Alternatives to evaluate | Notes |
| --- | --- | --- | --- |
| Takeout sidecars | Own parser in `metadata` (Python), using an existing tool (GPTH / immich-go) as a **cross-check** | GPTH, immich-go | Sidecar matching has many quirks (truncated filenames, `(1)` suffix placement, `-edited` variants, `.supplemental-metadata.json` naming). Writing our own makes the unmatched set visible; comparing against a mature tool catches our bugs. |
| EXIF | ExifTool (`-stay_open`, `-json -n -G`) | Rust `kamadak-exif` for hot paths | ExifTool is the only thing with full HEIC/MakerNote/XMP coverage. Perl, but called as a subprocess — fine. |
| Hashing | BLAKE3 (Rust `blake3`) | — | |
| Perceptual hash | Rust `image_hasher`: pHash + dHash on a normalised decode | — | Thresholds come from the Gate C eval, not from defaults. |
| Decode / thumbnails | libvips (via Rust bindings or CLI), libheif for HEIC | `image` crate for JPEG/PNG | libvips is far faster and lighter on memory for large TIFF scans. |
| Reverse geocoding | Offline GeoNames cities dataset (Rust crate or a small k-d tree) | Self-hosted Nominatim (overkill) | |
| Faces | InsightFace `buffalo_l` (SCRFD detector + ArcFace) via ONNX Runtime | OpenCV YuNet + SFace; AdaFace (better on low-quality faces, relevant for old scans) | Check model licence (non-commercial weights are fine for personal use, but note it). |
| Clustering | HDBSCAN (scikit-learn) on L2-normalised embeddings | Agglomerative with a distance threshold; Chinese Whispers | Agglomerative with a hard threshold is easier to reason about for "never merge two people"; let the eval decide. |
| Image–text embeddings | SigLIP 2 (base/large) | OpenCLIP models; MobileCLIP | Want a text tower small enough to run on the NAS CPU at query time, or the query encoder has to call the Mac Studio. |
| VLM tags/captions | Qwen-VL family via `mlx-vlm` on the M5/M1, at 2–3 sizes | Gemma, SmolVLM, Moondream, Florence-2 (fast tagger) | Ask for JSON constrained to the tag vocabulary + a one-sentence caption. Smallest model that passes Gate F wins — it also has to run on the M1 for incremental. |
| Index | SQLite + FTS5; embeddings as BLOBs; sqlite-vec or Rust brute force | Qdrant (rejected: extra service, weak at the filter-first queries we need) | |
| DB replication | Litestream → GCS | Nightly `.backup` | |
| Queue | Table in SQLite, HTTP lease API in `shoebox-api` | Redis/NATS (rejected: another service on the NAS for ~100 jobs/day) | |

**Considered and not chosen as the system:** Immich already does Takeout import, face clustering, CLIP search and has a phone app. It doesn't do eval-gated quality, scan-vs-phone, B&W, scan-date handling, or a controlled tag vocabulary, and its schema is its own. Still worth standing up once in Phase 0 on a sample as a **baseline** to beat in the face and search evals.

---

## 5. Eval design

### 5.1 Common machinery

- **Ground truth lives in `evals/` in git**: JSON/CSV keyed by asset id, versioned, with a README per set saying how it was sampled.
- **Dev/test split for every set.** Thresholds and prompts are tuned on dev; gates are scored on test. Otherwise we overfit thresholds to the set and the gate means nothing.
- **Gates are config** (`evals/gates.toml`): metric, threshold, set version. Written before the run.
- **One command** per stage: `shoebox eval faces --model buffalo_l --params params/faces-v3.toml` → runs the runner over the eval set only (minutes, not days) → writes `evals/runs/<date>_<stage>_<config-hash>.json` → prints a comparison against the current baseline, per stratum.
- **Every stored output carries `model_id` + `params_hash`.** Changing a model or parameter = new eval run; if it beats the baseline and passes the gate, a backfill job is queued for affected assets only.
- Results are reported **per stratum** (era, source, colour/mono), not just overall. An average can hide "fine on phone photos, useless on 1990s scans".

### 5.2 Sampling ground truth without labelling thousands

1. After Phase 2 we have cheap strata for every asset: era (5-year buckets), capture source (rule guess), colour/mono (computed), face count (from a quick detector pass), and later a CLIP embedding.
2. **Stratified sample** across era × source × colour, with a minimum per cell so small-but-important cells (B&W scans from the 2000s) aren't swamped by 2018 phone photos.
3. **Diversity within strata:** k-means the CLIP embeddings (k≈100) and pick from different clusters, so the set isn't 30 near-identical birthday shots.
4. **Deliberate hard cases**, flagged as such: sepia/toned prints, faded colour prints, screenshots, WhatsApp images, children, faces in profile, group shots, low light.
5. **Active labelling around thresholds** for binary decisions (dedup, face same/different): sample pairs by distance band, oversampling near the candidate threshold where the decision is actually made.
6. Report both the raw metric on the sample and a **re-weighted estimate** for the full archive using stratum sizes.

Rough labelling budget: ~300 for capture source/colour, ~300 face photos (~15–25 people), ~400 dedup pairs, ~100 scene images, ~40 search queries. A few evenings, not weeks.

### 5.3 Per stage

| Stage | Ground truth | Metrics | Proposed gate |
| --- | --- | --- | --- |
| Acquisition | Phase 0 inventory | count/bytes reconcile, sidecar match rate | 100% reconcile; unmatched sidecars listed |
| Timestamps | 50 assets checked against the Google Photos UI / originals | % correct; % by date kind | ≥ 98% of `exif`/`sidecar` dates correct; timezone errors counted separately |
| Capture source | ~300 stratified, labelled into the 7 classes | per-class precision/recall, confusion matrix | scan precision ≥ 0.95 (calling a phone photo a scan is the worse error); `unknown` allowed |
| Colour/mono | Same ~300 + 50 hard cases | accuracy; toned-print confusion | ≥ 0.98 colour vs not-colour |
| Dedup | ~400 pairs by distance band, labelled same / variant (crop, edit, rescan) / different | precision, recall at threshold | precision ≥ 0.99 |
| Face detection | Faces boxed on the face set | recall by face size and era | recall ≥ 0.9 on faces ≥ 40 px |
| Face clustering | ~300 photos, ~15–25 people, every face named; **labelled from photos, not by correcting model clusters** (that would bias toward the model) | pairwise precision/recall; **merge rate** (clusters with >1 person); **split rate** (clusters per person, per era); **labelling effort** = human actions to name the top N people | pairwise precision ≥ 0.98; zero merged clusters among the top 10 people; effort reported |
| Scene tags | ~100 images, tags from the vocabulary, two passes by the user | per-tag and per-facet precision/recall; caption judged 1–3 on a rubric (blind, side by side between models) | facet `setting` ≥ 0.9; overall tag precision ≥ 0.8 |
| Search (end to end) | ~40 queries incl. combined ones ("B&W, 2008, person X, outdoors") with judged relevant results | recall@20, nDCG@20 | set after the first baseline run |

Thresholds are starting proposals to argue about in review; the point is that they're fixed before the run.

The search eval is the one that matters most to the actual goal, so it also guards the others: a model that wins its own stage eval but drops search nDCG doesn't ship.

---

## 6. Data flow by machine

| Step | Where | Reads | Writes |
| --- | --- | --- | --- |
| Acquire | NAS (Docker: rclone, unzip) | Google | `staging/`, `originals/` |
| Scan + thumbnails | M5 (one-off), Mac Studio (incremental) | `originals/` via NFS (ro) | `results/scan/`, `derived/thumbs/` |
| Metadata merge | M5 / NAS | `originals/` (ro) | `results/metadata/` |
| Faces / embed / caption bulk | M5 | thumbnails or originals (ro) | `results/<stage>/` |
| Import | NAS (`shoebox-api` or one-shot container) | `results/` | `shoebox.sqlite` |
| Clustering | M5 / Mac Studio (batch) | embeddings via API export | cluster assignments → API |
| Incremental | Mac Studio worker | jobs + files via API/NFS | results via API |
| Serve | NAS | SQLite, `derived/`, `originals/` | — |

Rule: **SQLite is only ever opened by processes on the NAS.** Everything else talks HTTP or drops Parquet in `results/`.

---

## 7. Open questions

**Resolved by this plan (open to challenge):**
- Vector store → SQLite (BLOB + filtered brute force / sqlite-vec). Revisit only if the asset count is >1M.
- CLIP vs VLM → both; VLM for tags/captions, embeddings for ranking + dedup support.
- Queue → SQLite table behind the API, HTTP leasing.
- Storage layout → §2.
- Schema → after Phase 3, informed by eval needs; result bundles make schema changes cheap.

**Need your input or Phase 0 data:**
1. **How do new photos arrive after the bulk import?** (R1) Phone platform? Willing to change the phone's backup target?
2. NAS RAM/CPU → can it run a CLIP text encoder at query time?
3. Videos: in scope? They're probably a large share of the 210 GB. Proposal: catalogue them (metadata, poster frame, dedup) in Phase 2, no content analysis until later.
4. Photos backup quality: Original or Storage saver?
5. Who labels faces/tags — just you, or family too? (Affects whether the labelling UI needs per-user attribution.)
6. Pre-2002 photos in the scans: dates will mostly be estimated. Is decade-level ("1990s") good enough?

---

## 8. Changes from the handoff

1. **Don't fold sidecar JSON back into the files.** That either modifies the "originals" copy or doubles storage, and it loses provenance. Merge in the DB; if exported copies with fixed EXIF are ever wanted, generate them at export time with ExifTool.
2. **Colour/mono isn't EXIF metadata** — it's computed from pixels and needs a labelled eval (toned prints are the hard case).
3. **Capture source needs more than two classes.** "No camera EXIF ⇒ scan" misclassifies 2000s compact-camera shots, screenshots and every image received via a messaging app.
4. **Scans carry the scan date, not the capture date.** Dates need a kind/provenance, and search has to know the difference.
5. **Python is used for the pipeline and runners** per your steer, but only as batch CLIs and supervised runners — the long-running pieces are Rust.
6. **Thumbnails are pre-generated** during the bulk pass, not mainly on the fly on the NAS.

---

## 9. Risks and unknowns

| # | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| R1 | **No incremental feed from Google Photos.** The Photos Library API no longer gives third-party apps access to the whole library (2025 change, to verify). | Phase 7 has nothing to consume. | Decide in Phase 0: phone backs up to the NAS directly (e.g. Syncthing), or scheduled Takeout exports as a fallback. |
| R2 | SQLite opened over a network share | DB corruption | API is the only writer; workers use HTTP; documented rule. |
| R3 | Takeout sidecar matching quirks | Wrong dates/GPS on a subset | Own parser + cross-check vs an existing tool; unmatched list is a Gate A output. |
| R4 | Faces across decades (children → adults) | One person split into many clusters | Expected; UI makes cluster merge cheap; split rate is measured per era. Never auto-merge across clusters. |
| R5 | Old scans: low-res, grainy faces | Detection misses, bad embeddings, false merges | Quality filter → "unassigned" bucket; AdaFace in the head-to-head; per-era metrics. |
| R6 | Bulk VLM run time | Days of M5 time | Pick smallest model passing the gate; run on thumbnails (~1024 px) not originals; resumable. |
| R7 | NAS too weak for query-time text encoding | No free-text semantic search from the NAS | Query encoder on the Mac Studio over HTTP with a timeout, falling back to FTS-only. |
| R8 | Model licences (some face weights are non-commercial) | Fine for personal use; blocks any future sharing | Record the licence per `model_id`. |
| R9 | Storage-saver originals | Lower quality, missing EXIF | Check in Phase 0; affects dedup choices (prefer Drive scan over recompressed Photos copy). |
| R10 | Eval sets too small for rare strata | Gates pass on averages and hide failures | Minimum per stratum; report per stratum; confidence intervals on small cells. |
| R11 | Human labels lost | Hours of work gone | Labels in DB (Litestream) + periodic export to `evals/` in git. |

---

## 10. Suggested order of work

1. Phase 0 recon (an evening).
2. Phase 1 acquisition (mostly waiting on downloads).
3. `shoebox-scan` + `metadata` → Phase 2, build the capture-source/colour ground truth while the scan runs.
4. Dedup + Phase 4 API/UI with the labelling screens.
5. Faces (biggest payoff, most eval work).
6. Scene content.
7. Incremental worker.
