# Spec Delta

## Purpose

Groups the library's files into assets and captures, so each moment is captioned once and returned once by search. Copies are found across every source, and nothing is ever deleted.

## ADDED Requirements

### Requirement: Pixel hash per asset
Every derived asset SHALL have a pixel hash computed from its decoded, orientation-normalised, full-resolution pixels, with the hash version recorded. RAW and layered files SHALL be hashed from their rendered image and marked as rendered.

#### Scenario: Metadata-only difference
- **WHEN** two files hold the same JPEG image with different or stripped EXIF
- **THEN** they have different asset ids and the same pixel hash

#### Scenario: Orientation flag variant
- **WHEN** one file is stored rotated with an orientation flag and another holds the same image stored upright
- **THEN** their pixel hashes match

#### Scenario: Re-encoded copy does not match
- **WHEN** a JPEG is re-encoded at a different quality
- **THEN** its pixel hash differs from the original's

### Requirement: Exact and same-pixel groups across sources
Files with identical bytes SHALL be one asset at several locations. Assets with identical pixel hashes SHALL be linked in a `same_pixels` group. Groups SHALL span sources. Each group SHALL have a stable id derived from its members.

#### Scenario: Group spans sources
- **WHEN** identical files sit in two different sources
- **THEN** they resolve to one asset, listing both locations

### Requirement: Capture grouping by conservative, versioned rules
Assets SHALL be grouped into captures by named rules: RAW+JPEG, scan export, Live Photo, Motion Photo, edited copy, and burst. Each group SHALL record its rule, the rule version and each member's role. When a rule is unsure, the assets SHALL stay apart.

#### Scenario: RAW+JPEG pair
- **WHEN** a NEF and a JPG with the same stem are in the same folder
- **THEN** they form one capture, with roles `raw` and `primary`

#### Scenario: Same stem in different folders
- **WHEN** two files share a stem but sit in different folders
- **THEN** they are not grouped

#### Scenario: Edited copy without its original
- **WHEN** an `-edited` file's original is not present
- **THEN** the edited file is its own capture, and is not grouped with an unrelated file

#### Scenario: Burst is one capture
- **WHEN** burst members and a cover are present
- **THEN** they form one capture, with the cover as primary

### Requirement: One canonical member per capture
Each capture SHALL have exactly one canonical member, chosen by a fixed, documented rule order. The reason for the choice SHALL be recorded. The same inputs SHALL always give the same choice.

#### Scenario: Deterministic choice
- **WHEN** canonical selection is run twice on the same groups
- **THEN** it picks the same member for every capture, with the same reason

#### Scenario: Consented copy preferred
- **WHEN** a capture has members in a consented and a non-consented source
- **THEN** the canonical member is the consented one

### Requirement: Caption-once worklist with a cost re-quote
The dedup step SHALL produce a worklist with one row per capture, giving:
- the canonical asset;
- hosted eligibility;
- the group size.

It SHALL record the dedup version and the set of sources it covers. A summary SHALL report files → assets → captures → eligible captures, and the re-quoted cost.

#### Scenario: Worklist summary
- **WHEN** a worklist is built
- **THEN** its summary gives each count and the cost at the day's batch price, and contains no paths
