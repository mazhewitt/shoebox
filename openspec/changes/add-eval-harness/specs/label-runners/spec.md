# Spec Delta

## Purpose

Runs each candidate labeller or embedder over a list of assets and stores keyed outputs that both evals and the later bulk job consume, with safeguards on hosted calls.

## ADDED Requirements

### Requirement: One runner interface
Every candidate SHALL run as a batch process. It SHALL read asset requests as JSONL and emit one JSONL result per asset. The candidates are:
- the VLM labeller (hosted or local);
- the embedder;
- the rule-based baseline.

The same runner and parameters SHALL serve eval runs and bulk runs.

#### Scenario: Same runner for eval and bulk
- **WHEN** the bulk job later labels an asset that was in an eval set with the same model_id and params
- **THEN** it uses the stored output rather than re-running, and the output equals what the eval scored

### Requirement: Keyed, immutable outputs
Every output SHALL be stored keyed by `(asset_id, model_id, params_hash)`. The params hash SHALL cover:
- the prompt;
- the output schema;
- the image profile;
- the image-token budget;
- the thinking level;
- quantisation;
- runtime version.

An existing key SHALL NOT be overwritten.

#### Scenario: Re-run is a cache hit
- **WHEN** a runner is asked for a key that already exists
- **THEN** it returns the stored output without calling the model

#### Scenario: Param change is a new key
- **WHEN** the prompt or image budget changes
- **THEN** outputs are written under a new params_hash, and old outputs remain

### Requirement: Structured label output
The VLM labeller SHALL return JSON valid against a versioned schema. The schema covers:
- caption;
- scene tags from the controlled vocabulary;
- free-form tags;
- capture medium;
- digitiser;
- colour class (`colour`, `mono`, `toned`);
- a per-field confidence or abstain value.

Output that fails the schema SHALL be stored as a failure, never coerced.

#### Scenario: Invalid JSON
- **WHEN** a model returns output that fails the schema
- **THEN** the result is stored with `status=invalid` and the raw text, and it counts as wrong, not missing, in evals

### Requirement: Embedding output
The embedder SHALL store, per asset, an image embedding and, when a caption exists, a caption-text embedding in the same space. Each SHALL record its dimension and the model's precision. Query embeddings SHALL use the same model and precision.

#### Scenario: Precision guard
- **WHEN** the embedder is configured to use float16 for EmbeddingGemma 2
- **THEN** it refuses to run

### Requirement: Usage and timing recorded
Every output SHALL record:
- input, output and thinking tokens where the model reports them;
- wall-clock latency;
- cost in USD for hosted calls;
- the machine and runtime version.

#### Scenario: Throughput derivable
- **WHEN** a run over a sample finishes
- **THEN** images per hour and USD per 1,000 images can be computed from stored outputs alone

### Requirement: Hosted-call privacy safeguards
Hosted calls SHALL go only through the configured billing-linked project. They SHALL:
- disable stored interactions;
- use no grounding tools;
- delete uploaded files and batch jobs after results are fetched.

Before a run, the runner SHALL check a recorded confirmation that project logging and dataset sharing are off.

#### Scenario: Missing privacy confirmation
- **WHEN** a hosted run starts and the privacy checklist confirmation is absent or older than the configured age
- **THEN** the run refuses to start and names the missing items

#### Scenario: Cleanup after batch
- **WHEN** a batch job's results have been downloaded and stored
- **THEN** the job and its uploaded input files are deleted, and the deletion is recorded

### Requirement: Hosted spend caps
Every hosted run SHALL have a USD cap. The runner SHALL estimate cost before starting and stop when actual spend reaches the cap. Cumulative hosted spend SHALL be tracked against the project budget of USD 150.

#### Scenario: Estimate over cap
- **WHEN** a run's pre-flight estimate exceeds its cap or the remaining project budget
- **THEN** it does not start, and it reports the estimate and the remaining budget

### Requirement: Rate-limit handling
Hosted runs SHALL respect the account's rate and batch-queue limits. They SHALL retry rate-limited requests with backoff and resume after interruption without repeating completed keys.

#### Scenario: Interrupted run
- **WHEN** a run is killed part-way and restarted
- **THEN** it continues from the first missing key, and no completed asset is billed twice

### Requirement: Rule-based baseline
A rule-based labeller SHALL produce capture medium, digitiser and colour class from file metadata and pixel statistics alone. It SHALL abstain when its rules are ambiguous. It is scored like any other candidate.

#### Scenario: Ambiguous case abstains
- **WHEN** a JPEG has no camera or scanner EXIF and no origin signal
- **THEN** the baseline outputs `unknown` for the digitiser, and does not guess `scan`

### Requirement: Never-send list and consent
Every source SHALL name the person it belongs to. Hosted calls SHALL skip any asset on the never-send list, and any asset from a source whose person has not consented to hosted processing. Consent defaults to "no" for everyone except the owner. Skipped assets SHALL be reported, not silently dropped.

#### Scenario: Source without consent
- **WHEN** a hosted run includes assets from Jude's, Kyra's or Toren's folders and that person's consent is not recorded
- **THEN** those assets are not sent, and the run report lists them as skipped for consent

#### Scenario: Copy in a consented source
- **WHEN** an asset with the same BLAKE3 also exists in a non-consented source
- **THEN** it is still not sent: an asset is eligible only if none of its copies is restricted
