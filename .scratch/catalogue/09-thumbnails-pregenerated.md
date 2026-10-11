# Pre-generated UI thumbnails, two sizes

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Speed:** a NAS decoding 40 MB TIFF scans on request would be slow (PLAN.md §0, decision "Thumbnails"). The UI (`web-app/`) needs a grid size and a detail size, pre-generated.
- **What exists:**
  - `derive` produces `infer-v1`: 2048 px JPEG, metadata stripped, for model input.
  - `inventory` makes a throwaway 256 px thumbnail in memory for its stats.

  Neither is a stored UI asset.

## What

- **Two new profiles in `config/profiles.toml`,** produced through the same `derive` pipeline:
  - `ui-grid-v1`: 400 px long edge, WebP, quality about 80;
  - `ui-detail-v1`: 1600 px long edge, WebP or AVIF; check which formats the UI's browsers decode.

  They use the same profile mechanics as `infer-v1`: `settings_hash`, the index, refuse-on-mismatch. Pick the format against libvips's build: `pyvips` must have the encoder.
- **Decode stays shared:** RAW via LibRaw, PSD composite, ICC → sRGB, orientation applied, metadata stripped. So the thumbnails and the inference images come from one render. Ideally, render the grid and detail sizes from the same decoded image as `infer-v1` in one pass, to avoid a second SMB read and decode.
- **Location:** `<shoebox_root>/derived/<profile>/ab/<asset_id>.webp`. `derived/` is regenerable and **excluded from the GCS backup** (decision 16).
- **One per asset, not per file:** byte-identical copies share a thumbnail.
- **Videos** get a poster frame from 10, through the same profiles.

## Done when

- Tests: profile hashing, and a refused run on a settings mismatch. These reuse the derive tests.
- A run over ImportantFiles gives every unique image asset_id both sizes, or a listed error. Disk use per profile is recorded in a comment.

## Depends on

01 (only if the decision moves decode to Rust; otherwise none).

## Comments
