# Takeout sidecar parser: match, merge Google metadata, report the unmatched

Status: blocked (on sources/02 for real data; the parser can be built on synthetic fixtures now)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **PLAN.md R3:** sidecar matching has many quirks and gets a wrong date or GPS on some subset.
  - truncated names (47-character limit);
  - `(1)` suffix placement (`IMG_1234(1).jpg` ↔ `IMG_1234.jpg(1).json`);
  - `-edited` variants;
  - `.supplemental-metadata.json` and its truncated forms;
  - Live Photo `.MP4`s sharing a sidecar.
- **Decision 30:** import the Google organisation as metadata, refreshed per Takeout, with provenance `google_photos`; Shoebox human edits always win. That covers:
  - albums, including shared ones;
  - favourites;
  - descriptions;
  - archived/locked state;
  - Google place names.
- **Decision 31:** Google's people names are stored as their own source. They're used only as naming suggestions and a disagreement audit, **never** as eval ground truth.
- **What exists:** `inventory.py` reads `photoTakenTime` from a sidecar next to the media file. That is the simple case only.

## What

- **Module:** `src/shoebox/catalogue/takeout.py`. It matches each media file in a Takeout tree to its sidecar by:
  - exact name, then
  - the known quirk rules, each rule named.

  Each match records `match_rule`. Album folders' `metadata.json` gives album membership.
- **Fields kept with provenance:**
  - `photoTakenTime` (UTC);
  - `geoData` / `geoDataExif`;
  - `description`;
  - `favorited`;
  - `archived` / `trashed`;
  - album names;
  - `people[].name` (stored as `google_people`, decision 31);
  - the Google URL.
- **Output:** a keyed store, `results/metadata/takeout/<params_hash>/`, keyed by asset_id plus the source path. The same bytes can sit in several albums.
- **Unmatched list:** media with no sidecar, and sidecars with no media, with counts per rule.
- **Cross-check:** run GPTH or immich-go (whichever installs cleanly; read-only, output to scratch) over the same tree. Diff the media → date mapping, and list every disagreement.

## Done when

- **Tests:** synthetic fixtures for each quirk rule.
- **Per account:** the matched/unmatched rate and the cross-check agreement are reported in `sources/06`'s Gate A report. Agreement must be ≥ 99% of matched files, and each disagreement class must be explained.
- **People names:** these never reach the eval sets. Add a test that `google_people` isn't read by any `evals/` module.

## Depends on

- sources/02 (the Takeout trees).
- 02 (EXIF, which is compared with the sidecar for the conflict list in 05).

## Comments
