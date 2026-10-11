# Image and thumbnail serving

Status: blocked (on 01; thumbnails from `catalogue/`)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

PLAN.md Phase 4 (image and thumbnail serving with range requests) and its pre-generated thumbnail decision. Decision 3: originals stay on the NAS and the host reads them over a read-only mount. Originals include 16-bit TIFF scans and RAW files (`frame/README.md`: 603.6 GB, about 72k images), which browsers can't display. So "view original" means a derived display image, plus a download of the true original.

## What

- **`GET /img/{asset_id}/thumb?size=256|1024`:**
  - serves the pre-generated WebP/AVIF from `derived/`;
  - long-lived `Cache-Control` plus an `ETag` (the derived file's BLAKE3), since the content is keyed by asset_id and profile;
  - falls back to on-the-fly generation (pyvips or libvips) with a write-through cache when one is missing, and logs the miss.
- **`GET /img/{asset_id}/display`:** a browser-safe rendition of about 2048 px (the `infer-v1`-style profile, `derive.derived_path`). Rotation is applied at serve time from the effective `rotate_cw` (06) or by CSS; the derived file is never rewritten.
- **`GET /img/{asset_id}/original`:**
  - the original bytes with `Range` support and `Content-Disposition: attachment`;
  - the path resolved only through `config.original_path` (it refuses `/Volumes`, `..` and symlink escapes) under the `*-ro` mounts;
  - every handle opened read-only.
- **Consent:** assets from held-back sources (`config/shoebox.toml` `held_back`) are still served to authenticated family users. Consent is about *hosted models*, not viewing in Shoebox ("everyone sees everything", decision 6). Items on `evals/never-send.txt` are servable too; that list governs Claude and hosted sends only.
- **No directory listing.** Unknown asset_id → 404, with no path information in the error.

## Done when

- Tests cover:
  - a range request returns 206 with the correct bytes;
  - `ETag` and `If-None-Match` return 304;
  - a traversal attempt (`..`, an absolute path, a symlink out of the root) is refused;
  - a missing thumbnail falls back and caches.
- On the host, serving a thumbnail grid of 60 × 256 px completes p95 ≤ 300 ms on the LAN (folded into 07).
- An audit grep shows no write-mode open on any source root.

## Depends on

01. Thumbnail generation lives in `catalogue/`.

## Open questions

- Should the display rendition be pre-generated for all 72k images (about 30 GB at 2048 px), or generated lazily and cached? Proposed: lazy plus cache, since thumbnails are what the grid needs.

## Comments
