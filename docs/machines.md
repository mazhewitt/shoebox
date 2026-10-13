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
- `ImportantFiles` moved to the same 3.4 TiB volume on 2026-10-12, after a Google Takeout filled its old 983 GiB volume.

### Backup
- `shoebox/` goes to GCS via Hyper Backup, **excluding `derived/` and `staging/`**. Derived images can be regenerated, and the staging zips are deleted once the extract is verified.
- Exclusion set in the Hyper Backup task (confirmed by the owner, 2026-10-10).

## Secrets

Secrets are in `secrets/shoebox.sops.yaml`, encrypted with SOPS + age and committed. It uses the same age key as radiobox and the owner's notes vault: `~/.config/sops/age/keys.txt` on each machine, with a passphrase-encrypted backup on the NAS (`~/radiobox/deploy/scripts/sops-key-nas.sh restore`).

- `GEMINI_API_KEY`: the Gemini runner.
- `ADUKI_NAS_USERNAME` / `ADUKI_NAS_PASSWORD`: the owner's **DSM admin** account, for DSM admin checks only (share permissions, Hyper Backup scope, Cloud Sync status) via the DSM Web API. **Never use it to mount shares or read photos.** Every pipeline read goes through the `shoebox` user's mounts above, so the read-only guarantee on `ImportantFiles` holds.
  - **One exception (owner, 2026-10-13): Takeout extraction on the NAS.** An SSH session under this account may run `7z` to test and extract Google Takeout zips, reading only `ImportantFiles/Takeout/*.zip` and writing only under `shoebox/originals/takeout/`. Nothing else: no other reads of photos, no writes anywhere else (above all never under `ImportantFiles`, which syncs to Drive), no deletes. The extracted tree is then verified from the Studio through the `shoebox` user's mount (`shoebox takeout verify`), so the manifest and the read-only checks stay in Shoebox's own code.

Use the secrets without reading them: `sops exec-env secrets/shoebox.sops.yaml 'uv run shoebox …'`. Never `sops decrypt` the file to the terminal and never echo a value. Add or change one with `sops set secrets/shoebox.sops.yaml '["KEY"]' '"value"'`. Don't add YAML comments: SOPS encrypts them.

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
