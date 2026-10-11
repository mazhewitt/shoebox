# Private git remote for code and eval sets

Status: ready-for-human (owner picks GitHub or Codeberg and creates the private repo)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 16:** code and eval sets go to a private git remote.
- **Today:** `git remote -v` prints nothing. The repo, including every eval set, `gates.toml` (whose commit order is part of the pre-registration), the owner's review results and all the run reports, exists only on this laptop.
- **The branch.** `plan/eval-harness` holds the whole eval phase (latest commit 4af7038).

## What

- **Owner:** create a private repo (GitHub or Codeberg) and say which.
- **Agent:**
  1. Add the remote.
  2. Audit before the first push:
     - no image bytes: `git ls-files | grep -iE '\.(jpe?g|png|heic|tiff?|dng|webp)$'` should list only synthetic test fixtures, if anything;
     - no secrets: `git log -p | grep -i 'GEMINI_API_KEY='` shows no values;
     - `.env` is ignored.
  3. Push `main` and `plan/eval-harness`.
- **Check the eval set files** for content that shouldn't leave the laptop even privately:
  - `evals/sets/eval-1k/*.jsonl` hold asset IDs, relpaths, queries, and the owner's notes and ratings;
  - `results/` (captions) is on the NAS, not in git;
  - confirm the owner is fine with folder names and queries being on the host.
- **Decide** whether `evals/never-send.txt` (relpaths of sensitive files) goes to the remote as is.

## Done when

- `git remote -v` shows the private remote.
- Both branches are pushed.
- The audit commands' output is recorded in the Comments below.

## Depends on

Nothing.

## Open questions

- Is `evals/never-send.txt` acceptable on the remote? Its paths name passport and medical files, though not their content.

## Comments
