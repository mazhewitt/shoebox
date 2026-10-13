# Hand-off: build `add-pipeline-foundations` on the Mac Studio

Written 2026-10-11 by the laptop session, which planned the build, wrote the change and then stopped. The work moves to the Mac Studio. The Studio is wired to the NAS at 1 GbE (about 110 MB/s). The laptop reaches the NAS over Wi-Fi, measured at 12–47 MB/s. Every job from here on is long, unattended and reads the NAS.

## Prompt to paste into the Studio session

> You are continuing the Shoebox build on the Mac Studio. Read `docs/handoff-studio.md` and work through it. Do the "Setup check" first and stop if any item fails. Then implement `openspec/changes/add-pipeline-foundations` with the openspec-apply-change skill, in the order under "Order of work". Start the full derive (task 1.4) as soon as tasks 1.2–1.3 pass, because it is the long pole. Ask me before any Gemini spend that isn't listed as approved below.

## Read first (don't duplicate; these are the source of truth)

- `openspec/changes/add-pipeline-foundations/`: `proposal.md`, `design.md` (D1–D11) and `tasks.md` (groups 1–10). This is the work.
- `.scratch/README.md`: the backlog, the queues, the language rule, and "Found while filing".
- `evals/RECOMMENDATION.md`: why it's Gemini Batch for the back catalogue, the 12B locally for new photos, and `eg2-blend` for search.
- `docs/machines.md`: the shares and mounts, including the Mac Studio section.
- `docs/pilot.md`: derive paths, the derive index and the stale-image rule.
- Memory in `~/.claude/projects/<repo-path-slug>/memory/`. The owner copies it from the laptop. `MEMORY.md` indexes the settled decisions (1–42), the consent statement, the language rule and the pilot lessons.

## Setup check (do this before any work)

If an item fails, stop and tell the owner. Don't work around it.

1. **Mounts are as in `docs/machines.md`.**
   - `touch ~/mnt/importantfiles-ro/x` fails with "Read-only file system".
   - A write and delete on `~/mnt/shoebox` succeed.
   - `mount` shows both as `//shoebox@192.168.1.9/...`.
   - Nothing on this machine mounts ImportantFiles read-write.
2. **Secrets.** The age key is at `~/.config/sops/age/keys.txt` (restore it with `~/radiobox/deploy/scripts/sops-key-nas.sh restore` if missing), and `sops exec-env secrets/shoebox.sops.yaml 'test -n "$GEMINI_API_KEY"'` succeeds. Until a machine has the key, a gitignored `.env` with `GEMINI_API_KEY` still works as a fallback. Never print, cat, grep for or commit a secret.
3. **Python.** `uv sync` and `uv run pytest -q` both pass, and `openspec validate add-pipeline-foundations --strict` passes.
4. **Derive tool versions match the existing derived set.** Check `uv run python -c "from shoebox.derive import tool_versions; print(tool_versions())"`. It must equal the `tools` in the last record of `~/mnt/shoebox/derived/infer-v1/index.jsonl`:
   - libvips 8.18.7;
   - pyvips 3.2.0;
   - rawpy 0.27.1 / LibRaw 0.22.1;
   - psd-tools 1.24.0;
   - numpy 2.5.3.

   uv.lock pins all of these, including `pyvips-binary`. A mismatch marks all 5,045 derived images stale, and labelling then skips them.
5. **Ollama models.** These must be pulled by these exact tags, with the same digests as on the laptop. Don't use `:latest`.
   - `embeddinggemma-2:740m-bf16`, digest `54d809644cd8`, for the image vectors;
   - `gemma4:12b`, digest `4eb23ef187e2`, for the refusal fallback.
6. **Network.** Time one large scan read (`dd if=<a >100 MB TIFF> of=/dev/null bs=4m`). Record the MB/s in `design.md`'s derive risk note.

## Rules that must not slip

The full wording is in memory and the repo. These are the ones a fresh session is most likely to trip over.

- **Never write to ImportantFiles or `/Volumes`.** Read sources only through `~/mnt/importantfiles-ro`. Write only under `~/mnt/shoebox` and the repo.
- **Never view or send:**
  - items on `evals/never-send.txt`;
  - the held-back folders in `config/shoebox.toml`;
  - other people's own libraries;
  - ID, medical or financial documents;
  - anything quarantined by the nudity screen, including `pending` items. Only the owner sees those, on a local page.
- **No face identification.** No image bytes in the repo; `evals/quarantine.jsonl` holds hashes only. Never put photos in iCloud-synced folders such as Desktop or Documents.
- **Spend.**
  - The budget is $150, and $10.04 is spent so far (`evals/spend.jsonl`).
  - Log every hosted call.
  - Ask the owner before each new Gemini spend.
  - **Approved already:** the pool5k confirmation, about $7 (owner's comment in `.scratch/bulk-captioning/02`).
  - **Not yet approved:** the task 8.6 smoke batch (about $0.03), and the bulk run.
- **Gemini introductory price ends 2026-12-31** (`config/candidates.d/gemini.toml`). After that the bulk run is about $196, which is over budget. The derive → dedup → screen → worklist path is therefore on the critical path.
- **Language rule:** Python for scripts and pipelines; Rust only for complex logic, long-running services, or speed/concurrency NFRs. Everything in this change is Python.

## What runs where

This is the M1 Studio (32 GB). radiobox shares it, through its Ollama on `:11434` (LAN-exposed on purpose; leave it) and its speech-recognition service on `:8645`. Decision 10 says not to plan Shoebox around the M1's memory. A new M5 Ultra Studio (96 GB) arrives at the end of October and becomes the long-term host (`.scratch/ingest/04`). Repeat this setup check there.

| Work | M1 Studio now? |
|---|---|
| Derive and pixel hash (1.2–1.4), inventory (2), eligibility (3), dedup (6) | Yes. They're bound by I/O and CPU, which is why we moved here. |
| Core ML nudity screen (4, 9.3, 9.4) | Yes. It's small and runs on the Neural Engine. |
| Gemini batch runner, worklist guard, label reuse (8.1–8.5), pool5k | Yes. The work is in the cloud. |
| EG2 image vectors over the library (7.1) | Possibly. The model is 1.5 GB, but measure whether it slows radiobox; run it off-hours or wait for the M5 Ultra. |
| 12B refusal fallback (8.7 run) | No. Build and test it here, but run it on the M5 Ultra or the laptop. It's only a few hundred images. |
| Owner's flag review (9.4) | Yes, on the Studio's own screen or over Screen Sharing. Claude never opens that page. |

`qwen-implementer` needs a 35B Qwen model in the local Ollama. Don't pull that onto the M1; use `opus-implementer` instead.

## Order of work

1. **Group 1.** Do 1.1 (config.yaml Languages), then 1.2 (pixel hash in the derive decode), then 1.3 (backfill over the 5,045). Then start **1.4 in the background**. Run it under `tmux` or `nohup` so it survives disconnects. Measure img/h on the first 1k and record it. The derive is resumable.
2. **While the derive runs**, build groups 2, 3, 5, 6 (code and tests, not the full runs) and 8.1–8.5, using synthetic frames and the fake batch transport.
3. **Group 4** (the screen). Then **9.3, the retro-check** over everything already sent or shown, as soon as the screen runner works. Report the flagged count (counts only).
4. **After 1.4:** 2.2, 6.5 (dedup over ImportantFiles and the 50-group spot check), 9.4 (full screen and the owner's review), and 7.1–7.3.
5. **Pool5k** (`.scratch/bulk-captioning/02`, approved). Run it through the batch runner once 8.4 is done.
6. **Last:** 9.1–9.2 (tiny images), then 10 (the end-to-end check against the fake transport), then 10.2 (backlog updates).

Commit often on `build/pipeline-foundations`. It tracks `origin`, and pushing it is fine.

## Things that stayed on the laptop

- **The owner's review answers** `~/Downloads/answers-ca9391b30efc1e35-2.json`, needed by `.scratch/eval-followups/01` (the 7 rephrasings). Ask the owner to copy it across before doing that issue. Keep it out of the repo.
- **`~/shoebox-review/`**, the eval-1k review page, which is finished. It doesn't need to move.

## Open owner actions (not blockers for this change)

- Request both Google Photos Takeouts and ask Jude about consent (`.scratch/sources/02`, `/07`). This is urgent because of the price deadline.
- DSM read-only access for the `shoebox` user to `Home Folders`, `Backup` and `Volume2` (`.scratch/sources/01`).
- Filename examples for scans (`.scratch/catalogue/04`). The backup scope check (`.scratch/ops/03`). Who uses the laptop's Ollama (`.scratch/ops/04`).

## Suggested skills and agents

- **`openspec-apply-change`** (or `/opsx:apply add-pipeline-foundations`). Use it to drive the tasks and tick `tasks.md`.
- **`opus-implementer`** agent for well-scoped TDD tasks, for example 1.2, 3.1, 5.1, 6.2, 6.3 and 8.4. Run several in parallel when they touch different files.
- **`adversarial-critic`** agent per task group before committing it. Use the spec-scenarios and test-validity lenses.
- **`diagnosing-bugs`** if the derive or the batch runner misbehaves. **`code-review`** before the change is archived.
- **`openspec-archive-change`** only once group 10 passes and the owner agrees.

The user-level skills and agents (`~/.claude/skills`, `~/.claude/agents`) are not in the repo. Copy them across with the memory folder if they're missing on the Studio.
