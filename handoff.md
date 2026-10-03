# Photo Archive Indexing — Hand-off for Planning

**Status:** Thinking stage. Produce a **plan**, not code. Nothing should be built, downloaded, or moved until the plan is reviewed.

## Goal

Organise ~210 GB of personal photos (2002–present) into a searchable, self-hosted index. Every photo should be labelled with:

- **Who** is in it (faces, clustered then named)
- **What** it shows (scene/content description or tags)
- **Capture source** — phone vs scanned negative/print
- **Colour** — black & white vs colour
- **Location** — GPS where available
- **When** — capture timestamp

Search must support combined queries, e.g. "black and white, 2008, person X, outdoors".

## Hard constraints

- **No commercial tools.** Open-source libraries and tools are fine and encouraged; nothing proprietary, nothing that phones home or locks the archive in.
- **Non-destructive.** Originals are never modified in place. All processing runs against a backed-up copy.
- **Evals before scale.** Data quality matters more than schema elegance. No labelling stage runs across the full archive until it has a measured quality score against ground truth.
- **Language preference:** Rust first (Axum for services); Node acceptable as a pragmatic compromise. Avoid Python for long-running processes. Python is acceptable only where it's the sole practical route to a model (e.g. one-off inference scripts), and should be isolated behind a clear boundary.
- **Future clients:** Search must be exposed as an HTTP API so a web UI now and a phone app later are both thin clients. No re-architecture to add either.

## Sources (acquisition)

| Source        | Contents                        | Extraction     | Known issues                                                                                                                                                 |
| ------------- | ------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Google Photos | Everything backed up from phone | Google Takeout | Metadata (timestamps, GPS) partly moved into sidecar JSON; must be merged back (ExifTool). Delivered as many multi-GB archives.                              |
| Google Drive  | Scans (negatives/prints)        | rclone         | Used to sync with Photos, no longer does — overlap with Photos likely. Processed and unprocessed scans are mixed together and not distinguishable by folder. |

## Infrastructure

| Machine                                                     | Role                                                                                                                                                                                                                          |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Synology NAS** (older, Intel/x86, already running Docker) | Durable home for staging + originals. Already backs up to Google Cloud Storage. Runs the always-on, light services: SQLite index, Axum API, thumbnail cache/serving. RAM unknown — check before planning any inference on it. |
| **M1 Mac Studio** (spare, will live next to the NAS)        | Always-on inference worker on the LAN. Incremental processing of new photos: face embeddings, captioning, thumbnail batches.                                                                                                  |
| **M5 Max, 128 GB** (Ultra coming)                           | One-time heavy bulk inference over the back catalogue, then returns to normal use.                                                                                                                                            |

**Network/auth:** Cloudflare tunnel on own domain → NAS, Google auth at the edge (Cloudflare Access). The API should trust the Cloudflare Access JWT rather than implementing its own login.

## Proposed pipeline (to be validated by the plan)

1. **Acquire** — Takeout + rclone into a staging area on the NAS.
2. **Merge metadata** — fold Takeout sidecar JSON back into files (ExifTool).
3. **Dedup & triage** — content hash (exact duplicates), perceptual hash (near-duplicates, e.g. same scan processed differently). Overlap between Drive and Photos is expected.
4. **Deterministic metadata** — ExifTool batch: timestamps, GPS, camera make/model, colour vs mono. Scanned-vs-phone inferred from scanner make or absent camera EXIF.
5. **Faces** — detection + embeddings (InsightFace is the candidate), clustering (HDBSCAN candidate). Human labels **clusters**, not photos; labels propagate.
6. **Scene content** — two candidate routes, possibly both:
   - CLIP-style embeddings for semantic similarity search
   - A local vision-language model producing captions/tags, indexed as text
7. **Index** — SQLite with FTS5 for text; vector search via an SQLite extension or a sidecar (e.g. Qdrant). Plan should recommend one.
8. **Serve** — Axum service in Docker on the NAS: query API, image range requests, on-the-fly thumbnails with cache.
9. **Incremental** — Mac Studio picks up new photos as they arrive and runs stages 3–7 on them, so the archive stays labelled without re-running the bulk job.

Model and library names above are **candidates**, not decisions. Verify current versions and Apple Silicon support; compare alternatives where it matters.

## Evals (the priority)

Each stage has a different error profile and needs its own eval:

- **Deterministic metadata** — minimal; spot-check. Exception: the scanned-vs-phone inference, which is a guess from absence of data and needs a labelled check set.
- **Faces** — labelled ground-truth set (a few hundred photos with known people). Measure pairwise precision/recall on clustering. Failure modes to catch: two people merged into one cluster; one person split across several (including across ages/decades).
- **Scene content** — hand-curated diverse sample (~100 images spanning decades, indoor/outdoor, groups/landscapes, colour/mono, scans/phone). Define what good tags look like; score models head to head. Do not assume the largest model wins.
- **Dedup** — check perceptual-hash thresholds against known near-duplicate pairs (false merges are worse than missed duplicates).

The plan should cover **how to sample** ground-truth sets so they represent the whole archive without hand-labelling thousands of images, and how evals are re-run when models or parameters change.

## Side project (parked, but design for it)

Sorting processed vs unprocessed scans in the Drive corpus. Likely approach: perceptual hash pairs a raw scan with its processed version; file characteristics/EXIF indicate which is which. The dedup stage should keep enough information to support this later.

## Open questions for the plan to resolve or surface

- NAS RAM and whether it can do any light inference at all
- Vector store choice: SQLite extension vs Qdrant
- CLIP embeddings, VLM captions, or both
- Where the job queue / work coordination lives between NAS and Mac Studio
- Storage layout on the NAS: staging vs canonical originals vs derived (thumbnails, embeddings)
- Schema — deliberately left until eval design is settled

## What the plan should deliver

1. Phased plan with eval gates between phases (no phase proceeds without a passing eval where one applies)
2. Recommended tools/models per stage, with reasons and alternatives
3. Eval design per stage, including ground-truth sampling strategy
4. Data flow and storage layout across NAS / Mac Studio / M5
5. Risks and unknowns