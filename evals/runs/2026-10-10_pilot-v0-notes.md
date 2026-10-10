# pilot-v0 run log, 2026-10-10

Factual log of the first end-to-end pilot run (docs/pilot.md). The figures are in `2026-10-10_pilot-v0.md` / `.json`. This file has no captions or tags, only aggregates.

- **Machine:** Mazdas-MBP, Apple M5 Max, 128 GB. Ollama 0.40.2 on 127.0.0.1:11434. Vally 0.18.0, Node 24.16.0.
- **Set:** `evals/sets/pilot-v0/items.jsonl`, 45 items (items_sha256 `7602e8b1dc56…`). Unlabelled: no `labels.jsonl`.
- **Local only.** Every label and embedding in this report came from localhost Ollama or local libraries. No image was viewed; all image checks were numeric.

## Derive

- **Command:** `shoebox derive --profile infer-v1 --items evals/sets/pilot-v0/items.jsonl`.
- **Result:** 45/45 ok, `settings_hash` 99b6818993fdd293.
- **Tools:** libvips 8.18.7, pyvips 3.2.0, rawpy 0.27.1, LibRaw 0.22.1, psd-tools 1.24.0, numpy 2.5.3.

## Label and embed stores (`~/mnt/shoebox/results/`)

Each label store holds 45 records with 45 distinct assets, all `ok`: no invalid and no error records.

| candidate | model_id | params_hash | store dir (under results/) | wall (UTC) | p50 / p90 ms | img/h |
|---|---|---|---|---|---|---|
| rules-v1 | rules-v1 | 025c960720d135f7 | labels/rules-v1/025c960720d135f7 | 13:11:17–13:11:23 | 49 / 64 | 64,619 |
| e4b-bf16 | ollama/gemma4:e4b-mlx-bf16 | ba2e8d5285b6a818 | labels/ollama__gemma4_e4b-mlx-bf16/ba2e8d5285b6a818 | 13:13:33–13:16:14 | 3,428 / 3,715 | 1,050 |
| e4b-q4 | ollama/gemma4:e4b | 6b3c45f6e34ecf3c | labels/ollama__gemma4_e4b/6b3c45f6e34ecf3c | 13:16:49–13:19:12 | 2,966 / 3,424 | 1,182 |
| g12b-q4 | ollama/gemma4:12b | 4a05141c7ac62278 | labels/ollama__gemma4_12b/4a05141c7ac62278 | 13:20:19–13:24:44 | 5,866 / 6,138 | 614 |
| g31b | ollama/gemma4:31b | 783af945a0bae97e | labels/ollama__gemma4_31b/783af945a0bae97e | 13:25:21–13:37:01 | 15,001 / 18,655 | 229 |
| eg2-bf16 (image only) | ollama/embeddinggemma-2:740m-bf16 | 5aebc3cd00eecca8 | embeddings/ollama__embeddinggemma-2_740m-bf16/5aebc3cd00eecca8 | 13:37:26–13:37:36 | ~160 | ~22k |
| eg2-bf16 + captions from e4b-bf16 | same | f128202d321bbb7a | embeddings/…/f128202d321bbb7a | 13:37:37–13:37:48 | ~160 | ~22k |
| eg2-bf16 + captions from g31b | same | 4b1ac2be9496959b | embeddings/…/4b1ac2be9496959b | 13:37:49–13:38:00 | ~160 | ~22k |

**Latency caveat.** The candidates ran one at a time. Another Ollama client on the LAN (192.168.158.129) kept reloading `gemma4:e4b-nvfp4` and sent occasional chat requests. These overlapped 4, 2, 3 and 6 of the 45 items for e4b-bf16, e4b-q4, g12b-q4 and g31b, so the latencies are slightly contaminated.

**MLX context.** The MLX runner (e4b-bf16) showed a 131072 context in `ollama ps`, although the candidate sets num_ctx 8192.

**Search smoke test.** An EG2 search smoke test (10 generic queries × image, caption and blend modes) ran. Its table is at `~/mnt/shoebox/results/pilot-v0/search-smoke-2026-10-10.txt`.

## Eval (Vally replay)

| step | command | time | result |
|---|---|---|---|
| gen | `uv run shoebox eval gen --set pilot-v0 --candidates rules-v1,e4b-bf16,e4b-q4,g12b-q4,g31b` | 1.8 s | 45 stimuli, 0 skipped. No candidate dropped: all 5 stores full. |
| lint | `cd evals/vally && npm run lint` | 0.3 s | valid (`--strict`) |
| eval | `npm run eval -- --model rules-v1,e4b-bf16,e4b-q4,g12b-q4,g31b` | 87.6 s | exit 0. 225 trial-results, all `success`, 0 errors, no missing keys. |
| report | `uv run shoebox eval report --results <results.jsonl> --set pilot-v0 --date 2026-10-10` | 0.1 s | `evals/runs/2026-10-10_pilot-v0.{json,md}`, replay, gate-eligible, no blockers |

- **Results:** `~/mnt/shoebox/results/vally/pilot-v0/2026-10-10T13-41-28-145Z/results.jsonl`. It stays out of git because it holds captions and paths.
- **Report vs stores:**
  - Latencies, img/h and token totals in the report match the stores.
  - The per-field distributions match the inference step's counts. For example, `film_negative` is 2/4/12/9 and `film_scanner` is 2/4/12/10 for e4b-bf16/e4b-q4/g12b-q4/g31b.
  - The agreement matrices were recomputed independently from the stores and match to 0.1 pt.
- **Agreement counts abstentions:** two candidates that both answer `unknown` count as agreeing.
- **Headline figures (unlabelled set, so no accuracy):**
  - Validity is 100% for all five candidates.
  - Abstain rate (medium / digitiser / colour_class):

    | candidate | medium | digitiser | colour_class |
    |---|---|---|---|
    | rules-v1 | 42.2% | 15.6% | 13.3% |
    | e4b-q4 | 13.3% | 15.6% | 8.9% |
    | e4b-bf16 | 4.4% | 2.2% | 0% |
    | g12b-q4 | 4.4% | 4.4% | 0% |
    | g31b | 4.4% | 4.4% | 0% |

  - Pairwise agreement is highest between g12b-q4 and g31b: medium 84.4%, digitiser 80.0%.
  - Next is e4b-bf16 against e4b-q4: medium 82.2%, digitiser 75.6%.
  - colour_class agreement among the models is 91–100%.

## Re-grade with no inference (plumbing check)

1. **Temporary labels.** I wrote `evals/sets/pilot-v0/labels.jsonl` for 3 items (7a6f1b77a47f…, 9e35dac21dd0…, fc35dd02ae73…). Its "expected" values were copied from rules-v1's own predictions, `labelled_by: plumbing-check`. **They are not ground truth.**
2. **Regenerate.** `shoebox eval gen` gave 3 stimuli `labelled=yes` and 9 `expected` values. Lint passed.
3. **Grade.**
   - With `~/mnt/shoebox/results/labels` renamed away so no store was readable, I ran `npm run -s grade < results.jsonl > regraded-plumbing.jsonl`. It took 0.43 s and exited 0, with all 225 trials re-graded. No executor ran and no model was called.
   - Trial passes went from 225 to 216. 45 grader verdicts changed from "unlabelled pass" to scored: 24 pass and 21 fail. rules-v1 passed all 9 of its fields, as it must by construction.
   - A report on the re-graded file showed labelled n=3 with accuracy, coverage and precision filled in. It was written to a scratch directory and then deleted.
4. **Clean-up.**
   - I deleted `labels.jsonl` and re-ran `shoebox eval gen`. The spec and `candidates.json` are byte-identical to the pre-label versions: 0 labelled stimuli.
   - The re-graded file stays only under shoebox_root, as `…/2026-10-10T13-41-28-145Z/regraded-plumbing.jsonl`. It reflects the fake labels: don't report from it.

## Sheet and sources-untouched

**Sheet.** `uv run shoebox pilot sheet` (0.7 s) wrote `~/mnt/shoebox/results/pilot-v0/sheet.html`.
- It has 5 label candidates, 45 items and a blind slice of 5. The other 40 items carry all 5 outputs, all `ok`.
- It embeds no image bytes; images load by relative path, and all 45 derived files exist.
- It was generated at 13:44 UTC, before any Gemini store existed (see below).

**Sources untouched.** `uv run shoebox pilot verify-untouched --snapshot …` reported untouched for all three snapshots:

| snapshot | taken (UTC) | checked | time |
|---|---|---|---|
| `snapshot-run.json` | 13:09 | 25,215 files and 242 folders | 152 s |
| `snapshot-prepick.json` | 12:53 | 25,215 files and 242 folders | 204 s |
| `snapshot-before.json` | 12:30 | 45 files and 25 folders | 28 s |

## Code fixes during the run

- **`src/shoebox/derive.py`** (with a regression test in `tests/test_derive.py`), from the derive step:
  - **Problem:** libvips' 50 MB libtiff metadata cap rejected Photoshop-saved TIFF scans that carry large layer tags (tag 37724).
  - **Fix:** TIFF buffers are now loaded with `unlimited=True`.
  - **Effect:** pixels, `settings_hash` and tools are unchanged.
- **No other code, prompts, schemas, profiles or candidate configs changed in this run.** No params_hash changed.
- **Tests:** `uv run pytest -q` gives 367 passed, 1 skipped. The extra test is from the concurrent change below.

## Not run

- **Gemini arms.** gemini-low and gemini-medium weren't part of this run and aren't in this report: sending photos to a hosted API is pending the owner's explicit approval.
- **A separate process did start Gemini.** At 13:50 UTC a separate process (another session, not this run) started `shoebox label run --candidate gemini-low`. It was still running when this log was written.
  - It wrote `results/labels/gemini__gemini-3.8-flash/a833e46c578af35d` (14 ok records when last checked) and `evals/spend.jsonl` (cumulative about $0.027).
  - It also edited `src/shoebox/runners/gemini.py` and `tests/test_gemini.py` (per-modality usage) at 13:50 UTC.
  - None of that is used here. A later `shoebox pilot sheet` will pick that store up automatically, because the sheet lists every label store dir.
- **Accuracy metrics.** There are no owner labels yet, so there is no accuracy, coverage or precision, and no gates.
- **Profile tasks still open:** task 3.1 (pixel ceiling) and task 3.2 (the 30-pair raw-vs-camera-JPEG comparison).

## Addendum: Gemini arms and the combined report (2026-10-10, after owner approval)

- The owner explicitly approved sending the 45 pilot derived images to Gemini. `shoebox privacy check` passed before the run.
- **gemini-low** (`a833e46c578af35d`): 44 ok, 1 invalid, $0.0861, about 262 image tokens per image.
  - The invalid item is `ef193384024c` (old-prints TIFF): `finishReason=PROHIBITED_CONTENT`. It was billed anyway, and gemini-medium labelled the same image without trouble.
- **gemini-medium** (`d8d9173ccb8b6b07`): 45 ok, $0.0969, about 537 image tokens per image.
- No thinking tokens were reported at thinking level LOW. That makes `est_thinking_tokens=1000` in the config about 3× too high.
- Total spend so far is $0.183, in `evals/spend.jsonl`.
- **Privacy incident:** asset `528fe060b56b` (low-jpg-scan) is a medical ultrasound with a patient's name and exam date printed on it. It was sent in both Gemini arms before anyone noticed. It's now on `evals/never-send.txt`.
- **Combined replay:** 7 candidates × 45 items = 315 trials, in `~/mnt/shoebox/results/vally/pilot-v0/2026-10-10T14-02-47-050Z/results.jsonl`. The report is `2026-10-10_pilot-v0.md`.
- `regraded-plumbing.jsonl` in the earlier Vally run folder was graded against fake labels. Don't report from it.
