# Spec Delta

## Purpose

Runs candidates over ground-truth sets and grades each item, using Microsoft Vally as the per-item run and grade layer, with results that can be re-graded without new inference.

## ADDED Requirements

### Requirement: Eval specs generated from ground truth
Each eval's Vally spec SHALL be generated from a frozen ground-truth set version and never edited by hand. It holds:
- one stimulus per asset, or per query for search;
- the derived image as an attachment;
- the strata, split and hard-case flags as tags;
- the expected label in the grader config.

#### Scenario: Regenerate after new set version
- **WHEN** a new set version is frozen
- **THEN** regenerating its spec produces stimuli whose names are the asset IDs, and a run records the set version

### Requirement: Replay and live execution
The executor SHALL support two modes:
- **replay** reads outputs from the keyed output store and fails loudly on a missing key;
- **live** calls the model through the label runner and writes to the same store.

Gate runs SHALL use replay.

#### Scenario: Missing output in replay
- **WHEN** a gate run in replay mode meets an asset with no stored output for the model and params
- **THEN** that trial errors, and the run is not eligible to pass a gate

#### Scenario: Live writes through
- **WHEN** a live run labels an asset
- **THEN** the output is stored under its key, and a later replay run returns the same output

### Requirement: Per-item graders
The eval layer SHALL provide graders that record predicted and expected values on every trial:
- single-field match (with abstain handling);
- tag-set match (per-tag true and false positives);
- retrieval (ranked IDs and per-query metrics).

#### Scenario: Abstain is recorded, not failed silently
- **WHEN** a model abstains on a field
- **THEN** the trial records `predicted=abstain`, and reports count it toward coverage, not precision

### Requirement: Re-grading without inference
Saved trial results SHALL be re-gradable against an updated spec without calling any model.

#### Scenario: Label fixed after a run
- **WHEN** a ground-truth label is corrected and the spec regenerated
- **THEN** re-grading the saved results changes that trial's verdict, and no model call is made

### Requirement: Multi-candidate runs
One command SHALL run several candidates over the same set and split, with run-level parameters. Each trial SHALL be attributable to its model_id and params_hash.

#### Scenario: Head-to-head run
- **WHEN** the colour eval is run for the rules baseline, E4B, 12B and Gemini on the test split
- **THEN** results hold one trial per asset per candidate, each tagged with its model_id and params_hash

### Requirement: Eval layer is private and pinned
The eval layer SHALL run with telemetry disabled. Any LLM-judge grader SHALL use an explicitly configured endpoint, never a default. The tool's version SHALL be pinned exactly.

#### Scenario: Judge without endpoint
- **WHEN** a judge grader is configured without an explicit endpoint
- **THEN** spec generation or lint fails before any call is made

### Requirement: Captions judged against the image
Caption quality SHALL be scored by a judge that sees the image and the caption together (Claude Opus as the reference auditor), with an owner spot-check. Candidates' captions SHALL be shown without model names and in random order. A text-only judge is limited to criteria that don't need the image.

#### Scenario: Blind caption audit
- **WHEN** Opus or the owner rates captions for an image
- **THEN** the candidates' captions are shown in random order without model names, and each rating records the wrong or invented facts and the missing searchable facts, with provenance
