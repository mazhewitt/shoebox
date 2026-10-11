# Litestream: DB → NAS → GCS

Status: blocked (on `search/01`: the SQLite schema and the DB's location on the Mac Studio)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 16:** Litestream replicates the DB to the NAS, and the NAS's GCS backup covers `db/`.
- **Decision 3:** the DB lives on the Mac Studio's SSD, and only processes there open it (PLAN.md R2: SQLite is never opened over SMB or NFS).
- **Human labels go in the DB:** corrections, rotations and later face names. They are the irreplaceable data (R11).

## What

- **Litestream on the Mac Studio** under launchd. It replicates `shoebox.sqlite` to a file replica on the NAS `shoebox` share (`db/`), which is already inside the NAS's GCS backup scope (confirm in 03).
- **Periodic label export.** Human labels are also exported to `evals/` in git (R11's second copy) by a small `shoebox labels export` command, run by ingest.
- **Restore drill.** Restore to a scratch path, then check `PRAGMA integrity_check`, row counts and the latest human label.

## Done when

- The restore drill is recorded in a run note, with the replica's lag.
- A kill of the API mid-write followed by a restore loses nothing older than the lag.

## Depends on

- `search/01` (schema).
- `web-app/` (the API process that writes).
- 03 (GCS scope confirmed).

## Comments
