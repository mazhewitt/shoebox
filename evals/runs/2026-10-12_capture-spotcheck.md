# Capture rules over ImportantFiles and the 50-group spot check (task 6.5), 2026-10-12

Inputs: `frame-2026-10-12.parquet`, `pixelhash-v1` after the full derive (1.4), screen `nsfw-coreml-v1`
(threshold 0.7) over every derived asset. Counts only.

## Groups and captures (6.1 to 6.4)

| Step | Count |
|---|---|
| Exact groups (byte copies) | 16,354 groups, 35,212 locations |
| same_pixels groups | 288 groups, 579 assets |
| Captures (`capture-v1`) | 49,287 |
| Edited copies linked to a capture | 1 |

| Capture rule | Captures | Member assets |
|---|---|---|
| single | 45,351 | 45,351 |
| raw_jpeg_basename | 3,251 | 6,531 |
| motion_photo | 311 | 311 |
| same_pixels | 250 | 503 |
| scan_export_basename | 123 | 256 |
| live_photo | 1 | 2 |

Worklist (`2026-10-12_gemini`): files 71,813 → assets 52,955 → captures 49,287 → eligible 48,218; queues
hosted 46,874, local 814, held 159 (pending screen review), not_derived 1,440, unscreened 0. Re-quoted bulk
cost at batch price: $99.49 (`evals/runs/2026-10-12_worklist-2026-10-12_gemini.md`).

## Spot check

`tools/capture_spotcheck.py`, seed 20261012. Of 3,626 multi-member captures, 3,466 had every member and linked
asset eligible for Claude viewing (dropped: copy_restricted 129, held_back 17, quarantined 13, unscreened 1).
50 drawn, stratified by rule (largest remainder over sqrt(size)): raw_jpeg_basename 33, same_pixels 9,
scan_export_basename 7, edited link 1 (the one live_photo capture was not eligible). One contact sheet per
capture, 480 px high tiles.

Five Claude subagents judged 10 sheets each; every verdict other than "same" went to two further readers.

| Result | Captures |
|---|---|
| Judged same by the first reader | 46 |
| Flagged (false_merge or unsure) | 4 (3 raw_jpeg_basename, 1 edited) |
| Flagged, confirmed by full-resolution comparison | **0** |
| **False merges** | **0 of 50** |

All four flags were reader errors at sheet resolution, resolved by viewing the pair at full size, rotated
upright, with crops and a difference map:

- raw + JPEG where the JPEG is a tight crop of the frame (one pair also straightened);
- a Leica DNG + JPG with the same ImageUniqueID, where the camera JPEG's lens correction shifts the geometry;
- an `_edited` copy whose original is stored sideways (no orientation tag), so the pair looked like two poses.

Lesson for later checks: a reader at ~480 px confuses crop, straightening, lens correction and rotation with a
second frame. Compare flagged pairs at full resolution, rotated upright, before counting a false merge.
