# Hosted Gemma for the back-catalogue VLM pass

Research date: 2026-10-10. Sources are primary only: Google docs, terms, pricing pages, Hugging Face model metadata and `transformers` source. Each claim cites its page. "Last updated" dates are the ones the pages showed on the research date. Anything that couldn't be verified from a primary source is marked **UNVERIFIED**.

Naming note: Google's Cloud docs now call Vertex AI the **"Gemini Enterprise Agent Platform"** (docs.cloud.google.com/gemini-enterprise-agent-platform/...). "Agent Platform" below means Vertex AI.

---

## Summary

- **Gemma 4 replaces Gemma 3 for this pass.** Gemma 4 (Apache 2.0) has sizes E2B, E4B, 12B, 26B A4B (MoE) and 31B. Its vision path changed. It keeps the image's aspect ratio, works at variable resolution and has a selectable token budget (70/140/**280 default**/560/1120). It has no Pan & Scan; the budget does that job. The supplement's "896×896 → 256 tokens + Pan & Scan" facts apply to Gemma 3 only. At the default budget, a typical photo costs about **252–266 image tokens** (max 280).
- There are **three hosted routes** for Gemma 4:
  - **A. Gemini API (AI Studio):** `gemma-4-31b-it` and `gemma-4-26b-a4b-it`. Free of charge; the paid tier is "Not available". No Batch API. Rate limits aren't published (you can only see them in AI Studio).
  - **B. Agent Platform managed API (MaaS):** `gemma-4-26b-a4b-it-maas` only. **$0.15 / 1M input, $0.60 / 1M output.** No batch. Experimental. ML processing in the **US multi-region**.
  - **C. Agent Platform self-deployed endpoint:** your own GPUs, billed per node-hour. Can be pinned to **Zurich (europe-west6, L4 GPUs only)**. Model Garden batch prediction is text-only and doesn't list Gemma 3 or 4, so you'd run your own client against an online endpoint.
- **Cost for the bulk pass** (100k images, 20% tiled at 3×):
  - Route B: about **$18.88** (256-token assumption) or **$19.38** (Gemma 4's 280-token cap).
  - Route A: $0.
  - Route C: depends on measured throughput. The budget buys about 19 h of a 4×L4 node or about 37 h of a 2×L4 node in Zurich, which needs roughly 1.5 or 0.75 images/s.

## Verdict

| Gate | A. Gemini API | B. Agent Platform MaaS | C. Self-deployed endpoint (Zurich) |
|---|---|---|---|
| 1. No training on content, known retention | **Pass, with caveats.** No training: Paid Services data terms apply to Swiss users even on free quota. Abuse logs are kept **55 days**. **No ZDR** on this API. | **Pass.** Contractual training restriction. Prompts are logged only when a classifier flags them, kept **≤90 days**, and you can request an exception (ZDR). | **Pass.** Same Cloud training restriction. Whether abuse monitoring applies to self-deployed endpoints is UNVERIFIED (see Q3). |
| 2. Residency pinnable / known | **Fail.** No region choice. Logged data "may be stored transiently or cached in any country". | **Known, not pinnable to EU/CH.** US multi-region only (EU multi-region excludes CH anyway). | **Pass.** europe-west6 (Zurich) offers L4 for serving. |
| 3. Hosted vs local consistency testable | Testable as a black box. 26B A4B and 31B are offered. Serving precision and image token budget are undocumented and can't be set. | Testable as a black box. 26B A4B only. Serving precision and image budget undocumented. | **Best.** You pick the weights, precision, processor and `max_soft_tokens`, so you can mirror the local pipeline exactly. |
| Fits $150 | Yes ($0), but throughput is capped by free-tier limits you can't see in the docs. | Yes. About $19–20 for the bulk pass, leaving more than $120 for evals. | Only if a pilot measures at least 0.75–1.5 images/s (see Q7). |

**Recommendation.**
- **If Swiss residency is a hard requirement, only Route C clears all three gates.** Fund a short pilot first: about $13 for 2 h on g2-standard-48 in Zurich. Measure images/hour with Gemma 4 26B A4B (or the size the Mac Studio will run). Commit only if the projected node-hours fit what's left of the budget.
- **If US processing with a contractual no-training term and ZDR is acceptable, Route B is the cheapest, simplest and most predictable** option, with large budget headroom.
- **Route A doesn't clear gate 2.** It also offers no ZDR, no batch and no documented limits. Its terms also say the Gemini API is "for professional or business purposes, not for consumer use" (see Q3), which fits a personal archive awkwardly.
- **No route offers a Batch API for Gemma.** Plan for the "rate-limited queue with backoff" design from the supplement, whichever route is chosen.

**Open items for the owner to check in their own account:**
1. Whether billing is enabled on the project used for the Gemini API, and its usage tier (AI Studio → Projects). Also the actual Gemma 4 RPM/TPM/RPD shown on AI Studio's rate-limit page. These aren't in the docs.
2. For Route B: enable `gemma-4-26b-a4b-it-maas` in Model Garden, check its quota in Cloud Quotas, and whether an abuse-monitoring exception (ZDR) can be requested on a self-serve account.
3. For Route C: L4 GPU quota for Agent Platform serving in europe-west6 (new projects often start low; UNVERIFIED for this account). Whether Model Garden offers one-click Gemma 4 deployment to europe-west6, or whether a custom vLLM container is needed. Whether the hosting agreement is the Cloud Platform ToS (self-serve) or a Master Agreement (this affects abuse monitoring).
4. Which Gemma 4 size and precision the Mac Studio will run locally. Gate 3 needs the hosted size to match it: A offers 26B A4B and 31B, B offers 26B A4B only, C offers any size.
5. Re-check that Gemma 4 on the Gemini API is still "Paid tier: Not available". If Google adds paid pricing, Route A's economics and terms change.

---

## Q1. Current Gemma generation

**Gemma 4 is current and supersedes Gemma 3 for vision.** The Gemma docs home page shows the banner "Gemma 4 released with text, audio and image input and long up to 256K context window!" (https://ai.google.dev/gemma/docs, last updated 2026-10-07). Gemma 3n also exists (an on-device line, E2B/E4B, images normalised to 256/512/768 px at 256 tokens each, 32K context; https://ai.google.dev/gemma/docs/gemma-3n/model_card, last updated 2025-06-17). It is older than Gemma 4 and isn't hosted on either API.

**Gemma 4 sizes** (https://ai.google.dev/gemma/docs/core/model_card_4, last updated 2026-07-30):

| Variant | Params | Context | Modalities | Vision encoder |
|---|---|---|---|---|
| E2B | 2.3B effective (5.1B with embeddings) | 128K | text, image, audio | ~150M |
| E4B | 4.5B effective (8B with embeddings) | 128K | text, image, audio | ~150M |
| 12B "Unified" | 11.95B | 256K | text, image, audio | none (encoder-free; patches projected straight into the LLM) |
| 26B A4B (MoE) | 25.2B total, 3.8B active | 256K | text, image | ~550M |
| 31B | 30.7B | 256K | text, image | ~550M |

**Image handling (Gemma 4):**
- **Token budget.** "The user can decide between budget sizes of 70, 140, 280, 560, or 1120 tokens." "All Gemma 4 models support variable resolution which means that images of different resolutions can be processed." "Depending on the budget, the input is resized." (https://ai.google.dev/gemma/docs/capabilities/vision/image, last updated 2026-06-04.) The model card says lower budgets suit classification and captioning, and higher budgets suit OCR and small text (paraphrase).
- **What the HF processor actually does** (`transformers/models/gemma4/image_processing_gemma4.py`, https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma4/image_processing_gemma4.py):
  - Defaults are `patch_size = 16`, `max_soft_tokens = 280`, `pooling_kernel_size = 3`, and the allowed values are `_SUPPORTED_SOFT_TOKENS = (70, 140, 280, 560, 1120)`.
  - `max_patches = max_soft_tokens * pooling_kernel_size**2`. The image gets an **aspect-ratio-preserving resize** to the largest size whose sides are multiples of 48 px (16×3) and which fits in `max_patches`. It's then split into 16 px patches, and each 3×3 block of patches becomes one soft token.
  - So **one image token ≈ a 48×48 px region**, and a 280 budget ≈ 645k pixels.
  - The Google-published preprocessor config for `google/gemma-4-26B-A4B-it` sets `"max_soft_tokens": 280`, `"patch_size": 16`, `"pooling_kernel_size": 3` (https://huggingface.co/google/gemma-4-26B-A4B-it/resolve/main/processor_config.json).
- **Actual token counts** (computed with the processor's formula):

  | Original | 280 budget | 560 | 1120 |
  |---|---|---|---|
  | 4000×3000 (4:3) | 912×672 → **266** | 540 | 1064 |
  | 6000×4000 (3:2) | 960×624 → **260** | 532 | 1080 |
  | 3000×3000 | 768×768 → **256** | 529 | 1089 |
  | 1920×1080 | 1056×576 → **264** | 527 | 1100 |
  | 6000×2000 (panorama) | 1344×432 → **252** | 520 | 1083 |

  These match Google's notebook, which observed 64/121/256/529 tokens at budgets 70/140/280/560 (vision/image page). The processor also wraps each image in `boi`/`eoi` tokens (`processing_gemma4.py`, `replace_image_token`), so add about 2 tokens per image.

**Gemma 3, for comparison:** "Images, normalized to 896 x 896 resolution and encoded to 256 tokens each". Sizes are 270M/1B/4B/12B/27B, with 128K context for 4B and up (https://ai.google.dev/gemma/docs/core/model_card_3, last updated 2025-08-14).

**Licence:**
- Gemma 4 is **Apache 2.0**. The model card lists "Apache 2.0" and links "Terms of use" and "Prohibited use" pages (model_card_4).
- Hugging Face metadata for `google/gemma-4-26B-A4B-it`, `-31B-it`, `-12B-it` and `-E4B-it` all report `license: apache-2.0`, ungated (https://huggingface.co/api/models/google/gemma-4-26B-A4B-it etc.).
- Gemma 3 and 3n used the custom Gemma Terms of Use (https://ai.google.dev/gemma/terms).

**Effect on the supplement:** the "896×896 / 256 tokens / Pan & Scan" section describes Gemma 3. For Gemma 4, the dial is `max_soft_tokens`, not Pan & Scan, and the local Mac Studio pipeline should use the same generation and size.

## Q2. Where Gemma 4 can be called hosted

### A. Gemini API (AI Studio)
- **Models:** "The Gemini API supports the following Gemma 4 models: gemma-4-31b-it, gemma-4-26b-a4b-it". It supports image input; thinking is toggled with thinking level `"high"` / `"minimal"` (https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api, last updated 2026-07-02).
- **Pricing:** Gemma 4 row: Input price "Free of charge" (free tier) / "**Not available**" (paid tier); output and caching the same; "Used to improve our products: Yes / No" (https://ai.google.dev/gemini-api/docs/pricing, last updated 2026-10-09). So Gemma 4 is **free-tier only; no per-token paid pricing exists**.
- **Rate limits:** the page publishes no Gemma numbers. "Rate limits depend on a variety of factors (such as your usage tier) and can be viewed in Google AI Studio." Limits are "applied per project, not per API key", and RPD resets at midnight Pacific.
  - Tier 1 = "Set up and link an active billing account".
  - Tier 2 = "Paid $100 + 3 days from first successful payment".
  - Spend caps are $10 (Tier 1) and $50 (Tier 2) per 10 minutes.
  - Source: https://ai.google.dev/gemini-api/docs/rate-limits, last updated 2026-10-09.
  - **The Gemma 4 RPM/RPD for the owner's tier is UNVERIFIED.** Read it in AI Studio. Because Gemma has no paid tier, a higher tier may not raise its limits (UNVERIFIED).
- **Batch API:** "Batch API supports a range of Gemini models. Refer to the Models page for each model's support of Batch API." Batch is priced at 50% of interactive (https://ai.google.dev/gemini-api/docs/batch-api, last updated 2026-09-17). Gemma isn't on the Gemini models page and doesn't appear in the batch enqueued-token tables (rate-limits page). **Batch for Gemma isn't documented, so treat it as unsupported.** A 50% discount on a $0 model would be moot anyway.

### B. Agent Platform (Vertex) managed API (MaaS)
- **Model:** `gemma-4-26b-a4b-it-maas`. Image is input-only. Consumption options: "Batch inference: Not supported"; "Pay-as-you-go Standard PayGo: Supported"; "Provisioned Throughput: Supported". Model availability is "Global: global"; ML processing is "Multi-region: us". Limits are 262,144 context and 128,000 max output. "Launch stage: Experimental", released April 3, 2026 (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/google/gemma-4-26b-a4b-it, last updated 2026-10-10).
- **Only 26B A4B is offered as MaaS.** It's the only Gemma in the open-models MaaS list (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/use-open-models, last updated 2026-10-07).
- **Pricing:** "Gemma 4 26B — Input $0.15, Output $0.60, Cache Hit $0.015" per 1M tokens (https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing; no last-updated date shown).
- **Calling:** through the OpenAI-compatible Chat Completions API (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/call-open-model-apis).
- **Quota:** no published RPM/TPM for Gemma MaaS. UNVERIFIED; check Cloud Quotas.

### C. Agent Platform self-deployed endpoint (Model Garden / own container)
- Gemma 4 is listed for use with Agent Platform via its Model Garden card (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/open-models/use-gemma, last updated 2026-10-07).
- **Billing:** per **node-hour**: "A node hour represents the time a virtual machine spends running your prediction job or waiting in an active state (an endpoint with one or more models deployed)" (https://cloud.google.com/products/gemini-enterprise-agent-platform/pricing). You pay while the endpoint is deployed, even when idle.
- **Batch prediction for self-deployed Model Garden models** is Preview and handles "multiple **text-only** prompts". Its supported-models list includes Gemma 1/2 only (https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/predictions/get-batch-model-garden, last updated 2026-10-07). **It's unusable for image labelling**, so drive an online endpoint from your own queue.
- **Payload limits:** dedicated public endpoint 10 MB per request, shared public endpoint 1.5 MB (https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/predictions/choose-endpoint-type, last updated 2026-10-07).
- Prices and hardware: see Q4 and Q7.

## Q3. Data terms per route

### A. Gemini API — Gemini API Additional Terms of Service (https://ai.google.dev/gemini-api/terms, "Effective March 23, 2026", page last updated 2026-04-28)
- **Unpaid services train on content:** "When you use Unpaid Services, including, for example, Google AI Studio and the unpaid quota on Gemini API, Google uses the content you submit to the Services and any generated responses to provide, improve, and develop Google products and services and machine learning technologies… human reviewers may read, annotate, and process your API input and output."
- **Swiss override (key clause for this owner):** "If you're in the European Economic Area, Switzerland, or the United Kingdom, the terms under 'How Google uses Your Data' in 'Paid Services' apply to all Services, including Google AI Studio and unpaid quota in the Gemini API, even though they are offered free of charge." Also: "You may use only Paid Services when making API Clients available to users in the European Economic Area, Switzerland, or the United Kingdom."
- **Billed project = Paid Service:** "Your access to Gemini API is a 'Paid Service' only when accessing the API through a Cloud Project associated with an active billing account."
- **Paid terms:** "Google doesn't use your prompts (including associated system instructions, cached content, and files such as images, videos, or documents) or responses to improve our products". Content is processed under the Data Processing Addendum. "For Paid Services, Google logs prompts and responses for a limited period of time, solely for detecting and preventing violations of the Prohibited Use Policy… This data may be stored transiently or cached in any country in which Google or its agents maintain facilities."
- **Ambiguity:** for a non-Swiss user, Gemma's "free on a billed project" status sits between the Unpaid and Paid definitions. For this owner, the Swiss clause settles it: paid-tier data terms apply.
- **Retention window:** "Google retains the following data for fifty-five (55) days": prompts, contextual information and outputs. "Data can be accessed for human review only by authorized Google employees" (https://ai.google.dev/gemini-api/docs/usage-policies, last updated 2026-06-09).
- **ZDR:** "If your workload requires guaranteed zero data retention or enterprise data processing agreements, use Vertex AI." File API uploads persist until deleted or expired (48 h). Implicit in-memory caching has a 24-hour TTL and "does not violate zero data retention" (https://ai.google.dev/gemini-api/docs/zdr, last updated 2026-09-14). **The 55-day abuse log can't be turned off on this route.**
- **Use restriction to note:** "Use of Google AI Studio and Gemini API is for developers building with Google AI models for professional or business purposes, not for consumer use." (terms). Whether a developer's personal-archive project counts is for the owner to judge.

### B/C. Agent Platform (Vertex)
- **Training:** Service Specific Terms, "Training Restriction": "Google will not use Customer Data to train or fine-tune any AI/ML models without Customer's prior permission or instruction." (https://cloud.google.com/terms/service-terms, "Last modified October 08, 2026".) The ZDR page adds: "This applies to all managed models on Gemini Enterprise Agent Platform, including GA and pre-GA models." (https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/zero-data-retention, last updated 2026-10-07.)
- **Abuse-monitoring retention:** "If automated safety classifiers detect suspicious activity… Google may log customer prompts solely for the purpose of examining whether a violation… has occurred. This data won't be used to train or fine-tune any AI/ML models. This data is stored securely for up to 90 days in the same region or multi-region selected by the customer for their project."
  - Scope: "Only customers whose use of Google Cloud is governed by the Google Cloud Platform Terms of Service… customers with a Google Cloud Master Agreement are exempt."
  - "Services in scope: Google's Generative AI Services."
  - Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/abuse-monitoring, last updated 2026-10-07.
  - The stricter 30-day "Advanced AI" logging lists only certain Claude models, not Gemma (same page).
- **ZDR:** "If you are in scope for prompt logging for abuse monitoring and want zero data retention, you can request an exception for abuse monitoring." Leave request-response logging (BigQuery) off; it's off by default. In-memory caching (24 h TTL, location-bound) can be disabled per project through the `cacheConfig` API (ZDR page). The abuse-monitoring page doesn't describe the exception request process; how a self-serve account gets the exception is **UNVERIFIED**.
- **Self-deployed endpoints (C):** the training restriction covers all Customer Data. **Whether self-deployed endpoints count as "Generative AI Services" for abuse logging is UNVERIFIED.** Either way, any such logging would sit in the project's chosen region.

## Q4. Data residency

- **A. Gemini API:** no region selection is documented. The terms say logged data "may be stored transiently or cached in any country in which Google or its agents maintain facilities" (terms). Switzerland is on the list of regions where the service is *available* (https://ai.google.dev/gemini-api/docs/available-regions, last updated 2026-04-28). That's about access, not processing location. **It can't be pinned.**
- **B. MaaS Gemma 4:** availability is "Global"; ML processing is "Multi-region: us" (model page). In the data-residency table, Gemma 4 26B A4B IT is ticked for **US multi-region only**, with no EU multi-region (https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/data-residency, last updated 2026-10-07). Requests to a global endpoint "may be processed in any Google Cloud location around the world, and therefore don't provide any data residency guarantees". Also, "The European Union multi-region (eu) endpoint strictly covers data residency within EU member states… including the United Kingdom and Switzerland, are excluded" (same page). **Known (US), not EU/CH-pinnable.**
- **C. Self-deployed:** you choose the endpoint region. Locational endpoints "ensure that ML processing remains entirely within the broader multi-regional or country jurisdiction associated with that region" (data-residency page). **europe-west6 (Zurich) lists "L4 †"**, and † means "isn't available for training" only, so L4 is available for serving there. europe-west4 (Netherlands) offers A100 40/80GB, H100, H200 and L4 if EU (not Swiss) residency is acceptable (https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/general/locations, last updated 2026-10-07).

## Q5. Request limits and formats

**Gemini API:**
- **Inline data:** the input-methods table gives "100 MB per request or payload (50 MB for PDFs)" (https://ai.google.dev/gemini-api/docs/file-input-methods, last updated 2026-10-07). The image-understanding page still says "Inline image data limits your total request size… to 20MB" (https://ai.google.dev/gemini-api/docs/image-understanding, last updated 2026-09-23). The two pages conflict; **design for 20 MB**. This is moot if images are pre-resized; a 280-token derivative is under 1 MP.
- **File API:** "2 GB per file, up to 20GB per project", kept 48 h.
- **GCS URI registration:** 2 GB per file, "One time registration can give access for up to 30 days".
- **External URLs:** 15 MB per file.
- **MIME types:** PNG, JPEG, WEBP, **HEIC, HEIF**. **TIFF is not listed.** Max "3,600 image files per request". No maximum pixel dimensions are documented (image-understanding page). These are documented for "Gemini". **That the Gemma models accept the same set, HEIC especially, is UNVERIFIED.** Convert to JPEG regardless, so all routes and the local pipeline see identical bytes.
- **Batch (for reference, Gemma unsupported):** inline requests total under 20 MB; JSONL input via File API up to 2 GB; 100 concurrent batch jobs; 20 GB file storage; 48 h expiry; results kept 6 weeks (batch-api and rate-limits pages).

**Agent Platform MaaS (Chat Completions):** "The image_url type supports images stored in a Cloud Storage URI or a base64 encoding in the form `data:<MIME-TYPE>;base64,…`" (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/openai/overview, last updated 2026-10-10). Accepted MIME types and size limits for Gemma MaaS aren't documented (UNVERIFIED).

**Self-deployed:** 10 MB per request on a dedicated public endpoint, 1.5 MB on a shared one (choose-endpoint-type page). Formats depend on your serving container (e.g. vLLM with PIL decoding). Send JPEG.

## Q6. Pan & Scan

- **Hosted endpoints:**
  - Neither Gemma hosted route documents a Pan & Scan parameter or a Gemma image-budget parameter.
  - The Gemini API's `media_resolution` is documented for "Gemini 3" (image-understanding page). **Whether it maps to Gemma 4's `max_soft_tokens` is UNVERIFIED.**
  - The token budget hosted Gemma uses per image is undocumented on both routes (UNVERIFIED; measure it via `usage` token counts in responses).
  - **Tiling must be done client-side**, as extra images in the same request. Route C is the exception: you control the processor and can set `max_soft_tokens` directly.
- **Gemma 4 has no Pan & Scan.** The Gemma 4 image processor has no `do_pan_and_scan`. Detail is recovered by raising `max_soft_tokens` (560/1120) instead (source above).
- **What Gemma 3's `do_pan_and_scan` actually does** (`transformers/models/gemma3/image_processing_gemma3.py` and `processing_gemma3.py`, https://github.com/huggingface/transformers/tree/main/src/transformers/models/gemma3; image processor last changed 2026-03-30):
  - **Defaults:** `do_pan_and_scan: False`, `pan_and_scan_min_crop_size: 256`, `pan_and_scan_max_num_crops: 4`, `pan_and_scan_min_ratio_to_activate: 1.2`.
  - **It's driven by aspect ratio, not resolution.** If long/short < 1.2, no crops are made. **A near-square high-res scan is never tiled.**
  - **It crops along the long axis only:**
    - `n = floor(long/short + 0.5)`, capped by `floor(long/256)`, then clamped to `[2, 4]`.
    - Crops are equal width (`ceil(long/n)`), non-overlapping, and span the full short side.
    - If either crop dimension is under 256, it skips.
  - The original image plus each crop are each resized to 896×896 (256 tokens each).
  - **Prompt wrapping:** `"Here is the original image {img} and here are some crops to help you see better " + " ".join([img] * num_crops)`.
  - Worked examples: 4:3 and 3:2 photos give 2 crops (3 images = 768 tokens, the supplement's "~3×"); 3:1 panoramas give 3 crops.
- **Client-side mirror:**
  - For Gemma-3 parity, reproduce that rule exactly and send `[original, crop1, crop2…]` in one request with the same wording.
  - For Gemma 4, two options to compare in the eval:
    - (a) Raise the budget to 1120 on routes that allow it (C, local).
    - (b) Tile client-side. A grid (e.g. 2×2) is likely more useful than Gemma 3's 1-D strips for small background faces in square scans. This is a design suggestion, not a Google recommendation.

## Q7. Cost estimate

**Assumptions.** 100,000 images. Each has 300 prompt tokens and 150 output tokens (thinking off: `"minimal"` on the Gemini API; MaaS lists "Thinking: Not supported"). 20% of images (20,000) are tiled at 3× image tokens. Calls are sync; no Gemma route has batch.

**Bulk-pass tokens**

| | Image tokens/image | Input tokens | Output tokens |
|---|---|---|---|
| Supplement figure | 256 | 100k×(256+300) + 20k×512 = **65.84M** | 100k×150 = **15.0M** |
| Gemma 4 default cap (conservative) | 280 | 100k×(280+300) + 20k×560 = **69.20M** | **15.0M** |
| Gemma 4, 1120 budget instead of tiling | 280 / 1120 | 100k×580 + 20k×840 = **74.80M** | **15.0M** |

**Route B (MaaS, $0.15 in / $0.60 out per 1M):**
- 256 figure: 65.84 × 0.15 = $9.88, plus 15.0 × 0.60 = $9.00, so **$18.88** ($0.00019/image).
- 280 cap: 69.20 × 0.15 = $10.38, plus $9.00, so **$19.38**.
- 1120 variant: 74.80 × 0.15 = $11.22, plus $9.00, so **$20.22**.
- Batch: not offered. A 50% discount would have given about $9.69, but it doesn't apply.
- **Evals and consistency:** 3,000 images × 2 hosted runs = 6,000 × ~$0.000194 = **~$1.16**. Even 10 eval passes over 3,000 images plus a Pan & Scan/budget sweep stays **under ~$10**.
- **Total ≈ $30 worst case, against $150. Comfortable.**

**Route A (Gemini API):**
- **$0** in tokens.
- The constraint is wall-clock: days ≈ requests ÷ RPD. Put tiles in the same request, so requests ≈ 100k plus evals. As an illustration only, since the real RPD is UNVERIFIED: at 1,000 RPD that's about 100 days; at 10,000 RPD about 10 days.

**Route C (self-deployed, Zurich europe-west6, prediction node-hour prices):**

Prices are on-demand USD per hour, read from the pricing page's europe-west6 G2 prediction table (https://cloud.google.com/products/gemini-enterprise-agent-platform/pricing). GPU counts are from Compute Engine's G2 table (https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines, last updated 2026-10-08).

| Machine | L4 GPUs (GB) | $/h Zurich | Hours for $120 | Needed throughput for 100k |
|---|---|---|---|---|
| g2-standard-12 | 1 (24) | $1.60944 | 74.6 h | 1,341 img/h (0.37 img/s) |
| g2-standard-24 | 2 (48) | $3.21888 | 37.3 h | 2,682 img/h (0.75 img/s) |
| g2-standard-48 | 4 (96) | $6.43776 | 18.6 h | 5,365 img/h (1.49 img/s) |
| g2-standard-96 | 8 (192) | $12.87552 | 9.3 h | 10,730 img/h (2.98 img/s) |

For comparison, us-central1 g2-standard-12 costs $1.15069/h and europe-west4 $1.26692/h (same page).

**Fit (my arithmetic, not a Google figure):**
- bf16 weights ≈ params × 2 bytes.
- 26B A4B ≈ 50 GB needs g2-standard-48 (4×L4). On 2×L4 it would need about 8-bit quantisation, which would then differ from the local precision.
- 31B ≈ 61 GB needs 4×L4.
- E4B (8B with embeddings) ≈ 16 GB fits 1×L4.
- 12B ≈ 24 GB needs 2×L4.

**Node-hours needed:**
- At about 692 average input tokens and 150 output per image, 26B A4B on 4×L4 must sustain about 1,030 prefill tok/s and 225 decode tok/s, aggregated across batched requests, to finish in 18.6 h.
- That's plausible for a 3.8B-active MoE under vLLM batching, but **no Google source gives Gemma 4 throughput on L4. UNVERIFIED; measure it.**
- Add deploy/undeploy overhead (billed while deployed) and the eval and consistency runs, which also cost node-hours here.
- **Pilot:** 2 h on g2-standard-48 ≈ $12.88. Measure images/hour, then compute `100,000 / (img/h) × $6.44`. Go only if that plus evals stays under about $135.

---

## Sources

Google AI for Developers (Gemini API / Gemma):
- Gemma docs home: https://ai.google.dev/gemma/docs (2026-10-07)
- Gemma 4 model card: https://ai.google.dev/gemma/docs/core/model_card_4 (2026-07-30)
- Gemma 3 model card: https://ai.google.dev/gemma/docs/core/model_card_3 (2025-08-14)
- Gemma 3n model card: https://ai.google.dev/gemma/docs/gemma-3n/model_card (2025-06-17)
- Gemma image understanding: https://ai.google.dev/gemma/docs/capabilities/vision/image (2026-06-04)
- Run Gemma with the Gemini API: https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api (2026-07-02)
- Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing (2026-10-09)
- Gemini API rate limits: https://ai.google.dev/gemini-api/docs/rate-limits (2026-10-09)
- Batch API: https://ai.google.dev/gemini-api/docs/batch-api (2026-09-17)
- Gemini API Additional Terms of Service: https://ai.google.dev/gemini-api/terms (effective 2026-03-23; page 2026-04-28)
- Abuse monitoring / usage policies: https://ai.google.dev/gemini-api/docs/usage-policies (2026-06-09)
- Zero data retention (Gemini API): https://ai.google.dev/gemini-api/docs/zdr (2026-09-14)
- Available regions: https://ai.google.dev/gemini-api/docs/available-regions (2026-04-28)
- Image understanding (Gemini): https://ai.google.dev/gemini-api/docs/image-understanding (2026-09-23)
- File input methods: https://ai.google.dev/gemini-api/docs/file-input-methods (2026-10-07)
- Files API: https://ai.google.dev/gemini-api/docs/files (2026-09-23)

Google Cloud (Gemini Enterprise Agent Platform, formerly Vertex AI):
- Gemma 4 26B A4B IT MaaS model page: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/google/gemma-4-26b-a4b-it (2026-10-10)
- Open models for MaaS: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/use-open-models (2026-10-07)
- Call MaaS APIs for open models: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/call-open-model-apis
- Use Gemma open models: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/open-models/use-gemma (2026-10-07)
- Generative AI pricing: https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing
- Agent Platform pricing (node-hours): https://cloud.google.com/products/gemini-enterprise-agent-platform/pricing
- Zero data retention: https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/zero-data-retention (2026-10-07)
- Abuse monitoring: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/abuse-monitoring (2026-10-07)
- Data residency: https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/data-residency (2026-10-07)
- ML locations / accelerators: https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/general/locations (2026-10-07)
- Batch predictions from self-deployed Model Garden models: https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/predictions/get-batch-model-garden (2026-10-07)
- Choose an endpoint type: https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/predictions/choose-endpoint-type (2026-10-07)
- OpenAI compatibility overview: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/openai/overview (2026-10-10)
- Google Cloud Service Specific Terms: https://cloud.google.com/terms/service-terms (last modified 2026-10-08)
- Compute Engine accelerator-optimized machines (G2): https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines (2026-10-08)

Hugging Face / transformers (Google-published weights; HF-maintained processor source):
- https://huggingface.co/google/gemma-4-26B-A4B-it (and `/resolve/main/processor_config.json`); licence metadata via https://huggingface.co/api/models/google/gemma-4-26B-A4B-it, `-31B-it`, `-12B-it`, `-E4B-it`
- https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma4/image_processing_gemma4.py
- https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma4/processing_gemma4.py
- https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma3/image_processing_gemma3.py
- https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma3/processing_gemma3.py
