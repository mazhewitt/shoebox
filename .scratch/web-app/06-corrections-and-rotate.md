# Corrections and rotate, stored as human labels

Status: blocked (on 05, search/01)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 14:** ad-hoc correction anywhere in the UI, stored as a human label.
- **Decision 9:** a human label outranks every other source.
- **Decision 4:** `labelled_by`; the owner is the labeller.
- **Orientation** (`evals/RECOMMENDATION.md` "Known gaps"): about 4% of scans are stored sideways or upside down. Gemini's `rotate_cw` caught 5 of 8, and nothing is rotated automatically. A UI rotate action that uses the stored suggestion is the agreed fix.

## What

- **Owner-only write endpoints** (role checked per 04):
  - `POST /api/captures/{id}/labels` with `{field, value}`, for caption, tags, date (with `date_kind: human`), place, colour_class and medium;
  - `DELETE` to retract a human label, which reverts to the precedence chain.
- **Rotation:**
  - When the active label's `rotate_cw ≠ 0`, the detail view shows a "rotate as suggested?" prompt.
  - Accepting writes a human `rotate_cw` label. Manual rotate (90° steps) writes the same.
  - Rotation is applied when serving (03), never to originals or derived files.
- **Re-embedding after a correction:**
  - A changed caption marks the asset's caption vector stale.
  - The next `shoebox ingest` / `db import` run re-embeds it with EG2 (no always-on worker, decision 13).
  - Until then the old vector stays and the UI says "search index updates on next import".
  - FTS is updated immediately.
- **Audit trail:** every human label row keeps the previous display value and its provenance, so a correction can be reviewed or undone.
- **Never written:** originals, derived files or the keyed results store.

## Done when

Tests show:
- a family token cannot write (403);
- an owner correction overrides the model value in `GET /api/captures/{id}` and in FTS;
- retracting restores the model value;
- a human label survives `shoebox db rebuild` (`search/02`);
- an accepted rotate changes the served display orientation and leaves the file bytes' BLAKE3 unchanged.

## Depends on

05, `search/01`, `search/02`, 04.

## Open questions

- Should corrections feed back into eval sets (e.g. a growing "owner-corrected captions" set)? It costs nothing to keep; deciding how to use them is later work.

## Comments
