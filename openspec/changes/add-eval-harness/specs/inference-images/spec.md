# Spec Delta

## Purpose

Produces the model-input copies of originals (converted, colour-managed, resized) so every model, hosted or local, sees identical bytes and no original is ever sent or modified.

## ADDED Requirements

### Requirement: Derived inference images
For each asset that a run needs, the system SHALL produce a derived inference image:
- 8-bit sRGB JPEG;
- correct orientation applied;
- long edge sized for an image profile;
- metadata stripped.

Input formats SHALL include JPEG, PNG, HEIC/HEIF, WebP, 8/16-bit TIFF (colour and greyscale), camera raw (NEF, DNG) and layered PSD (flattened composite).

#### Scenario: 16-bit greyscale TIFF scan
- **WHEN** a 16-bit greyscale TIFF is derived
- **THEN** the output is an 8-bit sRGB JPEG at the profile's size, with tonal range mapped and not clipped beyond the profile's tolerance

#### Scenario: Camera raw
- **WHEN** a NEF or DNG is derived
- **THEN** it is rendered with a fixed, recorded raw-development setting, and the derived image records that it came from a raw rendering, not the camera's JPEG

#### Scenario: Unsupported or unreadable file
- **WHEN** an original can't be decoded
- **THEN** no derived image is written, and the asset is listed with the decode error rather than skipped silently

### Requirement: Orientation of untagged scans
Each scan's orientation SHALL be measured before any rotation is applied: every captioner's `label-v2` output gives a `rotate_cw` value, scored against the Opus reference on the eval scans. A rotation SHALL be applied at derive time only by a method whose precision on upright scans is at least 0.98. Such a pass SHALL record its rotation per asset in the derive index and SHALL belong to a new profile version, so outputs from the old and new images never mix under one key.

#### Scenario: No method precise enough
- **WHEN** no orientation source reaches the precision bar
- **THEN** derived images are not rotated, and the `rotate_cw` values stay in the label records as un-gated data

#### Scenario: Sideways film scan under a passing method
- **WHEN** a method has passed and a film-scan TIFF with no orientation tag is stored rotated 90°
- **THEN** the derived image under the new profile is upright, the derive index records the rotation and the method, and the old profile's image is left unchanged
### Requirement: Named, versioned image profiles
Every derived image SHALL belong to a named profile that fixes format, maximum long edge, quality and conversion settings. A profile change SHALL create a new profile name. Model outputs SHALL record the profile they were run on.

#### Scenario: Profile in output key
- **WHEN** a model output is stored
- **THEN** its params include the image profile name, so outputs from different profiles never mix

### Requirement: Originals are never modified or sent
Derivation SHALL read originals only. Hosted model calls SHALL send derived images, never originals.

#### Scenario: Hosted call input
- **WHEN** an image is sent to a hosted model
- **THEN** the bytes sent are a derived inference image with no EXIF, GPS or XMP

### Requirement: Derived images are regenerable and placed outside originals
Derived images SHALL live under the `derived/` tree of a dedicated, non-synced Shoebox share on the NAS, keyed by profile and asset ID. They SHALL be excluded from the off-site backup and SHALL never be written into a synced source. Deleting them SHALL lose nothing that can't be regenerated.

#### Scenario: Regeneration is identical
- **WHEN** a derived image is deleted and regenerated with the same profile and tool versions
- **THEN** the new file's content hash matches the recorded one
