# Gate B report: the catalogue is trustworthy enough to search on

Status: blocked (on 02–10)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **PLAN.md Phase 2, Gate B:** the capture-source and colour/mono evals pass, the timestamp spot-check passes, and GPS coverage is reported.
- **Decision 18:** gates are precision-first.
  - precision ≥ 0.98–0.99 per stratum;
  - coverage reported;
  - thresholds tuned to minimise unknowns at that bar.
- **Decision 40:** Opus labels are the ground truth, with owner spot-checks. This report is what `search/` builds its schema and filters on.

## What

- **Assemble one report,** `evals/runs/<date>_gate-b.md`, from the per-issue reports:
  - 04 filename parsers: precision per parser, the unparsed list;
  - 05 dates: precision per kind, the conflict counts, the scan-dated share;
  - 06 places: GPS coverage per source and kind, place precision;
  - 07 colour/mono: precision per stratum, and the chosen black-and-white filter source;
  - 08 capture axes: precision per class and stratum, coverage;
  - 03 sidecars: matched/unmatched, cross-check agreement (if Takeout has landed; else marked pending);
  - 09/10: thumbnail and video coverage.
- **Verdict per field:** each field is either searchable (passed), display-only (shown with its provenance, not offered as a filter) or withheld. This is the list `search/` uses for facets.
- **Strata:** every number is reported per capture stratum (scans, phone, digital camera, received/screenshot), not only overall (PLAN.md R10).

## Done when

The report is committed with a pass/fail per check and a verdict per field. The `search/` schema issue is updated with the searchable-field list.

## Depends on

02–10. It can be produced first for ImportantFiles and re-run when the other sources land.

## Comments
