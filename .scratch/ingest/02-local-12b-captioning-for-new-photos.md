# Local 12B `label-v2` captioning for new photos

Status: blocked (on 01; the precedence rule can be written now)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Decision 41 and `evals/RECOMMENDATION.md`:
- New photos are captioned locally by Gemma 4 12B with `label-v2` (candidate `g12b-q4-v2` in `config/candidates.toml`, retry `num_predict 450`).
- Its search is non-inferior to Gemini (nDCG@20 0.785 vs 0.794, CI lower bound −0.018 against the 0.03 margin).
- It costs nothing to run, at about 560 images/hour on the M5 Max.
- Its caption error rate is higher (27% vs 16.5%), which is why it isn't used for the back catalogue.
- Its mono precision is 1.00.

## What

- **Run 12B as ingest's `label` stage** for assets with no Gemini caption. Use the existing Ollama runner (`ollama-runner-2`) with the truncation retry. Then caption-embed with EG2 so the asset joins the same `eg2-blend` space.
- **The label-source precedence rule**, for an asset (or capture) with more than one caption:
  1. a human correction (`web-app/06`);
  2. Gemini `label-v2` (back catalogue);
  3. 12B `label-v2`;
  4. anything older (v1 arms from evals).

  Write it as one function next to `runners.label_source` and use it in search import (`search/02`).
  - Search indexes exactly one caption per asset, and the record says which source supplied it.
  - Older captions stay in the store and are never deleted.
- **Gemini refusals and tiny images** may also route to 12B. That's tracked in `bulk-captioning/` (refusal fallback, tiny images). This issue only guarantees the 12B path exists for them.
- **Colour.** If `bulk-captioning/` picks 12B as the colour-filter source, its `colour_class` feeds the B&W filter for these assets too.

## Done when

- A test asset with both Gemini and 12B labels resolves to Gemini. With only 12B, it resolves to 12B, and the source is recorded.
- A batch of new photos (05's fresh batch) is captioned by 12B through `shoebox ingest`. Each is findable by a known-item query through the production search path.
- Throughput is recorded in a run note, against the 560 img/h baseline.

## Depends on

- 01 (ingest).
- `search/02` (import uses the precedence rule).

## Open questions

- If the M5 Ultra Studio can run something better locally, is 12B still the right incremental model? Re-open only with an eval-1k run under the same gates.toml rule.

## Comments
