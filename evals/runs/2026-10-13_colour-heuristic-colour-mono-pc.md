# Colour heuristic: mono filter source (2026-10-13)

Frozen thresholds hash `4e0d9ed3e18bdb9e`: colour_c95 30.0, colour_low_c95 15.0, colour_low_hue_r 0.75, mono_c95 1.0, mono_sure_c95 1.0, toned_c50 6.0, toned_hue_r 0.92.
Gate: mono precision >= 0.98 in every origin stratum; a stratum with fewer than 10 predicted-mono rows is `insufficient` (filter unavailable there). Counts only.

Primary: test half of `colour-mono-pc` (fresh data, never seen in tuning).

## Test half

| stratum | n | tp | fp | fn | precision | recall | status | filter |
|---|---|---|---|---|---|---|---|---|
| scan | 0 | 0 | 0 | 0 | - | - | insufficient | unavailable |
| phone | 183 | 0 | 0 | 0 | - | - | insufficient | unavailable |
| camera | 191 | 5 | 1 | 0 | 0.833 | 1.000 | insufficient | unavailable |
| received | 0 | 0 | 0 | 0 | - | - | insufficient | unavailable |
| overall | 374 | 5 | 1 | 0 | 0.833 | 1.000 | | |

## Secondary: `colour-mono` test half (already-seen data, not a gate)

This test half was scored and inspected before these thresholds were tuned; 152 of 152 items labelled.

| stratum | n | tp | fp | fn | precision | recall | status |
|---|---|---|---|---|---|---|---|
| scan | 43 | 29 | 0 | 1 | 1.000 | 0.967 | pass |
| phone | 24 | 0 | 0 | 0 | - | - | insufficient |
| camera | 45 | 1 | 0 | 0 | 1.000 | 1.000 | insufficient |
| received | 38 | 16 | 0 | 3 | 1.000 | 0.842 | pass |

Combination (heuristic mono AND Gemini colour_class != colour; 5 rows with a Gemini record): tp 0, fp 0, fn 0, precision -, recall -.

## toned

Default: separate: toned does not match black-and-white searches. Lenient variant (toned counted as mono on both sides): precision 0.034, recall 1.000.

## Comparison

- g12b-q4-v2: mono precision 1.000 (eval-1k caption audit; source /Users/mazhewitt/projects/shoebox/evals/runs/2026-10-11_eval-1k-caption-audit.json)
- gemini-medium-v2: mono precision 0.974 (eval-1k caption audit; source /Users/mazhewitt/projects/shoebox/evals/runs/2026-10-11_eval-1k-caption-audit.json)

## eval-1k audit sample (frame colour_guess)

tp 35, fp 1, fn 2, precision 0.972, recall 0.946.

## Dev tuning grid

| mono_c95 | feasible | precision | recall | predicted |
|---|---|---|---|---|
| 0.5 | True | 0.977 | 0.857 | 43 |
| 1.0 | True | 0.978 | 0.898 | 45 |
| 1.5 | False | 0.958 | 0.939 | 48 |
| 2.0 | False | 0.959 | 0.959 | 49 |
| 2.5 | False | 0.961 | 1.000 | 51 |
| 3.0 | False | 0.961 | 1.000 | 51 |
| 3.5 | False | 0.961 | 1.000 | 51 |
| 4.0 | False | 0.961 | 1.000 | 51 |
| 5.0 | False | 0.907 | 1.000 | 54 |
| 6.0 | False | 0.907 | 1.000 | 54 |
| 8.0 | False | 0.817 | 1.000 | 60 |

## Decision (2026-10-13)

Opus 5.5 (`opus-api-v2`, blind, label-v2) on all 800 drawn phone/camera photos:

| stratum | heuristic bucket | Opus mono | Opus toned | Opus colour | Opus unknown |
|---|---|---|---|---|---|
| phone | mono/toned guess (200) | 1 | 0 | 184 | 15 |
| phone | near threshold (200) | 0 | 0 | 200 | 0 |
| camera | mono/toned guess (200) | 14 | 1 | 185 | 0 |
| camera | near threshold (200) | 0 | 0 | 200 | 0 |

Black-and-white phone and camera photos are almost absent from the library; the heuristic's mono guesses there are
low-chroma colour photos (night, snow, fog). v2 (`mono_c95` 1.0, hash `4e0d9ed3e18bdb9e`) is **not adopted**: it
leaves phone insufficient, camera insufficient on test (5 tp, 1 fp), and would turn the received stratum from pass to
insufficient. The filter stays on v1 (`2228f47c8e77df69`): heuristic for scans and received photos, unavailable for
phone and camera. Spend: $4.04 of the Anthropic credits (805 photos incl. smoke).
