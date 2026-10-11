# Spec Delta

## Purpose

Provides the sampling frame, the eval corpora drawn from it, and the frozen reference that every captioning and search candidate is scored against. The reference is frontier-model-authored (captions, structured fields, queries, relevance judgments), audited by Claude Opus and spot-checked by the owner.

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

### Requirement: Eval corpora drawn from the frame
Eval corpora SHALL be drawn from the sampling frame by a recorded, reproducible rule (seed or deterministic id order). Each corpus SHALL have a README recording the rule, the size, the bucket and era counts, and every substitution. Previously used targets MAY be included and SHALL be flagged.

#### Scenario: Reproducible draw
- **WHEN** a corpus is rebuilt from the same frame and rule
- **THEN** it has the same members

#### Scenario: Substitution recorded
- **WHEN** a drawn photo is replaced (e.g. by the hosted-eligibility screen)
- **THEN** the README lists the removed and added asset ids and the reason

### Requirement: Hosted-eligibility screen
Before any photo in an eval corpus is sent to a hosted model, the corpus SHALL be screened. Photos SHALL be excluded if they:
- are on the never-send list;
- have a copy in a non-consented source;
- are under paths configured as held back (e.g. folders that look like another person's library);
- are flagged by a pre-screen as medical, identity or financial documents.

The screen's results SHALL be recorded.

#### Scenario: ID document in the owner's share
- **WHEN** a corpus draw includes a passport scan under `documents/`
- **THEN** it is excluded before any hosted call, replaced, and listed in the README

### Requirement: Frozen dev/test splits
Every set SHALL be split into dev and test at creation, deterministically from a recorded seed: query sets by query (grouped by source photo's capture group), item sets by asset. Members of one duplicate or capture group SHALL land in the same split. Once a set version is frozen, its membership and labels SHALL NOT change; corrections produce a new version.

#### Scenario: No leakage across splits
- **WHEN** two files share a capture or exact-duplicate group
- **THEN** both are in the same split

#### Scenario: Correction makes a new version
- **WHEN** a label in a frozen set is corrected
- **THEN** a new set version is written, the old version stays readable, and run records name the version they used

### Requirement: Frontier-model reference with audits
Reference captions and structured fields SHALL come from a named reference model and prompt (model_id and params_hash), or, for structured fields, from metadata rules where those are unambiguous. A stratified sample of the reference SHALL be audited by Claude Opus, and the owner SHALL check a smaller sample drawn from the audited set. The reports SHALL state the audit error rates, and the error rates SHALL feed the decision rule.

#### Scenario: Reference error rate known
- **WHEN** a report scores candidates against the reference
- **THEN** it shows the Opus-audited error rate of the reference itself and the owner–Opus agreement on the owner's sample

#### Scenario: Never identify people
- **WHEN** any hosted model writes a caption, query or judgment
- **THEN** it is not asked to recognise or name people from faces; names may appear only when taken from folder or file names

### Requirement: Synthetic query set
The search eval SHALL use a fixed, versioned query set written by a model that is not a candidate captioner (Claude Opus). The set SHALL include:
- known-item queries, written from a photo plus its metadata in a recorded style mix;
- category queries, written without viewing any photo.

Each query SHALL record its kind, style, source photo (if any) and the writer's model_id and params_hash. The owner's own notes MAY be included as a separately reported subset. The owner SHALL review a sample for realism, and the result SHALL be recorded.

#### Scenario: Results per style
- **WHEN** a search report is produced
- **THEN** metrics are shown overall, per query kind and per style, and the owner-notes subset separately

### Requirement: Pooled, graded relevance judgments
Relevance SHALL be judged over a pool of every candidate route's top-k results per query, on a graded scale (0, 1, 2). A judge model SHALL see the query and the images only, never captions. A stratified sample SHALL be re-judged by Claude Opus, and a smaller sample by the owner. Unjudged results SHALL count as non-relevant and be reported as pool coverage.

#### Scenario: Judge disagreement too high
- **WHEN** the weighted kappa between the judge model and Opus on the audit sample is below the configured floor
- **THEN** the test pool is re-judged by Opus before any gate is scored

#### Scenario: New route after judging
- **WHEN** a route added after judging returns unjudged images in its top-k
- **THEN** the report shows its unjudged fraction, and those images are queued for judgment before that route can pass a gate

### Requirement: Provenance of every reference item
Every reference item (caption, structured label, query or judgment) SHALL record who or what produced it and how:
- a model, with its model_id and params_hash;
- a metadata rule, with the rule name;
- an owner or Opus audit, with the item it audited.

An audit SHALL never overwrite the item it audited. Corrections SHALL produce a new set version.

#### Scenario: Owner corrects a judgment
- **WHEN** the owner disagrees with a Gemini judgment in their spot-check
- **THEN** the owner's judgment is stored alongside, with `labelled_by=owner`, and the next set version uses it

### Requirement: Ground truth is versioned and backed up
Reference sets, queries, judgments and audits SHALL live in git with the code. They SHALL NOT contain image bytes. They SHALL reach the off-site backup with the next push.

#### Scenario: Labels survive loss of the laptop
- **WHEN** the laptop is lost after labels were committed and pushed
- **THEN** every set version can be restored from the git remote, keyed by asset ID

### Requirement: Owner time budget
The owner's part in this change SHALL be limited to spot-checks: about 100 judgments, 30 captions, a realism review of 30 queries, and the residue of structured-field disagreements. The planned and actual counts SHALL be recorded in the set READMEs.

#### Scenario: Budget report
- **WHEN** a spot-check session ends
- **THEN** the set README shows items checked against the plan
