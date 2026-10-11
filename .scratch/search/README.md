# Search: index and query path

**Goal.** Turn the eval harness's winning route into the product's search path:
- one SQLite database on the Mac Studio's SSD (decision 3);
- the keyed result stores imported into it, without re-running inference;
- a query path that ranks **captures** (decision 8) by `eg2-blend`.

`eg2-blend` is the EmbeddingGemma 2 image vector plus the caption vector, at w = 0.5 (decision 41, `evals/RECOMMENDATION.md`). Filters and keyword search come as UI options, not as the default ranking.

The eval code already ranks this way, in memory, over `eval-1k`:
- `src/shoebox/evals/search.py`: `route_scores`, `blend_scores`, `apply_soft_filter`;
- `src/shoebox/embed.py`: `search_matrix`, `rank`.

The work here is to make that the product path, persistent and filterable, and to *prove* it still scores what the eval scored.

| # | Issue | Gate to pass before it lands |
|---|-------|------------------------------|
| 01 | [SQLite schema v1](01-sqlite-schema-v1.md) | `catalogue/` and `dedup/` record shapes are known (at least drafted); the schema is reviewed against decisions 8, 9, 14 and 30 |
| 02 | [Import the keyed result stores](02-import-results.md) | 01 landed; a re-import after a schema change is bit-identical with no model calls |
| 03 | [Embedding index and query-time text encoder](03-embedding-index-and-query-encoder.md) | 02 landed; p95 query latency is within budget on the full library |
| 04 | [FTS5 and filters](04-fts-and-filters.md) | 02 landed; the default ranking is unchanged, as 05 shows |
| 05 | [Production parity eval](05-production-parity-eval.md) | 03 and 04 landed; nDCG@20 on the eval-1k test queries equals the harness's within tolerance |
| 06 | [Mixed caption sources in one space](06-mixed-caption-sources.md) | `needs-triage`; decided before `ingest/` captions new photos with 12B |

## Sequencing rationale

- **01 first.** The schema depends on what `catalogue/` and `dedup/` emit (captures, provenance, date kinds), so it is drafted from their issue specs. It is not finalised until their first real output exists. 02 makes a schema change cheap, which is why the keyed store exists (PLAN.md §1, "Why results go through bundles").
- **03 and 04 can run in parallel** once 02 lands.
- **05 is the gate for the whole directory:** it is what stops the product quietly ranking differently from what the eval measured.
- **06 can wait** until the first 12B-captioned new photo is about to enter the index, but no later.

## Directory gate

`shoebox search parity` (05) passes on `eval-1k` test. Re-importing the full library into a fresh database reproduces the same rankings for the parity queries.

PLAN.md's Gate D (the end-to-end query suite, latency, auth) sits in [`web-app/07`](../web-app/07-gate-d.md), which builds on this.

## Notes

- PLAN.md §1/§6 put SQLite and the text encoder on the NAS ("SQLite is only ever opened by processes on the NAS"). **Decision 3 supersedes this:** the database and the encoder live on the Mac Studio; the NAS holds originals and backups. Until the M5 Ultra Studio arrives (end of October 2026), the laptop stands in. Never open the database over SMB/NFS.
