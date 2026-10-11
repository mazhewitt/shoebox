# Recommendation: captioner and search method for the MVP

**Decided by the owner on 2026-10-11:**
- **Back catalogue (about 71.6k photos):** Gemini 3.8 Flash with prompt `label-v2`, run through the Batch API.
- **New photos as they arrive:** Gemma 4 12B (`g12b-q4`) with `label-v2`, run locally.
- **Search:** blend the EmbeddingGemma 2 image vector with the caption vector (`eg2-blend`, w = 0.5).

The evidence is the `eval-1k` run (`evals/runs/2026-10-11_eval-1k-*.md`), scored under the decision rule in `evals/gates.toml`. That file was committed before any test-split search score existed. Version 1 is commit 542640b; version 2, 126c067, changed which judge's grades count before scoring.

## What was compared

**Captioners** (all on the same 1,000 screened photos, `eval-1k`):
- local:
  - Gemma 4 E4B with prompts `label-v1` and `label-v2`;
  - Gemma 4 12B with `label-v1` and `label-v2`;
- hosted:
  - Gemini 3.8 Flash with `label-v2`;
  - Claude Opus 5.5 with `label-v2`, run as Claude Code subagents;
- no captioner (image-only search).

**Search routes:** 75 in all, combining:
- EmbeddingGemma 2 image vectors;
- caption vectors;
- full-text search;
- RRF hybrids;
- folder/filename/date search;
- query expansion by E4B;
- black-and-white/film/year filters.

**Queries:** 317 in all.
- 200 known-item queries and 80 category queries, written by Opus.
- The owner's 37 notes.
- The owner judged 29 of 30 sampled queries as searches they would actually type.
- Queries are split into dev and test by capture group. Routes were chosen on dev; test was scored once.

**Relevance:** grades of 0, 1 or 2 on the pooled top 10 of every route.
- Gemini graded all 18,921 pairs.
- Opus graded every test pair (11,207). Opus's grades are the primary test ground truth.
- Gemini–Opus agreement: linear-weighted kappa 0.71, about the same for every route.

## Results (test split, 186 queries, Opus grades)

| Captioner | Route | nDCG@20 | vs Gemini (95% CI) | Caption error rate | Owner rating (1–5, blind) |
|---|---|---|---|---|---|
| Opus `label-v2` | eg2-blend | 0.805 | +0.011 (+0.004, +0.018) | 27%* | 4.60 |
| Gemini `label-v2` | eg2-blend | 0.794 | reference | 16.5% | 4.37 |
| 12B `label-v2` | eg2-blend | 0.785 | −0.009 (−0.018, 0.000); within the 0.03 margin | 27% | 3.77 |
| 12B `label-v1` | eg2-blend | 0.780 | −0.014; within the margin | 29% | 3.57 |
| E4B `label-v2` | eg2-blend | 0.773 | −0.021; fails | 34% | 3.70 |
| E4B `label-v1` | eg2-blend | 0.759 | −0.035; fails | 33% | 3.67 |
| No captioner | eg2-image@768 | 0.756 | −0.038; fails | — | — |

- **Caption error rate:** the share of photos with at least one wrong or invented fact, or no caption. Measured by a blind Opus audit of 200 test-split photos.
- \* **Opus's captions** were audited by Gemini. Each model would otherwise grade its own captions, so no auditor is neutral between Opus and Gemini.
- **Owner ratings:** blind, using a 1–5 usefulness rubric, on 30 photos.
- **Under the rule:**
  - The local captioners fail the caption rule (error rate no more than the reference's + 2 points).
  - No captioner fails search non-inferiority.
  - Rule 4 then recommends Gemini at batch price.
- **Opus also survives:** best on search and preferred by the owner. It costs about 229M subscription tokens (about 1½–2 weeks of a Max plan) and has no API pipeline.

## Why this choice

- **Gemini has the fewest caption errors**, and its search is within 0.011 of the best.
- **Cost:** the batch price is about $98 at the measured $0.00275 per image, against $140 of hosted budget left. Re-check the price on the day; the batch price is assumed to be 50% of standard.
- **12B `label-v2` for new photos:**
  - Its search is non-inferior.
  - It's free and local, at about 560 photos per hour on the M5 Max laptop.
  - Its black-and-white precision is perfect (1.00).
  - It is not used for the back catalogue, because its captions contain more wrong details.
- **One search space:** both captioners go through the same EG2 caption embedding and blend route, so search works across them.

## Known gaps and follow-ups

- **Gemini refusals:** Gemini's safety filter refused 2 of 1,000 ordinary family photos (PROHIBITED_CONTENT). In the bulk run those go to 12B.
- **Tiny images** (measured 2026-10-11, `evals/runs/2026-10-11_tiny-images.md`, n = 24 under 384 px):
  - E4B invents on 6 of the 11 images under 256 px (1.1% of `eval-1k`), e.g. a 72 px app icon described as "people in a park". It is not in the pipeline.
  - Gemini holds up: 75% clean, against 84% overall. Rule: do nothing.
  - 12B's wrong rate more than doubles (46% against 20%). Rule: route images under 256 px to the hosted candidate when eligible; otherwise caption them locally and mark the record `tiny`.
- **Colour filter:**
  - Gemini's mono precision is 0.974, below the 0.98 gate, so its `colour_class` is not trusted for a black-and-white filter.
  - 12B passes the gate (1.00), but would have to run over the library to provide it.
  - Alternatively, use the frame's colour heuristic, once that has been measured.
- **Orientation:**
  - About 4% of scans are stored sideways or upside down.
  - Gemini's `rotate_cw` caught 5 of 8, and the local models fewer. Nothing is rotated automatically. A UI rotate action, or a later model, can use the stored value.
- **Filters and expansion:** query expansion added nothing on dev. The filters help category searches only (+0.05), so they are kept as a UI option, not the default.
- **Still to do before the bulk run** (tasks section 9):
  - read access to the other NAS shares;
  - a Takeout landing;
  - skip rules (`.thumbnails`);
  - dedup, so each capture is captioned once;
  - consent screening per library;
  - a Gemini Batch API runner with the same guards as the sync runner;
  - task 7.4: confirm on `pilot-v0-pool5k`.

## Privacy notes

- Only the owner's library is sent to Gemini (and was viewed by Claude), per the 2026-10-11 consent. Other people's own libraries are not sent.
- Held back from hosted models:
  - `Pictures/toren phone`;
  - `Pictures/Jude Profile`;
  - `Important documents`.
- The Opus screen of 1,010 photos excluded 10. Three passport pages and a second ultrasound were added to `evals/never-send.txt`.
- Only derived JPEGs are sent, with no metadata. Gemini is never asked to identify anyone from their face.

## Spend

| Use | USD |
|---|---|
| Pilot | 0.18 |
| Gemini `label-v2` on `eval-1k` | 2.77 |
| Gemini judging | ≈ 6.5 |
| Gemini caption audit | 0.62 |
| **Total** | **10.04** (of 150) |

Opus work ran on the owner's Max subscription. One measured window of about 9M tokens used 6 weekly points and 50 session points.
