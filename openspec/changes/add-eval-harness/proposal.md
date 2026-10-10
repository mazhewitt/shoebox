# Proposal

## Why

The MVP is to sort, catalogue and describe every photo for search. Faces come later. "Evals before scale" means no labelling model runs over the archive until it has passed a gate on frozen ground truth. So the first thing to build is the harness that measures the candidates and picks the bulk labeller, not the bulk pipeline itself.

The candidates are now concrete and different in kind:
- **Gemini 3.8 Flash:** hosted, paid per token.
- **Gemma 4 E4B and 12B:** run locally on the M5 Max.
- **EmbeddingGemma 2:** a local multimodal embedding route for search.

Only a like-for-like eval can say which wins, or which combination. The owner also wants to learn Microsoft's **Vally**, so the harness uses it where it fits.

Background: `research/mvp-models.md`, `research/vally.md`, `research/hosted-gemma.md`, `handoff-supplement.md`.

## What Changes

- **Sampling frame.** A read-only inventory keyed by BLAKE3. It covers **all** the NAS photo shares and the first Takeout export. The shares are `ImportantFiles`, `Home Folders`, `Backup` and `Volume2`, about 880 GB of images and video. Duplicates across them are expected; they are grouped by hash, never deleted, and cleaned up later from a dedup report. `ImportantFiles` is the Drive-synced share: about 605 GB of film-scan TIFFs, Nikon NEF and Leica DNG raws, JPEGs and PSDs, including an old `Google Photos` year tree for 2004–2019. The frame records:
  - the cheap strata: era, file origin, file type/bit depth, EXIF camera/scanner, computed colour statistics;
  - sibling groups: RAW+JPEG, scan TIFF + exported JPG, PSD edits.

  Ground truth is sampled from it.
- **Ground-truth sets** for the MVP fields:
  - capture medium × digitiser and colour/mono, with deliberate hard cases;
  - scene tags from a controlled vocabulary;
  - caption quality;
  - a search query set with pooled relevance judgments.
- **Labelling protocol.** Sets are stratified with diversity sampling and frozen dev/test splits, inside a staged labelling budget. Gemini 3.8 Flash pre-labels, the owner confirms, and a ~10% blind slice measures anchoring. Every label records its provenance.
- **Inference images.** Derived JPEG copies of originals: converted from TIFF/HEIC, camera raws (NEF/DNG) and layered PSDs, colour-managed, 8-bit, resized to the largest image-token budget under test. Stored on a dedicated `shoebox` NAS share, never on `ImportantFiles`. Originals are never touched.
- **Label runners.** Python batch runners with one interface (JSONL in/out) that write outputs keyed by `(asset_id, model_id, params_hash)`:
  - a VLM labeller for Gemini 3.8 Flash and for Gemma 4 E4B/12B via mlx-vlm;
  - an EmbeddingGemma 2 embedder;
  - a rule-based baseline for capture source and colour/mono.

  The bulk job will reuse the same runners later.
- **Per-person consent.** Every source names its person (owner, Jude, Kyra, Toren). Only the owner's sources may be sent to Gemini until another person's consent is recorded.
- **Hosted-call safeguards.** Gemini runs only through the billing-linked project, with the privacy checklist enforced: no logging, no stored interactions, no grounding, uploads and batch jobs deleted after use. There is a per-run USD cap, and everything runs within the $150 hosted budget.
- **Vally execution layer:**
  - Generated `eval.yaml` per set (one stimulus per asset, strata as tags).
  - A thin TypeScript `shoebox-vlm` executor that replays outputs from the keyed store (gate runs) or calls models live (dev exploration).
  - Field, tag-set and retrieval graders.
  - The Vally version is pinned and its telemetry is off.
- **Python reporting and gates:**
  - per-stratum precision/recall and confusion matrices;
  - coverage at an abstain threshold;
  - retrieval recall@k/nDCG;
  - cost and throughput;
  - McNemar head-to-heads;
  - a check against thresholds fixed in `evals/gates.toml` before the test-split run.

  Each run writes a record under `evals/runs/`.
- **Head-to-head and throughput benchmark** on the same sample:
  - rules vs E4B vs 12B vs Gemini for structured fields;
  - captions and tags;
  - image budgets 280 vs 560 (1120 where it pays);
  - EmbeddingGemma 2 image-only vs image+caption search.

  The result is a written recommendation for the bulk labeller, with images/hour and projected cost.

Out of scope:
- faces;
- the catalogue DB schema, API and UI;
- the bulk run itself;
- dedup (its eval comes with the dedup change);
- full Takeout acquisition and scheduling, beyond landing one export for the sampling frame;
- importing the 13 Lightroom catalogues (keywords, ratings, collections) as an owner-authored metadata source. That is a follow-up change.

## Capabilities

### New Capabilities
- `eval-ground-truth`: the sampling frame, stratified and diversity sampling, frozen dev/test splits, the labelling protocol (pre-label + confirm + blind slice), label provenance, and storage/backup of ground truth.
- `inference-images`: derived model-input images (format conversion, colour management, resizing, layout on the NAS), regenerable and never written over originals.
- `label-runners`: candidate labelling and embedding runners with one JSONL interface; keyed, re-usable outputs; hosted-call privacy and budget safeguards.
- `eval-execution`: running candidates over ground-truth sets with Vally (spec generation, replay/live executor, graders, re-grading).
- `eval-gates`: metrics, per-stratum reporting, gate thresholds in `evals/gates.toml`, run records, and model comparison.

### Modified Capabilities

None. There are no existing specs.

## Impact

- **New code:**
  - `shoebox` Python package (uv) with `inventory`, `derive`, `label` runners and `eval gen` / `eval report` commands;
  - `evals/vally/` TypeScript plugins (Node ≥ 22.14, pinned `@microsoft/vally*`);
  - `evals/sets/`, `evals/gates.toml`, `evals/runs/` in git.
- **Systems:**
  - NAS shares mounted on the M5 Max: `ImportantFiles` read-only (it syncs to Google Drive, so Shoebox must never write to it), plus a new `shoebox` share for staging, Takeout originals, `derived/` and `results/`;
  - Ollama/mlx-vlm on the laptop;
  - Gemini API on the owner's individual billed project.
- **Constraint amendments:** `openspec/config.yaml` was updated on 2026-10-10 to match the owner's decisions:
  - hosted Gemini under conditions;
  - the laptop, then the M5 Ultra Studio, as the model host;
  - TypeScript Vally plugins;
  - the never-write rule for synced sources.
- **Cost:** hosted spend for pre-labelling and the head-to-head is estimated at well under $30 of the $150 budget (see design).
- **Owner time:** labelling about 1,000–1,500 items in this change, from the ~3,000 total budget.
