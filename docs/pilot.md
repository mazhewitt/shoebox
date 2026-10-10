# Pilot (`pilot-v0`)

An unscored, end-to-end pilot over about 45 hand-recipe files from `ImportantFiles`. Its aim is to get the eval pipeline working on real images and to learn Vally. The scored sets come later (tasks 5.x). It implements early, reduced versions of tasks 1.4, 2.3, 3.2, 4.1–4.4 and 6.1–6.4 of `openspec/changes/add-eval-harness`.

**Gemini:** included as a gated arm (see "Gemini arm" below). It runs only after `shoebox privacy check` passes. The owner confirmed the checklist on 2026-10-10.

## Rules every component follows

- **Sources are read-only.** Read originals only through `~/mnt/importantfiles-ro` (the `shoebox` user's `rdonly` mount). Never write, create temp files or open SQLite under a source root. Never use the `/Volumes/*` mounts: they're the admin user's and writable.
- **Writes go to** `shoebox_root` (`~/mnt/shoebox`, from `config/shoebox.toml`) or to the repo. Image bytes never go in the repo.
- **No photo leaves the machine.** The only model endpoint is the local Ollama on port 11434, plus local Python libraries. *(Integration note: code uses `http://127.0.0.1:11434` with proxy environment variables ignored, from `shoebox.runners.ollama.HOST`.)* **Viewing by Claude:** on 2026-10-10 the owner agreed that Claude may view their photos, after reading Anthropic's privacy policy. Claude prefers the derived infer-v1 JPEGs to originals. It never views items on `evals/never-send.txt`, and never views sources whose person hasn't consented (Jude, Kyra, Toren: default no). Otherwise, check images numerically where that's enough. Synthetic test fixtures are fine.
- **One guard implementation:** `shoebox.config.original_path(cfg, item)` (read an original: refuses `/Volumes`, `..`, and symlinks out of the source root) and `shoebox.config.check_write_location(cfg, path)` (refuses writes under `/Volumes` or a source root). Every component uses these.
- **Code layout:** Python is `src/shoebox/`. Subcommands are `src/shoebox/commands/<name>.py` with `register(subparsers)`. Tests are `tests/`. Run everything with `uv run`.

## Shared formats (the contract between components)

### 1. Pilot items: `evals/sets/pilot-v0/items.jsonl`
Written by `shoebox pilot pick`. One line per file:

```json
{"asset_id": "<blake3 hex of original bytes>", "source": "importantfiles", "relpath": "Pictures/Greece/L1003941.DNG",
 "ext": "dng", "bytes": 12345, "mtime": 1500000000.0, "bucket": "leica-dng-jpg-pair",
 "group_id": "g-<first 12 hex of the smallest member asset_id>", "group_rule": "raw_jpeg_basename"}
```

- `group_id` and `group_rule` are `null` when there's no sibling.
- `group_rule` is `raw_jpeg_basename` or `scan_export_basename`.
- The original's path is `config.sources[source].root / relpath`.
- `shoebox.ids.asset_id(path)` computes the ID.

### 2. Derived images: profile `infer-v1` (`config/profiles.toml`)
- **Image path:** `<shoebox_root>/derived/infer-v1/<asset_id[:2]>/<asset_id>.jpg`. Get it from `shoebox.derive.derived_path(cfg, profile, asset_id)`.
- **Index:** `<shoebox_root>/derived/infer-v1/index.jsonl`, one record per attempt (the latest record per asset_id wins):

```json
{"asset_id": "...", "profile": "infer-v1", "status": "ok|error", "path": "derived/infer-v1/ab/<id>.jpg",
 "derived_blake3": "...", "width": 2048, "height": 1365, "src_width": 6000, "src_height": 4000,
 "src_bits": 16, "src_kind": "jpeg|png|heif|webp|tiff|raw|psd", "rendered_from": "decoded|raw|composite",
 "error": null, "tools": {"libvips": "8.18.x", "rawpy": "...", "libraw": "..."}}
```

- `shoebox.derive.load_index(cfg, profile)` returns `{asset_id: record}`.

### 3. Label schema: `config/schemas/label-v1.json`
JSON Schema, draft 2020-12. Model output is an object with:

| Field | Type |
|---|---|
| `caption` | string, 1–3 sentences, factual, for search |
| `tags` | array of lowercase strings (free tags; the scene vocabulary arrives with task 5.5) |
| `medium` | enum: `digital`, `film_negative`, `film_slide`, `print`, `document`, `screen`, `unknown` |
| `digitiser` | enum: `camera`, `phone`, `film_scanner`, `flatbed_scanner`, `camera_rephoto`, `screenshot`, `software`, `unknown` |
| `colour_class` | enum: `colour`, `mono`, `toned`, `unknown` |
| `confidence` | object `{medium, digitiser, colour_class}`, each a number in [0, 1] |

- `medium` is what the picture originally was. `digitiser` is how it became this file.
- A digital-camera photo is `digital`/`camera`. A scanned print is `print`/`flatbed_scanner`. A scanned negative is `film_negative`/`film_scanner`.
- `unknown` means the model abstains.
- The rule baseline emits the same object with `caption: null` and `tags: []`. The schema allows a null caption, and a null caption with empty tags is valid for `runtime = "rules"` only.

### 4. Candidates: `config/candidates.toml`
- Each `[candidates.<name>]` has `runtime` (`rules` | `ollama` | `embed`), `model` (for example `gemma4:e4b-mlx-bf16`), `quant`, `prompt` (a file under `config/prompts/`), `schema`, `profile`, `think`, `temperature`, `seed` and `num_ctx`, as applicable.
- **model_id:** `rules-v1`, or `ollama/<model>`.
- **params_hash:** the first 16 hex characters of the sha256 of canonical JSON (sorted keys, no spaces) of everything that affects output:
  - the prompt **text**;
  - the schema **text**;
  - the profile name and its settings;
  - runtime, model, quant, think, temperature, seed, num_ctx and image budget (`"auto"` for Ollama);
  - the runtime version (the Ollama server version, or the package versions).
- Get it from `shoebox.store.params_hash(params)`.
- *(Integration note.)* The profile entry is `{name, settings, settings_hash, tools}`. `settings` are the byte-affecting settings (`shoebox.derive.byte_settings`: no `status`, no `[<profile>.check]` table). `settings_hash` is derive's. `tools` are derive's tool versions, because a libvips/LibRaw upgrade re-derives images under the same profile name.
- *(Integration note.)* `shoebox label` only labels an item whose latest derive-index record is `ok` and carries that same `settings_hash` and `tools`. Otherwise it's skipped with a reason, never labelled from a stale image.

### 5. Output store (design D2)
- **Path:** `<shoebox_root>/results/labels/<model_dir>/<params_hash>/part-NNNN.jsonl`, with `params.json` alongside. `model_dir` is `model_id` with `/` → `__` and `:` → `_`.
- **Record format**, one line per output, append-only:

```json
{"asset_id": "...", "model_id": "ollama/gemma4:e4b-mlx-bf16", "params_hash": "...", "status": "ok|invalid|error",
 "output": {...label-v1...} | null, "raw": "<model text, kept for invalid>", "error": null,
 "usage": {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": null}, "latency_ms": 0, "usd": 0.0,
 "machine": "<hostname, chip>", "runtime": "ollama 0.40.2", "ts": "<ISO 8601 UTC>"}
```

- An existing `(asset_id, model_id, params_hash)` key is a cache hit and isn't re-run. `error` (transport failure) records may be retried; `ok` and `invalid` are final.
- **Python API** (`shoebox.store`): `params_hash`, `store_dir`, `lookup(...)`, `append(...)` and `iter_records(...)`, plus `index(...)`, `iter_dir(d)`, `index_records(records)` and `store_dirs(cfg, kind)`. Readers outside Python (the Vally executor) use the same rule: the first final record per asset, else its latest error, with torn lines skipped.
- *(Integration note.)* Embedding runs (`runtime = "embed"`) use the same record format under `results/embeddings/<model_dir>/<params_hash>/`, not `results/labels/`. They aren't label-v1 records, so the sheet and Vally never see them.

### 6. CLI surface
| Command | What it does |
|---|---|
| `shoebox pilot pick` | writes `evals/sets/pilot-v0/items.jsonl` and `README.md` |
| `shoebox pilot snapshot --out F` / `shoebox pilot verify-untouched --snapshot F` | sources-untouched check over the pilot originals and their folders |
| `shoebox derive --profile infer-v1 --items evals/sets/pilot-v0/items.jsonl` | writes the derived images and the index |
| `shoebox label run --candidate <name> --items <jsonl>` | fills the store (a cache hit skips) |
| `shoebox label one --candidate <name> --asset-id <id>` | prints one record as JSON (cache-aware). Used by the Vally executor in live mode |
| `shoebox label resolve --candidate <name>` | prints `{"candidate", "model_id", "params_hash", "store_dir"}` |
| `shoebox embed run --candidate eg2-bf16 --items <jsonl>` and `shoebox embed search --candidate eg2-bf16 "query"` | EmbeddingGemma 2, if it can be obtained |
| `shoebox eval gen --set pilot-v0 --candidates a,b,c` | writes `evals/vally/generated/pilot-v0/eval.yaml` and `candidates.json` |
| `shoebox eval report --results <vally results.jsonl>` | writes `evals/runs/<date>_pilot-v0.json` and `.md` |
| `shoebox eval search-queries / search-run / gen-search / search-report --set pilot-v0` | known-item search eval: owner notes → queries, rankings under `<shoebox_root>/results/search/`, Vally stimuli, `evals/runs/<date>_pilot-v0-search.{json,md}` (see `docs/evals.md`) |
| `shoebox pilot sheet` | writes `<shoebox_root>/results/pilot-v0/sheet.html`, a local contact sheet and blind labelling form that exports `labels.jsonl` |

### 7. Owner labels (optional for the pilot): `evals/sets/pilot-v0/labels.jsonl`

```json
{"asset_id": "...", "medium": "...", "digitiser": "...", "colour_class": "...", "notes": "",
 "labelled_by": "owner", "method": "blind", "ts": "..."}
```

Graders treat a missing label as **unlabelled**, which is reported separately and is neither a pass nor a fail. After labels are added, `vally grade` re-scores with no model call.

## Gemini arm (added on request; gated)
- **Key:** `GEMINI_API_KEY` is loaded from `.env` (gitignored, so never commit it). Code reads the env var only and never logs or prints the key.
- **Runner:** `shoebox/runners/gemini.py`, runtime `gemini`. It calls `generateContent` with:
  - model `gemini-3.8-flash`;
  - `media_resolution` low or medium;
  - thinking level `low`;
  - a JSON-schema response;
  - no tools, no grounding, no Interactions storage.

  It sends derived infer-v1 bytes inline, never originals.
- **Pre-flight (`shoebox privacy check`):** the runner refuses unless all of the following hold:
  - `evals/privacy-checklist.toml` has every item true and `confirmed_on` within the last 90 days;
  - the source's `hosted_consent` is true for the item;
  - the item isn't in `evals/never-send.txt`;
  - the run's estimated USD is under `max_usd_per_run` (pilot: $2).

  Spend is appended to `evals/spend.jsonl`, and the cumulative total is checked against $150.
- **Price** (research/mvp-models.md, introductory until 2026-12-31): $0.75/1M input, $3.75/1M output, including thinking. Record the `price_date`.

### Runner plug-in interface (all runtimes)
```python
# shoebox/runners/<runtime>.py
def runtime_version(cand: dict) -> str: ...
def preflight(cand: dict, items: list[dict], cfg) -> list[dict]: ...  # optional; raise to refuse; returns items allowed
def run_one(cand: dict, item: dict, image_path: Path, cfg) -> dict: ...
#   -> {status, output, raw, error, usage, latency_ms, usd}; the framework adds the key fields, machine, runtime, ts
```
`shoebox.runners.get(runtime)` imports `shoebox.runners.<runtime>`. The framework (`shoebox label`) calls `preflight` when it's present.
