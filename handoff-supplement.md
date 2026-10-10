# Shoebox — Supplemental Hand-off

**Read alongside `HANDOFF.md`.** This adds decisions and options from a later planning session. Where it conflicts with `HANDOFF.md`, this file wins. Still **plan only**; build nothing yet.

## Project name

**Shoebox.** Use it for the repo, crate and service names (e.g. `shoebox-api`, `shoebox-index`).

## Corrected source state

| Source | Where it is now | Acquisition |
|---|---|---|
| Google Drive scans | Already backed up on the Synology NAS (and still in Drive) | No export needed; read from the NAS copy |
| Google Photos | Cloud only | Still needs Google Takeout + sidecar JSON merge |

Network transfer time is **not** the primary concern. **Inference throughput** is.

## New option: hosted bulk, local incremental

The back catalogue already lives on Google infrastructure. Rather than running the one-time bulk labelling pass locally, consider:

- **Bulk pass (back catalogue):** hosted Gemma (open-weights model served by Google AI, not Gemini) for captions/tags/people detection.
- **Incremental pass (new photos):** local pipeline on the Mac Studio, as in `HANDOFF.md`.

**Rationale:** the expected win is throughput via concurrency and batch processing, not per-image speed. The Mac Studio is effectively serial for this workload.

**Account constraint:** the user already has a **paid** Google AI account, but on the lowest or second-lowest tier, so **rate limits are low**. Design for this from the start:

- Investigate the **Batch API** (asynchronous jobs, usually discounted and on separate, larger quota). Confirm it is available on this tier and supports Gemma, not just Gemini.
- If batch isn't available: a rate-limited queue with backoff that runs unattended for days. Estimate wall-clock as RPM ceiling × hours.
- The plan should compare hosted against a **measured** local baseline. Benchmark a few hundred images through each and extrapolate; don't rely on published figures.

### Gates before committing the bulk pass to hosted

1. **Retention and training terms:** confirm in the actual terms of service (not blog summaries) that the paid tier does not train on inputs or outputs, and what the retention window is.
2. **Data residency:** which region the endpoint processes in.
3. **Hosted vs local consistency:** run the eval set through both hosted and local Gemma (same size). Quantisation and serving config can shift outputs. If they diverge materially, old and new photos will show a visible seam in one index. Either they agree within tolerance or the plan picks one path for everything.

### Open question

Bulk inference on the Drive scans: send from the NAS copy, or have the pipeline read directly from Drive? Choose whichever keeps the pipeline simplest; the copies should be identical.

## Image handling for Gemma

Verified for Gemma 3 vision (4B and up). **Check whether a newer Gemma generation exists and supersedes this; don't assume.**

- The vision encoder works on fixed **896×896** square input. Every image is resized to that and encoded as **256 tokens**, whatever its original resolution.
- Common formats (JPEG, PNG, WebP) are fine. Hosted endpoints typically cap file size (~16 MB is common; verify). Plan for HEIC, TIFF and other formats from scans and phones to be converted first.
- **Pre-resize before sending.** Don't ship high-resolution originals to a model that downsizes them anyway; it only costs transfer and request size. Keep originals untouched; derive inference copies.
- **Pan & Scan** is an optional inference-time mode. It splits large or non-square images into several 896×896 crops plus the base image. It recovers small detail (background faces, text), but multiplies tokens and time per image. Treat it as a **dial set by eval**, not a default. Candidate rule: on for high-resolution scans and group shots, off otherwise.
- Hosted Gemma pricing is very low per token (small models in the cents-per-million-input-tokens range at time of writing). Verify current pricing for the tier and the batch discount; don't hard-code figures.

## Pipeline change: people gate before face detection

Use the VLM pass as a cheap gate. Run face detection/embedding (InsightFace) only on images the VLM flags as containing people.

- **Benefit:** large reduction in face workload; much of the archive is landscapes, documents, objects and scans without people.
- **Risk:** a gate miss is **silent and permanent**. A person the VLM misses never reaches face detection and can never be found by name.
- **Therefore:** tune the gate for **recall over precision**. When uncertain, pass the image through. A false positive costs a little compute; a false negative loses a person.
- **Tiling interaction:** small background faces are exactly what the single 896 tile flattens away and what Pan & Scan recovers. Run the gate on tiles for high-res and scan images.
- The gate only needs to say "worth a look". InsightFace does its own precise detection.
- **New eval:** people-gate recall on a labelled set that deliberately includes small, partial, background and low-contrast (old scan, black & white) faces. Report the false-negative rate explicitly.

Updated stage order (replacing steps 5–6 of `HANDOFF.md`):

1. Acquire → 2. merge metadata → 3. dedup & triage → 4. deterministic metadata →
5. **VLM pass** (captions/tags + "contains people" flag; tiled where the rule says) →
6. **Face detection & embedding on gated subset only** → 7. clustering & naming → 8. index → 9. serve → 10. incremental (Mac Studio).

## Additional evals (on top of `HANDOFF.md`)

- People-gate recall (above)
- Hosted vs local output consistency (above)
- Pan & Scan on/off: quality gain vs time/token cost, per image category (phone, scan, group, landscape)
- Throughput benchmark: local Mac Studio vs hosted (sync) vs hosted (batch), images/hour, on the same sample

## Extra items the plan should deliver

1. A recommendation on hosted bulk vs local bulk, with the benchmark and the three gates as decision criteria
2. Rate-limit handling design for the user's tier (batch vs queued sync)
3. The derived-image strategy (resize/convert/tile) and where derived copies live on the NAS
4. Where the people-gate sits and its recall threshold, set by eval
