# Google Takeout: request both libraries, land on the NAS, verify, manifest

Status: ready-for-human (owner: request the exports); the verify/manifest code is ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 2:** there are two Google Photos libraries, the owner's (Android) and Jude's (iPhone).
- **Decision 7:** ingest is Takeout-based.
- **Decision 29:** Drive cleanup happens only after the export is verified on the NAS.
- **Status:** tasks.md 9.2 (old task 1.3). ImportantFiles has a `Pictures/Google Photos` folder (27,559 images) but **no Takeout JSON sidecars at all** (the frame's 1,131 sidecars are all XMP), so it isn't a Takeout export. It looks like an old Drive/Photos sync, and its relationship to the Takeout is unknown until both are inventoried (`dedup/` will tell).

## What

**Owner:**
1. **Check backup quality.** In Google Photos settings, confirm Original (not Storage saver) for **both** accounts. Decision 2 has it confirmed for only one.
2. **Request the exports.** Request a Photos-only Takeout per account with 50 GB zips, delivered to Drive. Decision 7 sets the bi-monthly scheduled export, which can be set up at the same time; `ingest/` owns the repeat.
3. **Land the zips.** They go under `<shoebox_root>/staging/takeout/<account>/<export-date>/`, not under any source root. The owner moves them by rclone or by hand. The landing mechanism is shared with `ingest/`.

**Agent, `shoebox takeout verify` (new command, `src/shoebox/commands/takeout.py`):**
- `unzip -t` every archive. Record pass or fail per zip.
- Extract to `<shoebox_root>/originals/takeout/<account>/<export-date>/`, then make that tree read-only.
- **Manifest** (`manifest.parquet` beside the tree): one row per entry, with zip name, entry path, size, BLAKE3 (`shoebox.ids.asset_id`) and the zip's CRC, which must match.
- **Reconcile:** zip entry count = extracted file count = manifest rows. Any mismatch fails the command.
- **Register the tree as a source:**
  - `[sources.takeout-owner]`, `person = "owner"`, with the same consent as importantfiles;
  - `[sources.takeout-jude]`, `person = "jude"`, `hosted_consent = false`.
- Report the drive-cleanup line: "export <date> for <account> verified on the NAS; safe to delete from Drive" (decision 29; the deletion itself is manual).

## Done when

- Both accounts' exports are extracted, and `shoebox takeout verify` exits 0 with zero failed zips and zero reconcile mismatches.
- The quality setting per account is recorded in a comment here.
- The two takeout sources are in `config/shoebox.toml`, and inventory over them runs (06).

## Depends on

01 is not needed. Disk space on the shoebox share must cover the zips plus the extracted tree, about 2× the export size. Check before requesting.

## Open questions

- Should the zips be deleted after verification, or kept until the next export? PLAN.md §2 deletes them only after a verified extract.

## Comments

- 2026-10-16 (claude, `add-takeout-landing`): the owner's export `20261012T083010Z` is landed and verified. It has
  134/134 zips, 568.9 GB of zips (569.7 GB extracted) and 263,065 files, with 0 size, CRC or reconcile mismatches
  after the non-ASCII name repair. Extracted on the NAS one folder per zip; Shoebox deleted no zips (decision 29 is
  the owner's). Source `takeout-owner` is registered (`hosted_consent = true`, `hosted_models = ["gemini", "claude"]`)
  and inventoried. Details: `evals/runs/2026-10-14_takeout-verify.md`, `evals/runs/2026-10-16_takeout-inventory.md`.
  Commits `1bd7a99`, `a0c40da`, `455f558`. Still open: Jude's export, and the backup-quality record per account.
