# `shoebox ingest`: one batch command with a per-asset stage ledger

Status: blocked (on `catalogue/` and `dedup/` defining the stages it runs; the ledger itself can be designed now)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 13:** no always-on worker or job queue. Incremental work is a batch `shoebox ingest` run after each Takeout pull, using the same code path as the bulk run. A per-asset record of stages and `model_id`s done means each run does only what's missing. Backfills use the same command. The Mac Studio runs what fits and the laptop does the rest.
- **PLAN.md Phase 7:** a model change gets a new `model_id`, a re-eval and an explicit backfill. Outputs are never silently mixed.
- **Today:**
  - each stage is its own command: `inventory`, `derive`, `label run`, `embed`;
  - "what's done" is spread across `derive/infer-v1/index.jsonl` and the keyed stores `results/<kind>/<model>/<params_hash>/part-*.jsonl`;
  - only `runners.label_index` and `source_index` know how to read the stores.

## What

- **Ledger.** One record per `(asset_id, stage, model_id, params_hash)`, derived from the keyed stores and the derive index. Rebuildable at any time, so it's a cache, not a source of truth.
  - Stages for the MVP: `inventory`, `metadata`, `dedup`, `derive:infer-v1`, `label`, `embed:image`, `embed:caption`.
  - The ledger is read through the existing `source_index` semantics, so a `truncated` record still triggers its `~retry`.
- **`shoebox ingest [--source S] [--only STAGE] [--dry-run] [--machine laptop|studio]`.**
  - It works out, per asset, which stages are missing for the **pinned pipeline** and runs them in dependency order through the existing runners.
  - The pinned pipeline is a config file listing the candidate per stage, e.g. `label = g12b-q4-v2` for new photos and `embed = eg2-bf16`.
- **The same code path for the bulk run.** The Gemini Batch run (`bulk-captioning/`) writes into the same keyed store, so ingest sees those assets as captioned and doesn't re-caption them with 12B.
- **Backfill.** Changing the pinned candidate for a stage makes every asset "missing" that stage. `--dry-run` prints counts and an estimated time or cost before anything runs.
- **Guards.**
  - Privacy: every hosted stage goes through `privacy.filter_items(provider=)`.
  - Writes: everything goes through `config.check_write_location`.
  - No stage writes under a source root.

## Done when

- `shoebox ingest --dry-run` on the current stores reports eval-1k and pilot-v0 as already done for the stages they have, with zero inference.
- A second real run on an unchanged tree runs nothing.
- Unit tests cover:
  - the missing-stage computation, including retry substitution;
  - pinned-candidate change → backfill;
  - a hosted stage refused for a held-back source.

## Depends on

- `catalogue/` (metadata stage) and `dedup/` (caption once per capture). Ingest runs stages, it doesn't define them.
- `search/02` (importing results into SQLite) becomes the last stage once it exists.

## Open questions

- Should the ledger live in the SQLite DB (`search/01`) or as a JSONL next to the stores? JSONL keeps it rebuildable without the DB. Decide when the schema lands.

## Comments
