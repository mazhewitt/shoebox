# Capture axes: medium × digitiser, and taking camera vs digitising device

Status: needs-triage (decide how rules and the caption model combine), then ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Decision 28.** Capture is two axes:
  - medium: colour negative, B&W negative, slide, print, digital;
  - digitiser: none, phone, digital camera, film or flatbed scanner, lab, screenshot/messaging.

  The taking camera is stored separately from the digitising device. It's inferred from format (6×6 → Hasselblad, 35 mm → Leica) only where that's unambiguous, otherwise from the filename. Provenance is recorded.
- **Two sources exist today:**
  - the rule baseline `rules-v1` (`src/shoebox/runners/rules.py`: EXIF Make/Model/Software, bands, XMP, size, path), whose `medium`/`digitiser` match the `label-v1/v2` schema enums;
  - the caption models' `medium`/`digitiser` fields.
- **Gemini `label-v2` versus the Opus reference** (`evals/runs/2026-10-11_eval-1k-caption-audit.md`):

| Field | All photos | Scans |
|---|---|---|
| medium | 0.958 | 0.854 |
| digitiser | 0.931 | 0.861 |

  Neither is ≥ 0.98, so neither is gated yet.
- **These axes matter beyond display:**
  - date precedence for scans (05);
  - dedup canonical choice (`dedup/`);
  - the raw ↔ processed pairing after the MVP.

## What

- **Combiner:** `src/shoebox/catalogue/capture.py`. Per field, it takes the rule output and the bulk caption output (Gemini `label-v2` once the bulk run lands; 12B for new photos) and emits a value only above a cut-off. Otherwise the value is `unknown`, with the scores kept (decision 14).
- **Taking camera and digitiser:**
  - The taking camera comes from EXIF for digital files; for scans, from the filename (04) or the frame aspect ratio.
  - A 1:1 aspect means 6×6, i.e. Hasselblad, but only if no other square-format camera is known. Ask the owner.
  - The digitiser comes from scanner Make/Model/Software (Nikon Coolscan, Epson) and the TIFF tags.
- **Eval:** use the Opus reference labels already on the eval-1k audit sample, plus a scan-stratified top-up (scans are where every source is weakest). Tune on dev, score test once, and report per stratum.

## Done when

- `evals/runs/<date>_capture-axes.md` reports precision per class and stratum at the chosen cut-offs, with coverage (the share of `unknown`) shown. Every emitted class is ≥ 0.98, else that class is withheld.

## Depends on

02, and 04 for scan filenames. The caption-model input arrives with `bulk-captioning/`. The rules can be gated first and the model added after.

## Open questions

- Is a rules + caption-model vote worth the complexity, or should the gate decide per field which single source wins?
- Which square-format cameras exist besides the Hasselblad? (Needed for the 1:1 → 6×6 → Hasselblad rule.)

## Comments
