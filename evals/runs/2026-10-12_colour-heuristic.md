# Colour heuristic: mono filter source (2026-10-12)

Frozen thresholds hash `2228f47c8e77df69`: colour_c95 30.0, colour_low_c95 15.0, colour_low_hue_r 0.75, mono_c95 2.0, mono_sure_c95 2.0, toned_c50 6.0, toned_hue_r 0.92.
Gate: mono precision >= 0.98 in every origin stratum; a stratum with fewer than 10 predicted-mono rows is `insufficient` (filter unavailable there). Counts only.

## Test half

| stratum | n | tp | fp | fn | precision | recall | status | filter |
|---|---|---|---|---|---|---|---|---|
| scan | 43 | 30 | 0 | 0 | 1.000 | 1.000 | pass | heuristic |
| phone | 24 | 0 | 0 | 0 | - | - | insufficient | unavailable |
| camera | 45 | 1 | 0 | 0 | 1.000 | 1.000 | insufficient | unavailable |
| received | 38 | 17 | 0 | 2 | 1.000 | 0.895 | pass | heuristic |
| overall | 150 | 48 | 0 | 2 | 1.000 | 0.960 | | |

Combination (heuristic mono AND Gemini colour_class != colour; 3 rows with a Gemini record): tp 2, fp 0, fn 0, precision 1.000, recall 1.000.

## toned

Default: separate: toned does not match black-and-white searches. Lenient variant (toned counted as mono on both sides): precision 0.732, recall 0.963.

## Comparison

- g12b-q4-v2: mono precision 1.000 (eval-1k caption audit; source evals/runs/2026-10-11_eval-1k-caption-audit.json)
- gemini-medium-v2: mono precision 0.974 (eval-1k caption audit; source evals/runs/2026-10-11_eval-1k-caption-audit.json)

## eval-1k audit sample (frame colour_guess)

tp 35, fp 1, fn 2, precision 0.972, recall 0.946.

## Dev tuning grid

| mono_c95 | feasible | precision | recall | predicted |
|---|---|---|---|---|
| 0.5 | True | 0.970 | 0.821 | 33 |
| 1.0 | True | 0.971 | 0.872 | 35 |
| 1.5 | True | 0.973 | 0.923 | 37 |
| 2.0 | True | 0.974 | 0.949 | 38 |
| 2.5 | False | 0.975 | 1.000 | 40 |
| 3.0 | False | 0.975 | 1.000 | 40 |
| 3.5 | False | 0.975 | 1.000 | 40 |
| 4.0 | False | 0.975 | 1.000 | 40 |
| 5.0 | False | 0.975 | 1.000 | 40 |
| 6.0 | False | 0.975 | 1.000 | 40 |
| 8.0 | False | 0.951 | 1.000 | 41 |
