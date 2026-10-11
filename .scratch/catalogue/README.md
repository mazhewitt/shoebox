# Catalogue: one metadata record per asset, with provenance

**Goal.** Every asset gets one catalogue record. Each field carries its provenance and its kind, so search can tell an EXIF capture date from a scan date or a filename guess. The fields:
- raw EXIF/XMP/QuickTime metadata;
- Takeout sidecar data, including Google albums, favourites and descriptions;
- owner-authored filename fields;
- a display date with its kind;
- place names;
- colour/mono;
- the capture axes (medium × digitiser, taking camera vs digitising device);
- pre-generated UI thumbnails;
- for videos, metadata and keyframes.

A lot of this exists in rough form inside `shoebox inventory` (`src/shoebox/inventory.py`):
- ExifTool read-only extraction into the frame's `exif` column;
- a best date with a `date_kind` (`scan`, `exif`, `sidecar`, `filename`, `mtime`);
- chroma stats and a `colour_guess`;
- the rule baseline `rules-v1` (`src/shoebox/runners/rules.py`) for medium/digitiser/colour;
- RAW+JPEG and scan-export basename groups.

This feature turns those rough forms into gated, provenance-carrying catalogue fields. It closes **Gate B**.

## Order and gates

| # | Issue | Gate to pass before it lands |
|---|-------|------------------------------|
| 01 | [Catalogue tooling decision (Rust scanner or Python)](01-catalogue-tooling-decision.md) | owner decision recorded |
| 02 | [ExifTool metadata extract](02-exiftool-metadata-extract.md) | every media file has a raw-metadata row or a listed error; tests on fixtures |
| 03 | [Takeout sidecar parser](03-takeout-sidecar-parser.md) | unmatched list and rate; agrees with a GPTH/immich-go cross-check on ≥ 99% of matched files |
| 04 | [Filename parsers](04-filename-parsers.md) | field precision ≥ 0.98 per parser on an Opus-checked sample; unparsed list |
| 05 | [Date kinds and display precedence](05-date-kinds-and-display-precedence.md) | date spot-check precision ≥ 0.98 per date kind; conflict list produced |
| 06 | [Offline reverse geocoding](06-offline-reverse-geocoding.md) | place-name precision ≥ 0.98 (city level) on a GPS sample; GPS coverage reported |
| 07 | [Colour/mono heuristic](07-colour-mono-heuristic.md) | mono precision ≥ 0.98 per stratum vs the Opus reference; coverage reported |
| 08 | [Capture axes: medium × digitiser](08-capture-axes.md) | per-class precision ≥ 0.98 at the chosen cut-off, else `unknown`; coverage reported |
| 09 | [Pre-generated UI thumbnails](09-thumbnails-pregenerated.md) | every image asset has both sizes or a listed error |
| 10 | [Videos: catalogue only](10-videos-catalogue-only.md) | every video has metadata, a date with provenance and keyframes, or a listed error |
| 12 | [EG2 image vectors for every asset](12-eg2-image-vectors.md) | every unique image asset has a checked vector or a listed error; img/h recorded |
| 11 | [Gate B report](11-gate-b-report.md) | Gate B, below |

## Sequencing

- **01 first.** It decides where the code lives.
- **02 next.** 03, 04, 05, 06 and 08 all read its output.
- **05 last of the date work.** 03 and 04 feed it: it merges their dates.
- **No dependencies:** 07 and 09 need only the decoded image, so they can run at any time.
- **10 runs alongside the rest.**
- **12** needs only the `infer-v1` images. `dedup/03` and `search/` both read it, so start it early.
- **11 runs last.**

None of this needs the other sources (`sources/01`–`03`). Build it and gate it on ImportantFiles now, then re-run when the new sources land.

## Gate B

Per decision 18, gates are precision-first:
- precision ≥ 0.98 per stratum;
- below the cut-off, a field is `unknown`, with its score kept;
- coverage is reported, and thresholds are tuned to minimise unknowns at that bar.

Ground truth comes from Opus labels with owner spot-checks (decision 40), not owner labelling. It passes when:
- **Colour/mono (07)** and **capture axes (08)** pass their precision bars per stratum (scans, phone, digital camera, received/screenshot).
- **Dates (05):** the spot-check passes per date kind. Scan-dated items are marked, and year search can exclude them (decision 15).
- **Places (06):** GPS coverage and place-name precision are reported.
- **Filenames (04):** parser precision per parser, and the unparsed list.
- **Sidecars (03):** the unmatched list and rate.
