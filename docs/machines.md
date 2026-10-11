# Machines, shares and mounts

## NAS (Synology, 192.168.1.9 / aduki-nas.local)

| Share | Role | Synced to Google Drive? | `shoebox` user access |
|---|---|---|---|
| `ImportantFiles` | Source: scans, raws, JPEGs, old Drive "Google Photos" tree, Lightroom catalogues | **Yes** (Cloud Sync) | **Read only** |
| `shoebox` | Shoebox working data: `staging/`, `originals/`, `derived/`, `results/`, `frame/` | No | Read/Write |
| `Home Folders`, `Backup`, `Volume2` | Sources: family folders, old PC/phone backups | No | **Read only** (to grant, task 1.1b) |
| all other shares | — | — | No access |

**Rule:** no Shoebox process writes to `ImportantFiles`. Anything written there could reach Google Drive. This is enforced three ways:
- the NAS permission;
- a client-side read-only mount;
- a check in code that refuses writable synced roots.

The `shoebox` NAS user (created 2026-10-10; effective permission on `ImportantFiles` confirmed **Read only** in DSM's Preview):
- is a member of `users` only;
- may use SMB only, with every other application denied;
- has no quota.

Volumes:
- `shoebox` sits on the 3.4 TiB volume, which had 3.1 TiB free at setup.
- `ImportantFiles` sits on a 983 GiB volume.

### Backup
- `shoebox/` goes to GCS via Hyper Backup, **excluding `derived/` and `staging/`**. Derived images can be regenerated, and the staging zips are deleted once the extract is verified.
- Exclusion set in the Hyper Backup task (confirmed by the owner, 2026-10-10).

## Laptop (M5 Max, 128 GB): mounts

Mount as the `shoebox` NAS user, by **IP address**. Your own mounts in `/Volumes` use the host name `aduki-nas.local` as `adukiman`, and macOS reuses sessions per host name, so the IP keeps the two apart.

```sh
mkdir -p ~/mnt/importantfiles-ro ~/mnt/shoebox
mount_smbfs -o rdonly,nobrowse //shoebox@192.168.1.9/ImportantFiles ~/mnt/importantfiles-ro
mount_smbfs -o nobrowse //shoebox@192.168.1.9/shoebox ~/mnt/shoebox
```

- `rdonly` makes the Mac refuse writes, on top of the NAS permission.
- `nobrowse` keeps the mounts out of Finder's sidebar, so Finder doesn't write `.DS_Store` into a source.
- Mounts don't survive a reboot or sleep-induced disconnect. Re-run the commands above.

Verified 2026-10-10:
- `touch` on `~/mnt/importantfiles-ro` fails with "Read-only file system";
- a write and delete on `~/mnt/shoebox` succeeds;
- `mount` shows both as `//shoebox@192.168.1.9/...`.

Don't use the `/Volumes/*` mounts for Shoebox. They are read-write under the owner's account.

## Mac Studio (M1, 32 GB; 192.168.1.117 wired): mounts

From 2026-10-11 the build runs here, because it is wired to the NAS at 1 GbE. The laptop's Wi-Fi measured 12–47 MB/s. Use the same mounts and paths as the laptop, so `config/shoebox.toml` works unchanged:

```sh
mkdir -p ~/mnt/importantfiles-ro ~/mnt/shoebox
mount_smbfs -o rdonly,nobrowse //shoebox@192.168.1.9/ImportantFiles ~/mnt/importantfiles-ro
mount_smbfs -o nobrowse //shoebox@192.168.1.9/shoebox ~/mnt/shoebox
```

- Don't mount `ImportantFiles` read-write on the Studio at all.
- radiobox shares this machine: its Ollama listens on `:11434` on the LAN, and its speech-recognition service on `:8645`. Leave both as they are.
- Run the 12B and other large local models on the M5 Ultra (due end of October 2026) or the laptop.
- The setup check is in `docs/handoff-studio.md`. Repeat it on the M5 Ultra when it arrives.
