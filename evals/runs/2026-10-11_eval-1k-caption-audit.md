# Caption audit: eval-1k (Opus vs every arm, blind)

- Opus: `claude/opus-5.5` `e4e4be915182d461`
- Photos audited: 200; reference arm `gemini-medium-v2`; bootstrap 2000 resamples over photos

## Per arm (mean per photo, 95% CI)

| arm | n | no caption | wrong | invented | missing | error rate | useful |
|---|---|---|---|---|---|---|---|
| e4b-bf16 | 200 | 0 | 0.335 (0.260–0.410) | 0.060 (0.025–0.095) | 0.450 (0.365–0.545) | 0.330 (0.265–0.395) | 3.195 (3.105–3.280) |
| e4b-bf16-v2 | 200 | 0 | 0.360 (0.270–0.455) | 0.105 (0.065–0.155) | 0.345 (0.265–0.425) | 0.340 (0.280–0.405) | 3.405 (3.300–3.505) |
| g12b-q4 | 200 | 0 | 0.265 (0.195–0.340) | 0.075 (0.040–0.115) | 0.215 (0.155–0.280) | 0.290 (0.225–0.355) | 3.620 (3.535–3.695) |
| g12b-q4-v2 | 200 | 0 | 0.230 (0.165–0.305) | 0.115 (0.070–0.170) | 0.125 (0.080–0.180) | 0.270 (0.210–0.335) | 3.815 (3.725–3.910) |
| gemini-medium-v2 | 199 | 1 | 0.045 (0.020–0.075) | 0.126 (0.080–0.176) | 0.025 (0.005–0.050) | 0.165 (0.115–0.215) | 4.795 (4.715–4.865) |

## Paired differences vs `gemini-medium-v2` (candidate minus reference)

| arm | n | error rate | wrong | invented | missing | useful | D11 error rule |
|---|---|---|---|---|---|---|---|
| e4b-bf16 | 200 | 0.165 (0.085–0.240) | 0.286 (0.211–0.362) | -0.065 (-0.126–-0.005) | 0.427 (0.347–0.507) | -1.600 (-1.715–-1.490) | False |
| e4b-bf16-v2 | 200 | 0.175 (0.095–0.260) | 0.312 (0.221–0.412) | -0.020 (-0.085–0.045) | 0.322 (0.246–0.407) | -1.390 (-1.510–-1.270) | False |
| g12b-q4 | 200 | 0.125 (0.050–0.205) | 0.221 (0.146–0.291) | -0.050 (-0.105–0.005) | 0.191 (0.131–0.256) | -1.175 (-1.275–-1.075) | False |
| g12b-q4-v2 | 200 | 0.105 (0.035–0.175) | 0.186 (0.116–0.266) | -0.010 (-0.060–0.045) | 0.101 (0.055–0.151) | -0.980 (-1.095–-0.865) | False |

## Structured fields vs the Opus reference (high/medium confidence)

| arm | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| e4b-bf16 | 0.696 (n=191) | 0.497 (n=189) | 0.970 (n=197) | 0.902 | 1.000 | False | n/a (n=0) | n/a (n=0) |
| e4b-bf16-v2 | 0.775 (n=191) | 0.609 (n=189) | 0.970 (n=197) | 0.921 | 0.946 | False | 0.000 (n=8) | 1.000 (n=189) |
| g12b-q4 | 0.843 (n=191) | 0.741 (n=189) | 1.000 (n=197) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| g12b-q4-v2 | 0.900 (n=191) | 0.783 (n=189) | 1.000 (n=197) | 1.000 | 1.000 | True | 0.375 (n=8) | 1.000 (n=189) |
| gemini-medium-v2 | 0.958 (n=190) | 0.931 (n=188) | 0.995 (n=196) | 0.974 | 1.000 | False | 0.625 (n=8) | 0.995 (n=188) |

### Scans only

| arm | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| e4b-bf16 | 0.024 (n=41) | 0.023 (n=43) | 0.957 (n=47) | 0.926 | 1.000 | False | n/a (n=0) | n/a (n=0) |
| e4b-bf16-v2 | 0.512 (n=41) | 0.512 (n=43) | 0.957 (n=47) | 0.926 | 1.000 | False | 0.000 (n=1) | 1.000 (n=46) |
| g12b-q4 | 0.610 (n=41) | 0.674 (n=43) | 1.000 (n=47) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| g12b-q4-v2 | 0.805 (n=41) | 0.814 (n=43) | 1.000 (n=47) | 1.000 | 1.000 | True | 0.000 (n=1) | 1.000 (n=46) |
| gemini-medium-v2 | 0.854 (n=41) | 0.861 (n=43) | 0.979 (n=47) | 0.962 | 1.000 | False | 1.000 (n=1) | 1.000 (n=46) |

### Per stratum

#### e4b-bf16

| stratum | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| 1990s|other|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2000-09|mono|noscan | 0.500 (n=4) | 0.500 (n=4) | 1.000 (n=4) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| 2000-09|other|noscan | 0.958 (n=48) | 0.875 (n=48) | 0.979 (n=48) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|mono|noscan | 0.000 (n=1) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|other|noscan | 1.000 (n=19) | 0.444 (n=18) | 0.947 (n=19) | 0.000 | n/a | False | n/a (n=0) | n/a (n=0) |
| 2015-19|mono|noscan | 0.000 (n=1) | 0.000 (n=1) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2015-19|other|noscan | 0.935 (n=46) | 0.447 (n=47) | 0.979 (n=48) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2020+|mono|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| 2020+|other|noscan | 1.000 (n=6) | 1.000 (n=6) | 0.833 (n=6) | 0.000 | n/a | False | n/a (n=0) | n/a (n=0) |
| 2020+|other|scan | 0.000 (n=1) | 0.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| unknown|mono|noscan | 0.286 (n=7) | 0.333 (n=6) | 1.000 (n=6) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| unknown|mono|scan | 0.043 (n=23) | 0.043 (n=23) | 1.000 (n=25) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| unknown|other|noscan | 0.750 (n=16) | 0.714 (n=14) | 1.000 (n=17) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| unknown|other|scan | 0.000 (n=17) | 0.000 (n=19) | 0.905 (n=21) | 0.000 | n/a | False | n/a (n=0) | n/a (n=0) |

#### e4b-bf16-v2

| stratum | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| 1990s|other|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| 2000-09|mono|noscan | 0.750 (n=4) | 0.750 (n=4) | 1.000 (n=4) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=4) |
| 2000-09|other|noscan | 0.917 (n=48) | 0.875 (n=48) | 1.000 (n=48) | n/a | n/a | None | 0.000 (n=4) | 1.000 (n=44) |
| 2010-14|mono|noscan | 0.000 (n=1) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|other|noscan | 0.895 (n=19) | 0.500 (n=18) | 1.000 (n=19) | n/a | n/a | None | n/a (n=0) | 1.000 (n=19) |
| 2015-19|mono|noscan | 0.000 (n=1) | 0.000 (n=1) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2015-19|other|noscan | 0.891 (n=46) | 0.383 (n=47) | 0.979 (n=48) | n/a | n/a | None | 0.000 (n=1) | 1.000 (n=47) |
| 2020+|mono|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=1) |
| 2020+|other|noscan | 1.000 (n=6) | 1.000 (n=6) | 0.833 (n=6) | 0.000 | n/a | False | n/a (n=0) | 1.000 (n=6) |
| 2020+|other|scan | 0.000 (n=1) | 0.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| unknown|mono|noscan | 0.286 (n=7) | 0.333 (n=6) | 0.667 (n=6) | 1.000 | 0.667 | True | n/a (n=0) | 1.000 (n=6) |
| unknown|mono|scan | 0.870 (n=23) | 0.870 (n=23) | 1.000 (n=25) | 1.000 | 1.000 | True | 0.000 (n=1) | 1.000 (n=24) |
| unknown|other|noscan | 0.750 (n=16) | 0.786 (n=14) | 1.000 (n=17) | 1.000 | 1.000 | True | 0.000 (n=2) | 1.000 (n=15) |
| unknown|other|scan | 0.059 (n=17) | 0.105 (n=19) | 0.905 (n=21) | 0.000 | n/a | False | n/a (n=0) | 1.000 (n=21) |

#### g12b-q4

| stratum | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| 1990s|other|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2000-09|mono|noscan | 0.500 (n=4) | 0.500 (n=4) | 1.000 (n=4) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| 2000-09|other|noscan | 0.938 (n=48) | 0.896 (n=48) | 1.000 (n=48) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|mono|noscan | 0.000 (n=1) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|other|noscan | 0.947 (n=19) | 0.500 (n=18) | 1.000 (n=19) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2015-19|mono|noscan | 0.000 (n=1) | 0.000 (n=1) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2015-19|other|noscan | 0.935 (n=46) | 0.702 (n=47) | 1.000 (n=48) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2020+|mono|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| 2020+|other|noscan | 1.000 (n=6) | 1.000 (n=6) | 1.000 (n=6) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2020+|other|scan | 0.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| unknown|mono|noscan | 0.857 (n=7) | 0.833 (n=6) | 1.000 (n=6) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| unknown|mono|scan | 0.696 (n=23) | 0.696 (n=23) | 1.000 (n=25) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| unknown|other|noscan | 0.875 (n=16) | 0.786 (n=14) | 1.000 (n=17) | 1.000 | 1.000 | True | n/a (n=0) | n/a (n=0) |
| unknown|other|scan | 0.529 (n=17) | 0.632 (n=19) | 1.000 (n=21) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |

#### g12b-q4-v2

| stratum | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| 1990s|other|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| 2000-09|mono|noscan | 1.000 (n=4) | 1.000 (n=4) | 1.000 (n=4) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=4) |
| 2000-09|other|noscan | 0.917 (n=48) | 0.875 (n=48) | 1.000 (n=48) | n/a | n/a | None | 0.750 (n=4) | 1.000 (n=44) |
| 2010-14|mono|noscan | 0.000 (n=1) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|other|noscan | 0.947 (n=19) | 0.500 (n=18) | 1.000 (n=19) | n/a | n/a | None | n/a (n=0) | 1.000 (n=19) |
| 2015-19|mono|noscan | 0.000 (n=1) | 0.000 (n=1) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2015-19|other|noscan | 0.957 (n=46) | 0.681 (n=47) | 1.000 (n=48) | n/a | n/a | None | 0.000 (n=1) | 1.000 (n=47) |
| 2020+|mono|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=1) |
| 2020+|other|noscan | 1.000 (n=6) | 1.000 (n=6) | 1.000 (n=6) | n/a | n/a | None | n/a (n=0) | 1.000 (n=6) |
| 2020+|other|scan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| unknown|mono|noscan | 0.857 (n=7) | 0.833 (n=6) | 1.000 (n=6) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=6) |
| unknown|mono|scan | 0.870 (n=23) | 0.870 (n=23) | 1.000 (n=25) | 1.000 | 1.000 | True | 0.000 (n=1) | 1.000 (n=24) |
| unknown|other|noscan | 0.938 (n=16) | 0.929 (n=14) | 1.000 (n=17) | 1.000 | 1.000 | True | 0.000 (n=2) | 1.000 (n=15) |
| unknown|other|scan | 0.706 (n=17) | 0.737 (n=19) | 1.000 (n=21) | n/a | n/a | None | n/a (n=0) | 1.000 (n=21) |

#### gemini-medium-v2

| stratum | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| 1990s|other|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| 2000-09|mono|noscan | 1.000 (n=4) | 1.000 (n=4) | 1.000 (n=4) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=4) |
| 2000-09|other|noscan | 0.979 (n=48) | 0.979 (n=48) | 1.000 (n=48) | n/a | n/a | None | 0.500 (n=4) | 0.977 (n=44) |
| 2010-14|mono|noscan | 0.000 (n=1) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|other|noscan | 1.000 (n=18) | 0.765 (n=17) | 1.000 (n=18) | n/a | n/a | None | n/a (n=0) | 1.000 (n=18) |
| 2015-19|mono|noscan | 1.000 (n=1) | 1.000 (n=1) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2015-19|other|noscan | 1.000 (n=46) | 0.957 (n=47) | 1.000 (n=48) | n/a | n/a | None | 0.000 (n=1) | 1.000 (n=47) |
| 2020+|mono|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=1) |
| 2020+|other|noscan | 1.000 (n=6) | 1.000 (n=6) | 1.000 (n=6) | n/a | n/a | None | n/a (n=0) | 1.000 (n=6) |
| 2020+|other|scan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| unknown|mono|noscan | 1.000 (n=7) | 1.000 (n=6) | 1.000 (n=6) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=6) |
| unknown|mono|scan | 0.913 (n=23) | 0.913 (n=23) | 1.000 (n=25) | 1.000 | 1.000 | True | 1.000 (n=1) | 1.000 (n=24) |
| unknown|other|noscan | 1.000 (n=16) | 1.000 (n=14) | 1.000 (n=17) | 1.000 | 1.000 | True | 1.000 (n=2) | 1.000 (n=15) |
| unknown|other|scan | 0.765 (n=17) | 0.789 (n=19) | 0.952 (n=21) | 0.000 | n/a | False | n/a (n=0) | 1.000 (n=21) |

## Usefulness against the rubric (Opus rescore)

| arm | n | rubric useful (95% CI) | unanchored useful (95% CI) | paired diff vs reference (95% CI) |
|---|---|---|---|---|
| e4b-bf16 | 200 | 3.830 (3.720–3.930) | 3.195 (3.105–3.280) | -1.090 (-1.205–-0.965) |
| e4b-bf16-v2 | 200 | 3.915 (3.790–4.040) | 3.405 (3.300–3.510) | -1.005 (-1.135–-0.880) |
| g12b-q4 | 200 | 4.190 (4.080–4.295) | 3.620 (3.535–3.705) | -0.730 (-0.850–-0.610) |
| g12b-q4-v2 | 200 | 4.305 (4.200–4.410) | 3.815 (3.720–3.900) | -0.615 (-0.725–-0.495) |
| gemini-medium-v2 | 200 | 4.920 (4.865–4.965) | 4.795 (4.715–4.865) | reference |

- rubric: Opus usefulness against config/prompts/caption-usefulness-v1.md (1-5), mean per photo
- unanchored: the caption audit's original 1-5 usefulness (no anchored rubric), same photos
- paired: candidate minus reference, photos resampled jointly; 95% percentile bootstrap
- owner: owner's blind 1-5 on the review page; agreement vs the Opus rubric score: Spearman across the arms of each photo (photos where either side is constant are skipped), averaged; pooled Spearman over all (photo, arm) pairs; mean absolute difference

## Rules

- error_rate: share of photos with >= 1 wrong or invented fact, or no caption at all (a blocked or invalid photo counts as an error and as usefulness 1; it adds nothing to the wrong/invented/missing means)
- d11: candidate error rate <= reference + 2% (point estimate; paired CI shown); mono precision >= 0.98
- structured: scored against the Opus reference where its confidence is high or medium
- ci: 95% percentile bootstrap over photos; paired differences resample photos jointly (candidate minus reference)
