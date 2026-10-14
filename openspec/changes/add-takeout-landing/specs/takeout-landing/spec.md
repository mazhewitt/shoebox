## ADDED Requirements

### Requirement: Every Takeout zip is tested and extracted without writing to its source
`shoebox takeout verify` SHALL read the zips read-only from the given folder, check every entry's CRC, extract each
zip into `<shoebox_root>/originals/takeout/<account>/<export>/`, and record pass or fail per zip. It SHALL NOT write
under any source root or under `/Volumes`.

#### Scenario: A good zip
- **WHEN** a zip's entries all pass their CRC check
- **THEN** its files are in the tree and its row in `zips.jsonl` is `done`

#### Scenario: A corrupt zip
- **WHEN** an entry of a zip fails its CRC check, or the zip cannot be opened
- **THEN** that zip is recorded `failed`, no partial file of it is left in the tree, the other zips still run, and
  the command exits non-zero

#### Scenario: Destination inside a source
- **WHEN** the destination would be under a configured source root or `/Volumes`
- **THEN** the command refuses before reading any zip

### Requirement: A manifest proves the extract
The command SHALL write one manifest row per extracted file: zip name, entry path, size, CRC-32 and BLAKE3
(`ids.asset_id`), and SHALL reconcile zip entry count, files on disk and manifest rows.

#### Scenario: Counts reconcile
- **WHEN** every zip is `done`
- **THEN** the number of file entries in the zips, the number of files under the tree and the manifest row count
  are equal, and every file's size matches its row

#### Scenario: A missing or changed file
- **WHEN** a file listed in the manifest is missing from the tree or has another size
- **THEN** the command reports a reconcile mismatch and exits non-zero

### Requirement: Re-runs resume
A re-run SHALL skip zips already `done` whose files are all present with their recorded sizes, and process the rest.

#### Scenario: Interrupted run
- **WHEN** a run stops after some zips are done
- **THEN** the next run extracts only the zips not yet done, and the final manifest has each file once

### Requirement: Drive cleanup is announced only after full verification
The command SHALL print "export <export> for <account> verified on the NAS; safe to delete from Drive" only when no
zip failed and the reconcile passed. It SHALL NOT delete anything.

#### Scenario: All verified
- **WHEN** every zip is `done` and the reconcile passes
- **THEN** the cleanup line is printed and exit status is 0

#### Scenario: Any failure
- **WHEN** a zip failed or the reconcile found a mismatch
- **THEN** no cleanup line is printed and exit status is non-zero

### Requirement: The extract is a registered, consented source
The verified tree SHALL be configured as source `takeout-owner` (person owner, hosted consent as importantfiles,
`added` the registration date), so the copy index fails closed until its first inventory frame exists.

#### Scenario: Before the first frame
- **WHEN** `takeout-owner` is configured but has no frame dated on or after `added`
- **THEN** hosted sends are refused with the missing-frame error

### Requirement: Partner-shared photos are left out and Locked Folder photos are private
Before the first inventory of `takeout-owner`, `shoebox takeout flags` SHALL write a quarantine verdict for every media
file whose sidecar has `fromPartnerSharing` (`exclude`) or `inLockedFolder`, or that lies under a `Locked Folder`
directory (`private`), including same-stem companions in the same directory. It SHALL NOT delete anything.

#### Scenario: Partner-shared photo
- **WHEN** a photo's sidecar has `fromPartnerSharing: true`
- **THEN** its asset has verdict `exclude` and no stage, local or hosted, processes it

#### Scenario: Locked Folder photo
- **WHEN** a photo's sidecar has `inLockedFolder: true`, or it is under `Locked Folder/`
- **THEN** its asset has verdict `private`: local stages may run, hosted sends and Claude viewing are refused

#### Scenario: A flagged sidecar matches no photo
- **WHEN** a sidecar carries either flag but no media file in its directory matches it
- **THEN** the command writes the verdicts it could match, reports the unmatched count, and exits non-zero
