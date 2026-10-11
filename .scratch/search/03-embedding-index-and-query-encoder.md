# Embedding index and query-time text encoder

Status: blocked (on 02)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 41:** the default ranking is `eg2-blend`, i.e. `w·cos(image) + (1−w)·cos(caption)` with w = 0.5; an asset with no caption vector uses its image score (`evals/search.py` `route_definition`, `embed.rank`).
- **Decision 3:** the query text encoder runs on the Mac Studio.
- **PLAN.md (Vector store row):** filtered brute force over BLOBs; no ANN index, no Qdrant.

About 72k images (plus Takeout) × 768-d float32 is roughly 220 MB per vector kind, which fits easily in memory.

## What

- **A query encoder:**
  - `embed.embed_query` with the frozen `eg2-bf16` candidate (`config/candidates.d/embed.toml`: `embeddinggemma-2:740m-bf16`, digest pinned, `query_prompt = "task: search result | query: {text}"`);
  - served from the same machine as the DB, via the local Ollama;
  - the digest is checked at startup; a mismatch refuses to serve. Image vectors embedded under one digest must not be queried with another.
- **An in-memory matrix:**
  - built at startup from the `embeddings` table (`search_matrix` shape: ids, image matrix, caption matrix, has-caption mask);
  - refreshed after each import, not on every query.
- **Filter-first ranking:** a boolean mask from SQL filters (04) is applied before the dot product, then `argsort` on the masked rows only. Ranks **captures**: a capture's score is its canonical asset's score. Should other members be able to raise it? That's an open question below.
- **Tie-break exactly as the harness:** score descending, then asset_id ascending (`route_definition`'s `tie_break`). Otherwise 05 cannot match the harness.

## Done when

- Over the full library on the laptop (then the Studio):
  - query encode p95 ≤ 150 ms with a warm model;
  - rank p95 ≤ 100 ms for an unfiltered query and ≤ 50 ms for a filtered one;
  - measured with a `shoebox search bench` command over the eval-1k query texts. The budget is provisional; web-app/07 sets the end-to-end number.
- A digest-mismatch test refuses to serve.
- A unit test shows that the ranking equals `evals.search.route_scores` for `eg2-blend:<cand>` on a synthetic corpus, ties included.

## Depends on

02. 05 consumes it.

## Open questions

- **Encoder residency:**
  - Does EG2 stay resident in Ollama next to 12B on the Mac Studio (96 GB)? Probably.
  - Until the Studio arrives, the laptop serves.
  - Radiobox's Ollama on the old M1 Studio is not to be used (decision 10).
- **Capture scoring:** the canonical asset only, or the max over members? Start with canonical-only, because it matches the eval, where each eval-1k item was one asset.

## Comments
