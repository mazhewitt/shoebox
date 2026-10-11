# Rewrite PLAN.md to match decisions 1–41, and put the decisions in the repo

Status: ready-for-agent (the owner reviews the diff before it's committed)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **PLAN.md still says "Plan for review"** and describes an architecture the owner has since overridden. The settled decisions (1–41, from the 2026-10-03 grilling through the 2026-10-11 captioner decision) exist only in Claude's project memory (`shoebox-plan-decisions`). That's not in git and not visible to the owner's tools.
- **`.scratch/` cites them by number**, so they need a home in the repo.
- **Sections now wrong:**
  - **§0:** NAS-hosted API and HTTP job leasing (decisions 3 and 13: the Mac Studio hosts everything and there is no queue); the VLM row (Qwen-VL → Gemini Batch + 12B, decision 41); SigLIP (→ EmbeddingGemma 2, `eg2-blend`).
  - **§1:** the diagram (NAS API, worker daemon, Parquet bundles; the keyed JSONL store is what exists).
  - **§6:** where things run.
  - **§9:** R1 (decided: scheduled Takeout, decision 7), R2 (the DB is on the Studio SSD), R7 (the query encoder runs on the Studio).
  - **Phase 6:** scene content is now the MVP, decided by eval-1k.
  - **Phase 7:** `shoebox ingest`, not a worker daemon.
  - **Phase order:** the MVP is now sources → catalogue → dedup → describe → search, with faces after (decisions 35 and 36).

## What

- **`docs/decisions.md`:** the numbered decisions 1–41, each with its date, the decision, and what it superseded. Keep the original numbers, including the gaps and the order (20–23 are out of order in memory).
  - Leave out personal details that don't belong in a repo that may go to a remote (`ops/01`), e.g. people's device details beyond what the code needs. Use the role names the code uses: owner, Jude, Kyra, Toren.
- **PLAN.md:** rewrite the sections above to match. Mark the status "Plan of record, 2026-10-11". Link `docs/decisions.md`, `evals/RECOMMENDATION.md` and `.scratch/README.md`. Keep §8 (changes from the handoff) as history.
- **Claude's memory file** then points at `docs/decisions.md` as the source of truth. The memory is updated by the session that lands this, not by the issue.

## Done when

- `docs/decisions.md` exists and every "decision N" in `.scratch/**/*.md` resolves to an entry. Check with `grep -rhoE "decisions? [0-9]+" .scratch` and look each one up.
- The owner has read the PLAN.md diff and approved it in the Comments below.

## Depends on

Nothing.

## Comments

- 2026-10-11: include the owner's language rule in PLAN.md §0/§1 and in the decisions file: Owner's language rule (2026-10-11): Python for simple scripts and pipelines; Rust only where the logic is complex, the process is long-running (a service), or a non-functional requirement needs the speed or concurrency. It refines decision 17 and PLAN.md's "anything fast or long-running in Rust".
