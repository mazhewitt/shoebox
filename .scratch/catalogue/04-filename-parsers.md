# Filename parsers: owner-authored fields, per era, with provenance and an eval

Status: needs-info (owner: examples of their scan naming conventions), then ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 27:** filenames are owner-authored data.
  - per-era parsers with provenance `filename`;
  - evaluated for field accuracy, with an unparsed list.
  - For scans, precedence is human label > filename > in-file metadata > scan date.
- **Decision 23:** the owner is an active film photographer who records some data in filenames: Leica 35 mm, Hasselblad 6×6, colour and B&W. There is no roll entity.
- **What exists:** `inventory.py` parses only device patterns: `20190704_123456`, `IMG-20190704-WA0001`, `2019-07-04`. The 2026-10-10 frame has 3,171 `filename`-kind dates.

## What

- **Module:** `src/shoebox/catalogue/filenames.py`. A list of named parsers, each a regex plus a field mapping, tried in order. The first match wins and records `parser`.
- **Device parsers:**
  - Android camera;
  - WhatsApp (`-WA`; also marks the file as `received`, which feeds 08);
  - Screenshot;
  - iPhone `IMG_E` (edited);
  - Pixel `PXL_`;
  - DSLR `DSC_`/`_DSC`;
  - Leica `L100xxxx`.
- **Owner scan parsers:** written from the owner's examples and from a frequency scan of folder and file names under the scan folders. Fields: date (with precision: day, month, year or decade), camera, film stock, subject or place words, frame number. Unknown fields stay null.
- **Folder names count too.** `Pictures/2017`, `Pictures/Greece` and the like are owner-authored. Record folder tokens as `folder` provenance: a year folder gives a weak date, a place word a weak place.
- **Output:** fields with provenance `filename` or `folder`, merged by 05.
- **Eval:**
  - Draw 50 files per parser, stratified, plus 100 unparsed.
  - Opus checks each parsed field against the file and folder name and the image (decision 40).
  - The owner spot-checks 20.
  - Precision ≥ 0.98 per parser; a parser below the bar is dropped or narrowed.
  - The unparsed list is reported by folder.

## Done when

- Tests per parser.
- The eval report is at `evals/runs/<date>_filename-parsers.md`, with precision per parser, coverage, and the unparsed counts by top folder.

## Depends on

The owner's examples, for the scan parsers only. The device parsers can start now.

## Open questions

- Which folders hold the owner's scans? (Expected: Drive-synced paths in ImportantFiles; see `sources/03`.)

## Comments
