# API stack: Rust (Axum) as planned, or Python?

Status: needs-triage
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md §1 and decision 17 say: a Rust Axum API under launchd on the Mac Studio, with Python only for the pipeline and runners. The rule was "anything fast or long-running in Rust". That was written before any code existed. Since then the whole working system has been built in Python (`src/shoebox/`):
- inventory and derive;
- the store and runners;
- `embed.rank`;
- the eval harness, including `route_scores` and `apply_soft_filter`.

The query path that `search/05` must keep at parity is Python today. The decision needs re-confirming with that on the table, not re-litigating for its own sake.

## What

Decide between three options and record the decision here, then in PLAN.md (via `eval-followups/`):

- **A. Rust Axum, as planned.**
  - For: matches decision 17; one small binary; strong at serving, range requests and JWT; launchd-friendly.
  - Against: the ranking (03), filter parsing (04) and query encoding have to be re-implemented or called over a boundary. The encoder is Ollama over HTTP anyway, so only the ranking and filters cross. Parity (`search/05`) then really tests two implementations, which is the point of that eval but doubles its risk.
- **B. Python (e.g. Starlette/FastAPI under uvicorn, run by launchd via `uv run`).**
  - For: reuses `embed.py` / `evals/search.py` directly; parity is nearly automatic; a single language.
  - Against: overrides decision 17's steer. A long-running Python service is the thing PLAN.md §1 avoided ("Python never owns a long-lived service").
- **C. Split.**
  - Rust serves images, thumbnails and auth (03, 04), which are hot, byte-heavy and security-critical.
  - A small Python search service on localhost does ranking (02), called by the Rust front.
  - Cost: two processes.

**Recommendation: C**, falling back to A if the owner wants one binary.
- Byte serving and JWT checks are where Rust earns its place.
- Ranking is about 50 lines of NumPy that the eval already proves; rewriting it buys nothing at about 100k vectors.
- Under C, `search/05` exercises the production Python path directly.

## Done when

- The owner picks A, B or C. The choice is recorded in Comments with the reason, and decision 17 is amended in PLAN.md if it changes.
- Status flips the dependent issues (02–04) from `blocked` to `ready-for-agent`.

## Depends on

Nothing. It blocks 02–07.

## Comments
