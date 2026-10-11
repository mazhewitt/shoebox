# Date kinds and display precedence, with a conflict list

Status: ready-for-agent (after 02; 03 and 04 plug in as they land)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 9:** display-value precedence is human label > in-file metadata > Takeout sidecar > filename date > mtime. Across libraries, the owner's copy (identified by camera) wins. Date disagreements over 1 day go to a conflict list.
- **Decision 12:** scans use the scan date, marked as such, until a real date is known.
- **Decision 27:** for scans, precedence is human > filename > in-file metadata > scan date.
- **Decision 15:** year searches exclude scan-dated items by default, with an "include scan dates" toggle.

**The current inventory disagrees** (`inventory.py`, "Best date and its kind"). It puts `scan` first: a scan's EXIF date beats a filename date. Decision 27 says a filename date beats the scan date. The frame's counts by kind:

| Kind | Images |
|---|---|
| exif | 50,568 |
| mtime | 13,604 |
| scan | 6,184 |
| filename | 3,171 |

So some scans with a dated filename are shown with their scan date today.

## What

- **Module:** `src/shoebox/catalogue/dates.py`. It merges every candidate date into `{value, precision (second/day/month/year/decade), kind, provenance, tz}`. The candidates come from:
  - EXIF/QuickTime (02);
  - the sidecar (03);
  - filename and folder (04);
  - mtime;
  - human labels, later, from the UI.
- **Kinds:**
  - `human`;
  - `exif_original`;
  - `sidecar_taken`;
  - `filename` / `folder`;
  - `scan_date` (when the scan was made, never presented as a capture date);
  - `estimated` (reserved);
  - `mtime`;
  - `unknown`.
- **Precedence:** decision 9 for camera files, decision 27 for scans. Whether a file is a scan comes from 08, using today's `rules.classify_origin` until 08 lands.
- **Zones:** QuickTime dates are UTC; EXIF and filename dates are local wall-clock time without a zone. Normalise for comparison, and keep the as-recorded value.
- **Conflict list:** a date conflicts when candidates disagree by more than 1 day. The list records the asset, each candidate with its kind and the chosen value, and goes in `results/metadata/date-conflicts.jsonl` (no review queue, decision 11).
- **Search-facing fields:**
  - `capture_year` is null when the only date is `scan_date` or `mtime`;
  - `scan_year` is kept separately, for the toggle.

## Done when

- Tests for each precedence path, including a scan with a dated filename (the filename wins) and a QuickTime UTC case.
- **Spot-check:** Opus checks 50 per date kind against the image and path (plausibility: era, season, film vs digital). The owner checks 20. Precision ≥ 0.98 per kind, or that kind is demoted.
- The conflict count by kind pair is reported in the Gate B report (11).

## Depends on

02 (raw fields), and 08 (the scan classification; the rules-v1 stand-in is fine until then).

## Comments
