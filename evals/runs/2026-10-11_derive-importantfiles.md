# Derive over ImportantFiles (task 1.4), 2026-10-11

Profile `infer-v1` (settings_hash `99b6818993fdd293`, unchanged). Mac Studio (M1), NAS over SMB at about
100 MB/s, read-only mount, `--workers 4`. Items: every unique ImportantFiles image asset in
`frame-2026-10-10.parquet` (52,481 asset ids). Counts only.

## Throughput

| Window | Images | img/h |
|---|---|---|
| First 1k (19:08:01Z to about 19:11:30Z) | 1,182 | about 20,000 |
| Whole run (19:08:01Z to 22:51Z) | 47,436 processed (5,045 pool5k already derived) | about 12,800 |

The rate fell through the evening as the run moved into large raws and scans (Google Photos NEFs, film scans);
it ranged from about 5,000 to 31,000 img/h per 10-minute sample.

## Coverage

| Result | Assets |
|---|---|
| ok, with an ok pixel hash | 52,371 |
| error (listed) | 110 |
| **total** | **52,481** |

A re-run retried every error record: 1 recovered (an Ultra HDR JPEG, fixed by mapping libvips's
`uhdrload_buffer` to its SDR base image), 110 stayed errors. A second re-run is a no-op.

2,017 of the ok images clipped beyond the tone tolerance (recorded per image in the derive index).

## Errors by kind

| Kind | Assets | Handling |
|---|---|---|
| GIF (`unsupported file type .gif`) | 90 | Not decoded (owner, 2026-10-11). Listed in `evals/derive-unsupported.jsonl` |
| BMP (`unsupported file type .bmp`) | 3 | Not decoded. Listed in `evals/derive-unsupported.jsonl` |
| Not an image (libvips: not in a known format) | 10 | 8 are saved server error pages named `.jpg`/`.tif`, 1 empty file, 1 corrupt PNG |
| PSD without a flattened composite | 5 | Saved without "maximise compatibility"; would need a layer renderer |
| Truncated JPEG | 2 | Premature end of JPEG data |

The GIFs are mostly Google Photos `-ANIMATION`, `-MOTION` and `LiveMessage` exports, so their stills are
usually in the library as separate JPEGs.
