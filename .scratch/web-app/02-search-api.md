# Search API

Status: blocked (on 01, search/03, search/04)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md Phase 4 ("faceted search … API"). Decisions:
- 8: results are captures;
- 15: scan dates are excluded from year searches by default;
- 41: `eg2-blend` is the default ranking; keyword mode and filters are opt-in.

## What

HTTP JSON endpoints (names indicative):

- **`GET /api/search`**, with:
  - `q`;
  - `mode=semantic|keyword` (default `semantic`);
  - filters (`from`, `to`, `include_scan_dates`, `colour`, `medium`, `digitiser`, `source`, `place`, `has_gps`, `unprocessed`);
  - `limit`, `cursor`.

  It returns captures in rank order. Each carries:
  - capture_id and its canonical asset_id;
  - the display date with its `date_kind`;
  - place;
  - thumbnail URL;
  - score.

  It also returns any `suggested_filters` from `parse_filters` (never applied unless the client sends them back).
- **`GET /api/captures/{id}`:** members (assets, files, sources), the active label (caption, tags, medium, digitiser, colour_class and their confidences), each metadata field's display value with its provenance and conflicts, Google albums, human labels with `labelled_by`, and `rotate_cw` (suggested, plus any human value).
- **`GET /api/facets`:** counts for filter chips, over the filtered set.
- **Pagination** is stable for a given query, by rank, then asset_id tie-break.
- **No query text is logged in production logs** beyond a hash. The family's searches are private too.

## Done when

- Contract tests against a seeded DB:
  - a known query returns the expected capture first;
  - filters intersect correctly;
  - `include_scan_dates` flips the scan-dated fixture in and out;
  - a capture with three members returns once;
  - an unknown capture id returns 404.
- An OpenAPI (or equivalent) description is committed, which the UI (05) is written against.
- Latency is folded into 07.

## Depends on

01, `search/03`, `search/04`.

## Comments
