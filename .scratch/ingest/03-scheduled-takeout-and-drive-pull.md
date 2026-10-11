# Scheduled Takeout and Drive-scan pull for both libraries

Status: ready-for-human (owner sets up scheduled Takeout on both Google accounts; the code half is blocked on `sources/` and `dedup/`)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 7:**
  - Google scheduled Takeout runs every 2 months and is renewed yearly, for both accounts (owner and Jude).
  - Exports land in Google Drive, then go to the NAS. Each is a whole-library export.
  - Known files are skipped by hash.
  - Items deleted in Google are kept, and marked as removed upstream.
- **Decision 29:** Takeout cleanup on Drive is manual at first. Shoebox reports when an export has been verified on the NAS. A later, separately scoped rclone remote may delete under `Takeout/` only after NAS verification. Shoebox warns when Google free storage is smaller than the next export.
- **Decisions 26/33:** Drive scans reach the NAS through Synology Cloud Sync, and the pipeline reads the NAS copy. There must not be two mirrors (`sources/` reconciles 26 vs 33).

## What

- **Owner (ready-for-human):**
  - turn on scheduled Takeout (Photos only, 50 GB zips) on both accounts;
  - note the renewal date;
  - confirm original-quality backup on Jude's library (decision 2).
- **Code** (after `sources/` lands the first Takeout):
  - `shoebox ingest` picks up a new export: verify (`unzip -t`), extract to `originals/takeout/<account>/<export-date>/`, then the manifest.
  - Hash new files and skip the ones already known. A previously seen asset missing from this export is marked `removed_upstream` with the export date, not dropped.
  - Print a verification report per export, the signal for manual Drive cleanup, and a storage warning.
- **Consent:** Jude's library is never hosted-eligible unless Jude consents (decision 39). New assets from it go to local 12B only.

## Done when

- Two consecutive exports from one account run through ingest. The second adds only new files, and any removed item shows as `removed_upstream`.
- The verification report names the export and its file and byte counts.
- No path under a source root is written; the `check_write_location` tests cover the new paths.

## Depends on

- `sources/` (first Takeout landed, Gate A, consent per source).
- `dedup/` (known-file rule).
- 01 (ingest).

## Open questions

- Whether Synology Cloud Sync can also pull the `Takeout/` Drive folder, replacing rclone for exports too. That would mean one mechanism for Drive.

## Comments
