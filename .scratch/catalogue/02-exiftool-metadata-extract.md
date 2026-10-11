# ExifTool metadata extract: raw fields per asset, batch mode, stored by asset_id

Status: ready-for-agent (after 01)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **PLAN.md Phase 2:** ExifTool is the only tool with full HEIC/MakerNote/XMP/QuickTime coverage.
- **What exists:** `shoebox inventory` already runs ExifTool read-only (`-j -n -G1 -struct -fast2`) and keeps a JSON blob in the frame's `exif` column. It runs **one call per file**; the aborted 2026-10-10 work dir (`aborted-work-2026-10-10-exif-slow`) shows that was the bottleneck.
- **What's missing:** `-fast2` skips trailers (e.g. some MakerNotes and Samsung/Google motion-photo trailers that `dedup/` needs for capture grouping).
- **Why store raw:** the merge (03–05), capture axes (08) and dedup all re-read these fields. Store them raw, keyed by `asset_id`, so the merge logic can change without re-reading 600 GB over SMB.

## What

- **One persistent ExifTool process per worker** (`-stay_open True -@ -`), with arguments `-json -n -G1 -struct` plus `-ee` for embedded video metadata. Run it once per asset, not per path: identical bytes give identical metadata.
- **Drop `-fast2` here** (keep it for the inventory walk if speed demands it). Record the ExifTool version and argument list with every record.
- **Output:** a keyed result store like the caption runners, `results/metadata/exiftool/<params_hash>/part-*.jsonl`, one record per asset_id: `{asset_id, exiftool_version, args, status, error, tags}`. Reuse `src/shoebox/store.py`.
- **Command:** `shoebox catalogue exif [--sources S] [--resume]`. It reads asset_ids and paths from the latest frame, resolves originals via `config.original_path` and never writes under a source.
- **Never** pass a write option. Add a test that the argument list contains no `-o`, `-overwrite_original` or `-tagsFromFile`.

## Done when

- Tests on fixtures (JPEG, HEIC, DNG, TIFF scan, MP4, a Pixel motion photo) show the expected groups present, including the trailer/embedded-video tags `-fast2` dropped.
- A run over ImportantFiles covers every unique image and video asset_id (52,481 images and 1,332 videos in the 2026-10-10 frame), each with `ok` or a listed error. The wall time is recorded in a comment.
- Sources-untouched check passes after the run.

## Depends on

01 (where the code lives).

## Comments
