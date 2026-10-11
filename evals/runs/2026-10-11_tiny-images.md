# Tiny images, 2026-10-11 (add-pipeline-foundations 9.1; `.scratch/bulk-captioning/04`)

**Set:** eval-1k images whose source long side is under 384 px, from `derived/infer-v1/index.jsonl` (`src_width`, `src_height`):
- 11 are under 256 px; 17 are 256–383 px.
- 4 of the 28 already had verdicts in the 200-photo caption audit: 2 under 256 and 2 in the band. Those verdicts are reused.
- The other 20 got a fresh blind Opus audit: all 9 remaining under 256 px, plus a seeded 11 of the 15 remaining in the band (seed 20261011).
- The audit used the `caption-audit-v1` instructions on 1024 px thumbnails, with arms shown as A–D in a seeded shuffle. All 20 passed the Claude eligibility and quarantine checks.

**Stored arms, with no new inference and no spend:**
- `e4b-bf16-v2`: `ollama/gemma4:e4b-mlx-bf16` 27fec4b0, with retry af84a378;
- `g12b-q4-v2`: `ollama/gemma4:12b` ab1adeff;
- `gemini-medium-v2`: b610d323;
- `opus-v2`: 0b436cb5. Opus here audits its own captions, so it is a self-audit and not used for the rule.

A caption **invents** if it states something not in the image, and is **wrong** if it misstates something that is there (audit counts > 0). It is **clean** if it does neither.

| Arm | n | Invents: tiny (base) | Wrong: tiny (base) | Clean: tiny (base) | Useful 1–5: tiny (base) | Invents < 256 px |
|---|---|---|---|---|---|---|
| e4b-bf16-v2 | 24 | 46% (10%) | 42% (28%) | 25% (66%) | 2.50 (3.40) | 6 / 11 |
| g12b-q4-v2 | 24 | 12% (10%) | 46% (20%) | 46% (73%) | 3.25 (3.81) | 2 / 11 |
| gemini-medium-v2 | 24 | 21% (12%) | 8% (4%) | 75% (84%) | 4.67 (4.79) | 2 / 11 |
| opus-v2 (self) | 20 | 15% | 0% | 85% | 4.85 | 3 / 9 |

"Base" is the same arm on the 200-photo audit sample. These samples are small: one image is about 4 points.

## Decision per model

- **Gemini (`gemini-batch-v2` / `-medium-v2`): (a) do nothing.**
  - It is slightly worse on tiny images, but still the best captioner on them (75% clean, useful 4.67).
  - Every alternative is worse, and Gemini captions nearly all of the back catalogue.
- **12B (`g12b-q4-v2`, local): (c) route.**
  - On tiny images its wrong rate more than doubles (46% against 20%) and its clean rate falls from 73% to 46%. It invents no more than usual, but misreads detail.
  - The rule: an image whose source long side is under **256 px** should be captioned by the hosted candidate when it is hosted-eligible. When it is not eligible (local-only captures and fallbacks), 12B captions it and the record is marked `tiny`, so search can weigh it lower.
  - In the back catalogue, every eligible capture already goes to Gemini, so this changes only the future local path (`ingest/`) and marks local-only tiny captions.
- **E4B: not in the pipeline.** It invents on 6 of the 11 images under 256 px. That is the original finding, now measured.
- **Upscaling (b) was not tested.** It would mean a 12B run; the evidence above doesn't call for it.
- **Caption-skip (d): no.** Gemini is good on these images.

**Threshold:** 256 px source long side. The 256–383 px band is not clearly worse than under 256 for Gemini or 12B, so it gets no rule.
