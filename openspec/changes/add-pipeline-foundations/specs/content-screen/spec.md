# Spec Delta

## Purpose

Keeps nude or explicit photos that got into the library by mistake out of every hosted model, every Claude-viewing path and the family view, until the owner has ruled on each one.

## ADDED Requirements

### Requirement: Local nudity screen on every derived asset
Every derived asset SHALL be screened by a local classifier before any stage that sends, shows or captions it. The screen SHALL run without network access. It SHALL store only a yes/no result per asset, keyed by asset and candidate parameters, including the threshold.

#### Scenario: Screen stores a boolean only
- **WHEN** an asset is screened
- **THEN** its record holds `sensitive` true or false, with no score, labels or region data

#### Scenario: No network during screening
- **WHEN** the screen runs with network access blocked
- **THEN** it completes, and makes no network request

#### Scenario: Threshold change re-runs the screen
- **WHEN** the screen's threshold changes
- **THEN** the screen runs again for every asset, under a new key

### Requirement: Flagged assets are quarantined until the owner rules
An asset the screen flags SHALL get a quarantine record with verdict `pending`. While `pending`, it SHALL be refused by every hosted runner, every path that shows images to Claude, eval-set draws and local caption stages. The quarantine SHALL apply to every asset in the flagged asset's dedup group.

#### Scenario: Pending asset refused for hosted captioning
- **WHEN** a hosted caption run is asked to process a `pending` asset
- **THEN** the asset is refused with reason `quarantined`, and no request is made

#### Scenario: Copy of a flagged asset is refused
- **WHEN** a same-pixel copy of a flagged asset sits in another folder or source
- **THEN** that copy is refused, just as the flagged asset is

#### Scenario: Pending asset excluded from contact sheets
- **WHEN** a contact sheet or Claude review packet is built over a set containing a `pending` asset
- **THEN** that asset is left out, and the count left out is reported

### Requirement: Owner verdicts decide where a flagged asset may go
The owner SHALL rule on each flagged asset as `include`, `private` or `exclude`. The verdict SHALL be stored as a human label with who ruled and when.
- **`include`:** the asset proceeds normally.
- **`private`:** local processing only, never hosted.
- **`exclude`:** left out of the catalogue. The original is not touched.

#### Scenario: Include releases the asset
- **WHEN** the owner marks a flagged asset `include`
- **THEN** it is eligible for every stage, exactly as if it had never been flagged

#### Scenario: Private stays local
- **WHEN** an asset is `private`
- **THEN** local captioning and embedding accept it, and every hosted runner refuses it

#### Scenario: Exclude never re-flags or re-sends
- **WHEN** an `exclude` asset is seen again by a later screen run or another source
- **THEN** it is not re-queued for review, and it stays refused everywhere

### Requirement: Review happens on a local page only the owner sees
Flagged assets SHALL be reviewed on an offline page generated into a non-synced local folder. Thumbnails SHALL be blurred until clicked. Building the page SHALL report counts only. Answers SHALL be ingested only when they match the page they came from.

#### Scenario: Answers from a different page are refused
- **WHEN** an answers file whose page id doesn't match the current review page is ingested
- **THEN** ingest refuses it and changes no verdict

#### Scenario: Page is never in a synced or repo location
- **WHEN** the review page is built with an output folder under the repo, Desktop or Documents
- **THEN** the build refuses

### Requirement: Retro-check of past hosted sends
The screen SHALL be runnable over every asset already sent to a hosted model or shown to Claude. The number flagged SHALL be reported to the owner as a count only.

#### Scenario: Past sends screened
- **WHEN** the retro-check runs
- **THEN** it screens the union of assets in all hosted-run and Claude-viewing records, and prints the flagged count
