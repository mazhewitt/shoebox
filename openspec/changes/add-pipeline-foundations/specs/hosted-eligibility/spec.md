# Spec Delta

## Purpose

Decides which assets and captures may be sent to a hosted model or viewed by Claude. The decision is taken across every copy and capture member in every configured source, not just the file at hand.

## ADDED Requirements

### Requirement: Eligibility considers every copy across all sources
An asset SHALL be refused for a hosted provider if **any** copy of it, by identical bytes or identical pixels, is in any of these places:
- a source without that provider's consent;
- under a held-back path;
- on the never-send list;
- in quarantine.

The refusal SHALL name the reason and the restricting source.

#### Scenario: Copy in a non-consented source
- **WHEN** an asset is in the owner's library and an identical copy is in a source whose person has not consented
- **THEN** the asset is refused for every hosted provider, with reason `copy_restricted` naming that source

#### Scenario: Copy under a held-back path
- **WHEN** one copy of an asset is under a held-back path and another is not
- **THEN** both copies are refused

#### Scenario: Only consented copies
- **WHEN** every copy of an asset is in a consented source and on no list
- **THEN** the asset is eligible

### Requirement: Missing inventory fails closed
If any configured source has no inventory newer than its configuration entry, every hosted send SHALL be refused with an error naming that source.

#### Scenario: Source configured but not inventoried
- **WHEN** a new source is added to the configuration and no inventory exists for it
- **THEN** every hosted run refuses to start, and names the source

### Requirement: A capture is eligible only if every member is
A capture SHALL be eligible for hosted processing only when every member asset is eligible.

#### Scenario: RAW+JPEG pair with a restricted member
- **WHEN** a capture's RAW member is eligible and its JPEG member is refused
- **THEN** the capture is not eligible, and is tagged for local captioning

### Requirement: Privacy check reports refusals by reason and source
The privacy check SHALL report refused counts per reason and per restricting source for a given provider. It SHALL report counts only.

#### Scenario: Report counts
- **WHEN** the privacy check runs for a provider
- **THEN** it prints refused counts by reason (including `copy_restricted` and `quarantined`) and by source, and no paths
