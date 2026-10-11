# Proposal

## Why

The MVP is to sort, catalogue and describe every photo for search. Faces come later. "Evals before scale" means no labelling model runs over the archive until it has passed a gate. So the first thing to build is the harness that measures the candidates and picks the bulk captioner and the search method, not the bulk pipeline itself.

**Replanned on 2026-10-11.** The first plan scored models against owner-written ground truth: about 700 owner labels, a scene vocabulary and 50 owner-written queries. The pilots showed that this is slow and a weak baseline.
- The owner's 39 search notes were written while looking at each photo. They are short and generic ("Portrait", "rose"), and only the noted photo counted as relevant. On a 5,042-photo corpus, image-only MRR fell from 0.98 to 0.65, mostly because other photos also fit the notes.
- With 39 queries, the minimum detectable MRR difference is about 0.13–0.15. Captioners differed by 0.01–0.05, so the planned 1.5-day, three-captioner run could not separate them.
- The owner doesn't yet know what they will search for beyond black-and-white and scene descriptions.

So the yardstick moves to frontier models, with the owner spot-checking:
- Claude Opus 5.5 writes the test searches and is the source of truth for audits.
- Gemini 3.8 Flash writes the reference captions and does the bulk relevance judging.
- Cheap captioners and search methods are measured against that yardstick.

The goal is the most cost- and time-effective way to caption the library at close to frontier quality.

Background: `research/mvp-models.md`, `research/vally.md`, `research/hosted-gemma.md`, `handoff-supplement.md`, `docs/pilot.md`, and the run records in `evals/runs/`.

## What Changes

**Already built (kept):**
- the sampling frame over `ImportantFiles` (`shoebox inventory`);
- inference images (`shoebox derive`, profile `infer-v1`);
- keyed label runners: Ollama Gemma 4, Gemini and rules, the EG2 embedder, and the hosted-call safeguards;
- the Vally execution layer;
- the known-item search eval with its distractor pool;
- run reports.

**New in the replan:**
- **Hosted models.** Claude (Opus 5.5) joins Gemini as an approved hosted model. Consent is per library: the owner's library may be sent to both, including photos of family and friends. Other people's own libraries are not sent until their consent is recorded.
- **Eval corpus `eval-1k`:** 1,000 photos from the 5k pool, including the 45 pilot targets, screened for hosted eligibility. The 5k pool stays for confirming the winner.
- **Reference captions.** Gemini 3.8 Flash captions `eval-1k` with a new search-oriented prompt (`label-v2`). Opus audits a sample of captions for errors and invented details.
- **Candidate captioners.** Gemma 4 E4B and 12B, run locally with the `label-v1` and `label-v2` prompts.
- **Synthetic queries.** Opus writes about 300 queries:
  - known-item queries in mixed styles, from each photo plus its date and folder;
  - broad "category" queries with many right answers.
  - The owner's 39 notes stay as an owner-voice check.
- **Search methods compared:**
  - EG2 image vectors;
  - caption vectors;
  - FTS over captions;
  - hybrid and blend;
  - query expansion by a small local model;
  - metadata search (folder, filename, date);
  - attribute filters (black-and-white, film scan, year).
- **Pooled, graded relevance judging.** Gemini judges the pooled top-10 of every route for every query, shown as image grids. Opus audits about 10%, and the owner spot-checks about 100 judgements. This replaces "only the noted photo is relevant".
- **Structured fields** (colour/mono, medium, digitiser) are scored against a reference: metadata rules where they are unambiguous, otherwise Gemini. Opus audits it and the owner checks the residue. This replaces 700 owner labels.
- **Pre-registered decision rule.** Queries are split dev/test, and the decision rule is committed in `evals/gates.toml` before the test split is scored. The rule: pick the cheapest captioner + prompt + search method that is non-inferior to the Gemini-reference pipeline on test nDCG@20 (paired bootstrap), with an audited error rate no worse than Gemini's.
- **Fixes first:**
  - rotate sideways film scans;
  - salvage or retry captions truncated at the output cap;
  - lowercase tags instead of rejecting proper nouns;
  - add a paired bootstrap to the search report.

**Dropped from the first plan:**
- Label Studio and owner pre-label confirmation with an anchoring check;
- the 700-item `capture-colour-v1`, the `scene-v1` vocabulary and tag set, and owner caption rankings;
- the 50 owner-written queries;
- the 12B/31B runs over the whole 5k pool.

**Out of scope:**
- faces and identity: no hosted model names people from their faces;
- the catalogue DB schema, API and UI;
- the bulk run itself;
- dedup;
- Takeout acquisition and the other NAS shares. Those are deferred to before the bulk run and aren't needed for the captioner decision.
- importing the Lightroom catalogues.

## Capabilities

### New Capabilities
- `eval-ground-truth`: the sampling frame, eval corpora, frozen dev/test splits, the frontier-model reference (captions, structured fields, synthetic queries, pooled judgments) with owner and Opus audits, and provenance.
- `inference-images`: derived model-input images, including orientation correction for scans, regenerable and never written over originals.
- `label-runners`: captioning, embedding, judging and query-writing runners with one JSONL interface, keyed outputs, and hosted-call privacy and budget safeguards.
- `eval-execution`: running candidates over eval sets with Vally (spec generation, replay/live executor, graders, re-grading).
- `eval-gates`: metrics, the pre-registered decision rule, paired comparisons, cost/time projections, and durable run records.

### Modified Capabilities

None. There are no archived specs yet.

## Impact

- **Code:** the `shoebox` Python package (uv), `evals/vally/` TypeScript plugins, and `evals/sets/`, `evals/gates.toml` and `evals/runs/` in git.
- **Systems:**
  - NAS shares: `ImportantFiles` read-only, and the `shoebox` share for `derived/`, `results/` and `frame/`;
  - Ollama on the laptop;
  - the Gemini API on the owner's billed project;
  - Claude Opus 5.5 through Claude Code subagents viewing derived JPEGs. There is no API key; outputs are stored keyed like any runner.
- **Cost:** about $35–45 of the $150 hosted budget ($0.18 spent so far). That leaves room for a Gemini bulk pass if it wins.
- **Time:** about 2–3 days to a decision, mostly build time. Owner time is about 45 minutes of spot-checks, down from about 8 hours of labelling.
