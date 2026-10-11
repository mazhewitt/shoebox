# Spec Delta

## Purpose

Turns per-item eval results into metrics, compares candidates with paired statistics, and applies a decision rule fixed before the test split is scored, leaving a durable record of every run.

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
Search evals SHALL report nDCG@k with graded relevance (primary: nDCG@20), plus recall@k, MRR and hit@k for known-item queries, and pool coverage. Results SHALL be shown per query kind and style. Search is the guard eval: a captioner that wins on a field but lowers search nDCG beyond the configured tolerance SHALL NOT be recommended.

#### Scenario: Field winner hurts search
- **WHEN** a candidate beats the reference on tag precision but lowers search nDCG@20 beyond tolerance
- **THEN** the comparison marks it not recommended, and gives both deltas

### Requirement: Pre-registered decision rule
`evals/gates.toml` SHALL hold the decision rule: the reference pipeline, the candidate set, the non-inferiority margin for search, the caption error-rate margin, the structured-field precision floors, and the cost tie-break. It SHALL be committed before the test split is scored. The report SHALL name the cheapest passing candidate, with its projected full-library hosted cost and local hours. If no local candidate passes, it SHALL say so.

#### Scenario: Cheapest non-inferior candidate chosen
- **WHEN** E4B with `label-v2` and the blend route has a lower 95% bound on (candidate − reference) test nDCG@20 above −0.03, and its audited caption error rate is within margin
- **THEN** it passes, and it is recommended over a passing 12B arm because its projected library time is lower

#### Scenario: "No captioner" is a candidate
- **WHEN** the image-plus-metadata routes without captions pass the search rule
- **THEN** the report shows them as a passing candidate with zero captioning cost, and the recommendation weighs that against the catalogue's need for descriptions

### Requirement: Cost and throughput in every comparison
Comparisons SHALL show, per candidate:
- images per hour on the stated machine;
- USD per 1,000 images;
- projected wall-clock and cost for the full frame size.

#### Scenario: Projection for the bulk run
- **WHEN** a head-to-head report is produced
- **THEN** it projects each candidate's cost and time over the frame's image count at current prices, and states the price date

### Requirement: Head-to-head significance
Pairwise comparisons on the same items or queries SHALL use a paired test: a paired bootstrap on the metric delta, with one resample index for both candidates, for search; McNemar for classification fields. Reports SHALL give the minimum detectable difference at the sample size, and SHALL say when a difference isn't significant.

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
