# FTS5 keyword search and filters

Status: blocked (on 02)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 41:** the eval found that FTS/hybrid routes and query expansion did not beat `eg2-blend` on dev. The soft filters (`+filt`) helped only category queries (about +0.05). So keyword search and filters ship as **explicit UI options, not the default ranking**.
- **Decision 15:** year searches exclude scan-dated items by default, with an "include scan dates" toggle.
- **Decision 14:** a filter on an uncertain attribute uses only values that cleared the eval cut-off; others are `unknown`.

## What

- **The `captions_fts` FTS5 table** (01), over:
  - the active caption and tags;
  - place names (offline geocoder, `catalogue/`);
  - folder and filename tokens: the `meta_tokens` idea from `evals/search.py`, versioned like `META_VERSION`;
  - Google album names and descriptions (decision 30).
- **The tokenizer** is `porter unicode61`, the same as the harness.
- **Query construction** is the harness's `fts_query`: casefolded terms, stopwords removed, quoted, ORed. Used only when the user picks "keyword" mode, or as an exact-phrase box.
- **Hard filters** (SQL predicates → the mask for 03):
  - **date range,** on capture dates whose kind is not `scan_date` unless the toggle is on, per decision 15;
  - **colour class: mono / colour / toned,** from whichever source `bulk-captioning/` picks. Gemini's mono precision is 0.974, below the 0.98 gate, so its `colour_class` is not used until that issue decides;
  - **medium and digitiser** (decision 28);
  - **source library and person;**
  - **place;**
  - **has GPS;**
  - **unprocessed scan** (decision 24).
- **Query-text filter parsing** (`parse_filters`: "black and white", "scan", years, decades) offered as *suggested chips*, never applied silently.
  - If the soft filter is offered at all, it uses `apply_soft_filter` semantics (FILTER_VERSION 2: it reorders scored items and never promotes unscored ones).

## Done when

- Running a filter suite on a seeded DB (e.g. `uv run pytest tests/search/test_filters.py`) gives exact expected sets for: a year with and without scan dates; mono; medium; place; and a combination.
- 05's parity run shows that the default (no filter, no keyword mode) ranking is unchanged by this issue.
- The FTS rebuild is incremental: only assets whose active label changed are rewritten (timed on a 1k-row change).

## Depends on

02. It uses the colour-filter decision from `bulk-captioning/` and the date kinds and places from `catalogue/`.

## Open questions

- Should the filters become default for queries `parse_filters` recognises as category-style? This needs its own pre-registered eval. Don't decide by feel.

## Comments
