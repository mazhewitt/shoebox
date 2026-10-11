# Gate D: end-to-end query suite, latency and auth rejection

Status: blocked (on 02–06)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md Phase 4 Gate D: "an end-to-end query suite over deterministic facets returns the expected results; p95 latency … measured; JWT rejection tested (missing, expired, wrong audience)." The host is now the Mac Studio (decision 3), not the NAS. This is the gate before family accounts get access.

## What

- **A deterministic facet suite:** 20–30 queries over the real DB whose answers are computable from metadata alone, independently of the API. Examples:
  - "all captures from 2017 excluding scan dates";
  - "mono film negatives";
  - "source = importantfiles, folder Jordan";
  - "has GPS within <place>".

  The expected sets come from a direct SQL or frame computation, written before the API is run against them.
- **Search quality is linked, not repeated:** `search/05`'s parity report must be current, i.e. run on the same DB build.
- **Latency on the host, LAN plus tunnel:**
  - p50 and p95 for `/api/search` (semantic, keyword, filtered);
  - detail;
  - a 60-thumbnail grid page.

  Targets: search p95 ≤ 500 ms end to end over the tunnel, and grid p95 ≤ 1 s. If those turn out wrong, revise them here with the measurement, not silently.
- **Auth:** re-run 04's rejection matrix against the deployed instance through the real tunnel, plus an unauthenticated request straight to the host's port from another LAN machine (must fail: localhost bind).
- **Report:** `evals/runs/<date>_gate-d.md`, with suite pass/fail per query, the latency table, the auth matrix, the DB build id and the commit.

## Done when

- The report shows every facet query exact, latency within target (or targets revised with the reason), and every auth case rejected.
- The owner signs off in Comments, and family access is enabled after that.

## Depends on

02–06, `search/05`.

## Comments
