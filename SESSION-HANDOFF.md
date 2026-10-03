# Session hand-off — Shoebox planning

**Date:** 2026-10-03
**State:** Planning only. Nothing built, downloaded or moved. No git repo yet.

## Files
- `handoff.md` — the original brief (goal, constraints, sources, infra, evals). Unchanged.
- `PLAN.md` — the plan produced this session. **This is the main output; read it first.**
- `SESSION-HANDOFF.md` — this file.

## User steer given this session (overrides handoff.md where they conflict)
- **Pipeline in Python or TypeScript; anything fast or long-running in Rust.**
  The plan applies this as: Python for the pipeline CLIs, inference runners and evals (run with `uv`, no Python daemons); Rust for the Axum API, the scanner (hashing/decode/thumbnails/colour metric) and the Mac Studio worker daemon; TypeScript for the web UI.

## Key decisions in PLAN.md (for review, not yet signed off)
- Originals immutable; Takeout sidecar data merged **in the DB with provenance**, not written back into files.
- Assets keyed by BLAKE3 of original bytes; model outputs written as Parquet result bundles keyed by `(asset_id, model_id, params_hash)` so the schema can change without re-running inference.
- Vector store: SQLite with embeddings as BLOBs + filtered brute force (or sqlite-vec). No Qdrant.
- Both CLIP-style embeddings (ranking, near-dup support) and a VLM (controlled tag vocabulary + short caption).
- Job queue: a table in the NAS SQLite, exposed by the API; workers lease over HTTP. SQLite is never opened over SMB/NFS.
- Thumbnails pre-generated in the bulk pass on the M5.
- Phases 0–7 with gates A–G; eval design in §5 (stratified + embedding-diversity sampling, dev/test splits, gates fixed in `evals/gates.toml` before runs, per-stratum reporting, end-to-end search eval as the guard).
- §8 lists deliberate deviations from handoff.md; §7 lists questions that need the user's answers.

## Open questions waiting on the user (PLAN.md §7)
1. How new photos arrive after the bulk import (the Google Photos API likely can't read the whole library any more) — phone platform, willingness to change the backup target.
2. NAS RAM/CPU (Phase 0 recon).
3. Are videos in scope?
4. Was Photos backed up in Original or Storage saver quality?
5. Who labels faces/tags — the user alone or family too?
6. Is decade-level dating good enough for pre-2002 scans?

## Research step
A background research agent was asked to verify current (late-2026) status, licences and Apple Silicon support for: InsightFace and alternatives, SigLIP 2 / OpenCLIP / MobileCLIP, local VLMs + mlx-vlm/Ollama, sqlite-vec, Google Photos Library API and Takeout sidecar naming, GPTH / immich-go, Rust crates (image_hasher, libvips, libheif-rs, reverse_geocoder, ort), and HDBSCAN in scikit-learn.
**Status:** see the "Research results" section below. If it says *pending*, the research didn't finish before the restart: rerun it, then update PLAN.md §4 (the tools table) and R1/R8 in §9.

## Research results
_Pending._

## Next steps
1. Review PLAN.md with the user; settle the §7 questions and the proposed gate thresholds in §5.3.
2. Phase 0 recon (NAS specs, network throughput, Drive inventory via `rclone lsjson`, Takeout size/quality).
3. Only then start Phase 1.
