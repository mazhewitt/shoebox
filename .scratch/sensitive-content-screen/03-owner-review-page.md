# Owner review page for flagged items

Status: ready-for-agent
Reported: 2026-10-11
Reporter: mazda (request), filed by claude

## Why

Flagged items are filed for the owner's review before they go into the pipeline (owner request, 2026-10-11). Only the owner looks at them: no Claude viewing, no hosted model.

## What

- **Generation:** a local, offline HTML page, the same pattern as the eval review page (`src/shoebox/evals/review.py`: localStorage, "Download answers" JSON). Built by `shoebox screen review-page`.
- **Location:**
  - The page and its thumbnails go to a non-synced local folder, e.g. `~/shoebox-review/screen/`. **Never** Desktop or Documents, which iCloud may sync. Never the repo.
  - The page-map stays outside the page folder, as before.
- **Image handling:**
  - Thumbnails are written by the `derive` code. The builder must **not** read the images back for display in the session.
  - The command prints counts only.
- **Per item:** a thumbnail (blurred by default, click to reveal), the detector and its reason (e.g. NudeNet part labels), the score, and source and folder. Buttons:
  - **Include:** false positive.
  - **Private:** keep, owner-only, local processing only.
  - **Exclude:** keep the original, leave it out of the catalogue.
- **Speed:** keyboard shortcuts, a grid view and "mark all on page as include", so a batch of false positives clears in minutes.
- **Ingest:** `shoebox screen ingest <answers.json>` checks the page id and completeness, then writes the verdicts to the quarantine file (02) as human labels with `labelled_by = owner`. Verdicts go on record for the whole dedup group.
- **Clean-up:** after ingest, the command offers to delete the review folder's thumbnails.
- **Also serves `bulk-captioning/08`.** The same page and ingest handle the document screen's flags, with a category column.

## Done when

- Tests:
  - page build from a stub flag list;
  - answers ingest with a page-id mismatch, which is refused;
  - an incomplete answers file, which is refused, or applied partially with a warning (decide);
  - verdicts applied to group members.
- The owner clears the first real batch (01's union of flags) and comments here on how long it took.

## Depends on

None to build. 01 for the first real batch.

## Comments
