# Colour / mono / toned from pixels, gated: the candidate black-and-white filter source

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

**The filter needs a gated source.** `evals/RECOMMENDATION.md` (known gaps, colour filter) says:
- Gemini's mono precision is 0.974, below the 0.98 gate (`evals/gates.toml` `mono_precision_min`).
- 12B passes (1.00), but would have to run over the whole library.
- The alternative, "the frame's colour heuristic, once that has been measured", isn't measured yet.

Black-and-white is one of the two search kinds the owner already knows they want (decision 40). So the filter needs a source that passes the gate.

**What exists:**
- `rules.chroma_stats`: CIELAB chroma over mid-tones of a 256 px thumbnail, with hue concentration.
- `classify_colour`: `colour` / `mono` / `toned` / `unknown`.
- `inventory.py` already stores `chroma_mean`, `chroma_median`, `chroma_p95`, `chromatic`, `hue_r` and `colour_guess` in the frame. The 2026-10-10 frame's `colour_guess` counts:

| colour_guess | Images |
|---|---|
| colour | 48,069 |
| mono | 7,662 |
| toned | 4,245 |
| unknown | 11,617 |
| none | 8,904 |

Its precision has never been scored against the reference.

## What

1. **Score the existing heuristic.** Compare the frame's `colour_guess` with the Opus reference `colour_class`:
   - first on the 200-photo eval-1k audit sample, which already has an Opus label;
   - then on a mono-stratified sample, since mono is rare in a random 200: about 150 `mono`/`toned` guesses plus 150 near-threshold `colour` and `unknown` cases, labelled by Opus in the same way as the caption audit.

   Report precision and recall for mono, per stratum: scans, phone, camera, received.
2. **Tune the thresholds** in `classify_colour` on a dev half for mono precision ≥ 0.98 per stratum, so as few as possible fall below the bar into `unknown`. Freeze them, then score the test half once.
3. **Write up** the result in `evals/runs/<date>_colour-heuristic.md` and compare it with Gemini (0.974) and 12B (1.00). Give the decision-rule-5 outcome: which source the black-and-white filter uses. A combination counts too, e.g. heuristic `mono` AND not Gemini `colour`, if that scores better.
4. **Keep `toned` separate.** Sepia, cyanotype and colour-cast mono are the hard case (PLAN.md §8.2). Toned photos match "black and white" searches only if the owner wants that; record the default chosen.

## Done when

- The report exists, and the filter source is chosen with precision ≥ 0.98 per stratum on test, or the filter ships as "unavailable" with the reason.
- RECOMMENDATION.md's colour-filter gap is updated with a link to the report.

## Depends on

Nothing. The stats are already in the frame.

## Comments
