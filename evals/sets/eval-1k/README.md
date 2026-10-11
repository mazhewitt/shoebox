# eval-1k

Eval corpus: the targets of `pilot-v0-pool5k` plus 957 items drawn in a seeded order (seed 20261011) from base items already labelled by `g12b-q4`, each allowed by the privacy rules for gemini and claude and screened (screen.jsonl). Written by `shoebox eval corpus-draw`; details in `draw.json`. No image bytes and no file names here.

- Items: 1000 (requested 1000)
- Targets kept: 43 of 45 (2 dropped)
- Candidates (final label, non-target): 1964; rule-eligible: 1920
- Screened: 1010; group-mates of excluded items skipped: 0

## Items by folder

| folder | files |
|---|---|
| Pictures/Google Photos | 519 |
| Pictures/Saved Pictures | 91 |
| Pictures/photos | 77 |
| Mazda Documents/from-memory-stick | 59 |
| Pictures/2017 | 33 |
| Pictures/2019 (1) | 32 |
| Pictures/2016 | 26 |
| Pictures/Jordan | 20 |
| Pictures/Phone Photos | 19 |
| Pictures/Greece | 13 |
| Pictures/2018 | 10 |
| Pictures/Processed | 10 |
| Pictures/100D5600 | 6 |
| Pictures/D70 | 6 |
| Pictures/Dad's pics Dec09-Jan10 | 6 |
| Pictures/New Nikon Scans | 6 |
| Pictures/Export | 5 |
| Pictures/Italy2007 | 5 |
| Pictures/Misc | 5 |
| Pictures/To Work | 5 |
| Pictures/Digital Photos | 4 |
| Pictures/WWW | 4 |
| Pictures/2026 | 3 |
| Pictures/Family New Year | 3 |
| Pictures/Grandad's photos May2010 | 3 |
| Pictures/Karen and Evan wedding 2006 | 3 |
| Pictures/Kyra and Toren | 3 |
| Pictures/100NCD70 | 2 |
| Pictures/Camera Roll | 2 |
| Pictures/Scanned | 2 |
| Pictures/Zermatt-rope-park | 2 |
| Pictures/old photos from dad | 2 |
| For Facebook/Untitled Export | 1 |
| Mazda Documents/Calibre Library | 1 |
| Pictures/200711 | 1 |
| Pictures/200801 | 1 |
| Pictures/200802 | 1 |
| Pictures/2024 | 1 |
| Pictures/DCIM | 1 |
| Pictures/Flims-Jan08 | 1 |
| Pictures/Hebdon Bridge | 1 |
| Pictures/Wales 2023 | 1 |
| Pictures/lib dems | 1 |
| Pictures/pinhole | 1 |
| Pictures/ready to upload | 1 |
| Pictures/stuff | 1 |

## Rule skips (candidates)

| reason | items |
|---|---|
| held_back | 40 |
| never_send | 4 |

## Substitutions (screen excludes)

| category | count |
|---|---|
| financial | 3 |
| id_document | 1 |
| other_sensitive | 5 |

Per-item notes stay in screen.jsonl.
