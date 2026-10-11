# Pairwise agreement counts unknown–unknown as agreeing

Status: ready-for-agent
Reported: 2026-10-11 (first noted 2026-10-10 in `evals/runs/2026-10-10_pilot-v0-notes.md`)
Reporter: claude (build-planning session)

## Why

- **Task 9.5** (`openspec/changes/add-eval-harness/tasks.md`).
- **The bug.** `shoebox.evals.report.agreement` (`src/shoebox/evals/report.py:210`) compares `label.get(field)` for two candidates. When both answer `unknown`, it counts as agreement.
- **Why it matters.** Two models that both abstain haven't agreed on anything, and inflated agreement between abstaining models misleads any "models agree, so trust them" reading. The pilot-v0 report's matrices (`evals/runs/2026-10-10_pilot-v0.md`) carry this bias.
- **Scope.** The eval-1k audits use their own kappa code (`evals/audit.py`, `caption_audit_gemini.py`) and are not affected. Verify that while fixing.

## What

- **`agreement`** excludes pairs where both are `unknown` from both numerator and denominator.
  - It adds a separate `both_unknown` count per cell, so the information isn't lost.
  - One `unknown` against a real answer still counts as a disagreement.
- **Render the new count** in the markdown table.
- **Regenerate the pilot-v0 report** with replay only (no inference), and state the before and after for the headline pairs (g12b-q4 vs g31b: medium 84.4%, digitiser 80.0%).
- **Grep for other agreement computations** that might do the same, e.g. `caption_audit_gemini.agreement`, and record what was found.

## Done when

- A unit test fails on the current code and passes after the fix: two candidates both `unknown` on one item, so `n` excludes it and `both_unknown` = 1.
- `evals/runs/2026-10-10_pilot-v0.md` is regenerated, and a dated note in that run's notes file gives the changed numbers.

## Depends on

Nothing.

## Comments
