# `.scratch/` — the build backlog

This is the informal half of a two-tier tracker, with the same conventions as radiobox's `.scratch/`.

- **Here:** issues still being triaged or waiting on a decision.
- **`openspec/changes/<name>/`:** formal work. An issue moves there when it's promoted, by proposal, design and tasks.
- **Status:** the `Status:` line inside each file is the truth; this index is only a map. List the open queue with:

```sh
grep -rl "^Status: ready-for-agent" .scratch --include="*.md" | grep -v /archive/
```

## Where we are (2026-10-11)

**The eval phase is done.** `openspec/changes/add-eval-harness` is complete except task 7.4. Its decision is in `evals/RECOMMENDATION.md` (decision 41):
- **Back catalogue:** Gemini 3.8 Flash `label-v2` through the Batch API.
- **New photos:** Gemma 4 12B `label-v2`, run locally.
- **Search:** `eg2-blend` (EmbeddingGemma 2 image vector + caption vector, w = 0.5).

**MVP:** sort, catalogue and describe every photo for search (decisions 35 and 36). The pipeline runs Takeout + Drive scans → metadata merge → cross-source dedup → catalogue and describe → search.

**After the MVP:** faces (detection, clustering, naming). Also raw ↔ processed scan pairing, automatic scan processing, and video content analysis. These are not filed yet.

**Already built** (Python, `src/shoebox/`):
- inventory over the read-only ImportantFiles mount, with BLAKE3 asset IDs;
- `derive` (the `infer-v1` JPEGs);
- the keyed results store;
- Ollama, Gemini and external (Opus) caption runners, with privacy guards;
- EG2 embedding;
- the eval harness.

**Not built yet:** a database, an API, a UI, dedup, metadata merge, Takeout or Drive acquisition, and the bulk run.

## The plan: features in build order

Each directory has a `README.md` that orders its issues and names the gate each must pass. Across features, the order is below. Arrows are hard dependencies; anything without one can run in parallel.

| # | Directory | What it delivers | Depends on |
|---|---|---|---|
| 1 | [`sources/`](sources/README.md) | Every photo share mounted read-only, Takeout landed and verified, Drive scans mirrored once, per-person consent, skip rules, a complete inventory (Gate A) | owner DSM and Takeout actions |
| 2 | [`catalogue/`](catalogue/README.md) | One metadata record per asset with per-field provenance: EXIF, Takeout sidecars, filename parsers, date kinds, places, colour/mono, capture axes, thumbnails, EG2 image vectors, videos catalogued (Gate B) | 1 |
| 2a | [`sensitive-content-screen/`](sensitive-content-screen/README.md) | Local nudity/explicit classifier; flagged assets quarantined (no hosted send, no Claude viewing, hidden from family) until the owner rules on a local review page | `infer-v1` images; gates 4 and every ingest run |
| 3 | [`dedup/`](dedup/README.md) | Exact, pixel and near-duplicate groups; captures (Live/Motion, RAW+JPEG, edits); a canonical member, so each capture is captioned once (Gate C) | 2 |
| 4 | [`bulk-captioning/`](bulk-captioning/README.md) | Gemini Batch runner; pool5k confirmation (7.4); refusal fallback; tiny images; colour-filter source; then the back-catalogue run itself | runner and 7.4 now; the bulk run → 1, 3 |
| 5 | [`search/`](search/README.md) | SQLite schema v1; importing the keyed result stores; FTS5 + EG2 blend + filters; a parity eval proving the production path reproduces the eval-1k numbers | 2 (schema), 4 (captions) |
| 6 | [`web-app/`](web-app/README.md) | API on the Mac Studio, image/thumbnail serving, Cloudflare Access auth, search UI, rotate and corrections as human labels (Gate D) | 5 |
| 7 | [`ingest/`](ingest/README.md) | `shoebox ingest`: a per-asset stage ledger, scheduled Takeout and Drive pulls, local 12B captioning for new photos, the move to the Mac Studio (Gate G) | 1–5 |
| 8 | [`ops/`](ops/README.md) | Private git remote, Litestream DB → NAS → GCS, results-store backup, Ollama bound to localhost | 5 for the DB parts; the rest now |
| 9 | [`eval-followups/`](eval-followups/README.md) | Close out the eval change: owner-rephrase subset, older harness bugs, archive the OpenSpec change, rewrite PLAN.md to match decisions 1–41 | none |

**Critical path to a usable MVP:** 1 → 2 → 2a → 3 → 4 (bulk run) → 5 → 6.

**Spend gate:** every new Gemini spend is approved by the owner first and logged in `evals/spend.jsonl`. Remaining budget: $139.96 of $150.

## Queues (2026-10-11)

**`ready-for-agent`, can start now:**

| Directory | Issues |
|---|---|
| `bulk-captioning/` | 01 (Batch runner), 03 (refusal fallback), 04 (tiny images), 07 (store `rotate_cw`) |
| `catalogue/` | 07 (colour/mono heuristic), 12 (EG2 image vectors). 02, 05, 06, 09 and 10 follow once 01 is decided |
| `dedup/` | 01 (exact/pixel groups), 02 (capture grouping) |
| `sources/` | 04 (consent eligibility), 05 (skip rules) |
| `sensitive-content-screen/` | 01 (Core ML nudity screen), 02 (quarantine stage), 03 (owner review page) |
| `eval-followups/` | 01 (rephrase subset), 02 (agreement matrix), 05 (PLAN.md rewrite; owner reviews the diff) |

**Owner actions** (`ready-for-human` / `needs-info`):
- **DSM:** read-only access to the other shares (`sources/01`).
- **Takeout (most urgent: it is the long pole before the price deadline):** check backup quality, request both accounts' exports and ask Jude about consent (`sources/07`), then set up scheduled Takeout (`ingest/03`).
- **Backups:** private git remote (`ops/01`); check the NAS backup job's scope (`ops/03`).
- **Ollama:** confirm what uses each Ollama on the LAN before binding it to localhost (`ops/04`).
- **Filenames:** examples of your scan-naming conventions (`catalogue/04`).
- **Spend:** approve about $7 for the pool5k confirmation (`bulk-captioning/02`).
- **Hardware:** the Mac Studio arrives (`ingest/04`).

**Decisions to make** (`needs-triage`, each file has a recommendation):
- Rust scanner or Python (`catalogue/01`; recommends Python).
- API stack (`web-app/01`; recommends Rust serving + a Python ranking service).
- Drive scans: Cloud Sync or rclone (`sources/03`).
- How rules and the caption model combine for capture axes (`catalogue/08`).
- Near-duplicate method and labels (`dedup/03`).
- Colour-filter source (`bulk-captioning/05`).
- Document-screen method (`bulk-captioning/08`).
- Mixed Gemini/12B captions in one index (`search/06`).
- Whether to caption once every source is deduped, or in stages by source (`dedup/06`; recommends staging unless the Takeout lands by about mid-November).
- Enum leakage, likely wontfix (`eval-followups/03`).

## Found while filing (these change the plan)

- **Price deadline.** Gemini's introductory price is valid only until **2026-12-31** (`config/candidates.d/gemini.toml`). After that the batch bulk run is about $196, which is over budget. The bulk run should finish in 2026, or be re-quoted.
- **The $98 quote is too high for ImportantFiles.** It assumed 71.6k images, but there are only 52,481 unique asset_ids (27% are byte-identical copies). Capture dedup will cut it further; Takeout and the other shares will add to it. Re-quote from `dedup/04`'s worklist. `dedup/06` makes every caption run go through dedup first.
- **`Pictures/Google Photos` (27,559 images) is not a Takeout:** there are no JSON sidecars. Its overlap with the real Takeout shows up only after both are inventoried (`sources/07`).
- **Only eval-1k has had the document screen.** That screen found about 1% sensitive, i.e. roughly 500 images in the bulk set. Now `bulk-captioning/08`.
- **No issue owned the library-wide EG2 image-vector pass.** Now `catalogue/12`.
- **The inventory's date order contradicts decision 27.** It ranks the scan date above a filename date (`catalogue/05`).
- **Mixed caption sources.** Gemini and 12B captions in one index were never measured together (`search/06`).
- **Where the DB and text encoder live.** PLAN.md puts the DB and the query encoder on the NAS; decision 3 puts them on the Mac Studio (`eval-followups/05`).
- **Off-laptop copies.** The repo has no git remote yet (`ops/01`).
- **Ollama.** The laptop's Ollama still listens on all interfaces, and radiobox uses the M1 Studio's Ollama over the LAN (`ops/04`).
- **Task 4.3** is done but unticked; the owner rephrased 7 queries, not 8 (`eval-followups/01`, `04`).

## Conventions

- One feature per directory; issue files are numbered `NN-short-name.md`. Each directory's `README.md` holds the order and the gates.
- Each issue opens with the title, then `Status:`, `Reported:` and `Reporter:` lines. Conversation goes at the bottom under `## Comments`, as dated bullets.
- **Status strings:**
  - `needs-triage`: a decision is still open;
  - `needs-info`: waiting on the owner for a fact;
  - `ready-for-agent`: fully specified, an AFK agent can do it;
  - `ready-for-human`: the owner has to act (DSM, Google, spend approval, hardware);
  - `blocked`: waiting on another issue, named in the line;
  - `done`;
  - `wontfix`.
- Each issue names the evidence that would close it: a command, a report or a gate. Then a later sweep can close it without re-deriving.
- **A decision can expire.** If an issue rests on something being impossible or too expensive, say what would reopen it.
- **Decision numbers** (e.g. "decision 13") refer to the owner's settled decisions, recorded in Claude's project memory (`shoebox-plan-decisions`). Once `eval-followups/04` rewrites PLAN.md, they will live there.
- **Promotion:** when an issue (or a whole directory) is ready, create an OpenSpec change for it and carry the content over. Then delete the directory, or move it to `archive/` if its history is worth keeping.
- **Rules that bind every issue:**
  - sources are read-only, via `~/mnt/*-ro` only, never `/Volumes`;
  - no image bytes in the repo;
  - never view or send anything on `evals/never-send.txt`;
  - hosted models get only consented sources;
  - no face identification;
  - never print or commit `GEMINI_API_KEY`.
