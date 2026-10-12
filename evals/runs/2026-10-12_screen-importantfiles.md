# Nudity screen over ImportantFiles (task 9.4), 2026-10-12

Candidate `nsfw-coreml-v1` (OpenNSFW2, Core ML FP32), threshold 0.7 (owner, 2026-10-11), params_hash
`330ec15f8c734bdd`. Mac Studio, derived `infer-v1` images read from the NAS. Counts only.

## Run

| | Assets |
|---|---|
| Derived (ok) assets | 52,371 |
| Already screened under this key (retro set, pool5k-500) | 7,567 |
| Screened in this run | 44,804 |
| Errors | 0 |
| Flagged (score >= 0.7) | 160 |
| Pending on the first review page (flags plus same-pixel copies) | 173 |

Every derived asset has an `ok` screen record. The 110 assets that failed to derive (GIF, BMP, broken files;
`evals/runs/2026-10-11_derive-importantfiles.md`) are unscreened and so stay out of every hosted, Claude and
local stage.

Throughput: about 13,000 img/h at first, limited by one store append per record over SMB (about 240 ms each);
about 48,000 img/h after records were written 200 at a time (`store.append_many`).

## Review

Review page `s-b7f60bcd8068` (in `~/shoebox-review/screen/` on the Studio; Claude does not open it): the 174
pending assets plus a seeded random 200 screened, unflagged assets to check for misses at 0.7. (It replaced the
first build, `s-c4988221fd8b`, to add the one possible miss below.)

Possible misses reported since: Claude subagents labelling the 7.2 colour sample described 2 photos as
sensitive body images; both scored below 0.7. One had already been ruled `include` by the owner (2026-10-11);
the other was queued `pending` by the owner and is on the page.

Owner verdicts and the threshold confirmation: (pending).
