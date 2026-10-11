# Dedup report for the owner's manual cleanup

Status: blocked (on 01, 02 and sources/ complete)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Decision 39: every NAS share (ImportantFiles, Home Folders, Backup, Volume2, about 880 GB) plus Takeout is in scope. Dedup groups them, and the owner cleans up later from a dedup report.

**Shoebox itself never deletes**, and never writes to a source; sources are read-only mounts.

## What

`shoebox dedup report` writes a local, offline HTML page and CSV under `shoebox_root/reports/`. It is never written to the repo or a synced folder; this is the same pattern as the owner review page in `~/shoebox-review/`.

The report has five parts:
- **Summary by source pair:** bytes and file counts that are exact duplicates, same-pixel and near-duplicate.
  - Example: "Backup/2014-phone duplicates 92% of Takeout 2014".
- **Folder-level containment:** folders whose every file has a copy elsewhere. These are the cheapest cleanup wins.
- **Per group:**
  - members (source, path, size, kind of match);
  - which member is canonical, and why (from 04);
  - thumbnails, for consented sources only. For other sources, no thumbnails: paths and sizes only.
- **Never "delete" buttons.** At most a copyable path list per folder, for the owner to act on in DSM.
- **Upstream links:** Takeout items marked removed upstream (decision 7) are listed separately and are not cleanup candidates.

## Done when

- The report runs over all sources. The owner opens it and confirms it answers "what can I safely remove from which share".
- A test asserts the command has no write path under any source root (`config.check_write_location`).

## Depends on

- 01, 02 (and 03 for near-duplicates, marked as lower confidence).
- `sources/` read-only mounts of every share.

## Comments
