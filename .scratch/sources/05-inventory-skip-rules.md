# Inventory skip rules: thumbnail caches and other derived junk

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- tasks.md 9.3: add a skip rule for Android `DCIM/.thumbnails` caches.
- The 2026-10-10 frame of ImportantFiles has **1,714 images under `.thumbnails`** directories, which would otherwise be captioned and searched as photos.

**What `inventory.py` already skips:**
- directories `#recycle`, `$RECYCLE.BIN`, `.tmp.drivedownload`, `@eaDir`, `.Spotlight-V100`, `.Trashes` and `.fseventsd`;
- files `.DS_Store`, `Thumbs.db`, `desktop.ini` and `._*`;
- symlinks.

## What

Extend `SKIP_DIRS` / `skip_reason` in `src/shoebox/inventory.py`. Each rule gets its own named reason, so the frame's skip counts and examples show it:

- **`.thumbnails`.** Android gallery cache, under `DCIM/` or anywhere.
- **iPod/iTunes photo caches.** The frame has 273 each of `jpg_170x128`, `jpg_320x320` and `jpg_56x42` files, plus 216 `.ithmb` (the `Photo Cache`/`iPod Photo Cache` folders). These are already `other`, not media, but the folders should be skipped as a whole.
- **Picasa.** `.picasa.ini` and `Picasa.ini` (162 `.ini` in the frame) are recorded, not skipped. They carry stars, albums and captions, which are owner data. Keep them recorded, and file a catalogue follow-up to parse them rather than dropping them.
- **Lightroom.** `*.lrdata` preview folders (`Previews.lrdata`, `Smart Previews.lrdata`) are derived caches. Skip them. Keep the `.lrcat` catalogues: they're owner data (see the NAS sources memory).
- **Synology and macOS.** `@SynoEAStream`, `@SynoResource` and `.@__thumb` (QNAP-style). Check that each actually occurs before adding it.
- **Other.** Apply the same treatment to any further cache the new sources show (Backup: Time Machine `*.sparsebundle`, Hyper Backup `*.hbk`) once 01 lands. Add rules from evidence, not speculation.

**Rule:** skip only folders that hold **derived copies**. Never skip on image size alone. The 11 sub-256 px images in eval-1k are real owner images; how to caption those is `bulk-captioning/`'s tiny-images issue.

## Done when

- Unit tests for each new reason in `tests/` (synthetic trees).
- Re-run `shoebox inventory --resume` on ImportantFiles. The new frame differs from `frame-2026-10-10.parquet` only by the skipped paths, listed with their reason, and the frame README shows the new skip counts.
- No eval-1k item is under a newly skipped path. Check with `evals/sets/eval-1k/items.jsonl`; if one is, that's a finding to raise, not a silent drop.

## Depends on

Nothing.

## Comments
