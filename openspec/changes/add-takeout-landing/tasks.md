## 1. Verify and extract

- [x] 1.1 Implement `src/shoebox/takeout.py` and `shoebox takeout verify --zips DIR --account owner [--dest]` per
  design D1–D5 and D8: one folder per zip (`<export>/<zip stem>/`), SMB-safe names (trailing space/dot stripped
  per component, `entry` column keeps the zip's name), one pass per zip (CRC on read, BLAKE3 on the same stream, temp
  file then rename), `zips.jsonl`, `manifest.parquet`, resume, per-zip reconcile, the cleanup line. Counts only on
  stdout (no file names).
  - Verify with tests on synthetic zips: good zip; corrupt entry (CRC); unopenable zip; resume after an interrupt;
    same entry path in two zips with different bytes both kept; trailing-space album folder stored SMB-safe; two
    entries of one zip mapping to one name fail; missing/resized file caught by reconcile; unexpected top-level
    folder caught by reconcile; destination under a source root refused; no writes under the zips folder.
  - Verify-only mode `--extracted --export TS [--workers N]` per design D7, for the per-zip tree 7-Zip extracts on the
    NAS (`takeout-perzip.sh`): central directory only, per-file size + CRC-32 + BLAKE3, NFC/case-insensitive name
    matching within a zip folder, Synology metadata ignored, same manifest columns. Tests: all good; missing file;
    size mismatch; same-size byte flip; extra file; unexpected top-level item; `@eaDir` ignored; NFD name on disk vs
    NFC entry; same entry path in two zips; trailing-space album; trailing-space/case/NFC twins in one zip conflict;
    resume skips verified zips; no writes under the zips folder; no names on stdout.
- [x] 1.2 Run it over `~/mnt/importantfiles-ro/Takeout` (owner's export `20261012T083010Z`, and the
  `20261012T083009Z` zip). Record per-zip results, totals, throughput and the reconcile in
  `evals/runs/<date>_takeout-verify.md` (counts only).

## 2. Register and inventory

- [x] 2.1 Add `[sources.takeout-owner]` to `config/shoebox.toml` (design D6). Verify with a test that hosted sends
  fail closed before its first frame.
- [x] 2.1b Implement `shoebox takeout flags` per design D9 and run it on the verified export before 2.2. Tests on a
  synthetic extract: partner-shared → `exclude`, `inLockedFolder` and `Locked Folder/` → `private`; truncated sidecar
  name; `(1)` duplicate numbering; `-edited` and Live Photo companions inherit; both rules → `exclude`; an existing
  more restrictive verdict kept; unmatched flagged sidecar fails; re-run appends nothing; no names on stdout. Record the
  counts in the 1.2 run note.
- [ ] 2.1c Inventory respects `exclude` (design D9): after hashing, an asset whose latest quarantine verdict is `exclude`
  gets no decode, thumbnail statistics or EXIF, and its frame row carries `quarantine = "exclude"`. Tests: excluded
  image hashed but not decoded (decoder not called) and not passed to ExifTool; `private` and unflagged decoded; video
  likewise; resume keeps the behaviour.
- [ ] 2.2 Run `shoebox inventory --sources takeout-owner`. Record the frame counts, sidecar count and skip counts in
  `evals/runs/` (counts only).
- [ ] 2.3 Overlap with ImportantFiles: assets in both sources by exact BLAKE3, and the share of the Takeout's image
  assets already present, in `evals/runs/` (counts only). Same-pixel overlap follows once the Takeout is derived.

## 3. Close-out

- [ ] 3.1 Update `.scratch/sources/02` and `07` comments (export size, account, verify result) and the README queue.
  Verify `openspec validate add-takeout-landing --strict` passes.
