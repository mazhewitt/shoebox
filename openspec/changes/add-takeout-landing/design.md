## Context

The Takeout zips are in a synced, read-only source (ImportantFiles, Drive via Cloud Sync). Shoebox must never write
there (`config.check_write_location`). The shoebox share has 2.0 TB free; the export is 530 GB. The Studio reads and
writes the NAS over 1 GbE (about 100 MB/s each way), so one pass over the zips is about 1.5 hours.

## Goals / Non-Goals

Goals: a verified, read-only extract with a manifest that proves every byte; a registered source; the first frame.
Non-goals: sidecar date/album parsing (`catalogue/03`), Live/Motion Photo capture rules for the Takeout, deleting
anything on Drive or the NAS (the owner does decision 29 by hand).

## Decisions

### D1. Read the zips where they landed
`--zips DIR` points at the folder holding the zips (here `~/mnt/importantfiles-ro/Takeout`). No staging copy: it
would double the write volume for no extra safety, since the zips are only ever read. The command refuses a
destination under any source root or `/Volumes`.

### D2. Test and extract in one pass per zip, hash while writing
Each zip is processed with Python's `zipfile`: every entry is streamed to a temporary file under the destination,
with zipfile's CRC check on read and BLAKE3 computed on the same stream, then renamed into place. One read of each
zip gives the CRC test, the extract and the hash. A zip whose entries fail CRC, or that cannot be opened, is
recorded `failed`, its partial output removed, and the command exits non-zero at the end.

### D3. Layout and per-zip state
`<shoebox_root>/originals/takeout/<account>/<export>/<zip stem>/` holds each zip's own tree (each has a `Takeout/`
root). Zips are NOT merged into one tree: Takeout numbers duplicate names per zip, so `Photos from 2019/2023-07-30(23).jpg`
in one zip is a different photo from the same path in another, and each has its own `...supplemental-metadata(23).json`
(found 2026-10-13: a merged extract lost 841 such photos and sidecars). One folder per zip keeps every file and keeps
each sidecar next to its own photo; byte-identical copies across zips are left to dedup. `<shoebox_root>/originals/takeout/<account>/<export>.manifest/` holds `zips.jsonl` (one row per
zip: name, size, entries, status, ts) and `manifest.parquet` (one row per entry: zip, entry path, size, crc32,
blake3). `<export>` is the timestamp in the zip names (`20261012T083010Z`); a zip with another timestamp from the
same request (here the `archive_browser.html` zip) goes under its own export.

Resume: a zip with a `done` row in `zips.jsonl` and every one of its manifest entries present on disk with the
recorded size is skipped. Paths are per zip, so the same entry path in two zips is two files, not a duplicate.

### D4. Read-only tree
After a zip is extracted its files are chmod `a-w` (best effort over SMB; the effective guard is that the tree is
a source root, so `check_write_location` refuses any Shoebox write under it).

### D5. Reconcile
Per zip folder: the zip's entry count (files, not directories) = files on disk under `<export>/<zip stem>/` = the
zip's manifest rows, and every manifest row's size matches the file; the export folder holds nothing at the top level
but zip folders (Synology metadata and `.DS_Store` aside). Any mismatch fails the command.

### D6. Register and inventory
`[sources.takeout-owner]` with `root = "~/mnt/shoebox/originals/takeout/owner/20261012T083010Z"`, person owner,
`synced = false`, `hosted_consent = true`, `hosted_models = ["gemini", "claude"]`, `added = 2026-10-13`, and the
same never-send / held-back handling as other sources (no held-back paths unless the owner names some). The
inventory then runs over it; `add-pipeline-foundations` D5 keeps hosted sends closed until that frame exists.

### D7. Verify-only mode for a tree extracted on the NAS
Extracting over SMB from the Studio ran at about 15 MB/s, so the owner extracts on the NAS with 7-Zip 16.02
(`shoebox/logs/takeout-perzip.sh`: `7z x -y -aoa -o<export>/<zip stem> <zip>` per zip, into the D3 layout, then
the D8 renames). 7-Zip must run under a UTF-8 locale (`LC_ALL=en_US.utf8`): under the SSH session's default POSIX
locale it wrote non-ASCII names as Latin-1 bytes, which SMB hides (2026-10-14: 66 files in 9 zips, 5 folders, all
Latin-1 characters). `shoebox/logs/takeout-fix.py` repaired them with the owner's approval: it re-extracted just those
entries under a UTF-8 locale, moved them into place under their D8 names, and deleted the 14 mis-encoded paths.
`shoebox takeout verify --zips DIR --account owner --export TS --extracted [--workers N]` then
proves that tree without reading zip data: for each zip only the central directory (`ZipFile.infolist()`) is read,
and every file entry (name, size, CRC) must have a file at `<export>/<zip stem>/<smb_name path>` with that size whose
single 1 MiB-chunked read gives the same CRC-32 (`zlib.crc32`); the same read gives the BLAKE3 (as `ids.asset_id`)
for the manifest. A zip's entries are looked up only in its own folder. Names are matched by NFC-normalised,
casefolded path, because the SMB mount may present NFD names and the share is case-insensitive; two entries of one
zip that collide on that key (after D8) fail the zip (`conflicts`). Per zip, `zips.jsonl` records `verified` or
`failed` with counts of `missing`, `size_mismatch`, `crc_mismatch` and `conflicts`; the zip's rows go to
`parts/<zip>.jsonl`, and `manifest.parquet` has the D3/D8 columns (`entry` is the zip's entry name, `path` the name as
found on disk). Resume skips a `verified` zip whose part rows still exist with their sizes. Reconcile as D5, where
files on disk named by no manifest row (including a second spelling the case-sensitive NAS filesystem may hold, or a
name whose D8 rename was skipped) are errors except Synology metadata (`@eaDir`, `@SynoEAStream`, `@SynoResource`,
`#recycle`) and `.DS_Store`, which are ignored and counted. Verified files are chmod `a-w` (best effort). Reads run on `--workers` threads (default 8) because per-file
open latency over SMB dominates. Write guards, counts-only stdout and the cleanup line are as in the self-extract
mode; the command refuses if the export's tree does not exist.

### D8. SMB-safe names
The Studio reads the tree over SMB, which cannot show a name ending in a space or a dot (Synology presents a mangled
8.3 name such as `C0VYM3~Y`). Google keeps album names as typed, so 5 album folders of this export end in a space.
Every path component is stored as `smb_name(c) = c.rstrip(" .")`; the NAS extraction renames such names after 7-Zip
(`shoebox/logs/takeout-perzip.sh`, which logs and skips a rename whose target exists), and the self-extract mode writes
them that way. The verify maps each zip entry through `smb_name` per component before looking it up; two entries of one
zip that map to the same name are a `conflicts` error. `manifest.parquet` gains an `entry` column holding the zip's own
entry name, so the original album name is kept; `path` is the name on disk, relative to the export folder (it starts with
the zip stem).

