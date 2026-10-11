# Ops: backups and exposure

**Goal.** Make the irreplaceable data survive, and stop exposing local inference on the LAN.
- **Decision 16 (backups):**
  - code and eval sets go to a private git remote;
  - the SQLite DB goes to the NAS via Litestream;
  - the NAS's GCS backup covers `originals/`, `results/` and `db/`, but not thumbnails.
- **Why it matters.** Human labels and eval ground truth are the only data that cost human hours (PLAN.md R11). The keyed results store is cheap to back up but expensive to recreate: about $10 of Gemini, a large share of a week of Opus subscription, and many hours of local inference so far.

| # | Issue | Gate to pass before it lands |
|---|-------|------------------------------|
| 01 | [Private git remote](01-private-git-remote.md) | The owner picks a host. A push succeeds, and the repo has no image bytes or secrets. |
| 02 | [Litestream DB backup](02-litestream-db-backup.md) | `search/01` schema exists. A restore from the replica is tested. |
| 03 | [Results-store backup](03-results-store-backup.md) | The NAS's GCS backup job is confirmed to include `results/` and exclude `derived/`. A restore of one store is tested. |
| 04 | [Ollama bound to localhost](04-ollama-localhost-only.md) | Nothing on the LAN loses access unexpectedly (radiobox's use of the Studio's Ollama is checked first). |

## Sequencing rationale

- **01 now.** Nothing in the repo is backed up off this laptop today: `git remote -v` is empty.
- **04 now.** It's cheap, and it removes a known source of latency noise in benchmarks.
- **03 next.** It's a check of the NAS backup job, not code.
- **02 last.** It waits on the DB existing.
