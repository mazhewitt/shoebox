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
`<shoebox_root>/originals/takeout/<account>/<export>/` holds the merged tree (Takeout zips of one export share one
`Takeout/` root). `<shoebox_root>/originals/takeout/<account>/<export>.manifest/` holds `zips.jsonl` (one row per
zip: name, size, entries, status, ts) and `manifest.parquet` (one row per entry: zip, entry path, size, crc32,
blake3). `<export>` is the timestamp in the zip names (`20261012T083010Z`); a zip with another timestamp from the
same request (here the `archive_browser.html` zip) goes under its own export.

Resume: a zip with a `done` row in `zips.jsonl` and every one of its manifest entries present on disk with the
recorded size is skipped. A duplicate entry path across zips is an error unless the bytes are identical (then it is
recorded once with both zips).

### D4. Read-only tree
After a zip is extracted its files are chmod `a-w` (best effort over SMB; the effective guard is that the tree is
a source root, so `check_write_location` refuses any Shoebox write under it).

### D5. Reconcile
Across the export: zip entry count (files, not directories) = files on disk under the tree = manifest rows, and every
manifest row's size matches the file. Any mismatch fails the command.

### D6. Register and inventory
`[sources.takeout-owner]` with `root = "~/mnt/shoebox/originals/takeout/owner/20261012T083010Z"`, person owner,
`synced = false`, `hosted_consent = true`, `hosted_models = ["gemini", "claude"]`, `added = 2026-10-13`, and the
same never-send / held-back handling as other sources (no held-back paths unless the owner names some). The
inventory then runs over it; `add-pipeline-foundations` D5 keeps hosted sends closed until that frame exists.
