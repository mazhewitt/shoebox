# Screen retro-check, 2026-10-11 (add-pipeline-foundations 9.3)

Counts only. `uv run shoebox screen run --retro` with `nsfw-coreml-v1` (OpenNSFW2 FP32 Core ML, threshold 0.2, params_hash `3bac8ba1408df54e`), on the Mac Studio, with no network.

**Retro set (1,697 assets):** every asset id in a hosted store, of any kind, under `gemini__*`, `gemini-batch__*` and `claude__*`. Also every asset id in the `pilot-v0` and `eval-1k` set files, plus the pool5k files Claude viewed or decided on: `near-dups.jsonl`, `exclude.jsonl` and `search-known-item.jsonl`. Query-category keys and file checksums are excluded. All 1,697 were screened, with 0 errors.

*Corrected the same day:* the first pass (1,565 assets, 39 flagged) missed the pool5k near-duplicate pairs and exclusions Claude had viewed. A review caught it.

| | assets |
|---|---|
| Flagged (`sensitive`) | 43 |
| ... of which have a Gemini record (sent to Gemini) | 27 |
| ... of which have a Claude record (seen by Claude) | 26 |
| ... in either hosted store | 27 |
| ... only in the eval-set files (pool/packet lists, pool5k near-dup pairs; may have been on a contact sheet or packet) | 16 |

**What this means:**
- The threshold is recall-first and not yet tuned, so many of these 43 are probably false positives.
- All 43 are now `pending` in `evals/quarantine.jsonl`. They are refused by every hosted runner and every Claude-viewing path until the owner rules on them on the review page (9.4).
- Nothing can be recalled from Gemini (paid tier, logging off, 7-day retention per the checklist) or Claude. This only records the exposure.
