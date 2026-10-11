# SQLite schema v1

Status: blocked (on `catalogue/` and `dedup/` record shapes; a draft can start now from their issue specs)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md Phase 4 ("SQLite schema v1, designed now that the Phase 2/3 outputs and eval needs are known"). Decisions:
- 3: SQLite on the Mac Studio SSD;
- 8: file → asset → capture; search returns captures;
- 9: display-value precedence;
- 14: uncertain outputs are stored as `unknown` with the score kept;
- 4: `labelled_by`;
- 30: Google organisation as metadata.

Nothing in the repo has a database yet. Every output is in the append-only keyed store (`src/shoebox/store.py`) or in parquet frames (`frame/`).

## What

A versioned DDL file (e.g. `src/shoebox/db/schema_v1.sql`) plus a `PRAGMA user_version` check. Tables, roughly:

- **`files`:** source, relpath, bytes, mtime, BLAKE3 (`ids.asset_id`), and `removed_upstream` (decision 7).
- **`assets`:** one viewable image, keyed by asset_id; an original and its `-edited` version are linked rows (decision 8). Pixel hash, perceptual hashes, width/height, and the derived-image paths (`derive.derived_path`).
- **`captures`:** one moment, grouping assets (Live/Motion, RAW+JPEG, bursts), with a canonical member chosen by `dedup/`. The search unit.
- **`metadata_values`:** asset_id, field, value, `provenance` (exif / takeout_sidecar / filename / file_mtime / google_photos / human / model), and `date_kind` for dates (exif_original, sidecar_taken, filename_pattern, scan_date, estimated, unknown). Display values are computed from these by the decision 9 precedence, as a view or a materialised column. A conflict list covers date disagreements of more than a day.
- **`labels`:**
  - keyed `(asset_id, model_id, params_hash)` as in the keyed store;
  - holds caption, tags, medium, digitiser, colour_class, `rotate_cw`, and confidences;
  - a `label_source` column (e.g. `gemini-medium-v2`, `g12b-q4-v2`);
  - an `active` flag: exactly one active label per asset, chosen by an explicit rule, never by "latest wins".
- **`embeddings`:** `(asset_id, kind ∈ {image, caption}, model_id, params_hash, dim, vector BLOB float32)`. A caption vector also records which label it embeds.
- **`human_labels`:** asset_id or capture_id, field, value, `labelled_by`, ts. These are never overwritten by a re-import (decision 14).
- **`captions_fts`:** an FTS5 virtual table (contentless or external-content), `porter unicode61`, matching `FTS_TOKENIZER` in `evals/search.py`.
- **`google`:** albums, favourites, descriptions and archived state, provenance `google_photos` (decision 30).
- **`schema_meta`:** schema version, import runs, and the source result-store dirs with their `params.json` hashes.

## Done when

- The DDL is committed with a one-page `docs/schema.md` that maps each table to the decision it serves.
- Tests create an empty DB, apply v1, and round-trip a synthetic file → asset → capture with two labels, one human label, two embeddings and an FTS row.
- The schema has been reviewed against `catalogue/` and `dedup/` outputs. Any field they emit that has no home is listed in the doc.

## Depends on

The `catalogue/` and `dedup/` issue specs. The schema is finalised after their first real output.

## Open questions

- Store `metadata_values` as an EAV table, or as typed columns plus a provenance side-table? EAV fits provenance and conflicts; typed columns make filters cheap. Proposed: typed display columns on `captures`, with EAV for the provenance trail.
- Should embeddings be stored at 768-d only, or also at a 256-d MRL truncation for a faster first pass? Decide with 03's latency numbers.

## Comments
