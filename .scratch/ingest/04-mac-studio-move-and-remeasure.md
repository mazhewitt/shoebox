# Move ingest to the M5 Ultra Mac Studio and re-measure

Status: ready-for-human (waiting on hardware: the M5 Ultra Mac Studio arrives end of October 2026)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Task 9.4** (`openspec/changes/add-eval-harness/tasks.md`): re-measure throughput on the M5 Ultra Mac Studio when it arrives, and re-project.
- **Decision 10:** the M5 Ultra Studio (96 GB) is the long-term host and ingest machine. It can likely run the same models as the laptop, so one `model_id` everywhere becomes feasible. The current M1 Studio (32 GB) is busy with radiobox and is not used for Shoebox.
- **Decision 17:** it runs native, with Python via `uv` under launchd. No Docker.
- **Fingerprints.** A run is fingerprinted by `{model_digest, runner}` (`runners/ollama.py`). The same digest on both machines means the same `model_id`, so stores written on either machine are interchangeable.

## What

- **Setup.**
  - Install `uv`, Ollama (bound to localhost: `ops/04`), the repo and `config/`.
  - Mount the NAS shares read-only, like `~/mnt/*-ro` on the laptop.
  - The `shoebox_root` share is read-write.
- **Pull the same model tags** (`gemma4:12b` / g12b-q4, `e4b-mlx-bf16`, EG2 bf16). Check that `fingerprint()` digests match the laptop's.
- **Re-measure**, on the eval-1k sample:
  - 12B `label-v2` img/h, and EG2 image and caption embedding;
  - latency, with no other Ollama clients (the laptop's numbers had LAN-client noise, see the pilot notes).
- **Re-project** library hours for incremental volume (about one Takeout every 2 months) and for a full 12B backfill.
- **Replay check.** Run `shoebox label replay` on 50 eval-1k items on the Studio and compare with the laptop's store: valid rate, tag overlap, and the caption embedding cosine.

## Done when

- A run note `evals/runs/<date>_studio-remeasure.md` covers:
  - digest match yes/no per model;
  - img/h per stage, Studio vs laptop;
  - the re-projection;
  - the replay comparison.
- `shoebox ingest --dry-run --machine studio` runs there.

## Depends on

- The hardware.
- `ops/04` (localhost bind).
- 01 (ingest) for the final check; the re-measure itself only needs the runners.

## Comments
