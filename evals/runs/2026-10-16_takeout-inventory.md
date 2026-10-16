# Takeout landing: inventory and overlap (2026-10-16)

Tasks 2.2 and 2.3 of `add-takeout-landing`. Source `takeout-owner` (export `20261012T083010Z`, verified in
`2026-10-14_takeout-verify.md`), inventoried from the Studio over the `shoebox` SMB mount with
`shoebox inventory --sources takeout-owner --date 2026-10-15 [--resume] [--retry-errors]`. Frame
`frame/frame-2026-10-15.parquet`, listing `frame/listing-2026-10-15.json`. Counts only.

## 2.2 Frame

- 263,065 files in 1,866 folders = the verify count. 0 skipped entries, 0 walk errors, 0 unstable folders (3,914
  listings for the SMB cross-check). Sources untouched: 200 sampled files and 1,866 folders unchanged.
- By kind: image 119,143; video 22,355; sidecar 47,321; other 74,246.
- By status: ok 188,815; stat_only 74,246 (the `other` files); error 4.
- The 4 errors are real decode failures, all images: 2 LibRaw data errors, 1 LibRaw I/O error, 1 truncated JPEG.
- Quarantine (design D9): 63,762 media files carry `quarantine = "exclude"` (48,771 images, 14,991 videos). They are
  the 63,722 excluded assets plus byte-identical copies elsewhere in the export. None of them has a width or EXIF:
  hashed, not decoded or read (2.1c). The 100 `private` assets are decoded as usual.
- Dates of the 77,736 media files not excluded: EXIF 47,992; filename 14,979; Takeout sidecar 8,902; scan 2,481;
  mtime 3,382. Excluded files have no EXIF read, so 43,941 of them fall back to mtime, by design.

### Sidecars: matched within a folder only

The inventory pairs a JSON sidecar with media in the same folder. It classed 47,321 of the 121,522 JSON files as
sidecars; the other 74,201 are `other` (with 45 non-JSON files: 40 with no extension, 2 `mp4_56x42`, 2 `ico`, 1
`_smjpg`). Google puts about 60% of sidecars in a different zip folder from their photo (D9), and `takeout flags`
already matches them across zip folders and caches the contents in `20261012T083010Z.manifest/sidecars.parquet`.

The 3,382 own media dated by mtime carry the extraction date (2026), not the capture date: 2,594 jpg, 239 jpeg, 238
mp4, 217 mov, 46 3gp, 32 png, 6 tif, 5 gif. Most likely they are the photos whose sidecar sits in another zip folder.
Follow-up for `catalogue/03` (sidecar dates): take `photoTakenTime` from the D9 cross-folder match, not the folder
pairing, and report the unmatched rate per account (Gate A).

### Run history: a temp-file leak filled the disk

The run took four starts on 2026-10-15 and 2026-10-16:
- the first died with the Claude session;
- the second hit the background job's 2-hour cap;
- the third finished the remainder;
- the fourth re-probed the errors.

13,959 records (lines 110,845 to 232,998 of `records.jsonl`) failed with `OSError: [Errno 28] No space left on
device` in the local temp folder. `probe` dropped the descriptor that `tempfile.mkstemp` returns, so each deleted
local copy kept its space until the process exited: free space was 81 GB during the run and 204 GB after it.

Fixed in `455f558`; the test checks the open descriptor count is unchanged after a run with local copies. The
`--retry-errors` pass re-probed all 13,963 error records in 37 min at 27 MB/s, with free space flat at 203 GB. 4 errors
remain.

## 2.3 Overlap with ImportantFiles (exact BLAKE3)

Against the ImportantFiles frame `frame-2026-10-12.parquet` (52,955 distinct image and video assets).

| | Takeout distinct assets | in ImportantFiles | share |
|---|---:|---:|---:|
| Images, all | 113,185 | 36,883 | 32.6% |
| Images, not excluded | 64,444 | 36,823 | 57.1% |
| Videos, all | 21,678 | 1,180 | 5.4% |
| Videos, not excluded | 6,697 | 1,166 | 17.4% |

- Over half of the owner's own Takeout images are already in ImportantFiles byte for byte. Same-pixel overlap waits
  until the Takeout is derived.
- **74 excluded assets also sit in ImportantFiles** (60 images, 14 videos). They are partner-shared in Google Photos,
  yet the owner keeps a copy in their own files. The quarantine is keyed by asset, so the `exclude` verdict now
  applies to those ImportantFiles copies too. **For the owner:** keep them excluded, or rule them `include` as the
  owner's own?
