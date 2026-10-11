# Sources and acquisition

**Goal.** Every photo the household owns sits on the NAS as a read-only original Shoebox can walk:
- the four NAS shares (ImportantFiles, Home Folders, Backup, Volume2), via read-only `~/mnt/*-ro` mounts;
- one Google Takeout per account, landed and verified;
- a single mirror of the Drive scan folder.

Each source names its person and their hosted-processing consent. Skip rules keep caches and junk out. A complete inventory across all of them closes **Gate A**. Today only `importantfiles` is configured (`config/shoebox.toml`); its frame (`~/mnt/shoebox/frame/frame-2026-10-10.parquet`) has:
- 80,497 files;
- 71,622 images, of which 52,481 are unique asset_ids;
- 1,905 videos;
- 1,131 XMP sidecars and no Takeout JSON.

## Order and gates

| # | Issue | Gate to pass before it lands |
|---|-------|------------------------------|
| 01 | [NAS shares read-only](01-nas-shares-read-only.md) | `touch` fails on every new mount; `shoebox inventory` refuses a writable synced root |
| 02 | [Takeout request and landing](02-takeout-request-and-landing.md) | every zip passes `unzip -t`; the manifest reconciles with the zips; Original quality confirmed per library |
| 03 | [Drive scans: one mirror](03-drive-scans-single-mirror.md) | owner decision recorded; a single configured source for scans |
| 04 | [Per-source consent and hosted eligibility](04-per-source-consent-and-hosted-eligibility.md) | tests: an asset with any copy in a non-consented source is refused for every provider |
| 05 | [Inventory skip rules](05-inventory-skip-rules.md) | skip counts and examples reported in the frame README; no media lost (diff against the old frame) |
| 07 | [Google Photos Takeout, end to end](07-google-photos-takeout.md) | both libraries verified, inventoried and sidecar-matched; overlap with ImportantFiles reported; Jude's consent recorded |
| 06 | [Inventory over all sources (Gate A)](06-inventory-all-sources-gate-a.md) | Gate A, below |

## Sequencing

- **Owner actions first.** 01 and 02 are owner actions (DSM permissions, requesting Takeout exports) with long lead times, so they go first. Takeout exports take days.
- **Agent work in parallel.** 04 and 05 are agent work that can start now against ImportantFiles.
- **04 needs exact-hash groups.** It needs them across sources, and those exist in the frame today (`dup_n`). It doesn't wait for `dedup/`'s perceptual groups.
- **03 is a decision**, needed before 06 can count the Drive scans once and only once.
- **07 is the Takeout end to end.** It strings together 02, 05, 06, `catalogue/03` and `dedup/`, plus the Takeout quirks. Its owner steps (quality check, export request, Jude's consent) are the most urgent items in the backlog.
- **06 runs last** and is the phase gate.

## Gate A (completeness, no ML)

Precision-first in the decision-18 sense means no file is silently lost or double-counted.
- **Counts and bytes reconcile, per source:**
  - inventory files + skipped files = files listed;
  - Takeout extracted files = zip entries;
  - the listing cross-check (SMB truncation, `inventory.py`) converged in every folder.
- **Zero failed archive tests** (`unzip -t`). The Drive mirror is verified against its remote (whichever mechanism 03 picks).
- **Sidecars:** every Takeout media file is matched to a JSON sidecar or listed as unmatched. The unmatched rate is reported per account, not hidden.
- **Probes:** decode errors are listed (today 29 `error` rows in ImportantFiles). Every source is proven untouched after the walk (the existing sources-untouched check).
- **Per-source summary:** each source's person, consent and `hosted_models` appear in the frame README.
