# Google Photos Takeout: both libraries, end to end into the catalogue

Status: ready-for-human (owner: check backup quality and request both exports now; it is the long pole for the bulk run)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **The largest source, not yet in hand.**
  - The Takeout is the main library: about 210 GB of phone photos from 2002 to now (handoff.md), across two accounts (decision 2: the owner's on Android, Jude's on iPhone).
  - Every later stage needs it: inventory (06), sidecars (`catalogue/03`), dedup (`dedup/06`), the bulk caption run (`bulk-captioning/06`).
- **The deadline.** Google builds an export over hours to days, and 50 GB zips take a while to land on the NAS. The bulk run has to finish before **2026-12-31** (price change). Requesting now is the cheapest risk reduction available.
- **The pieces are filed, but not the whole.** `02` lands and verifies the zips, `catalogue/03` parses sidecars, `dedup/` groups. This issue is the end-to-end checklist, plus the Takeout quirks that each piece must handle.

## What

**Owner, now:**
1. **Backup quality per account.** Original or Storage saver? Record it below. Storage-saver files are Google recompressions. They won't match ImportantFiles' copies by bytes or by pixels, only as near-duplicates (`dedup/03`), and their EXIF may be thinner.
2. **Request the exports.** One Photos-only export per account, 50 GB zips, delivered to Drive. Record the size Google shows; it checks disk space (`02`) and the re-quote.
3. **Jude's consent.** Jude's library is held back from hosted models (decision 39) until Jude says otherwise. Ask now, so the bulk run can include it or explicitly not.

**Pipeline, in order (each part lives in its own issue):**
1. **Land, verify, manifest** (`02`): `shoebox takeout verify`, read-only tree, one source per account.
2. **Inventory** (`06`), with Takeout-aware skip rules (`05`): `.json` sidecars and `metadata.json` album files are metadata, not media.
3. **Sidecars** (`catalogue/03`). Takeout quirks to cover with fixtures:
   - truncated names (about 46 characters);
   - the `(1)` suffix placement;
   - `-edited` variants without their own sidecar;
   - `.supplemental-metadata.json` naming;
   - sidecar timestamps in UTC;
   - album folders (decision 30: albums, favourites, descriptions, archived; people names stored as their own source, decision 31).
4. **Within-Takeout duplicates** (`dedup/01`). Check whether this export copies album photos into album folders as well as the year folders. If so, they are byte-identical and collapse by BLAKE3. Keep the album membership from the album copy; don't drop it with the duplicate.
5. **Live Photos and Motion Photos** (`dedup/02`): iPhone `.HEIC` + `.MOV` pairs (Jude), Android Motion Photos with an embedded MP4 (owner). One capture each.
6. **Overlap with ImportantFiles** (`dedup/`): report how much of the Takeout is already in `Pictures/Google Photos` and elsewhere, as exact / pixel / near-duplicate counts. This decides how much new captioning the Takeout costs.
7. **Re-quote** the bulk run from the combined worklist (`dedup/04`, `dedup/06`).

## Done when

- Both accounts' quality settings and export sizes are recorded below.
- Both Takeouts are verified (`02`), inventoried (`06`) and sidecar-matched (`catalogue/03`, with its unmatched rate).
- The overlap report against ImportantFiles is committed to `evals/runs/` (counts only).
- Jude's consent answer is recorded, and `config/shoebox.toml` matches it.

## Depends on

- Owner actions 1–3.
- Disk space on the shoebox share: about 2× the export size.
- `sources/01` is not needed.

## Open questions

- Is the Takeout the only feed for Jude's iPhone photos, or does some of that library also reach the NAS another way?
- Keep the zips after a verified extract until the next export, or delete them (`02`)?

## Comments

- 2026-10-16 (claude): owner's library, steps 1, 2 and the exact part of 6 done (`add-takeout-landing`).
  - Inventory: 263,065 files; 119,143 images, 22,355 videos, 4 decode errors.
  - Partner-shared photos: 63,722 assets are `exclude` (D9), hashed but never decoded.
  - Overlap with ImportantFiles by exact BLAKE3: 57.1% of the owner's own image assets (36,823 of 64,444) and 17.4%
    of own video assets.
  - **Sidecars for step 3:** the inventory pairs sidecars within a folder only (47,321 of 121,522 JSON files). 3,382
    own media fall back to mtime, which is the 2026 extraction date. `catalogue/03` should use the cross-folder match
    and the cached contents from `takeout flags` (`20261012T083010Z.manifest/sidecars.parquet`).
  - **Owner question:** 74 excluded (partner-shared) assets also sit in ImportantFiles. Keep them excluded, or rule
    them the owner's own?
  - `evals/runs/2026-10-16_takeout-inventory.md`.
