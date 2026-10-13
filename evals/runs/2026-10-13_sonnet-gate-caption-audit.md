# Caption audit: eval-1k (Opus vs every arm, blind)

- Opus: `claude/opus-5.5` `1b3c3d9e61591c1e`
- Photos audited: 197; reference arm `gemini-medium-v2`; bootstrap 2000 resamples over photos

## Per arm (mean per photo, 95% CI)

| arm | n | no caption | wrong | invented | missing | error rate | useful |
|---|---|---|---|---|---|---|---|
| gemini-medium-v2 | 196 | 1 | 0.138 (0.092–0.189) | 0.199 (0.138–0.260) | 0.056 (0.025–0.092) | 0.299 (0.239–0.365) | 4.523 (4.436–4.604) |
| sonnet-api-v2 | 197 | 0 | 0.046 (0.020–0.076) | 0.117 (0.071–0.162) | 0.025 (0.005–0.046) | 0.152 (0.107–0.203) | 4.858 (4.797–4.914) |

## Paired differences vs `gemini-medium-v2` (candidate minus reference)

| arm | n | error rate | wrong | invented | missing | useful | D11 error rule |
|---|---|---|---|---|---|---|---|
| sonnet-api-v2 | 197 | -0.147 (-0.218–-0.076) | -0.092 (-0.153–-0.036) | -0.082 (-0.153–-0.015) | -0.036 (-0.076–0.005) | 0.335 (0.249–0.426) | True |

## Structured fields vs the Opus reference (high/medium confidence)

| arm | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| gemini-medium-v2 | 0.932 (n=191) | 0.920 (n=188) | 0.995 (n=193) | 0.974 | 1.000 | False | 0.571 (n=7) | 1.000 (n=184) |
| sonnet-api-v2 | 0.964 (n=192) | 0.979 (n=189) | 0.990 (n=194) | 0.974 | 1.000 | False | 1.000 (n=7) | 1.000 (n=185) |

### Scans only

| arm | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| gemini-medium-v2 | 0.818 (n=44) | 0.848 (n=46) | 0.979 (n=47) | 0.962 | 1.000 | False | n/a (n=0) | 1.000 (n=46) |
| sonnet-api-v2 | 0.955 (n=44) | 0.978 (n=46) | 1.000 (n=47) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=46) |

### Per stratum

#### gemini-medium-v2

| stratum | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| 1990s|other|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| 2000-09|mono|noscan | 1.000 (n=4) | 1.000 (n=4) | 1.000 (n=4) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=4) |
| 2000-09|other|noscan | 0.979 (n=48) | 0.979 (n=48) | 1.000 (n=48) | n/a | n/a | None | 0.500 (n=4) | 1.000 (n=43) |
| 2010-14|mono|noscan | n/a (n=0) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|other|noscan | 1.000 (n=18) | 0.722 (n=18) | 1.000 (n=18) | n/a | n/a | None | n/a (n=0) | 1.000 (n=18) |
| 2015-19|mono|noscan | 1.000 (n=1) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2015-19|other|noscan | 0.979 (n=47) | 0.957 (n=47) | 1.000 (n=48) | n/a | n/a | None | 0.000 (n=1) | 1.000 (n=47) |
| 2020+|mono|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=1) |
| 2020+|other|noscan | 1.000 (n=3) | 1.000 (n=3) | 1.000 (n=3) | n/a | n/a | None | n/a (n=0) | 1.000 (n=3) |
| 2020+|other|scan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| unknown|mono|noscan | 0.714 (n=7) | 1.000 (n=6) | 1.000 (n=6) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=6) |
| unknown|mono|scan | 0.875 (n=24) | 0.875 (n=24) | 1.000 (n=25) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=24) |
| unknown|other|noscan | 0.941 (n=17) | 1.000 (n=14) | 1.000 (n=17) | 1.000 | 1.000 | True | 1.000 (n=2) | 1.000 (n=15) |
| unknown|other|scan | 0.737 (n=19) | 0.809 (n=21) | 0.952 (n=21) | 0.000 | n/a | False | n/a (n=0) | 1.000 (n=21) |

#### sonnet-api-v2

| stratum | medium | digitiser | colour_class | mono precision | mono recall | mono gate | rotate rotated | rotate upright |
|---|---|---|---|---|---|---|---|---|
| 1990s|other|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| 2000-09|mono|noscan | 1.000 (n=4) | 1.000 (n=4) | 1.000 (n=4) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=4) |
| 2000-09|other|noscan | 1.000 (n=48) | 0.979 (n=48) | 0.979 (n=48) | n/a | n/a | None | 1.000 (n=4) | 1.000 (n=43) |
| 2010-14|mono|noscan | n/a (n=0) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2010-14|other|noscan | 1.000 (n=19) | 1.000 (n=19) | 0.947 (n=19) | 0.000 | n/a | False | n/a (n=0) | 1.000 (n=19) |
| 2015-19|mono|noscan | 1.000 (n=1) | n/a (n=0) | n/a (n=0) | n/a | n/a | None | n/a (n=0) | n/a (n=0) |
| 2015-19|other|noscan | 0.979 (n=47) | 0.979 (n=47) | 1.000 (n=48) | n/a | n/a | None | 1.000 (n=1) | 1.000 (n=47) |
| 2020+|mono|noscan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=1) |
| 2020+|other|noscan | 1.000 (n=3) | 1.000 (n=3) | 1.000 (n=3) | n/a | n/a | None | n/a (n=0) | 1.000 (n=3) |
| 2020+|other|scan | 1.000 (n=1) | 1.000 (n=1) | 1.000 (n=1) | n/a | n/a | None | n/a (n=0) | 1.000 (n=1) |
| unknown|mono|noscan | 0.571 (n=7) | 1.000 (n=6) | 1.000 (n=6) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=6) |
| unknown|mono|scan | 0.958 (n=24) | 0.958 (n=24) | 1.000 (n=25) | 1.000 | 1.000 | True | n/a (n=0) | 1.000 (n=24) |
| unknown|other|noscan | 0.941 (n=17) | 0.929 (n=14) | 1.000 (n=17) | 1.000 | 1.000 | True | 1.000 (n=2) | 1.000 (n=15) |
| unknown|other|scan | 0.947 (n=19) | 1.000 (n=21) | 1.000 (n=21) | n/a | n/a | None | n/a (n=0) | 1.000 (n=21) |

## Usefulness against the rubric (Opus rescore)

| arm | n | rubric useful (95% CI) | unanchored useful (95% CI) | paired diff vs reference (95% CI) |
|---|---|---|---|---|
| e4b-bf16 | 200 | 3.830 (3.720–3.930) | n/a (n/a) | -1.090 (-1.205–-0.965) |
| e4b-bf16-v2 | 200 | 3.915 (3.790–4.040) | n/a (n/a) | -1.005 (-1.135–-0.880) |
| g12b-q4 | 200 | 4.190 (4.080–4.295) | n/a (n/a) | -0.730 (-0.850–-0.610) |
| g12b-q4-v2 | 200 | 4.305 (4.200–4.410) | n/a (n/a) | -0.615 (-0.725–-0.495) |
| gemini-medium-v2 | 200 | 4.920 (4.865–4.965) | 4.523 (4.431–4.609) | reference |

### Owner review (30 photos)

| arm | n | owner (95% CI) | Opus rubric (95% CI) | Opus unanchored (95% CI) |
|---|---|---|---|---|
| e4b-bf16 | 30 | 3.667 (3.367–3.900) | 4.067 (3.867–4.267) | n/a (n/a) |
| e4b-bf16-v2 | 30 | 3.700 (3.367–3.967) | 4.100 (3.733–4.400) | n/a (n/a) |
| g12b-q4 | 30 | 3.567 (3.333–3.800) | 4.333 (4.133–4.533) | n/a (n/a) |
| g12b-q4-v2 | 30 | 3.767 (3.533–4.000) | 4.533 (4.300–4.767) | n/a (n/a) |
| gemini-medium-v2 | 30 | 4.367 (4.067–4.633) | 4.700 (4.400–4.933) | 4.300 (3.933–4.600) |
| opus-v2 | 0 | n/a (n/a) | n/a (n/a) | n/a (n/a) |

- Owner–Opus Spearman per photo-set: mean 0.422 over 23 photos; pooled 0.330
- Mean absolute difference (owner vs Opus rubric): 0.707 over 150 pairs

- rubric: Opus usefulness against config/prompts/caption-usefulness-v1.md (1-5), mean per photo
- unanchored: the caption audit's original 1-5 usefulness (no anchored rubric), same photos
- paired: candidate minus reference, photos resampled jointly; 95% percentile bootstrap
- owner: owner's blind 1-5 on the review page; agreement vs the Opus rubric score: Spearman across the arms of each photo (photos where either side is constant are skipped), averaged; pooled Spearman over all (photo, arm) pairs; mean absolute difference

## Rules

- error_rate: share of photos with >= 1 wrong or invented fact, or no caption at all (a blocked or invalid photo counts as an error and as usefulness 1; it adds nothing to the wrong/invented/missing means)
- d11: candidate error rate <= reference + 2% (point estimate; paired CI shown); mono precision >= 0.98
- structured: scored against the Opus reference where its confidence is high or medium
- ci: 95% percentile bootstrap over photos; paired differences resample photos jointly (candidate minus reference)
