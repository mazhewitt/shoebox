# Spec Delta

## Purpose

Turns per-item eval results into per-stratum metrics, compares candidates, and decides pass/fail against thresholds fixed before the run, leaving a durable record of every run.

## ADDED Requirements

### Requirement: Thresholds fixed before the run
Gate thresholds SHALL live in `evals/gates.toml`, naming the metric, field or facet, stratum scope, threshold and set version. A gate run SHALL record the hash of that file. A run scored against thresholds changed after the run started SHALL NOT pass.

#### Scenario: Thresholds edited after run start
- **WHEN** `gates.toml` changes between a run's start and its report
- **THEN** the report marks the gate result invalid

### Requirement: Precision-first gates with coverage
Classification gates SHALL require precision at or above the threshold in every reported stratum, with a stratum-size floor. Coverage SHALL also be reported. The abstain cut-off SHALL be tuned on dev to minimise abstentions at that precision, then frozen for test.

#### Scenario: One weak stratum fails the gate
- **WHEN** overall precision is 0.99 but B&W film scans from the 2000s are at 0.95
- **THEN** the gate fails, and the report names that stratum

#### Scenario: Cut-off tuned on dev only
- **WHEN** a test-split gate run is reported
- **THEN** its abstain cut-off equals the one frozen from the dev split, and the test split was not used to choose it

### Requirement: Metrics reported per stratum
Reports SHALL give per-field precision, recall, coverage and confusion matrices, overall and per stratum (era, capture medium/digitiser, colour class, hard-case kind). They SHALL include a re-weighted estimate for the full archive using frame stratum sizes.

#### Scenario: Re-weighted estimate
- **WHEN** a report is produced
- **THEN** it shows both the raw sample metric and the frame-weighted estimate, with the weights used

### Requirement: Search metrics
Search evals SHALL report recall@k and nDCG@k per query and overall, plus pool coverage. Search is the guard eval: a candidate that wins a field eval but lowers search nDCG beyond the configured tolerance SHALL NOT be recommended.

#### Scenario: Field winner hurts search
- **WHEN** a candidate beats the baseline on tag precision but lowers search nDCG@20 beyond tolerance
- **THEN** the comparison marks it not recommended, and gives both deltas

### Requirement: Cost and throughput in every comparison
Comparisons SHALL show, per candidate:
- images per hour on the stated machine;
- USD per 1,000 images;
- projected wall-clock and cost for the full frame size.

#### Scenario: Projection for the bulk run
- **WHEN** a head-to-head report is produced
- **THEN** it projects each candidate's cost and time over the frame's image count at current prices, and states the price date

### Requirement: Head-to-head significance
Pairwise comparisons on the same items SHALL use a paired test, and SHALL say when a difference isn't significant at the sample size.

#### Scenario: Tie
- **WHEN** two candidates differ by less than the paired test can resolve
- **THEN** the report calls it a tie, and the recommendation falls back to cost, privacy and consistency

### Requirement: Durable run records
Every reported run SHALL write a record under `evals/runs/` naming:
- the set version and split;
- the candidates with model_id and params_hash;
- the gates file hash;
- the metrics and pass/fail;
- the spend.

Records SHALL be committed to git.

#### Scenario: Reproduce a decision
- **WHEN** someone later asks why a model was chosen
- **THEN** the run record and set version identify the exact items, outputs and thresholds behind the decision

### Requirement: Mixed-model seam is measured
If the recommended bulk labeller differs from the model planned for incremental labelling of new photos, the comparison SHALL report:
- per-field agreement between the two on the test split;
- the search-metric change when their outputs are mixed in one index.

#### Scenario: Hosted bulk, local incremental
- **WHEN** Gemini is recommended for the bulk run and a local Gemma model for new photos
- **THEN** the report shows their per-field agreement and the nDCG of a mixed index against a single-model index, so the seam is a visible, accepted trade-off
