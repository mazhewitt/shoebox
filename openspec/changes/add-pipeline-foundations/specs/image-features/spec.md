# Spec Delta

## Purpose

Gives every asset the local, pixel-derived features that search and dedup rely on. These are an EmbeddingGemma 2 image vector, and a black-and-white filter source that passes the precision gate.

## ADDED Requirements

### Requirement: Every derived image asset has an image vector
Every derived image asset SHALL have an image vector from the frozen search embedder, stored once per unique asset. A vector that fails the vector checks SHALL be recorded as an error, not stored as valid.

#### Scenario: Coverage
- **WHEN** the vector pass completes over a source
- **THEN** every derived image asset has a valid vector or a listed error

#### Scenario: Copies share a vector
- **WHEN** two files have identical bytes
- **THEN** one vector is computed and stored for their asset

### Requirement: Black-and-white filter source passes the precision gate
The black-and-white filter SHALL use only a source whose mono precision, against the reference, is at least the gated minimum in every stratum on the held-out test half. If no source passes, the filter SHALL be marked unavailable, with the reason. The choice, its thresholds and the default for toned images SHALL be recorded before the filter is used.

#### Scenario: Heuristic passes the gate
- **WHEN** the tuned pixel heuristic scores mono precision of at least 0.98 in every stratum on test
- **THEN** it is recorded as the filter source, with its frozen thresholds

#### Scenario: Nothing passes
- **WHEN** no source reaches the gate in some stratum
- **THEN** the filter is recorded as unavailable for that stratum, and no mono filter is offered there
