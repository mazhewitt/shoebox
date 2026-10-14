# Takeout landing: verify (2026-10-14)

Task 1.2 of `add-takeout-landing`. The owner's Google Photos export (account `owner`, export `20261012T083010Z`)
was extracted on the NAS by 7-Zip, one folder per zip (design D3, D7, D8; `shoebox/logs/takeout-perzip.sh`), then
verified from the Studio over the `shoebox` SMB mount with
`shoebox takeout verify --zips ~/mnt/importantfiles-ro/Takeout --account owner --export 20261012T083010Z --extracted --workers 8`.
Counts only.

## Result

- 134 / 134 zips verified, 0 failed. 263,065 file entries = 263,065 files on disk = 263,065 manifest rows;
  0 size, 0 CRC mismatches, 0 conflicts, 0 unexpected files, 0 Synology metadata files.
- Zip bytes 568.9 GB; extracted file bytes 569.7 GB. 256,430 distinct BLAKE3 (6,635 entries are byte-identical copies
  across zips or albums, left to dedup).
- Entries by extension: json 121,522 (sidecars and album metadata); jpg 78,524; heic 27,717; mp4 21,232; nef 4,707;
  dng 3,278; png 2,589; jpeg 2,031; mov 848; tif 220; avi 211; gif 74; 3gp 60; webp 3; no extension 40.
- The command printed "export 20261012T083010Z for owner verified on the NAS; safe to delete from Drive". Deleting the
  zips from ImportantFiles / Drive is the owner's (decision 29); nothing was deleted by Shoebox.
- The second export, `20261012T083009Z` (one zip, 1 entry: `archive_browser.html`), was self-extracted and is `done`.

## Throughput

Per-zip read rate over SMB: median 10.6 MB/s (min 4.5, max 44.4); about 21 hours for both passes. The NAS was running
its Hyper Backup upload of the `shoebox` share throughout and has 2 GB RAM (the owner has ordered 8 GB).

## Name repair (non-ASCII)

The first pass failed 9 zips with only `missing` files (66 in all, 5 folders). 7-Zip had run under the SSH session's
POSIX locale and written non-ASCII names (all Latin-1 characters, e.g. ü, á, é, ä) as Latin-1 bytes, which SMB hides.
With the owner's approval, `shoebox/logs/takeout-fix.py` re-extracted just those 66 entries under a UTF-8 locale,
moved them into place under their D8 names, and deleted the 14 mis-encoded paths; a scan of the export found none
left. The second pass verified the 9 zips (the other 125 re-checked by size). `takeout-perzip.sh` now sets
`LC_ALL=en_US.utf8`.

## Clean-up

The two earlier attempts, `_merged-20261012T083010Z` and `_studio-partial-20261012T083010Z` (with their manifests;
about 630 GB), were deleted on the NAS with the owner's approval on 2026-10-14. Volume free space did not change
afterwards (852 GB), which suggests a share snapshot still holds them; the owner is to check Snapshot Replication.
