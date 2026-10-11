# Screen retro-check, 2026-10-11 (add-pipeline-foundations 9.3)

Counts only. `uv run shoebox screen run --retro` with `nsfw-coreml-v1` (OpenNSFW2 FP32 Core ML, threshold 0.2, params_hash `3bac8ba1408df54e`), on the Mac Studio, with no network.

**Retro set (1,565 assets):** every asset id in a hosted store, of any kind, under `gemini__*`, `gemini-batch__*` and `claude__*`, plus every asset id in the `pilot-v0` and `eval-1k` set files. Query-category keys and file checksums are excluded. All 1,565 were screened, with 0 errors.

| | assets |
|---|---|
| Flagged (`sensitive`) | 39 |
| ... of which have a Gemini record (sent to Gemini) | 27 |
| ... of which have a Claude record (seen by Claude) | 26 |
| ... in either hosted store | 27 |
| ... only in the eval-set files (pool/packet lists; may have been on a contact sheet or packet) | 12 |

**What this means:**
- The threshold is recall-first and not yet tuned, so many of these 39 are probably false positives.
- All 39 are now `pending` in `evals/quarantine.jsonl`. They are refused by every hosted runner and every Claude-viewing path until the owner rules on them on the review page (9.4).
- Nothing can be recalled from Gemini (paid tier, logging off, 7-day retention per the checklist) or Claude. This only records the exposure.
