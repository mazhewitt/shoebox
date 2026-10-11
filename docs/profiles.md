# Image profiles

An image profile fixes every setting that changes a derived inference image's bytes. The settings live in `config/profiles.toml`. Changing any of them needs a new profile name, because model outputs record the profile name and its settings in their `params_hash`. The label `params_hash` also covers derive's `settings_hash` and tool versions, but not `status` or the `check` table.

This rule is enforced:
- Every index record carries `settings_hash`: the first 16 hex characters of the sha256 of canonical JSON of the profile's settings, leaving out `status` and the `[<profile>.check]` table, which don't change bytes.
- `shoebox derive` refuses to run if the index already has ok images made with a different `settings_hash`. Either give the changed profile a new name or, while a profile is still provisional, delete `<shoebox_root>/derived/<profile>/` and rebuild it.
- An ok record is only reused (skipped) when its `settings_hash` and `tools` both match the current ones and the file's hash matches `derived_blake3`. A tool upgrade therefore re-derives the image on the next run, and the new record names the new tool versions. The code is in `src/shoebox/derive.py`, and you run it with `shoebox derive --profile infer-v1 --items <jsonl> [--force]`. The paths and the index record are set in `docs/pilot.md` section 2.

## `infer-v1`

**Status: provisional until task 3.1 freezes it.** Task 3.1 confirms the pixel ceiling per token budget from the processor configs.

### Settings

| Setting | Value |
|---|---|
| Format | baseline JPEG, quality 92, 4:4:4 chroma (`subsample_mode=off`), optimised Huffman tables, not progressive |
| Long edge | 2048 px. Smaller images are never upscaled. Resampling is libvips `resize`, Lanczos3, done in sRGB after the 8-bit conversion. |
| Orientation | EXIF orientation applied (`autorot`), then dropped |
| Colour | An embedded ICC profile is converted to sRGB with `icc_transform` (relative colorimetric intent, 8-bit output). With no ICC profile, pixels are taken as sRGB-encoded and libvips `colourspace("srgb")` maps them, so 16-bit becomes 8-bit by a `>>8` shift. |
| Greyscale | becomes 3-band sRGB with equal channels |
| Alpha | flattened onto white |
| Tonal stretch | none. D6 allows a percentile stretch "only where flagged", and v1 flags nothing. |
| Metadata | none kept (`keep=none`): no EXIF, GPS, XMP, IPTC, ICC, comment or JFIF/APP segment. The file goes straight from SOI to the quantisation tables. The output is plain sRGB with no profile embedded. |
| Decode strictness | `fail_on=truncated`. A truncated original becomes a decode error, not a grey-padded image. |
| Clip tolerance | `[infer-v1.check] max_clip_increase_pct = 1.0`. See "Tonal clipping check" below. It doesn't change bytes and isn't in `settings_hash`. |
| Determinism | Same input, same profile and same tool versions give the same bytes. This is tested on synthetic files and was checked on the real files below across separate processes. |

**Camera raw** (NEF, DNG and other raw extensions) is rendered with LibRaw through `rawpy.postprocess`, never from the embedded or sibling camera JPEG. The record's `rendered_from` is `raw`, and `src_bits` is the bit length of the raw white level.

| rawpy setting | Value |
|---|---|
| `demosaic_algorithm` | AHD |
| `use_camera_wb` / `use_auto_wb` | true / false |
| `output_color` | sRGB |
| `output_bps` | 16. libvips then maps it to 8-bit. |
| `gamma` | (2.222, 4.5). This is LibRaw's default curve (BT.709-style), not the exact sRGB curve. |
| `no_auto_bright` / `auto_bright_thr` / `bright` | false / 0.01 / 1.0. These are LibRaw's default auto-brightening, which clips at most 1% of pixels. |
| `highlight_mode` | clip |
| `half_size`, `four_color_rgb`, `median_filter_passes`, `fbdd_noise_reduction` | false, false, 0, off |
| `user_flip` | not set, so LibRaw applies the raw's own orientation |

**PSD/PSB** files use only the merged image data, the flattened composite Photoshop saved. Layers are never re-rendered.
- RGB and greyscale files at 8 or 16 bits are read straight from the merged channels at full depth. Their embedded ICC profile is then applied by libvips.
- Other modes (CMYK, Lab, indexed, bitmap, 32-bit) go through psd-tools' `topil()` of the same merged data.
- A PSD whose version info says it has no composite is recorded as a decode error.
- libvips 8.18.7 (`pyvips[binary]`) has no PSD or magick loader, which is why psd-tools is used.

**Other formats** go through libvips buffer loaders: JPEG, PNG, HEIF/HEIC/AVIF (`heifload`), WebP and TIFF (8/16-bit, colour and grey). If an original decodes to any other loader, it's recorded as an error.

### Tonal clipping check

This is the spec's "not clipped beyond the profile's tolerance". Every ok record has a `tone` object:
- **`src_black_pct` / `src_white_pct`:** the % of samples in the decoded source (alpha flattened, before any colour conversion) below 0.5/255 or above 254.5/255 of full scale. These are the samples that would be 0 or 255 at 8 bits anyway. They are null when the source isn't grey or RGB (CMYK, Lab).
- **`out_black_pct` / `out_white_pct`:** the % of samples at 0 or 255 in the 8-bit sRGB conversion, before resizing (so resampling ringing isn't counted).
- **`max_clip_increase_pct`:** the profile's tolerance.
- **`within_tolerance`:** false when either end's clip % rose by more than the tolerance, and null when there is no source measurement.

An image outside tolerance is still written with status `ok`, because it is a usable model input. `shoebox derive` logs it with `CLIP` and counts it as "clipped" in its summary, and anyone can find it in the index via `tone.within_tolerance == false`. The expected case is a dense grey film scan with a gamma ICC profile. Converted colorimetrically, its deepest shadows fall below sRGB's first code value (see the grey scan note below). A synthetic dark gamma-2.2 grey ramp is flagged in the tests.

**Integrity and safety checks:**
- Each original is read once into memory, read-only.
- Its BLAKE3 must equal the item's `asset_id`. A mismatch is an error, because the file has changed since it was picked.
- Output is written to a temp name in the same derived directory, then renamed into place.
- The run refuses to write under any source root or under `/Volumes`. It also refuses to start if any configured source root resolves under `/Volumes`, because originals are read only through the read-only `~/mnt` mounts.
- Each original's path is resolved (following symlinks) and must stay inside its source root and outside `/Volumes`. If not, that item gets an error record.
- An index line that won't parse, such as an append cut short by a killed run or a dropped SMB link, is skipped with a warning. That asset's earlier record, if any, stands, and the next append starts on a fresh line.

**Record fields beyond docs/pilot.md section 2:** `settings_hash` and `tone`, both described above.

**Tools recorded in every index record:** libvips 8.18.7, pyvips 3.2.0, rawpy 0.27.1, LibRaw 0.22.1, psd-tools 1.24.0 and numpy 2.5.3.

### Real-file check (2026-10-10)

One original of each kind was derived from `~/mnt/importantfiles-ro/Pictures` (read-only) into a temp `shoebox_root`, and then the outputs were deleted.
- **What was measured:** every check is numeric. No image was viewed.
- **Render s:** in-memory decode to JPEG on the Mac Studio, with the original already read.
- **CLI s:** the end-to-end per-item time logged by `shoebox derive`, including the read over the mount and the hash. The originals were warm in the OS cache from the first pass.
- **Clip columns:** the percentage of output channel values at 0 or 255.
- **Determinism:** every file gave the same `derived_blake3` on a `--force` re-run in a new process. A second plain run skipped all 6.
- **Metadata:** none of the outputs had EXIF, XMP, ICC or IPTC fields.

| Kind | Source | Source bytes | Source px, bits, bands | Out px | Out bytes | Mean R/G/B | Std R/G/B | Clip 0 / 255 % | Render s | CLI s |
|---|---|---|---|---|---|---|---|---|---|---|
| TIFF (grey scan) | Scanned Photos/Archive Nikon Scans, 16-bit grey + grey ICC | 15,208,220 | 3418×2224, 16, 1 | 2048×1333 | 664,277 | 62.7/62.7/62.7 | 67.2/67.2/67.2 | 12.9 / 0.0 | 0.03 | 0.1 |
| TIFF (colour scan) | Scanned Photos, 16-bit RGB + ICC | 45,805,646 | 2227×3413, 16, 3 | 1336×2048 | 704,208 | 30.8/33.3/29.9 | 38.7/22.7/37.1 | 6.0 / 0.5 | 0.04 | 0.1 |
| NEF | D70 | 5,784,872 | 3039×2014, 12, CFA | 2048×1357 | 858,069 | 144.3/118.9/96.1 | 40.2/40.1/40.6 | 0.03 / 0.43 | 0.20 | 0.2 |
| DNG | Greece (Leica) | 26,098,176 | 5984×4000, 14, CFA | 2048×1369 | 1,327,379 | 87.6/96.1/133.5 | 50.6/46.7/75.8 | 0.03 / 0.20 | 0.67 | 0.8 |
| PSD | L1002656.psd, 16-bit RGB + alpha, 5 layers, composite present | 492,373,674 | 5711×8567, 16, 4 | 1365×2048 | 308,520 | 225.9/221.0/218.0 | 52.3/57.9/63.4 | 0.11 / 57.5 | 0.27 | 0.5 |
| JPEG | Phone Photos/Camera | 517,373 | 1448×2576, 8, 3 (no ICC) | 1151×2048 | 642,698 | 145.6/140.3/133.5 | 21.6/24.1/34.4 | 0.0 / 0.04 | 0.03 | 0.0 |

**Notes on the numbers:**
- **Grey scan, 12.9% at 0.** The source has 6.6% of values below 257, which is 1/255. Its mean ÷ 257 is 65.0, against an output mean of 62.7. The extra zeros come from converting the embedded grey (gamma) ICC profile to sRGB. Converted colorimetrically, sRGB's linear toe maps the deepest gamma-encoded shadows to 0. This is correct ICC behaviour, not a 16→8 clipping fault. Revisit it in task 3.1 if shadow detail in dense negatives matters; black-point compensation alone won't change it, because both black points are 0.
- **PSD, 57.5% at 255.** The source composite itself is pure white (65535 in all three colour channels) in 58.0% of pixels, so this reflects the source and isn't introduced by the conversion. The composite uses the full 0–65535 range.
- **Tone check on the two real scans (re-run 2026-10-10 after the check was added; numbers from the `tone` record, measured before resizing):**
  - The grey scan's black clip went from 3.5% to 14.6%. The colour scan's went from 0.5% to 4.3%, and its white clip from 0.0% to 0.7%.
  - Both are flagged (`within_tolerance: false`) at the 1.0 point tolerance. That flag is the intended signal for task 3.1 to decide whether these scans need a flagged stretch or a different shadow treatment.
  - A second run skipped both, so the settings hash and tool versions are stable across processes.
- **Raws** use LibRaw auto-brightening, so the raw rendering's exposure differs from the camera JPEG by design.

### Not done in the pilot

- **Task 3.2's 30-pair comparison isn't done.** That task compares raw renderings against camera JPEGs, to decide whether the rawpy settings above (camera WB, default curve, auto-bright) are close enough to what the camera produced. Until it's done, the raw settings stand as recorded but unvalidated.
- **Task 3.1** (the pixel ceiling per budget) hasn't frozen the 2048 px edge.
- **No real HEIC or WebP original** was checked. The `heifload` path is covered by a synthetic AVIF test.
- **No real NEF/DNG/PSD fixture is in the tests.** The raw path is unit-tested with a stand-in LibRaw. The PSD path is unit-tested with a psd-tools–written file.

### Scan orientation (task 1.3, 2026-10-11)

`infer-v1` doesn't rotate scans, and no `infer-v2` exists yet. About 4% of scans are stored sideways or upside down. No local method detects this precisely enough to rotate at derive time (E4B, 12B and EG2 were tested), and rotating the mis-oriented search targets didn't change search. Orientation is captured as `rotate_cw` in `label-v2` instead. Details are in `evals/runs/2026-10-11_scan-orientation.md` and design D6a.
