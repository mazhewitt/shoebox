# Dedup and capture grouping

**Goal.** Turn every file found under `sources/` into the data model of decision 8: file (bytes) → asset (one viewable image) → capture (one moment).
- Group copies of the same picture across all libraries and shares (decision 6: by pixel hash, across libraries).
- Pick one canonical member per capture, so the bulk run captions each capture once and search returns captures.
- **Nothing is deleted.** Duplicates become groups. The owner gets a report to clean up from by hand (decision 39).

The pipeline today:
- `inventory.py` counts exact duplicates (`dup_n`, by BLAKE3).
- `pilot/select.py:assign_groups` groups same-folder siblings: `raw_jpeg_basename` and `scan_export_basename`.
- `pilot-v0-pool5k/near-dups.jsonl` has EG2-cosine near-duplicate pairs.
- None of these spans sources, or is stored as a group table.

| # | Issue | Gate to pass before it lands |
|---|---|---|
| 01 | [Exact and pixel-hash groups](01-exact-and-pixel-hash-groups.md) | Synthetic fixtures (same bytes; same pixels, different EXIF; rotated-flag variants) group correctly; counts reported per source |
| 02 | [Capture grouping](02-capture-grouping.md) | Each rule has fixtures; per-rule counts and a 50-group spot check on ImportantFiles, with no false merges |
| 03 | [Near-duplicate detection](03-near-duplicate-perceptual.md) | **Gate C:** precision ≥ 0.99 on labelled pairs at the chosen threshold, recall reported |
| 04 | [Canonical member and caption-once worklist](04-canonical-member-and-caption-once.md) | Deterministic canonical choice; the worklist count re-quotes the bulk cost |
| 06 | [Dedup before captioning](06-dedup-before-captioning.md) | caption runners refuse anything but a full-source worklist; existing labels are reused, never re-sent |
| 05 | [Dedup report for the owner](05-dedup-report-for-owner.md) | The owner can read it and act on it; Shoebox deletes nothing |

## Sequencing

1. **01** comes first. It is cheap and deterministic, and it fixes how much the rest has to look at.
2. **02** uses filename and container rules only, and doesn't need 03.
3. **03** needs labelled pairs and a gate, so it is the slow one.
4. **04** can ship on 01 + 02 alone. Near-duplicates only lower the count further, so a first worklist without 03 is allowed: it errs toward captioning too much, never too little.
5. **05** reads whatever groups exist.
6. **06** is the guard on captioning. It lands with 04 and before `bulk-captioning/02`: every caption run reads a worklist and reuses existing labels. It also settles whether to caption once all sources are deduped, or by stages.

Full value needs `sources/` complete, because cross-library duplicates only show up once every share and the Takeout are inventoried. The code can be built and tested now on ImportantFiles.

**Raw ↔ processed scan pairing** (decision 25) is a different matcher, tolerant of crop, rotation and processing. It is post-MVP and not filed here.
