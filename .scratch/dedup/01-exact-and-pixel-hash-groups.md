# Exact and pixel-hash duplicate groups across all sources

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Decision 6 dedups across libraries by pixel hash. Decision 39 says the owner's shares and both Takeouts overlap heavily, and that dedup is how that overlap is handled. PLAN.md Phase 3 has two kinds:
- **exact:** same BLAKE3;
- **same pixels, different metadata:** for example Takeout re-exports with rewritten EXIF, or a Drive copy against a Photos copy.

Today `inventory.py` only counts exact duplicates (`dup_n`), within one inventory run.

## What

- **Pixel hash** per asset: a BLAKE3 of the decoded, orientation-normalised pixel buffer, plus width, height and band count.
  - Decode with pyvips as `derive.py` does. Apply the EXIF orientation before hashing, so a rotated-flag copy matches its upright twin.
  - Record `pixel_hash_version`, so a decoder change is visible.
  - RAW and multi-page/PSD files: hash the rendered preview, and mark the hash `rendered`, not `decoded`. A RAW never pixel-matches a JPEG; that pairing is capture grouping (02).
- **Groups:**
  - `exact` (same `asset_id`): the files are one asset at several paths.
  - `same_pixels` (same pixel hash, different `asset_id`): separate assets, linked.
  - Groups span sources: `importantfiles`, the other NAS shares, Takeout.
- **Storage:** a keyed results kind (e.g. `results/pixelhash/<version>/part-*.jsonl`) through `store.py`, so the later SQLite import (`search/`) reads it like any other result. Group IDs follow the existing pattern: `g-` + the first 12 hex of the smallest member ID.
- **Cost:** hashing means a full decode of every file, so reuse the decode `derive`/`inventory` already does where possible. Measure img/h on ImportantFiles before running everything.
- **Privacy:** sources are read through `config.original_path` only.

## Done when

- Unit tests on synthetic fixtures:
  - byte-identical files;
  - the same JPEG with stripped or changed EXIF;
  - an orientation-flag variant;
  - a re-encoded JPEG, which must **not** match: that is near-duplicate territory (03).
- On ImportantFiles: exact and same-pixel group counts by source and folder pair, written to an `evals/runs/` note.
- Resumable: re-running skips assets that already have a pixel-hash record at the same version.

## Depends on

- `sources/` mounts for full coverage. The code and the ImportantFiles run can go ahead now.

## Open questions

- 16-bit TIFF scans and their 8-bit exports never pixel-match by design. Confirm that's what we want: they pair under 02 `scan_export_basename`, or under raw ↔ processed pairing later.

## Comments
