# Screen the bulk worklist for ID, medical and financial documents before any send

Status: needs-triage
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Only the eval photos have been screened.** The Opus contact-sheet screen (`config/prompts/screen-v1.md`) has covered only the 1,010 photos drawn for eval-1k.
- **That screen found about 1% sensitive:** 10 excludes (3 financial, 1 ID document, 5 other sensitive) plus never-send additions such as passport pages and an ultrasound.
- **The bulk set is about 52k unique images on ImportantFiles alone,** before other sources, which at that rate means roughly 500 sensitive images.
- **Consent excludes these.** The owner's consent covers their photo library, not ID, medical or financial documents. The pilot-v0 privacy incident (a medical image sent to Gemini) is the precedent.
- **No recall gate yet.** The rule screens (`held_back`, `never-send`) catch known paths and files only, not documents scattered through camera rolls.

This screen is a hard prerequisite of `06` and is listed in its checklist.

## What

Decide the screening method, then run it over the caption-once worklist (`dedup/04`). The options:

1. **Opus contact sheets over everything.**
   - This is the same method as eval-1k and the most trusted.
   - Measure its subscription cost from the eval-1k screen before quoting. Caption work cost about 3.2M tokens per 1,000 images; screening sheets are much cheaper per image.
2. **A local pre-filter, then Opus on the flagged items.**
   - The pre-filter is 12B or E4B with a yes/no "document / medical / ID" prompt, plus rules: folder names, `medium = document` from `rules-v1`, scanner-without-film signatures.
   - Only the flagged items and a random audit slice go to Opus.
   - This needs a measured recall on the eval-1k screen labels (the 10 known excludes plus the never-send items Claude can view numerically, not visually). The gate should be recall-first: a miss is a privacy incident; a false flag only delays one photo.
3. **12B labels everything first, then use its `medium`/tags.** This is free and runs anyway for refusals, but has unknown recall on documents.

**Output:**
- Screen results are appended to a library-level `screen.jsonl`, with the same schema as eval-1k's.
- Excludes are added to `evals/never-send.txt` or a new screen-excludes list.
- `privacy.filter_items` honours them.

## Done when

- The method is chosen, and its recall is measured on the known excludes (recall-first gate, e.g. 10 of 10 plus the never-send set).
- Every item on the bulk worklist has a screen verdict, or is held back.
- `shoebox privacy check --provider gemini --allowed-out` passes on the worklist.

## Depends on

- `dedup/04` (the worklist).
- `sources/04` (consent eligibility).

## Open questions

- Does a document-like photo the owner wants searchable (receipts, letters, tickets) go to local 12B captioning instead of being dropped? Probably yes. That keeps "describe every photo" without sending documents.

## Comments
