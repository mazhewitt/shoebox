# Web app: API, serving, auth and the search UI

**Goal.** The first usable Shoebox, which also serves as the shell for later labelling screens (face naming):
- a native service on the Mac Studio (decisions 3 and 17), reachable by the family through Cloudflare Access (decision 4);
- the API serves search results as captures (decision 8), plus thumbnails and originals;
- a thin TypeScript UI for search, grid and detail;
- the owner can correct anything, with each correction stored as a human label (decision 14).

PLAN.md's Phase 4 assumed a NAS-hosted Axum API. The host moved to the Mac Studio, but the shape stands. Until the M5 Ultra Studio arrives (end of October 2026), everything runs on the laptop, LAN only, with no tunnel.

| # | Issue | Gate to pass before it lands |
|---|-------|------------------------------|
| 01 | [API stack decision](01-api-stack-decision.md) | `needs-triage`; decided by the owner before 02 starts |
| 02 | [Search API](02-search-api.md) | 01 decided; `search/03` and `search/04` landed |
| 03 | [Image and thumbnail serving](03-image-and-thumbnail-serving.md) | 01 decided; thumbnails exist (`catalogue/`); originals readable read-only from the NAS on the host |
| 04 | [Cloudflare Access auth](04-cloudflare-access-auth.md) | 02 and 03 exist; rejection tests pass **before** the tunnel is turned on |
| 05 | [Web UI: search, grid, detail](05-web-ui-search-grid-detail.md) | 02 and 03 landed |
| 06 | [Corrections and rotate](06-corrections-and-rotate.md) | 05 landed; `search/01`'s `human_labels` table exists |
| 07 | [Gate D](07-gate-d.md) | all of the above; this *is* the phase gate |

## Sequencing rationale

- **01 blocks everything.** The language choice decides how `search/03`'s ranking code is shared.
- **02 and 03 are independent.**
- **04 comes before any public exposure, never after:** the tunnel stays off until 04's rejection tests pass.
- **06 comes last:** it is the only issue that writes, and it needs the UI to exist.
- **07 is the gate:** nothing is exposed to family accounts until it passes.

## Phase gate (PLAN.md Gate D, adapted)

- An end-to-end query suite over deterministic facets returns the expected captures.
- The search-quality half is `search/05`'s parity run, linked rather than repeated.
- p95 latency is measured on the host.
- JWT rejection is tested: missing, expired, wrong audience, wrong issuer, bad signature.

See [07](07-gate-d.md).

## Rules that bind this directory

- **Originals are never written:** they are served read-only from the NAS mount, through `config.original_path`'s guards (no `/Volumes`, no `..`, no symlink escape).
- **Rotation is a display transform** stored as a human label, never a file edit.
- **No face identification** anywhere in the UI until the faces feature, which is post-MVP.
