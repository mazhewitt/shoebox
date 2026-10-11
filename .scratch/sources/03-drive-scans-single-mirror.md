# Drive scans: choose one mirror (Synology Cloud Sync vs rclone pull)

Status: needs-triage (owner decision)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Two settled decisions disagree:
- **Decision 26:** Shoebox pulls the Drive scan folder with `rclone copy` (read-only scope) on each `shoebox ingest` run.
- **Decision 33:** Drive scans already arrive on the NAS through **Synology Cloud Sync**, and the pipeline reads the NAS copy, never Drive directly.

Running both gives two mirrors of the same raw TIFFs: double storage, plus a dedup job to undo it. Decision 33 itself says "reconcile with 26 so there aren't two mirrors". ImportantFiles is Cloud Sync's target today (`synced = true`); its TIFFs and DNGs (6,754 TIFF, 10,588 raw in the frame) are presumably that mirror.

## Options

- **A. Cloud Sync only (recommended).**
  - Shoebox treats the synced folder in ImportantFiles as the scan source and drops the rclone pull from decision 26.
  - Mirror verification becomes a Cloud Sync status check (DSM log or "last synced") plus a file-count spot comparison against Drive.
  - No second copy, no Drive credentials in Shoebox.
- **B. rclone only.** Turn Cloud Sync off for that folder; Shoebox owns the pull and `rclone check`.
  - More control and verifiable checksums.
  - But the owner's other Cloud Sync uses must not break, and Shoebox gains a Drive token.
- **C. Both, with Shoebox reading Cloud Sync and rclone used only to `rclone check`.** This verifies without copying. The cost is a read-only Drive token.

## What

1. **Owner:** pick A, B or C. Confirm which Drive folder(s) hold scans and that Cloud Sync covers them one-way (Drive → NAS), so the NAS copy can't write back.
2. **Agent:** carry out the choice.
   - Update decision 26/33 in memory and PLAN.md (via `eval-followups/`).
   - For A or C, add a `shoebox sources check-sync` command that compares counts and sizes between the Drive listing (`rclone lsjson`, read-only) and the NAS folder, and lists any differences.

## Done when

- The decision is recorded in a comment here and in the project memory.
- One configured source covers the Drive scans.
- Gate A (06) counts each scan once.

## Depends on

Owner. Needed before 06.

## What would reopen it

Cloud Sync dropping or corrupting large TIFFs (it has file-size limits per plan). If 06's reconcile finds missing scans, revisit B.

## Comments
