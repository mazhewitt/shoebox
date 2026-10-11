# Eval follow-ups: close out `add-eval-harness`

**Goal.** Finish the loose ends of the eval phase so `openspec/changes/add-eval-harness` can be archived cleanly, and put the settled decisions into the repo.
- **The loose ends:** a small robustness check the owner's review made possible, two older harness bugs, and the bookkeeping.
- **Decisions 1–41** currently live only in Claude's project memory and are cited by number throughout `.scratch/`. Putting them in the repo makes those citations checkable.

| # | Issue | Gate to pass before it lands |
|---|-------|------------------------------|
| 01 | [Owner-rephrase subset](01-owner-rephrase-subset.md) | Scored on the frozen routes only (`frozen-routes.json`); reported, not gated |
| 02 | [Agreement counts unknown–unknown as agreeing](02-agreement-matrix-unknowns.md) | The pilot-v0 report is regenerated and the change in numbers stated |
| 03 | [g31b enum leakage into tags](03-g31b-enum-leakage.md) | Triage: fix or wontfix, with what would reopen it |
| 04 | [Close out and archive `add-eval-harness`](04-close-out-add-eval-harness.md) | 7.4 done (`bulk-captioning/02`); section 9 moved into `.scratch/`; `openspec validate` passes |
| 05 | [Rewrite PLAN.md and put the decisions in the repo](05-rewrite-plan-md.md) | Every `.scratch/` "decision N" citation resolves to `docs/decisions.md` |

## Sequencing rationale

- **01–03 are independent and small.**
- **05 can start at once.** It doesn't depend on the archive, and the rest of `.scratch/` benefits from it immediately.
- **04 last.** It needs 7.4 to land.
