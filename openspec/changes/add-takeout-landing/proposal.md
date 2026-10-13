## Why

The owner's Google Photos Takeout has landed (2026-10-13): 135 zips, 530 GB, export `20261012T083010Z` (134
zips) plus one zip `20261012T083009Z` holding only the export's `archive_browser.html`. It arrived through the Drive sync, so the zips sit in the ImportantFiles share under
`Takeout/`, not in `<shoebox_root>/staging` as `.scratch/sources/02` planned. It is the owner's library only
(owner, 2026-10-13); there is no second account in this export.

The Takeout is the largest source and every later stage needs it: inventory, dedup against ImportantFiles, the
re-quote, and the bulk run, which must finish before the Gemini price change on 2026-12-31. Nothing yet tests,
extracts or records it, and Drive cleanup (decision 29) may only happen after a verified extract.

Issues covered: `.scratch/sources/02` (land, verify, manifest), and the first steps of `sources/07` and `sources/06`
for this source (register, first inventory frame).

## What Changes

- New command `shoebox takeout verify`:
  - reads the zips read-only from wherever they landed (here the ImportantFiles mount), never writing there;
  - tests every zip (CRC of every entry) and records pass or fail per zip;
  - extracts to `<shoebox_root>/originals/takeout/<account>/<export>/`, then makes the tree read-only;
  - writes `manifest.parquet` beside the tree: zip, entry path, size, CRC, BLAKE3 (`ids.asset_id`);
  - reconciles zip entry count = extracted file count = manifest rows, failing on any mismatch;
  - is resumable: a re-run skips zips already extracted and verified;
  - prints the decision-29 line only when everything passed.
- Register the tree as source `takeout-owner` in `config/shoebox.toml` (person owner, same consent and models as
  importantfiles, `added = 2026-10-13`). Until it has an inventory frame, hosted sends fail closed (design D5 of
  add-pipeline-foundations).
- First inventory frame of `takeout-owner`, and an overlap count against ImportantFiles (exact and same-pixel),
  counts only.

## Capabilities

### New Capabilities
- `takeout-landing`: verifying, extracting, manifesting and registering a Google Photos Takeout export.

### Modified Capabilities
None. The inventory already records Takeout JSON next to media as sidecars (`inventory.classify_kind`).

## Impact

- New `src/shoebox/takeout.py`, `src/shoebox/commands/takeout.py`, tests.
- `config/shoebox.toml`: a new source.
- About 530 GB written to the shoebox share (2.0 TB free). Reading and writing over 1 GbE: several hours.
- No hosted calls, no spend.
