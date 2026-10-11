# Capture grouping: Live/Motion photos, RAW+JPEG, scan exports, bursts, edits

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Decision 8:
- A **capture** is one moment. Examples: a still plus its Live Photo `.MP4` or embedded Android Motion Photo clip; RAW+JPEG; a burst.
- An **`-edited`** copy is a separate asset linked to its original.
- **Search returns captures, and the bulk run captions one member per capture** (04).

`pilot/select.py:assign_groups` already has two same-folder rules: `raw_jpeg_basename` (NEF/DNG + JPG) and `scan_export_basename` (TIFF + JPG). They run only inside an inventory and are not stored as a capture table.

## What

Write a `capture` grouping step, with one rule per kind. Each rule:
- records `rule`, `rule_version` and its member roles (`primary`, `raw`, `motion_clip`, `edited`, `burst_member`, `scan_export`);
- is conservative: when unsure, leave the files apart.

The rules:

| Rule | Basis |
|---|---|
| `raw_jpeg_basename` | Reuse `assign_groups`, extended to Takeout and the other shares. |
| `scan_export_basename` | Reuse `assign_groups`, extended to Takeout and the other shares. |
| `live_photo` | iPhone HEIC/JPG + `.MOV`/`.MP4` with the same stem. Check that the Takeout layout keeps the pairing. |
| `motion_photo` | Android `MVIMG_*`/`PXL_*.MP.jpg`, with an embedded clip detected from XMP `MicroVideo`/`MotionPhoto` tags (ExifTool). The clip stays inside the file, with no separate asset. |
| `edited` | Takeout `-edited`/`-bearbeitet` suffixes; Google Photos edit copies. A linked asset, not a duplicate. |
| `burst` | Takeout/Android burst naming (`_BURST`, `IMG_…_BURST…_COVER`). Members are grouped and the cover is primary. |

- Cross-source: a capture can gather members from several sources once 01 has linked identical assets.
- Output goes to a keyed results kind, e.g. `results/capture/<rule_version>/`.

## Done when

- Fixtures for every rule, including near misses: the same stem in a different folder, and an `-edited` file whose original is missing.
- An ImportantFiles run with per-rule group counts.
- A 50-group stratified spot check (contact sheets, owner's consented library only) with zero false merges, recorded in `evals/runs/`.
- Takeout rules are re-checked on the first real Takeout landing (`sources/`). Until then they are tested on fixtures only, and the note says so.

## Depends on

- 01, for cross-source asset links.
- The `catalogue/` ExifTool extract, for the Motion Photo XMP tags.

## Open questions

- Is a burst one capture (one caption) or several? Proposal: one capture, caption the cover only, and leave burst members out of search results by default.

## Comments
