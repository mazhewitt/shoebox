# Web UI: search, grid and detail

Status: blocked (on 02, 03)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md §1 ("TypeScript, thin client over the HTTP API") and Phase 4. Decisions:
- 8: one result per capture;
- 9: shown values follow the precedence order, with provenance visible;
- 15: the scan-date toggle;
- 41: filters as UI options.

The owner has said what they actually search for: black-and-white photos and scene descriptions. Those two must be one click away.

## What

- **Search page:**
  - a query box;
  - a semantic/keyword mode switch (default semantic);
  - filter chips: date range, "include scan dates" (off by default), B&W, colour, medium, source, place, has GPS, unprocessed;
  - `suggested_filters` from the API shown as tappable chips, never auto-applied.
- **Grid:**
  - 256 px thumbnails;
  - infinite scroll on the API cursor;
  - a capture badge when it has several members (Live, RAW+JPEG, burst, edited);
  - a scan-date marker on scan-dated captures.
- **Detail:**
  - the display image;
  - caption and tags;
  - each field with its value, `date_kind`, provenance and any conflict (e.g. "1998 · from filename"; "scan date 2019-03-02, actual date unknown");
  - place and Google albums;
  - capture members with source and path;
  - a download-original link.
- **Phone-width layout.** The family will mostly use phones.
- **Keyboard navigation** in the detail view (left/right through results).
- **Build output** is static assets served by the API host. No Node server in production. Pin dependency versions.

## Done when

- A Playwright (or equivalent) smoke test on a seeded DB covers: search → grid → detail → back, the scan-date toggle changes results, a B&W chip filters, and the phone-width viewport has no horizontal scroll.
- The owner uses it for 15 minutes on real data. Their notes go in Comments, and issues are filed for anything that blocks MVP.

## Depends on

02, 03.

## Comments
