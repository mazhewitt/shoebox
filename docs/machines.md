# Machines, shares and mounts

## NAS (Synology, 192.168.1.9 / aduki-nas.local)

| Share | Role | Synced to Google Drive? | `shoebox` user access |
|---|---|---|---|
| `ImportantFiles` | Source: scans, raws, JPEGs, old Drive "Google Photos" tree, Lightroom catalogues | **Yes** (Cloud Sync) | **Read only** |
| `shoebox` | Shoebox working data: `staging/`, `originals/`, `derived/`, `results/`, `frame/` | No | Read/Write |
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
