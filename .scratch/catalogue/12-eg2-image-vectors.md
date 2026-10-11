# EG2 image vectors for every asset (local pass)

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **The search route needs it.** `eg2-blend` (decision 41) uses the EmbeddingGemma 2 image vector for every asset, captioned or not. An asset with no caption is ranked on its image vector alone.
- **Dedup needs it.** `dedup/03` uses the same vector to confirm near-duplicates.
- **No issue owned this pass.** `bulk-captioning/06` makes captions and caption vectors, not image vectors. Today image vectors exist only for the eval sets.
- **It is local and free,** with no consent constraint. Since `eg2-image@768` is the "no captioner" route, it also gives a searchable library before any caption exists.

## What

- **Run `shoebox embed` with the frozen `eg2-bf16` candidate** (`config/candidates.d/embed.toml`, digest pinned) over every unique image asset_id. The input is the `infer-v1` derived JPEG, as on eval-1k.
- **Store:** the keyed store `results/embed/<model>/<params_hash>/`, unchanged. Re-runs are idempotent by `(asset_id, params_hash)`.
- **One vector per asset,** not per file. Byte-identical copies share one; ImportantFiles has 71,622 files but 52,481 unique asset_ids. Dedup to captures happens later, at query time.
- **Videos:** embed the poster frame from `catalogue/10` once it exists, recorded as such.
- **Throughput:** measure img/h on the M5 Max and record it in a comment. That number decides whether this runs on the laptop now or on the Mac Studio (`ingest/04`).

## Done when

- Every unique image asset_id on ImportantFiles that has an `infer-v1` image has a vector that passes `embed.check_vector`, or a listed error.
- Coverage, img/h and the store path are recorded in a comment.

## Depends on

- `infer-v1` derived images for the whole source. Check `derive`'s coverage first; deriving the rest may be the long pole.
- Re-run when the new sources land (`sources/06`).

## Comments
