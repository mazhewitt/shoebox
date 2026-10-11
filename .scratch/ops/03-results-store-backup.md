# Results store in the NAS's GCS backup (`derived/` excluded)

Status: ready-for-human (the owner checks the DSM Hyper Backup / Cloud Sync job scope; the agent writes the check)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 16:** the NAS's GCS backup covers `originals/`, `results/` and `db/`, not thumbnails.
- **What's at stake.** `~/mnt/shoebox/results/` holds every keyed output the eval phase produced:
  - `labels`, `embeddings`, `judgments`, `caption_audits`, `caption_usefulness`, `owner_reviews`, `queries`, `expansions`, `search`, `vally`, `pilot-v0`;
  - about $10 of Gemini spend;
  - the Opus judging and captioning that used a large share of a week's subscription;
  - many hours of local inference.
- **Soon** the bulk Gemini run (about $98) will land there too.
- **`derived/`** is regenerable from the originals plus `config/profiles.toml`, so it's excluded.

## What

- **Owner:** in DSM, confirm which backup task covers the `shoebox` share and that its scope includes `results/` (and `db/` once it exists), with `derived/` and `staging/` excluded. Say what retention it has.
- **Agent:**
  - a `shoebox ops backup-check` (or a documented script) that lists the store's top-level kinds with file counts and byte totals, so a restore can be compared with it;
  - a restore drill: restore one small store from GCS to a scratch location, and check it with `runners.source_index`.
- **Before the bulk run:** the bulk Gemini outputs must not exist in only one place. Confirm a backup has run after the first batch lands.

## Done when

- The backup scope is recorded below by the owner.
- The restore drill is recorded, with matching counts.
- `bulk-captioning/`'s bulk-run issue lists "backup confirmed" as a checkpoint.

## Depends on

Nothing for the check. The bulk-run checkpoint is in `bulk-captioning/`.

## Comments
