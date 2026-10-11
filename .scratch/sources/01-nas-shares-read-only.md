# Mount Home Folders, Backup and Volume2 read-only for the `shoebox` user

Status: ready-for-human (owner: DSM permissions)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 39:** all the NAS photo shares are in scope eventually, about 880 GB in total:
  - ImportantFiles;
  - Home Folders;
  - Backup;
  - Volume2.
- **Status:** tasks.md 9.1 (old tasks 1.1b and 1.2). Only ImportantFiles is mounted today (`~/mnt/importantfiles-ro`, see `docs/machines.md`).

## What

**Owner, in DSM:**
1. Give the `shoebox` NAS user **read-only** permission on `Home Folders`, `Backup` and `Volume2`. Use the same pattern as ImportantFiles: SMB only, every other application denied.
2. Tell Claude which top-level folders in each share belong to which person (owner, Jude, Kyra, Toren). Consent follows the person, not the share (decision 39).

**Agent, once the permissions exist:**
- Add the mount commands to `docs/machines.md`. Mount by IP as `shoebox`, with `rdonly,nobrowse`:
  - `~/mnt/homefolders-ro`
  - `~/mnt/backup-ro`
  - `~/mnt/volume2-ro`
- Add one `[sources.<name>]` entry per share to `config/shoebox.toml`, each with:
  - `root`;
  - `person`;
  - `synced`;
  - `hosted_consent = false`, the default for everyone but the owner;
  - no `hosted_models`.
- **Split shares that mix people.** A share that holds several people's libraries (Home Folders per user, almost certainly) becomes one source per person-folder. `Source.person` is a single string (`src/shoebox/config.py`), so it can't describe a mixed share.
- Check that `touch` fails on each mount. `shoebox inventory` already refuses a writable root when `synced = true`; set `synced` truthfully per share.

## Done when

- `mount` shows each share as `//shoebox@192.168.1.9/...` with `rdonly`, and `touch` fails with "Read-only file system".
- `uv run shoebox inventory --sources <new> --only <one small folder> --out-dir ~/mnt/shoebox/frame/dev` runs clean on each new source.
- Every new source names one person in `config/shoebox.toml`.

## Depends on

Nothing. This is owner time only.

## Open questions

- **Backup:** does it contain machine backups (Time Machine sparsebundles, Hyper Backup `.hbk`)? Those need a skip rule (05) rather than a walk.
- **Volume2:** is it a share or a volume? DSM's name suggests a volume. Its shares would each become a source.

## Comments
