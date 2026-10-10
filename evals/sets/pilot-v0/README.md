# pilot-v0

Unscored pilot set (docs/pilot.md). Written by `shoebox pilot pick` on 2026-10-10 from source `importantfiles`. No image bytes are stored here; `items.jsonl` holds asset IDs and paths.

- Files: 45
- Total bytes: 1,215,945,044 (1.22 GB)
- Sibling-group members: raw_jpeg_basename 12

## Files per bucket

| bucket | files |
|---|---|
| leica-dng-jpg-pair | 8 |
| nikon-nef-jpg-pair | 4 |
| nikon-d70-nef | 3 |
| film-scan-tiff | 5 |
| nikon-film-scan | 3 |
| processed-scan | 3 |
| to-work-scan | 2 |
| google-photos-old | 2 |
| google-photos-2010s | 3 |
| phone | 4 |
| camera-roll | 2 |
| psd | 2 |
| old-prints | 2 |
| pinhole | 1 |
| low-jpg-scan | 1 |

## Files per extension

| ext | files |
|---|---|
| dng | 4 |
| jpg | 19 |
| nef | 7 |
| psd | 2 |
| tif | 13 |

## Skipped buckets

- screenshot: no eligible files in Pictures/Screenshots

## Selection log

- [google-photos-old] folder has no image files: Pictures/Google Photos/1969
- [google-photos-old] folder has no image files: Pictures/Google Photos/1970
- [screenshot] folder has no image files: Pictures/Screenshots
- [screenshot] skipped: no eligible files in Pictures/Screenshots

## Recipe

Each bucket folder is listed recursively to depth 2, skipping hidden files, `#recycle`, `$RECYCLE.BIN`, `.tmp.drivedownload`, `*.lrdata` and `Thumbs.db`. Image extensions: jpg jpeg png heic heif webp tif tiff nef dng psd (any case). Files over 600 MiB are skipped. Candidates are sorted by relpath and picked evenly spaced at index `floor(i*(len-1)/(n-1) + 0.5)` (one pick takes the middle). Pair buckets pick among base names with both a raw (nef/dng) and a jpg in the same folder and take both files. A file is picked at most once (earlier buckets win). The total is capped at 50. Groups: same-folder raw+jpg base names (`raw_jpeg_basename`) and tif/psd+jpg base names (`scan_export_basename`) among the picked files; `group_id` is `g-` plus the first 12 hex of the smallest member asset_id.

| bucket | folders | rule |
|---|---|---|
| leica-dng-jpg-pair | `Pictures/Greece` | 4 raw+jpg pairs |
| nikon-nef-jpg-pair | `Pictures/100D5600` | 2 raw+jpg pairs |
| nikon-d70-nef | `Pictures/D70` | 3 files; exts nef |
| film-scan-tiff | `Pictures/Saved Pictures/Scanned Photos` | 5 files |
| nikon-film-scan | `Pictures/New Nikon Scans` | 3 files |
| processed-scan | `Pictures/Processed` | 3 files |
| to-work-scan | `Pictures/To Work` | 2 files |
| google-photos-old | `Pictures/Google Photos/1969`, `Pictures/Google Photos/1970`, `Pictures/Google Photos/1998`, `Pictures/Google Photos/1999` | 1 per folder |
| google-photos-2010s | `Pictures/Google Photos/2010`, `Pictures/Google Photos/2011`, `Pictures/Google Photos/2012`, `Pictures/Google Photos/2013`, `Pictures/Google Photos/2014`, `Pictures/Google Photos/2015`, `Pictures/Google Photos/2016`, `Pictures/Google Photos/2017`, `Pictures/Google Photos/2018`, `Pictures/Google Photos/2019` | 3 total, one each from evenly spaced non-empty folders |
| phone | `Pictures/Phone Photos` | 4 files; include a heic/heif if any |
| screenshot | `Pictures/Screenshots` | 2 files |
| camera-roll | `Pictures/Camera Roll` | 2 files |
| psd | `Pictures/Processed`, `Pictures/photos`, `Pictures/Ligtroom-Exports` | 1 files; exts psd; plus `Pictures/L1002656.psd`; skip names containing voucher, v-bucks |
| old-prints | `Pictures/old photos from dad` | 2 files |
| pinhole | `Pictures/pinhole` | 1 files |
| low-jpg-scan | `Pictures/11w-scan1-low_jpg_files` | 1 files |
