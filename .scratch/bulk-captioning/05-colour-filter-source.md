# Which source powers the black-and-white filter?

Status: needs-triage
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

The owner's one known search need, besides scene descriptions, is black-and-white. They are an avid B&W film photographer (decision 23).

`evals/gates.toml` rule 5 (`mono_precision_min = 0.98`): a captioner whose mono precision is below the gate keeps its captions, but its `colour_class` can't drive a filter. Measured against the Opus reference on eval-1k:

| Source                             | Mono precision | Passes? |
| ---------------------------------- | -------------- | ------- |
| Gemini `label-v2` (bulk captioner) | 0.974          | No      |
| 12B `label-v2`                     | 1.00           | Yes     |

The B&W filter is a UI option, not the default ranking (+0.05 on category queries only), but it must be trustworthy when used.

## Options

1. **12B over the whole library** for `colour_class` only.
   - Measured precision 1.00.
   - Costs a full local pass of about 70k at about 560 img/h, about 125 h on the M5 Max, less on the M5 Ultra Mac Studio.
   - Captions would be wasted unless kept as a second caption source.
2. **The frame colour heuristic** (`catalogue/`: chroma statistics on the 256 px thumbnail; `inventory.py` already computes `chroma_median`, `chroma_p95` and `chromatic` into `colour_guess`).
   - Free, deterministic, and covers non-captioned assets too.
   - **Unmeasured** against the Opus reference. It must pass the same 0.98 gate on eval-1k first.
   - Toned and sepia prints are the known hard case.
3. **Combined rule:** "mono" only when the heuristic and Gemini agree. That raises precision at some recall cost, and uses only data that will exist anyway.
4. Extract a 2D histogram of the $a^*b^*$ chrominance planes or calculate spatial color divergence metrics, then feed those into an XGBoost or SVM classifier. This often matches CNN accuracy on aged scans at a fraction of the computational footprint.

## What

- Score options 2 and 3 on eval-1k against the Opus reference with no new inference. The Gemini labels and the inventory chroma columns already exist.
- Report precision and recall per class, with mono recall shown explicitly.
- Pick the cheapest option that passes 0.98, recording what decided it. If neither 2 nor 3 passes, it is option 1, scheduled on the Mac Studio after it arrives (`ingest/`).

## Done when

- `evals/runs/<date>_colour-filter-source.md` holds the comparison and the decision.
- `evals/RECOMMENDATION.md` "Known gaps" is updated.
- The index import (`search/`) knows which field feeds the filter.

## Depends on

- Nothing for the measurement. Option 2's heuristic formally lives in `catalogue/`.

## Comments
