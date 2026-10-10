# Spec Delta

## Purpose

Provides the frozen, representative, human-confirmed ground truth that every labelling and search model is scored against, plus the sampling frame it is drawn from.

## ADDED Requirements

### Requirement: Sampling frame
The system SHALL build a read-only inventory of every file in the in-scope sources. Each record SHALL carry the asset ID (BLAKE3 of the original bytes), the source and path, file type, pixel dimensions, bit depth, the EXIF make/model/software, the best available date with its kind, and computed colour statistics. Building it SHALL NOT write to originals.

#### Scenario: Inventory over all sources
- **WHEN** the inventory runs over every configured source (the NAS photo shares and landed Takeout exports)
- **THEN** every image file appears exactly once per path, and each record has its asset ID, source, the source's person, and strata fields, or an explicit `unknown`

#### Scenario: Same bytes in several sources
- **WHEN** one file's bytes exist in several sources or folders
- **THEN** the frame keeps one record per path, all sharing one asset ID, and the frame README reports the cross-source duplicate count and bytes

#### Scenario: Originals untouched
- **WHEN** the inventory completes
- **THEN** no file under any source root has a changed mtime, size or content hash, and no file or folder was created in a source root

### Requirement: Sibling groups in the frame
The frame SHALL group files that are versions of one capture: same-folder RAW+JPEG pairs (NEF/DNG with a JPG of the same base name), scan TIFFs with their exported JPG/PSD, and edits named after their original. Each group SHALL record the rule that formed it.

#### Scenario: Leica DNG+JPG pair
- **WHEN** a folder holds `L1002656.DNG` and `L1002656.JPG`
- **THEN** both records share a capture-group ID with rule `raw_jpeg_basename`

### Requirement: Synced sources are never written
Sources synced to a cloud service, such as the Drive-synced `ImportantFiles` share, SHALL be read through a read-only path. No Shoebox process SHALL create, modify or delete anything under them, including temporary or index files.

#### Scenario: Writable mount refused
- **WHEN** a Shoebox command is pointed at a source root that is writable by the current process
- **THEN** it refuses to run and names the root, unless the source is explicitly marked as not synced

### Requirement: Stratified, diverse sampling
Ground-truth sets SHALL be drawn from the sampling frame stratified by era, capture medium/digitiser guess and colour/mono guess, with a minimum per cell. Within a cell, the draw SHALL spread across visual-embedding clusters. Hard cases SHALL be added deliberately and flagged.

#### Scenario: Small strata are represented
- **WHEN** a set is drawn
- **THEN** every non-empty stratum cell holds at least the configured minimum (or all of its members, if fewer), and the set's README records the cell counts and the frame size per cell

#### Scenario: Hard cases flagged
- **WHEN** a hard case (toned or sepia print, faded colour print, screenshot, messaging-app image, unprocessed raw scan) is added outside the stratified draw
- **THEN** it carries a `hard` flag naming its kind, and reports can include or exclude it

### Requirement: Frozen dev/test splits
Every set SHALL be split into dev and test at creation, by asset and deterministically from a recorded seed. Members of one duplicate or capture group SHALL land in the same split. Once a set version is frozen, its membership and labels SHALL NOT change; corrections produce a new version.

#### Scenario: No leakage across splits
- **WHEN** two files share a capture or exact-duplicate group
- **THEN** both are in the same split

#### Scenario: Correction makes a new version
- **WHEN** a label in a frozen set is corrected
- **THEN** a new set version is written, the old version stays readable, and run records name the version they used

### Requirement: Labelling with pre-labels and an anchoring check
Pre-labels from a hosted model MAY be shown to the owner for confirmation, but only for capture medium/digitiser, colour/mono, scene tags and caption quality. A blind slice of at least 10% of each set SHALL be labelled without the pre-label, so anchoring can be measured.

#### Scenario: Blind slice
- **WHEN** a set is labelled with pre-labels
- **THEN** at least 10% of its items, chosen at random before labelling, are labelled with no pre-label visible, and the report states the agreement rate between pre-labels and blind labels

#### Scenario: Anchoring too high
- **WHEN** the owner's agreement with pre-labels on the shown items exceeds agreement on the blind slice by more than the configured margin
- **THEN** that set is flagged, and its remaining items are labelled blind

#### Scenario: Never pre-label identity
- **WHEN** a set concerns faces or identity
- **THEN** no hosted pre-label is requested

### Requirement: Label provenance
Every ground-truth label SHALL record who set it, when, and how: blind, confirmed from a pre-label (naming the pre-label's model_id and params_hash), or edited from a pre-label. A confirmed label with no named source SHALL be rejected.

#### Scenario: Confirmed label
- **WHEN** the owner accepts a Gemini pre-label unchanged
- **THEN** the stored label records `labelled_by=owner`, `method=confirmed`, and the pre-label's model_id and params_hash

### Requirement: Search query set with pooled judgments
The search eval SHALL use a fixed set of natural-language queries, including combined facet queries. Relevance SHALL be judged by the owner over a pool built from the top-k results of every candidate system. Unjudged results SHALL be counted as non-relevant and reported as a pool-coverage figure.

#### Scenario: New candidate after judging
- **WHEN** a candidate added after judging returns unjudged images in its top-k
- **THEN** the report shows its unjudged fraction, and those images are queued for judgment before that candidate can pass a gate

### Requirement: Ground truth is versioned and backed up
Ground-truth sets, queries, judgments and the vocabulary SHALL live in git with the code. They SHALL NOT contain image bytes. They SHALL reach the off-site backup with the next push.

#### Scenario: Labels survive loss of the laptop
- **WHEN** the laptop is lost after labels were committed and pushed
- **THEN** every set version can be restored from the git remote, keyed by asset ID

### Requirement: Labelling budget
The sets in this change SHALL fit a planned owner-labelling budget, recorded per set, and SHALL report labels spent against it. The total across the project SHALL stay within about 3,000 items unless the owner raises it.

#### Scenario: Budget report
- **WHEN** a set's labelling session ends
- **THEN** the set's README shows labels done vs planned, and the project total
