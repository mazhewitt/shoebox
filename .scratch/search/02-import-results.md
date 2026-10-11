# Import the keyed result stores into SQLite

Status: blocked (on 01)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md §1, "Why results go through bundles": inference writes results keyed by `(asset_id, model_id, params_hash)`, so the schema can change and be re-imported without re-running the bulk pass. Decision 41's bulk Gemini run costs about $98 and cannot be repeated casually. The importer is what makes the keyed store worth having.

## What

A `shoebox db import` command that loads into the database:

- **labels** from `results/labels/<model_dir>/<params_hash>/part-*.jsonl` (`store.iter_dir`, `store.index_records`), using `runners.label_source` / `label_index`. A record superseded by a `~retry` record is resolved exactly as the eval harness resolves it.
- **embeddings** (`results/embed/...`): image vectors and caption vectors, decoded with `embed.decode_vector` and stored as float32 BLOBs. `embed.check_vector` is run on every vector.
- **catalogue and dedup outputs** (frames, metadata records, capture groups), in whatever form `catalogue/` and `dedup/` settle on.

Behaviour:
- **Idempotent:** importing the same store twice changes nothing. Each store dir's `params.json` hash goes in `schema_meta`.
- **Read-only on the store:** never writes under `results/`. It writes only to the DB path, which goes through `config.check_write_location`.
- **Selects the active label per asset by an explicit config rule:** the `[bulk]` and `[incremental]` captioners from decision 41, plus human labels on top. It never picks whichever record happens to be newest.
- **Keeps human labels:** a re-import drops and rebuilds everything except `human_labels`.
- **Rebuilds the FTS5 rows** for every asset whose active label changed.

## Done when

- Importing `eval-1k`'s stores (the gemini-medium-v2 labels, the eg2-bf16 image vectors and the gemini-medium-v2 caption vectors) gives row counts equal to the asset counts the harness reports.
- A second import is a no-op: same row counts and the same `sha256` of a canonical dump.
- After a schema change, `shoebox db rebuild` reproduces the DB from the stores with **zero model calls**. A test asserts that no runner is constructed.
- A test checks that a human label survives a rebuild.

## Depends on

01.

## Open questions

- Should the importer read the stores over the NAS mount, or from a local copy? The results store lives under `shoebox_root` (`~/mnt/shoebox`); importing over SMB is fine, because the store is read sequentially and the DB is local.

## Comments
