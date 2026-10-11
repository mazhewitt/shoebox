# Ingest: new photos after the bulk run

**Goal.** After the back catalogue is done, keep the catalogue current with one batch command.
- `shoebox ingest` runs after each scheduled Takeout pull and each Drive-scan sync. It uses the same code path as the bulk run, records which stages and `model_id`s each asset has had, and runs only what's missing.
- New photos are captioned locally by Gemma 4 12B `label-v2` (decision 41), not by Gemini.
- There is no always-on worker or job queue (decision 13).
- The long-term host is the M5 Ultra Mac Studio, arriving end of October 2026 (decision 10). PLAN.md's Gate G closes the feature.

| # | Issue | Gate to pass before it lands |
|---|-------|------------------------------|
| 01 | [`shoebox ingest` and the per-asset stage ledger](01-ingest-command-and-stage-ledger.md) | The ledger is rebuilt from the existing keyed stores with no inference. A second run on an unchanged tree does nothing. |
| 02 | [Local 12B captioning for new photos](02-local-12b-captioning-for-new-photos.md) | The label-source precedence rule is written and tested. A 12B-captioned asset is findable through the same `eg2-blend` path. |
| 03 | [Scheduled Takeout and Drive pull](03-scheduled-takeout-and-drive-pull.md) | `sources/` Gate A has passed on the first full Takeout. Known files are skipped by hash, and deleted-upstream items are marked, not dropped. |
| 04 | [Move to the Mac Studio and re-measure](04-mac-studio-move-and-remeasure.md) | The hardware is here. The same `model_id`s are reproduced on the Studio (digest match), and throughput is re-measured. |
| 05 | [Gate G: fresh batch end to end](05-gate-g.md) | 01–04 have landed. A fresh batch goes through end to end, and the eval suites still pass. |

## Sequencing rationale

- **01 first.** It is the seam everything else plugs into, and it can be built against the stores that already exist under `~/mnt/shoebox/results/`.
- **02 and 03 are independent.** 02 needs only 01. 03 needs a landed Takeout (`sources/`) and the dedup rule that tells a new file from a known one (`dedup/`).
- **04 waits on hardware.** Its measurements matter only once 01–03 exist to run there.
- **05 last.** It is the acceptance test for the whole incremental path.
