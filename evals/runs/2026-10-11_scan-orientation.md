# Scan orientation: methods vs an Opus reference (task 1.3, 2026-10-11)

**Result: no local method is chosen and `infer-v2` is not built.** Orientation becomes a field in `label-v2` (design D6a), scored in the main eval against the Opus audit.

## Data
- **Scans:** the 656 items in `pilot-v0-pool5k` with a scanner make/model (Nikon Coolscan / LS-5000, Epson, …) or scanning software (Nikon Scan, VueScan, SilverFast), minus never-send and held-back paths. 611 carry EXIF orientation 1, 34 none, 8 carry 8 and 3 carry 6.
- **Sample:** 50 drawn at random (seed 20261011), viewed as 640 px thumbnails of the `infer-v1` derived JPEGs.
- **Reference:** a Claude Opus 5.5 subagent judged the clockwise rotation needed for each scan: 47 upright, 1 at 270, 1 at 180. One was ambiguous (a blurry downward shot).
- **Synthetic:** the 46 unambiguous upright scans, each rotated by a random 90/180/270 (seed 7). The method must undo the rotation.

## Methods (all local, temperature 0, ~0.3–0.7 s per scan)
| Method | Real: agree /50 | Real: upright scans wrongly rotated | Real: sideways fixed /2 | Synthetic: recovered /47 | Synthetic: said "upright" |
|---|---|---|---|---|---|
| E4B, "how many degrees clockwise" | 47 | 2 | 1 | 10 | 27 |
| E4B, same with a cue-listing prompt | — | — | — | 9 | 29 |
| E4B, pick the upright one of 4 rotations | 33 | 17 | 2 | — | — |
| 12B, "how many degrees clockwise" | 47 | 1 (the ambiguous one) | 0 | 22 | 2 |
| EG2, image–text score "an upright photograph" | 0 | 48 | 0 | — | — |
| EG2, upright minus sideways/upside-down margin | 20 | 29 | 1 | — | — |

- **E4B** mostly answers "already upright", so it fixes few rotated scans.
- **12B** notices that a rotated scan isn't upright (45 of 47) but often picks the wrong direction (90 vs 270).
- **EG2** carries no usable orientation signal.
- **Risk:** at a 4% sideways rate, every method's false-rotation count is about the same size as its fixes.

## Effect on search
Two of the 44 search targets are mis-oriented: one film scan upside down, one print scan sideways. On the image route (EG2 image@768, 5,042-photo corpus) their known-item ranks are:

| Target | Query | As stored | Upright |
|---|---|---|---|
| film scan, upside down | "wedding scene with people" | 6 | 9 |
| print scan, sideways | "Snowy winter scene" | 79 | 51 |

The net MRR change is about −0.001. **Orientation explains none of the 0.98 → 0.65 drop** (task 1.5). That drop comes from generic notes with many fitting photos, as the replan assumed.

## Decision
- No rotation at derive time for now: no local method is precise enough, and the measured search effect is nil.
- `label-v2` asks every captioner for `rotate_cw` (0/90/180/270) at no extra inference cost. Gemini's answer and the local captioners' are scored against Opus on the eval-1k scans.
- Display and UI rotation can use whichever source passes later. `infer-v2` is built only if one does (precision ≥ 0.98 on upright scans).
- Scripts and per-item outputs were scratch work and are not kept. The asset_ids of the reference sample can be regenerated from the seed.
