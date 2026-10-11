# Per-source consent and hosted eligibility across copies

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

**Decision 39:**
- Every source names its person.
- Hosted processing defaults to **no** except for the owner.
- An asset is hosted-eligible only if **no copy** sits in a non-consented source.

Today `privacy.filter_items(provider=)` (`src/shoebox/privacy.py`) checks each item against its own source:
- `_own_restriction`: consent, `hosted_models`, `held_back`;
- the never-send list.

It already has a `copy_restricted` reason, but that only sees copies inside the items passed in. Once Home Folders and Jude's Takeout are sources, the same bytes can be in both the owner's library and Jude's. The bulk run must not send it.

## What

- **Copy index.** Build `asset_id → [(source, relpath)]` from the latest inventory frame(s) across **all** sources, not just the items being sent. The frame already has `asset_id` and `dup_n`. Exact bytes are enough for this rule; near-duplicates are covered below.
- **`filter_items` refuses on any copy.** It refuses an item if any copy is:
  - in a source without consent for that provider;
  - under a `held_back` glob;
  - on never-send.

  The reason is `copy_restricted`, naming the restricting source.
- **Capture-level rule.** Once `dedup/` exists, extend this from asset to capture: a RAW+JPEG pair or a near-duplicate group is eligible only if every member is. Leave a TODO naming `dedup/04` until then.
- **`shoebox privacy check --provider <p>`** reports refused counts by reason and by restricting source.
- **Stale frames.** The rule must fail closed. If a source is configured but has no frame newer than its config entry, refuse all hosted sends with a clear error. A missing inventory must not read as "no copies".

## Done when

- **Tests, all with synthetic frames:**
  - an asset whose only copies are in `importantfiles` passes;
  - the same asset also in a `person = "jude"` source is refused for gemini and claude;
  - a copy under `held_back` refuses the other copy;
  - a missing frame for a configured source refuses everything.
- `uv run shoebox privacy check --provider gemini` over the real frames prints the refused counts. The count of `copy_restricted` refusals is recorded in a comment here.

## Depends on

- **To land:** nothing; it works with ImportantFiles alone, where the rule is a no-op.
- **To matter:** 01 and 02, which add non-consented sources.
- **Capture extension:** `dedup/04`.

## Comments

**2026-10-11 (Studio, add-pipeline-foundations 3.3).** `shoebox privacy check --provider gemini --all-frames` over `frame-2026-10-10` (ImportantFiles only), with the copy index and an empty quarantine, gives the same counts for `gemini` and `claude`:
- 73,527 locations of 53,812 assets (all rows with an asset id, sidecars included): 71,905 allowed, 1,622 refused (1,058 assets).
- Refused by reason: `held_back` 1,355; `copy_restricted` 260; `never_send` 7; `quarantined` 0 (the screen hasn't run yet).
- Refused by restricting source: `importantfiles` 1,622.
- So 260 locations sit outside a held-back folder but share bytes with a held-back or never-send copy. Before this change they would have been allowed.
- Same-pixel links covered only the pool5k pixel hashes at this point. Re-run after the full derive (1.4).
