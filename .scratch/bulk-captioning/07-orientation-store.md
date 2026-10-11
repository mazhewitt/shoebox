# Store `rotate_cw` per asset as a suggestion; never auto-rotate

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

About 4% of scans are stored sideways or upside down. `label-v2` asks for `rotate_cw` (0/90/180/270; `config/schemas/label-v2.json`).
- **Gemini** caught 5 of 8 rotated scans; the local models caught fewer (`evals/RECOMMENDATION.md`, "Orientation"; `evals/runs/2026-10-11_scan-orientation.md`).
- **Not trustworthy enough to apply automatically.** Originals are immutable anyway.
- **Still worth keeping,** so the UI can offer a one-click rotate (`web-app/`), and a later model can do better.

## What

- The index import (`search/`) carries `rotate_cw` from the resolved label source into a per-asset `orientation_suggestion`, with:
  - `value`;
  - `source` (`model_id` + `params_hash`);
  - nothing applied.
- Display rotation is a separate, human-set field (decision 14: corrections are human labels). An owner's rotate action writes `rotation` with provenance `human`. That always wins over the model suggestion.
- Non-zero `rotate_cw` values, from any captioner, never change the derived image or the original. This is checked in tests.
- Optional: a "suggested rotations" list for the owner, counts first. Only if cheap.

## Done when

- The schema field and import mapping are tested.
- A query over the bulk output gives the count of non-zero suggestions per source.
- `web-app/` has the hook it needs: the suggestion and the human rotation, with the human one winning.

## Depends on

- `search/` schema v1 for where the field lives. The data already exists in label-v2 outputs.

## Comments
