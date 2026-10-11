# Spec Delta

## Purpose

Defines which files the inventory counts as source media, so derived caches made by phones, iPods and photo software are never catalogued, captioned or searched as photos.

## ADDED Requirements

### Requirement: Derived-cache folders are skipped with a named reason
The inventory SHALL skip folders that hold only derived copies, each rule with its own named reason. These include:
- Android `.thumbnails`;
- iPod/iTunes photo caches;
- Lightroom preview folders (`*.lrdata`).

Skip counts and examples SHALL be reported per reason. Folders SHALL NOT be skipped on image size alone.

#### Scenario: Android thumbnail cache skipped
- **WHEN** the inventory meets a `.thumbnails` folder
- **THEN** its files are not in the frame, and the frame's skip report counts them under the `.thumbnails` reason

#### Scenario: Owner data next to caches is kept
- **WHEN** a folder holds a Lightroom catalogue (`.lrcat`) beside a `Previews.lrdata` folder, or holds Picasa `.ini` files
- **THEN** the catalogue and the `.ini` files are recorded, and only the `.lrdata` folder is skipped

#### Scenario: Small images are not skipped
- **WHEN** a folder that is not a cache holds images under 256 px
- **THEN** those images are in the frame

### Requirement: New skip rules change nothing else
Re-running the inventory with new skip rules SHALL produce a frame that differs from the previous one only by the newly skipped paths.

#### Scenario: Frame diff
- **WHEN** the inventory is re-run after adding skip rules
- **THEN** every removed path carries a new skip reason, and no other row changes
