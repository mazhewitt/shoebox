# Spec Delta

## Purpose

Runs captioning over the library from a dedup worklist: one caption per capture, never re-sending what is already labelled. It uses Gemini through the Batch API, with a local fallback for refused images and a measured rule for tiny ones.

## ADDED Requirements

### Requirement: Caption runs take a worklist, not raw files
Bulk caption runs SHALL accept only a dedup worklist. They SHALL refuse:
- an inventory frame or a raw item list;
- a worklist that doesn't cover every configured source, unless a partial-sources reason is given and recorded.

Eval-set runs are exempt.

#### Scenario: Frame refused
- **WHEN** a bulk caption run is given an inventory frame
- **THEN** it refuses before making any request

#### Scenario: Partial worklist needs a reason
- **WHEN** a worklist is missing a configured source and no partial-sources reason is given
- **THEN** the run refuses, and names the missing source

### Requirement: Existing labels are reused, never re-sent
Before any request, every capture SHALL be checked for an existing label in the candidate's resolved label sources, across all its members. Labelled captures SHALL be skipped and counted.

#### Scenario: New copy of a captioned photo
- **WHEN** a later source adds a byte-identical copy of an already-captioned asset
- **THEN** the run makes no request for it, and counts it as reused

### Requirement: Gemini batch runs keep every sync-runner guard
A batch caption run SHALL apply every guard of the synchronous hosted runner before submitting:
- the privacy checklist;
- hosted eligibility;
- derived-image checks;
- the current price;
- the per-run cap;
- the project budget.

It SHALL log actual spend for each completed job.

#### Scenario: Over-cap estimate
- **WHEN** the batch-price estimate for a job would exceed the run cap or the remaining budget
- **THEN** the job is not submitted

#### Scenario: Spend logged per job
- **WHEN** a batch job's results are downloaded
- **THEN** its actual cost and item count are appended to the spend log before the labels are stored

### Requirement: Batch runs resume without resubmitting
Every submitted batch job SHALL be recorded with its items and state. A restarted run SHALL poll open jobs and SHALL NOT resubmit an item that is in an open or finished job. Items of expired or failed jobs SHALL return to pending.

#### Scenario: Crash while polling
- **WHEN** the runner stops while a job is running and is started again
- **THEN** it polls that job, and submits no new request for its items

### Requirement: Batch labels are stored as their own candidate
Batch labels SHALL be stored under a candidate key distinct from the synchronous eval candidate. They SHALL be validated and normalised exactly as synchronous labels are.

#### Scenario: Same parse as sync
- **WHEN** the same model response is parsed by the batch and the sync runner
- **THEN** the stored labels are equal, and only the candidate key differs

### Requirement: Refused and unrecovered images get a local caption
A final label that is invalid because the hosted model refused it, or because it was truncated and its retry failed, SHALL be captioned by the local fallback candidate. Label resolution SHALL use an ordered list of sources and keep the model that produced each resolved label. Other failures SHALL NOT be routed; they are reported with their reason.

#### Scenario: Safety refusal
- **WHEN** Gemini refuses an image with `PROHIBITED_CONTENT`
- **THEN** the image gets a 12B label, and its resolved label names the 12B model

#### Scenario: Fallback is idempotent
- **WHEN** the fallback runs twice
- **THEN** the second run makes no requests

### Requirement: Tiny images follow a measured rule
Images below the measured size threshold SHALL be handled by one recorded rule, implemented as a candidate or profile rule: no change, upscale, route to another captioner, or caption-skip. The rule and its threshold SHALL come from the comparison of stored outputs on the eval's tiny images.

#### Scenario: Rule applied consistently
- **WHEN** a tiny image is captioned in a bulk or incremental run
- **THEN** the recorded rule is applied, and the record says so
