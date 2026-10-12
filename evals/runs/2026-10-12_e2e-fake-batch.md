# E2E: `shoebox label bulk` against a fake batch transport (task 10.1)

- run: 2026-10-12T03:39:30; candidate gemini-batch-v2 (gemini-batch/gemini-3.8-flash, params dcfeb24f48fd37ec)
- worklist created 2026-10-12T00:07:49+00:00; sources importantfiles
- transport: in-process fake (no network; HttpTransport, the Gemini key and httpx were blocked)
- verdict: FAIL (bulk exit 0)

## Counts

files 71813 → assets 52955 → captures 49287 → eligible 48218 → hosted 46874 → reused 973 → fallback 2 → tiny routed 0 → to send 45899

this run: limit None, to send 45899; dropped at send time: unlocated 0, recheck 0, batch skipped 0, cached 0, not submitted (cap/budget) 43556; requests sent 2343 in 122 job(s)

stored synthetic labels (overlay only): ok 2343, invalid 0, error 0

## Checks

violations among sent ids (quarantined, privacy, unscreened, non-canonical, ineligible, already labelled): none

real results tree: added 1, removed 0, changed 2

real spend log: byte-identical

problems: the real results tree changed

## Re-quoted cost of the real run

$97.4206 for 45899 capture(s) to send with gemini-batch-v2 at batch price (discount 0.5; price_date 2026-10-11; price_valid_until 2026-12-31).
