# Catalogue tooling: build the Rust `shoebox-scan`, or extend the Python inventory?

Status: done (decided 2026-10-11: Python)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md §1 puts hashing, decode, thumbnails, perceptual hashes and colour stats in a Rust CLI, `shoebox-scan` (rayon, Parquet out). What has been built instead is Python:
- **`shoebox inventory`:** read-only walk with the SMB listing cross-check; BLAKE3; ExifTool; 256 px thumbnails via pyvips; chroma stats; dates with kinds; Parquet frame.
- **`shoebox derive`:** pyvips, rawpy/LibRaw and PSD composite; profile-hashed outputs.

It has already walked ImportantFiles: 80,497 files, 604 GB. The eval harness is built on its outputs and asset IDs.

**Inputs to the decision:**
- **Decision 17:** the Mac Studio runs native, with a Rust API under launchd and Python via uv. Rust is kept for the *API*; the scanner isn't named.
- **Accuracy over speed** (owner preference): dev time and speed rank below accuracy and richer metadata.
- **Where the time goes:** the SMB read, and LibRaw for raws. The hashing language isn't the bottleneck. The work dir `aborted-work-2026-10-10-exif-slow` shows the real slow step was ExifTool, which is a subprocess in either language.
- **Shared decoding:** derive, inventory and the eval harness share one decode path (`derive.render`, `inventory.decode_thumb`). A second decoder in Rust would have to reproduce LibRaw settings and ICC handling byte for byte, or the asset features would disagree with the inference images.

## Recommendation

**Stay in Python for the catalogue.** Extend `inventory` and `derive` with the missing passes:
- perceptual hashes (`dedup/`);
- UI thumbnails (09);
- the metadata merge (02–06).

Keep Rust for the API (`web-app/`).

**Revisit if** a full re-scan of all sources (about 880 GB plus Takeout) takes longer than a weekend on the Mac Studio. Measure it in `sources/06`.

## What

1. Owner: accept or reject the recommendation.
2. Agent: record the decision in PLAN.md §1 (via `eval-followups/`) and in the project memory. If rejected, open a `shoebox-scan` spec issue here, with the decoder-parity requirement as its gate.

## Done when

The decision is recorded in a comment here and in the memory. Issues 02–10 name the module they extend.

## Depends on

Nothing.

## Comments

- 2026-10-11 (owner): Owner's language rule (2026-10-11): Python for simple scripts and pipelines; Rust only where the logic is complex, the process is long-running (a service), or a non-functional requirement needs the speed or concurrency. The catalogue passes are batch pipelines, so **Python**: extend `inventory` and `derive`. The revisit trigger above is that rule's speed NFR: if a full re-scan is too slow, the hot loop moves to Rust.
