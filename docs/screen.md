# Sensitive-content screen, quarantine and owner review

Nude or explicit photos, and documents, that got into the library by mistake must stay out of every hosted
model, everything Claude views, and the family view until the owner has ruled on each one. Spec:
`openspec/changes/add-pipeline-foundations/specs/content-screen/spec.md`; design D3 and D4.

## 1. Screen

`shoebox screen run` (documented with that command) screens every derived asset locally and stores a yes/no
per asset under `<shoebox_root>/results/screen/...`. Every flagged asset gets a quarantine row with verdict
`pending`.

## 2. Quarantine

The quarantine file is `evals/quarantine.jsonl` in the repo, because verdicts are owner labels and the repo is
backed up. It is append-only and the latest row per asset wins. A row holds `asset_id`, `screen`
(`nudity` | `document`), `flagged_at`, `verdict`, and once ruled `verdict_by` and `verdict_at`. It holds asset
hashes only, never paths or images.

| verdict   | hosted models, Claude viewing, contact sheets, packets, corpus draws | local captioning / embedding |
|-----------|-------------------------------------------------------------------------|------------------------------|
| `pending` | refused                                                                 | refused                      |
| `include` | allowed, as if never flagged                                            | allowed                      |
| `private` | refused (owner-only, local processing only)                             | allowed                      |
| `exclude` | refused; left out of the catalogue (the original is never touched)      | refused                      |

- A verdict covers the asset's group: the same asset_id anywhere, and same-pixel copies in any source. The
  most restrictive verdict in the group wins.
- Hosted runners refuse with reason `quarantined` before any request is made.
- The pilot contact sheet and Claude packets leave the asset out and report the count left out.
- An asset that already has a row is never re-queued by a later screen run, so `exclude` stays excluded.

## 3. Owner review

Build the page:

```
shoebox screen review-page
```

- It lists every `pending` asset, as blurred thumbnails in a grid. Click (or press space) to reveal one.
- Each item shows its number and a `screen` column (`nudity` or `document`), with buttons include / private
  / exclude.
- Keys: arrows or j/k move, space reveals, `i` include, `p` private, `x` exclude.
- "mark page as include" sets every unanswered item to include, so a batch of false positives clears fast.
- Answers are kept in the browser (localStorage). "Download answers" saves `screen-answers-<page id>.json`.
- `--sample N` adds N seeded random screened-but-unflagged assets (shown as `sample`) for a recall check;
  `--seed` fixes the draw.

The page goes to `~/shoebox-review/screen/` (`index.html` and `img/`). The page-map, which links item numbers
to asset ids, goes to `~/shoebox-review/screen-page-map.json`, outside the page folder. The build refuses any
page or page-map path under the repo, `~/Desktop`, `~/Documents`, iCloud Drive (`~/Library/Mobile Documents`)
or `/Volumes`. It prints counts only.

Then ingest the answers:

```
shoebox screen ingest ~/Downloads/screen-answers-<page id>.json --delete-thumbnails
```

- Ingest refuses answers whose page id isn't the current page's, or that leave any item unanswered. In both
  cases no verdict is written.
- Each verdict is recorded with `verdict_by = owner` for the asset and its same-pixel copies.
- `--delete-thumbnails` deletes the page's `img/` folder afterwards. Without it, the thumbnails are kept and
  the command says so.

## Rules

- Only the owner opens the review page. Claude must never view the page, its thumbnails or the answers'
  images, and no hosted model receives them.
- Commands report counts only; no file names, captions or images.
