# g31b leaks enum values into free tags

Status: needs-triage (likely wontfix)
Reported: 2026-10-11 (first seen in pilot-v0, 2026-10-10)
Reporter: claude (build-planning session)

## Why

- **Task 9.5** (`openspec/changes/add-eval-harness/tasks.md`).
- **The bug.** In pilot-v0, Gemma 4 31B (`config/candidates.toml` → `[candidates.g31b]`, `gemma4:31b` Q4_K_M, `label-v1`) put structured-field enum values into `tags`. The case seen is `film_negative`, in 3 of 45 photos. They duplicate the structured fields and add noise to FTS.
- **Why it's probably moot.**
  - g31b is not in the chosen pipeline (decision 41: Gemini `label-v2` for the back catalogue, 12B `label-v2` for new photos).
  - It wasn't carried into the eval-1k grid.
  - `label-v2` changed the prompt and normalisation (`runners.normalise_label`).

## What

- **Check whether the chosen arms leak.** Count tags that exactly match an enum value of `medium`, `digitiser` or `colour_class` in the eval-1k stores for `gemini-medium-v2` and `g12b-q4-v2`. This is a quick script over `runners.label_index`, with no inference.
- **If either leaks** at more than about 1% of photos: retitle this issue to that arm. Add a tag filter in `normalise_label` (a versioned runner change, so the hash changes) or a search-import filter (`search/02`), and re-check search on dev.
- **If neither leaks:** close as `wontfix`.
  - What would reopen it: g31b (or any 31B-class model) becoming a candidate on the M5 Ultra Studio (`ingest/04`).

## Done when

The counts for both shipped arms are in the Comments below, and the status is changed to `wontfix` or `ready-for-agent` with the chosen fix.

## Depends on

Nothing.

## Comments

- **2026-10-11 (claude, quick count from the stores; no inference).** I counted tags matching an enum value, with spaces mapped to `_`.
  - **g31b, pilot store `783af945…`:** 3 of 45 photos, all with `film_negative`. These are true underscore-form leaks.
  - **Shipped arms on eval-1k:**
    - `gemini-medium-v2` (`b610d323…`): 36 of 1,007;
    - 12B `label-v2` stores: about 25 to 75 per 1,000–2,000.
  - **The shipped arms' matches are mostly ordinary words** ("camera", "phone", "document", "print", "screen", "screenshot"). Those can be real content tags: a photo of a phone, a screenshot.
  - **Next:** separate underscore-form leaks from ordinary words before deciding. The ordinary-word matches are probably fine.
