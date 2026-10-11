# Mixed caption sources in one search space: is ranking biased by captioner?

Status: needs-triage
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Decision 41 puts two captioners into one index:
- Gemini `label-v2` captions for the back catalogue;
- 12B `label-v2` captions for new photos.

Both are embedded with EG2 and blended at w = 0.5. The eval scored each captioner **alone**, over the whole corpus. It never scored a corpus where some photos carry Gemini captions and others carry 12B captions. The two models write differently: Gemini is denser and has fewer errors; 12B is more generic, with a 27% versus 16.5% error rate (Opus audit). So the caption cosine scores may sit on systematically different scales. In that case new photos would rank consistently above or below the back catalogue for the same relevance. Nobody would notice, because no query set would catch it.

## What

Decide whether this needs handling. Proposed eval (cheap, no new spend: both caption sets already exist for eval-1k):

1. **Build mixed corpora:** assign each eval-1k asset to Gemini or 12B captions at random (seeded, about 20% 12B, roughly the new-photo share after a year), several draws.
2. **Score** `eg2-blend` on dev with the Gemini qrels, against all-Gemini and all-12B.
3. **Measure bias directly:** among relevant items (grade ≥ 1), compare the rank distribution of 12B-captioned versus Gemini-captioned items at equal grade. Do the same for the caption-score distribution per source.
4. **If biased:** test per-source score normalisation (z-score the caption cosine per `label_source`), or a per-source w, on dev. Freeze the result. Then score test once.

Also decide what happens when a Gemini-captioned back-catalogue photo later also gets a 12B caption, for example after a reprocess. The rule is one active label per asset (`search/01`), so the active-label rule settles it, not recency.

## Done when

- A short run record `evals/runs/<date>_mixed-captions.md` shows bias within the MDD, so nothing is done. Or it records the normalisation chosen on dev and confirmed on test.
- The decision is written into this file's Comments, and into `search/03` if the ranking changes.

## Depends on

Nothing for the eval itself. It must be decided before `ingest/` starts indexing 12B-captioned photos alongside the bulk run.

## Open questions

- Does the image half of the blend (identical for both sources) dampen the effect enough that this is a non-issue? That's the likely outcome, but it's unmeasured.

## Comments
