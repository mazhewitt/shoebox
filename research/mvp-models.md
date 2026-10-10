# MVP model candidates: Gemini 3.8 Flash, local Gemma 4 (E4B / 12B), EmbeddingGemma

Research date: 2026-10-10. This builds on `research/hosted-gemma.md`, which covers the Gemma 4 vision path, the hosted Gemma routes and the general Gemini API / Agent Platform data terms. Those findings aren't repeated here.

Sources are primary only: Google AI for Developers and Google Cloud docs, Google's terms and pricing pages, Hugging Face model cards and metadata, and the official repos and release notes of mlx-vlm, Ollama, llama.cpp and LM Studio. "Last updated" dates are what the pages showed on the research date. Anything that couldn't be verified from a primary source is marked **UNVERIFIED**. Arithmetic and design suggestions that are mine, not a vendor's, are labelled as such.

MVP scope: caption, tags, capture source (phone/digital vs film scan vs print scan), colour vs mono and other search fields for about 100k images (≈210 GB). The machine is an M5 Max MacBook Pro with 128 GB. The owner is in Switzerland and has an individual, billing-enabled Gemini API account.

---

## Summary

- **Gemini 3.8 Flash (`gemini-3.8-flash`) is GA**, released 2 September 2026. "Gemini 3.7 Flash" is deprecated and silently routed to 3.8 Flash. There is **no 3.7 Flash-Lite**; the current Flash-Lite models are **3.5 Flash-Lite** and **3.1 Flash-Lite**.
  - **Image tokens:** `media_resolution` low / medium / high gives ≈ **280 / 560 / 1120** tokens per image. The **default (unspecified) is 1120**, so set `low` explicitly for the 280-token assumption.
  - **Formats:** PNG, JPEG, WEBP, HEIC, HEIF. **No TIFF**, so convert the 16-bit scans.
  - **Structured output:** JSON Schema (a subset).
  - **Thinking:** `low` / `medium` (default) / `high`. **`minimal` is rejected** on 3.8 Flash. Thinking tokens are billed as output tokens.
  - **Pricing:** introductory **$0.75 in / $3.75 out per 1M** through 31 Dec 2026, then **$1.50 / $7.50** from 1 Jan 2027. Batch and Flex are 50% off.
- **Cost for 100k images on Gemini 3.8 Flash** (low resolution, 580 input + 150 output tokens per image, before thinking):
  - **≈ $100 sync or ≈ $50 batch** at the introductory price. These double after 1 Jan 2027.
  - Every 100 thinking tokens per image adds **≈ $37.50 sync / $18.75 batch**. Thinking can't be turned off, so measure it.
  - **3.1 Flash-Lite** costs ≈ $37 sync / $18.50 batch, and **3.5 Flash-Lite** ≈ $55 / $27. Both support `minimal` thinking.
  - At **Tier 1**, the 3M-token batch queue for 3.8 Flash means ≈ 20 sequential batch jobs. The $250/month Tier 1 cap is enough at the introductory price.
- **Privacy gate (no retention or use for other purposes, especially training): conditional PASS.**
  - The paid-tier no-training terms apply to an individual on a billing-enabled project. No enterprise agreement is needed.
  - Still retained: a **55-day abuse-monitoring log** of prompts and responses that **can't be switched off** on this API. That log can feed Google's *policy-enforcement* classifiers. Also 55-day storage of Interactions-API calls unless `store=false`, opt-in logs and datasets, Files API uploads (48 h) and batch results (6 weeks).
  - With the settings in the checklist below it passes "no training on my photos". It does **not** pass a strict "no retention at all".
- **Gemma 4 on the Mac:** all four runtimes support Gemma 4 vision, including the encoder-free 12B "Unified":
  - **mlx-vlm 0.7.6**
  - **Ollama 0.40.2** (MLX engine by default on Apple Silicon)
  - **llama.cpp b11541**
  - **LM Studio 0.4.26**
- **Weights are small for 128 GB:**
  - E4B: 4-bit ≈ 5 GB, 8-bit ≈ 9 GB, bf16 ≈ 16 GB.
  - 12B: 4-bit ≈ 7 GB, 8-bit ≈ 13 GB, bf16 ≈ 24 GB.
  - Since memory isn't the limit, use 8-bit or bf16 for accuracy.
- **Image budget control differs by runtime:**
  - mlx-vlm: `max_soft_tokens`, default 280.
  - llama.cpp: `--image-max-tokens`, range 70–1120.
  - **Ollama has no budget parameter.** It picks the grid "closest to the input resolution", so full-size photos will likely use ≈ 1120 tokens. Pre-resize the images to get the 280 budget.
  - All four runtimes offer JSON-schema structured output.
- **No primary-source throughput figure exists for Gemma 4 image captioning on M-series.** It must be measured.
  - Memory-bandwidth arithmetic (M5 Max 40-core GPU, 614 GB/s; mine) gives single-stream decode lower bounds for the 150-token answers alone: E4B-8bit ≥ 31 h and 12B-8bit ≥ 82 h.
  - Batched serving should cut these several-fold.
  - Realistic expectation: **about 1–4 days for 12B, under a day to 2 days for E4B** (UNVERIFIED).
- **EmbeddingGemma 2 is real and multimodal.**
  - Released 6 October 2026, Apache 2.0, 740M parameters.
  - Text, image, video and audio map into **one shared 768-d space** (Matryoshka 512/256/128), so **text-to-image search works directly**.
  - The image budget is 70–1120 tokens (default 280).
  - It runs on the Mac via sentence-transformers, mlx-vlm and Ollama (`embeddinggemma-2`, image input via `/api/embed`).
  - EmbeddingGemma 1 (300M) is text-only.
  - **SigLIP 2 isn't needed as the primary route.** Keep it as a fallback to compare against.

## Recommendation (short)

1. **Search backbone:** EmbeddingGemma 2 locally. Embed each image *and* its caption/tags text into the same space. This is cheap, private and covers text-to-image search on day one.
2. **Structured fields and captions:** run **Gemma 4 12B locally (8-bit or bf16, mlx-vlm, 280 budget, thinking off)** as the default bulk labeller. E4B is the fast fallback if measured throughput disappoints.
3. **Gemini 3.8 Flash (batch)** as the **pre-labeller for an eval ground-truth set**: 1–3k images, a few dollars. Optionally also a quality ceiling for captions. Commit to a full Gemini bulk pass only if the eval shows a gap the owner cares about, and only after the privacy checklist below is done.
4. **Capture source and colour/mono:** compute these from EXIF and pixels first, with the VLM as tie-breaker (design suggestion, see Q5).

---

## Privacy: Gemini API paid tier vs the owner's criterion

**Criterion (from the owner):** Google must not retain or use the photos for any other purpose, model training above all. Data residency is **not** a gate. The account is an **individual, billing-enabled** Gemini API account with no enterprise or Workspace contract.

Sources:
- Gemini API Additional Terms of Service (https://ai.google.dev/gemini-api/terms, "Effective March 23, 2026", page last updated 2026-04-28)
- Abuse monitoring (https://ai.google.dev/gemini-api/docs/usage-policies, 2026-06-09)
- Data logging and sharing (https://ai.google.dev/gemini-api/docs/logs-policy, 2026-09-04)
- Logs and datasets (https://ai.google.dev/gemini-api/docs/logs-datasets, 2026-09-23)
- ZDR (https://ai.google.dev/gemini-api/docs/zdr, 2026-09-14)
- Interactions overview (https://ai.google.dev/gemini-api/docs/interactions-overview, 2026-10-09)
- Files API (https://ai.google.dev/gemini-api/docs/files, 2026-09-23)
- Batch API (https://ai.google.dev/gemini-api/docs/batch-api, 2026-09-17)
- Billing (https://ai.google.dev/gemini-api/docs/billing, 2026-09-28)

### (a) Do the paid no-training terms apply to an individual on a billed project? **Yes.**

- **What makes it a Paid Service:** "Your access to Gemini API is a 'Paid Service' only when accessing the API through a Cloud Project associated with an active billing account." No enterprise agreement or Workspace account is mentioned for the API. Workspace appears only as an alternative route to make *AI Studio* paid: "Your access to Google AI Studio is a 'Paid Service' even when it is offered free of charge, as long as the account you are using to access Google AI Studio has access to a Cloud Project with an associated and active Cloud Billing account or is a Workspace enterprise account."
- **The key clause:** "When you use Paid Services, including, for example, the paid quota of the Gemini API, Google doesn't use your prompts (including associated system instructions, cached content, and files such as images, videos, or documents) or responses to improve our products, and will process your prompts and responses in accordance with the Data Processing Addendum for Products Where Google is a Data Processor." The DPA is at https://business.safety.google/processorterms/.
- **Practical note:** the billing page says the paid tier needs a prepaid credit balance (minimum $5): "the Gemini API will only serve requests if you have a positive Prepay credit balance". It also footnotes the paid tier with "Enterprise-grade data privacy: For more information on data use for paid services, see the Terms of Service." Make sure the project used is the billing-linked one. The free-tier "Used to improve our products: Yes" applies to unbilled projects (pricing page).

### (b) What is retained, for how long, for what, and can it train models?

| What | Retention | Purpose | Training use? | Can you turn it off? |
|---|---|---|---|---|
| **Abuse-monitoring log** of prompts (incl. images), context and outputs | **55 days** | "detecting and preventing violations of the Prohibited Use Policy… and any required legal or regulatory disclosures" | "It is not used to train or fine-tune any AI/ML models **besides those used specifically for policy enforcement**." | **No** on the Gemini API. The ZDR page says: "If your workload requires guaranteed zero data retention… use Vertex AI." |
| **Interactions API state** (`store=true` is the default) | **55 days** on the paid tier, configurable to 7/14/28/55 | Server-side conversation state and observability | Processed "according to the terms" (paid, so no product improvement) | **Yes:** send `store=false`, or use `generateContent`, which defaults to `store=false` |
| **Project logs** (AI Studio "Logs and datasets"), if enabled | 55 days by default, configurable to 7/14/28/55 | Your own debugging | "prompts and responses within logs are not used for product improvement or development" | **Yes:** keep logging off at project level for both `generateContent` and Interactions |
| **Datasets** made from logs | "Logs stored in datasets don't have set retention periods" | Your own evals | Only if **shared** with Google (see c) | Don't create them, or delete them |
| **Files API uploads** | "Files are stored for 48 hours", or until deleted | Inputs for requests | Covered by the paid terms | Delete after use (`files.delete`) |
| **Batch job results** | "stored and available for download for 6 weeks, before they are permanently deleted" | Delivery of results | Paid terms. The pricing page's Batch table says "Used to improve our products: … No" for the paid tier | Delete the job and its files after download |
| **Implicit in-memory cache** | RAM only, 24 h TTL | Latency and cost | "This does not violate zero data retention." (ZDR page) | n/a |
| **Grounding with Google Search / Maps** | 30 days, "no way to disable" | Grounding, debugging | Under the DPA for paid quota | **Don't enable grounding tools** |
| **Account and usage metadata** (token counts, safety triggers, IPs, feedback) | Not stated | Operating the service | Governed by the Controller-Controller terms and the Privacy Policy, not the DPA | No |

"Data can be accessed for human review only by authorized Google employees" for flagged abuse-log content (usage-policies page). The abuse log "may be stored transiently or cached in any country in which Google or its agents maintain facilities" (terms).

### (c) Opt-in features that would change this: keep these off

- **Sharing datasets with Google.** "When you share a dataset with Google, your logs in that dataset, including requests and responses, will be processed in accordance with our Terms for 'Unpaid Services,' meaning the dataset may be used to develop and improve Google products, services, and machine learning technologies, including improving and training our models." Human reviewers may read these (logs-policy).
- **Feedback on logs** (thumbs up/down, written comments). These are listed as data you can "contribute… for product improvements and model training" (logs-policy).
- **Project logging in AI Studio.** It isn't training use in itself, but it creates a stored copy of every request, images included. "Inputs containing videos, GIFs or PDFs" are not logged; images are.
- **Interactions API with default `store=true`.** This is a 55-day stored copy.
- **Grounding with Google Search / Maps.** This is a 30-day copy that can't be disabled.
- **Explicit context caching.** The cache is stored until its TTL expires.
- The logs policy notes "Options are dependent on your location". Which sharing options appear for a Swiss account is **UNVERIFIED**.

### (d) Are Batch API and Files API covered by the same terms? **Yes.**

- Batch is part of the Gemini API. The pricing page's **Batch** table for Gemini 3.8 Flash states "Used to improve our products: Yes [free] / **No** [paid]". Batch output is kept 6 weeks, and failed or expired jobs after 48 h (batch page).
- `BatchGenerateContent` calls **are captured by project logging if logging is on** (logs-datasets: "All GenerateContent, BatchGenerateContent, StreamGenerateContent API calls, and Interactions API calls… are supported").
- Batch is "currently only available with the generateContent API", which defaults to `store=false`.
- The Files API is part of the Services accessed through the billed project, so the paid terms apply. Uploads persist for **48 h** unless deleted, and "you can't download user-uploaded files" (files page).

### (e) Does the Swiss/EEA/UK clause change anything for an individual?

- **Nothing material for a billed project, but it adds a safety net.** "If you're in the European Economic Area, Switzerland, or the United Kingdom, the terms under 'How Google uses Your Data' in 'Paid Services' apply to all Services, including Google AI Studio and unpaid quota in the Gemini API, even though they are offered free of charge."
  - So even an accidental call through an unbilled project or AI Studio's free quota is not used for training.
  - The 55-day abuse log still applies.
- Two related clauses:
  - "You may use only Paid Services when making API Clients available to users in the European Economic Area, Switzerland, or the United Kingdom." This is not relevant to a personal tool.
  - "Use of Google AI Studio and Gemini API is for developers building with Google AI models for professional or business purposes, not for consumer use." Whether a developer's personal archive tool fits is for the owner to judge (as noted in hosted-gemma.md).

### Verdict against the owner's criterion

| Part of criterion | Result |
|---|---|
| Not used for model training | **PASS.** Contractual for Paid Services. One residual carve-out: abuse logs may train *policy-enforcement* models. |
| Not used for other purposes | **PASS with caveat.** Only abuse and legal monitoring, plus whatever you opt into. |
| Not retained | **FAIL on a strict reading.** A 55-day abuse log of prompts, including images, can't be disabled on the Gemini API. Everything else can be avoided. |

**Overall: conditional PASS** if the owner accepts the 55-day abuse log. If not, the only Google-hosted route with a ZDR exception is Agent Platform (Vertex), per hosted-gemma.md Q3, and that needs a Cloud project and an exception request.

**Checklist before any Gemini run:**
1. Use the billing-linked project with a positive prepay balance.
2. Turn AI Studio logging **off** for both `generateContent` and Interactions, and set retention to 7 days as a backstop.
3. Never create or share datasets, and never give log feedback.
4. Use `generateContent` / Batch, or Interactions with `store=false`.
5. No grounding tools.
6. Delete Files API uploads and batch jobs after download.

---

## Q1. Gemini 3.8 Flash

### Release status and variants
- "Gemini 3.8 Flash (`gemini-3.8-flash`) is generally available (GA) and ready for production use." Its default thinking level is `medium` (https://ai.google.dev/gemini-api/docs/latest-model, 2026-10-09).
- Model page: inputs "Text, Image, Video, Audio, and PDF"; input limit 1,048,576 and output limit 65,536 tokens; "Structured outputs: Supported"; "Thinking: Supported (low, medium, high) Note: minimal is not supported and returns an error"; Batch API, Flex and Priority all "Supported"; "Stable: gemini-3.8-flash"; latest update September 2026 (https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash, 2026-09-02).
- Agent Platform (Vertex): "Launch stage: GA, Release date: September 2, 2026" (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash, 2026-10-10).
- **Gemini 3.7 Flash:** "Deprecated: Gemini 3.7 Flash (gemini-3.7-flash) is deprecated and has been replaced by Gemini 3.8 Flash… All requests to gemini-3.7-flash are automatically routed to gemini-3.8-flash." (https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash, 2026-10-09.) The `gemini-3.7-flash-lite` page returns 404, and no 3.7 Flash-Lite appears on the models page.
- **Cheaper current variants** (models page, https://ai.google.dev/gemini-api/docs/models, 2026-10-09):
  - **Gemini 3.6 Flash**: "previous-generation Flash", same price as 3.8. Supports `minimal` thinking.
  - **Gemini 3.5 Flash-Lite**: "Our fastest, most cost-effective 3.5 model for high-throughput execution", stable.
  - **Gemini 3.1 Flash-Lite**: stable.
  - All three: structured outputs and Batch supported (model pages for 3.6 Flash, 3.5 Flash-Lite, 3.1 Flash-Lite).
  - The models page says: "For any new projects, use our latest models: 3.5 Flash-Lite or 3.8 Flash."

### Image input
- **`media_resolution`** (https://ai.google.dev/gemini-api/docs/media-resolution, 2026-09-23): "Gemini 3 models" image tokens:

  | Level | Image tokens (approx.) |
  |---|---|
  | unspecified (**default**) | **1120** |
  | low | **280** |
  | medium | 560 |
  | high | 1120 |
  | ultra_high (per item only) | 2240 |

  - "Note that high provides the optimal performance for most use cases." Per-content-item resolution is "Gemini 3 only".
  - **Set `low` explicitly to get ~280 tokens.** The default is 4× that.
  - Whether `low` is good enough for captions and tags on film scans should be checked in the eval. Medium is the obvious second arm.
- The older "258 tokens per 768×768 tile" rule on the image-understanding page describes pre-Gemini-3 tokenisation. For Gemini 3 the `media_resolution` table governs.
- **Formats:** "PNG - image/png, JPEG - image/jpeg, WEBP - image/webp, HEIC - image/heic, HEIF - image/heif". **TIFF is not supported.** Max "3,600 image files per request". Inline-data note: "Inline image data limits your total request size… to 20MB" (https://ai.google.dev/gemini-api/docs/image-understanding, 2026-09-23).
- The file-input-methods page now says inline is "100 MB per request or payload" (https://ai.google.dev/gemini-api/docs/file-input-methods, 2026-10-07). The pages still conflict; **design for 20 MB**, as hosted-gemma.md also says.
- Other limits: Files API is 2 GB per file and 20 GB per project, kept 48 h. Vertex: inline 7 MB per file, Cloud Storage 30 MB per file, same MIME list.
- **Implication for the 16-bit TIFF scans** (my design suggestion): produce 8-bit sRGB JPEG derivatives sized for the chosen budget, and send the same bytes to every route.

### Structured output
- "You can configure Gemini models to generate responses that adhere to a provided JSON Schema."
- It is a subset of JSON Schema: types string/number/integer/boolean/object/array/null; `enum`, `format`, `minimum`/`maximum`, `items`, `prefixItems`, `minItems`/`maxItems`, `required`, `additionalProperties`.
- "Very large or deeply nested schemas may be rejected." "While output is syntactically correct JSON, always validate values in your application." (https://ai.google.dev/gemini-api/docs/structured-output, 2026-09-23.)

### Pricing (paid tier, per 1M tokens, USD)

Source: https://ai.google.dev/gemini-api/docs/pricing, 2026-10-09.

| Model | Standard in / out | Batch in / out | Notes |
|---|---|---|---|
| **3.8 Flash** | **$0.75 / $3.75** "through December 31, 2026"; **$1.50 / $7.50** "starting January 1, 2027" | **$0.375 / $1.875**, then $0.75 / $3.75 | Output price "including thinking tokens". Flex is the same as Batch. Priority is $1.35 / $6.75 (intro). |
| 3.6 Flash | Same as 3.8 Flash, same dates | Same | |
| 3.5 Flash-Lite | $0.30 / $2.50 | $0.15 / $1.25 | No date-based change |
| 3.1 Flash-Lite | $0.25 / $1.50 | $0.125 / $0.75 | |

- **The introductory rate does end.** The latest-model page says: "Introductory pricing: Gemini 3.8 Flash is available at an introductory rate of $0.75/1M input tokens and $3.75/1M output tokens through December 31, 2026. Standard pricing of $1.50/1M input tokens and $7.50/1M output tokens takes effect January 1, 2027."
- The terms also allow price changes "effective 30 days after they are posted".

### Batch, Flex, rate limits
- **Batch:** "process large volumes of requests asynchronously at 50% of the standard cost. The target turnaround time is 24 hours, but in majority of cases, it is much quicker."
  - Jobs pending more than 48 h expire.
  - "Note: This feature is currently only available with the generateContent API."
  - Inline batches under 20 MB; JSONL input via the Files API up to 2 GB.
  - Source: batch page.
- **Flex** (Preview): 50% off, synchronous, "Minutes (1–15 min target)" latency, "Best-effort (Sheddable)". It counts against normal rate limits, returns 503 when capacity is short, and never upgrades to Standard automatically (https://ai.google.dev/gemini-api/docs/flex-inference, 2026-10-09). This is an alternative to Batch with the same price and simpler plumbing.
- **Rate limits** (https://ai.google.dev/gemini-api/docs/rate-limits, 2026-10-09):
  - **RPM / TPM / RPD for 3.8 Flash per tier are not published**: "can be viewed in Google AI Studio". **UNVERIFIED; check in AI Studio.**
  - Published per-tier figures:

    | Tier | Qualification | Spend rate limit (per 10 min) | Monthly billing cap | 3.8 Flash batch enqueued tokens | Flash-Lite (3.5/3.1) batch enqueued tokens |
    |---|---|---|---|---|---|
    | Tier 1 | "Set up and link an active billing account" | $10 | **$250** | **3,000,000** | 10,000,000 |
    | Tier 2 | "Paid $100 + 3 days from first successful payment" | $50 | $2,000 | **400,000,000** | 500,000,000 |

  - Concurrent batch jobs: 100.
- **What this means at Tier 1 (my arithmetic):**
  - 100k images × 580 input tokens = 58M tokens. At a 3M-token queue cap that's **≈ 20 sequential batch rounds** of about 5k images each. If each round takes hours, the run takes days.
  - Tier 2 removes the queue constraint, but needs $100 of cumulative spend.

### Thinking
- 3.8 Flash supports `low`, `medium` (default) and `high`. **`minimal` "is not supported for Gemini 3.8 Flash and will return an error."** (latest-model page.)
- The thinking-levels table (https://ai.google.dev/gemini-api/docs/thinking, 2026-10-09):

  | Model | Default | Supported levels |
  |---|---|---|
  | gemini-3.8-flash | medium | low, medium, high |
  | gemini-3.6-flash | medium | **minimal**, low, medium, high |
  | gemini-3.5-flash-lite | **minimal** | minimal, low, medium, high |

- **Billing:** "When thinking is turned on, response pricing is the sum of output tokens and thinking tokens… Pricing is based on the full thought tokens the model needs to generate, despite only the summary being output from the API." Thinking counts are reported in `total_thought_tokens`.
- Google also warns: "Gemini 3.8 Flash can use more tokens on longer running and complex tasks, by design." (latest-model.)
- **Thinking-token count at `low` for an image-labelling prompt isn't documented. Measure it on a pilot.**

### EU/Swiss pinning (no longer a gate)
- **Gemini API:** no region selection.
- **Agent Platform (Vertex):** 3.8 Flash offers "Multi-region: us, eu" for ML processing and Standard PayGo, plus a "Data residency" control for online and batch inference (Vertex 3.8 Flash page).
- The EU multi-region **excludes Switzerland** (data-residency page, quoted in hosted-gemma.md Q4). **EU pinning is possible via Vertex; Swiss pinning is not.**

---

## Q2. Gemma 4 E4B and 12B locally on Apple Silicon

**Model facts** (from hosted-gemma.md and the model card, https://ai.google.dev/gemma/docs/core/model_card_4, 2026-07-30):

| | E4B | 12B "Unified" |
|---|---|---|
| Parameters | 4.5B effective (8B with embeddings) | 11.95B |
| Context | 128K | 256K |
| Vision path | ~150M vision encoder | **Encoder-free**: "projecting raw image patches and audio waveforms directly into the LLM's embedding space" |
| MMMU Pro | 52.6% | 69.1% |
| OmniDocBench edit distance | 0.181 | 0.164 |
| Release | 31 Mar 2026 | **3 Jun 2026** |

Release dates are from https://ai.google.dev/gemma/docs/releases (2026-10-07). On vision benchmarks, 12B sits much closer to 26B A4B than to E4B.

### Runtimes (versions as of 2026-10-10)

| Runtime | Version | Gemma 4 vision on Mac | Image-budget control | Structured / JSON output |
|---|---|---|---|---|
| **mlx-vlm** | **0.7.6** (PyPI, 2026-10-05); MLX 0.32.3 | Yes. Model dirs `gemma4` and `gemma4_unified` (12B) | **Yes.** Processor `max_soft_tokens` in (70, 140, 280, 560, 1120), default 280 | **Yes.** Server `/v1/chat/completions` and `/v1/responses` "support OpenAI-compatible `json_schema` structured outputs" ("not currently supported with speculative decoding") |
| **Ollama** | **0.40.2** (2026-10-08) | Yes. v0.33.3: "gemma4 now supports images and audio on MLX engine"; v0.40.0: models "automatically run on MLX" on Apple Silicon, gemma4 included | **No API parameter.** See note below | **Yes.** `format`: "`json` or a JSON schema" |
| **llama.cpp** | **b11541** (2026-10-10) | Yes. `tools/mtmd/models/gemma4v.cpp` and `gemma4uv.cpp` (12B unified) | **Yes.** `--image-min-tokens` / `--image-max-tokens`; Gemma 4 limits set to `(70, 1120)` | **Yes.** `json_schema`, `response_format` (json_schema), GBNF grammars |
| **LM Studio** | **0.4.26** (2026-10-08); llama.cpp runtime 2.50.0 (upstream b11337) | Yes, GGUF and MLX. Catalogue lists `google/gemma-4-e4b` (5.90 GB) and `google/gemma-4-12b` (7.40 GB); "Gemma 4 models support tool use, vision input, and reasoning" | **Not exposed in docs. UNVERIFIED.** 0.4.24 added "advanced llama.cpp argument overrides for GGUF model loading", which might accept `--image-max-tokens` (untested) | **Yes.** `/v1/chat/completions` with `response_format: json_schema` |

Sources:
- mlx-vlm: PyPI JSON; `mlx_vlm/models/gemma4/processing_gemma4.py`; README "Structured Outputs" and "Continuous Batching".
- Ollama: GitHub releases v0.33.3, v0.34.4, v0.40.0, v0.40.2; `docs/api.md`; PR #18603.
- llama.cpp: releases; `tools/server/README.md`; `tools/mtmd/clip.cpp`.
- LM Studio: https://lmstudio.ai/models/gemma-4, https://lmstudio.ai/changelog/lmstudio, https://lmstudio.ai/docs/developer/openai-compat/structured-output.
- Google's own run guides for these runtimes are at https://ai.google.dev/gemma/docs/integrations/{mlx,ollama,llamacpp,lmstudio}.

**Ollama image-budget behaviour.** PR #18603, "mlx: select Gemma 4 image resolution dynamically" (merged 2026-09-23, shipped in 0.34.4: "Gemma 4 on Apple Silicon now picks the best image resolution per image"):
- Quote: "Replace the fixed checkpoint image budget with per-image selection across the supported 70, 140, 280, 560, and 1120 budgets. Choose the publisher resize grid closest to the input resolution… **without adding an API parameter.**"
- A 12–24 MP photo will therefore be encoded at ≈ 1120 tokens, 4× the 280 budget, with matching prefill cost.
- **Workaround (my inference, verify with `prompt_eval_count`):** send derivatives already resized to the 280 grid (e.g. 912×672 for 4:3; see the table in hosted-gemma.md Q1).
- The llama.cpp engine in Ollama has its own handling, which is **UNVERIFIED**.

### Quantisations available (Hugging Face / Ollama, file sizes)

Sources: HF tree API for each repo; https://ollama.com/library/gemma4/tags.

| | E4B | 12B |
|---|---|---|
| Google official | `google/gemma-4-E4B-it` bf16 (16.0 GB); `google/gemma-4-E4B-it-qat-q4_0-gguf` (5.15 GB + 0.99 GB mmproj) | `google/gemma-4-12B-it` bf16 (23.9 GB); `google/gemma-4-12B-it-qat-q4_0-gguf` (6.98 GB + 0.18 GB mmproj) |
| ggml-org GGUF | Q4_0 4.59 GB, Q8_0 8.03 GB, BF16 15.05 GB; mmproj Q8_0 0.56 / BF16 0.99 GB | Q4_0 7.22 GB, Q8_0 12.67 GB, BF16 23.83 GB; mmproj 0.16–0.18 GB |
| mlx-community | 4bit 5.15 GB, 8bit 8.88 GB, bf16 15.88 GB; also 5/6-bit, mxfp4/8, nvfp4, OptiQ-4bit, qat-OptiQ-4bit | 4bit 6.74 GB, 8bit 12.72 GB, bf16 23.92 GB, qat-4bit; also 5/6-bit, mxfp4/8, nvfp4 |
| Ollama tags | `e4b` (= q4_K_M GGUF 6.6 GB / MLX nvfp4 9.5 GB), `e4b-it-qat` 6.1 GB, `e4b-it-q8_0` 9.0 GB, `e4b-it-bf16` 16 GB, `e4b-mxfp8` 12 GB, `e4b-mlx-bf16` 16 GB | `12b` (7.7–8.0 GB), `12b-it-qat` 7.2 GB, `12b-it-q8_0` 13 GB, `12b-it-bf16` 24 GB, `12b-mxfp8` 13 GB, `12b-mlx-bf16` 25 GB |

### Memory footprint
- Weights as above. Ollama PR #18603 measured resident memory by image budget:
  - **12B NVFP4:** 7.39 GiB (budget 70), 7.66 GiB (280), 8.15 GiB (1120).
  - **E4B NVFP4:** 9.03 GiB (70), 9.52 GiB (280), 9.56 GiB (1120).
- mlx-vlm's Gemma 4 README lists E4B at "~16 GB" (bf16).
- **On 128 GB, even bf16 12B (~24 GB) plus EmbeddingGemma 2 plus a batch KV cache fits easily.** Given the owner's accuracy-over-speed preference, start at 8-bit or bf16, not 4-bit.

### Throughput: no primary figures; must be measured
- **No Google, mlx-vlm, Ollama or llama.cpp source publishes Gemma 4 E4B or 12B image-captioning throughput on M-series.**
  - Ollama's MLX blog (https://ollama.com/blog/mlx-performance, 2026-06-11) shows Gemma 4 12B on an M5 Max only as a chart. Its text gives relative claims: "up to 20% faster"; "NVFP4 generates about 20% faster than q4_K_M".
  - mlx-vlm's README gives only the bandwidth rule: "The 31B dense model is bandwidth-bound at ~5 tok/s on 300 GB/s systems (62.5 GB / 300 GB/s)."
- **Hardware:** the 128 GB M5 Max comes only with the 40-core GPU at **614 GB/s**. Apple: "128GB unified memory (M5 Max with 40-core GPU)"; 40-core GPU "614GB/s memory bandwidth" (https://www.apple.com/macbook-pro/specs/).
- **Decode ceiling by the same rule (my arithmetic, single stream):**

  | Model and precision | Bytes per token | Decode ceiling | 150-token answer |
  |---|---|---|---|
  | E4B 8-bit | ≈ 4.5 GB | ≤ ~135 tok/s | ≥ 1.1 s |
  | E4B 4-bit | — | ≤ ~250 tok/s | ≥ 0.6 s |
  | 12B 8-bit | ≈ 12 GB | ≤ ~50 tok/s | ≥ 2.9 s |
  | 12B 4-bit | ≈ 6.5 GB | ≤ ~95 tok/s | ≥ 1.6 s |
  | 12B bf16 | ≈ 24 GB | ≤ ~25 tok/s | ≥ 5.9 s |

  - Prefill (~580 tokens) and the vision tower add an unknown amount on top.
  - **Batching** (mlx-vlm continuous batching, where "all requests decode together in a single batch"; Ollama `OLLAMA_NUM_PARALLEL`, default 1) shares each weight read across requests, so aggregate decode should rise several-fold until compute-bound.
- Non-primary sanity check, **UNVERIFIED**: community oMLX benchmark pages report roughly 108–156 tok/s generation for E4B on a 40-core M5 Max at 1k context. That is consistent with the bound above.

### Thinking locally
- mlx-vlm: "Thinking is off by default"; enable with `--enable-thinking` / `enable_thinking=True`.
- Ollama `think` accepts a boolean or a level.
- For labelling, run with thinking **off** so all output tokens go to the answer.

---

## Q3. EmbeddingGemma

**Current versions** (https://ai.google.dev/gemma/docs/releases; https://ai.google.dev/gemma/docs/embeddinggemma, 2026-10-06):
- "October 6, 2026: Release of EmbeddingGemma 2 in 740M parameter size."
- "September 4, 2025: Release of EmbeddingGemma in 300M parameter size."
- **The report of a multimodal "EmbeddingGemma 2" released in early October 2026 is verified.**

| | EmbeddingGemma 1 (`google/embeddinggemma-300m`) | **EmbeddingGemma 2 (`google/embeddinggemma-2`)** |
|---|---|---|
| Modalities | **Text only** (Gemma 3 based, 308M) | **Text (incl. code), image, video, audio, and interleaved combinations** |
| Dimensions | 768 (MRL 512/256/128) | **768** native; MRL **512/256/128** |
| Context | 2K (Ollama tag) | 8,192 tokens shared across modalities |
| Licence | Gemma Terms of Use (HF `license: gemma`, gated "manual") | **Apache 2.0** (HF `license: apache-2.0`, ungated) |
| Size | 300M | 740M total = 270M text (130M backbone + 140M embedder) + 170M vision + 300M audio. Text+image only = **440M** |

**Shared embedding space: yes.** From the model card (https://huggingface.co/google/embeddinggemma-2):
- "maps text (incl. code), images, video, and audio inputs… into a single, unified 768-dimensional vector space."
- On an interleaved example: "This embedding can be compared directly against any other EmbeddingGemma 2 embedding; for example, a text-only query such as 'waterproof shoes for trail running'."
- Google's multimodal guide computes "image-to-text similarity" directly: "the text query 'San Francisco' achieves the highest cross-modal similarity with the Golden Gate Bridge photo" (https://ai.google.dev/gemma/docs/embeddinggemma/multimodal-embeddinggemma-with-sentence-transformers, 2026-10-07).
- **Text-to-image search therefore works without a separate image–text model.**

**Image input handling** (model card):
- "Image: 280 tokens per image (default)". Users can set "soft token' amounts ranging from 70 to 1120".
- Per-call setting in sentence-transformers: `processing_kwargs={"image": {"max_soft_tokens": 1120}}`.
- Prefixes: "Prefixes apply to text only. Pass images, video, and audio without any prefix."
- Queries use `task: search result | query: {query}` (prompt name `SearchQuery`); documents use `title: {title} | text: {content}`.
- **Precision:** "Run inference in bfloat16 or float32. Do not use float16… In float16 the model returns NaN or silently degraded embeddings rather than raising an error."
- **Quality vs dimension:** "close to lossless down to 256 dimensions. 128 dimensions degrades multimodal quality substantially."
- **Benchmarks (768d):** MIEB-lite 64.64, MMEB v2-Image Hit@1 57.28. Text MTEB multilingual 61.36 vs 61.15 for v1.

**Runtimes on Mac:**
- **sentence-transformers** (reference): `pip install -U sentence-transformers transformers`; `SentenceTransformer("google/embeddinggemma-2")`. Select towers with `config_kwargs={"audio_config": None}` (text+image, 440M). The minimum ST version isn't stated; **UNVERIFIED**.
- **MLX (mlx-vlm)**: `mlx_vlm/models/embedding_gemma2`, added 2026-10-06 (commit "Add EmbeddingGemma 2 multimodal embedding support (#2446)").
  - The README says it accepts `max_soft_tokens` (70…1120).
  - Validated on "Apple M5 Max": "BF16 is fastest for text, image, and audio in these tests. 8-bit… minimum embedding cosine 0.99966 against the float32 reference; plain 4-bit falls to 0.96716 and fails the documented accuracy limits." **Don't use 4-bit.**
  - Its examples load `nativ-community/embeddinggemma-2`, a community conversion whose HF metadata wasn't retrievable. Prefer converting `google/embeddinggemma-2` yourself (`mlx_vlm.convert --hf-path google/embeddinggemma-2 … --dtype bfloat16`).
- **Ollama**: `embeddinggemma-2` (https://ollama.com/library/embeddinggemma-2).
  - Tags: `740m` / `latest` 1.3 GB (Text, Image); `440m` 714 MB (Text, Image); `270m` text-only; bf16, mxfp8 and nvfp4 MLX variants.
  - v0.40.0: "MLX now has support for an embedding model: embeddinggemma-2".
  - `/api/embed` accepts "an object with any of `text` (string), `image` (base64-encoded bytes), `audio`" (`docs/api.md`).
  - Ollama's EG2 runner uses the reference fixed 280 budget. Its code comment notes that a dynamic budget "regressed embeddings… costing 0.02-0.04 cosine vs sentence-transformers" (`mlxrunner/model/gemma4/process_image_test.go`).
  - Ollama's "256K context" label conflicts with the model card's 8,192; trust the card.
- **Throughput:** no published images/s. A 440M encoder at 280 tokens per image is small next to the VLM pass. Expect hours, not days, for 100k images (**UNVERIFIED; measure**).

**Image–text alternative (brief), if EG2's retrieval quality disappoints in the eval:** **SigLIP 2** (e.g. `google/siglip2-so400m-patch16-naflex`, Apache 2.0, Feb 2025) is the standard Google contrastive image–text encoder. It is a separate space from text-caption embeddings. Embedding generated captions as text with EG2 remains a complementary signal either way.

---

## Q4. Cost and time for ~100k images

Base assumptions per image: 300 prompt tokens, 150 output tokens, and each model's real image tokens. All costs are my arithmetic from the prices in Q1.

### Gemini (paid tier, USD)

Gemini 3 image tokens: low 280 / medium 560 / high-or-default 1120 (Q1). Input per image = image tokens + 300.

| Model, resolution | Input tokens (100k) | Sync (intro) | **Batch (intro)** | Sync (from 1 Jan 2027) | Batch (2027) |
|---|---|---|---|---|---|
| **3.8 Flash, low (280)** | 58M | 43.50 + 56.25 = **$99.75** | **$49.88** | $199.50 | $99.75 |
| 3.8 Flash, medium (560) | 86M | 64.50 + 56.25 = **$120.75** | **$60.38** | $241.50 | $120.75 |
| 3.8 Flash, default/high (1120) | 142M | 106.50 + 56.25 = **$162.75** | **$81.38** | $325.50 | $162.75 |
| 3.5 Flash-Lite, low | 58M | 17.40 + 37.50 = **$54.90** | **$27.45** | same | same |
| 3.1 Flash-Lite, low | 58M | 14.50 + 22.50 = **$37.00** | **$18.50** | same | same |
| 3.1 Flash-Lite, high | 142M | 35.50 + 22.50 = **$58.00** | **$29.00** | same | same |

All rows assume 15M output tokens.

**Thinking surcharge:**
- 3.8 Flash can't go below `low`. Each **100 thinking tokens per image** adds 10M output tokens: **+$37.50 sync / +$18.75 batch** at the introductory rate, doubled in 2027.
- 300 thinking tokens per image would make low-resolution batch ≈ $106. Measure on a 200-image pilot.
- 3.6 Flash (same price, supports `minimal`) or the Flash-Lites avoid most of this.

**Wall-clock:**
- Sync is limited by the Tier 1 RPM, which is unpublished (**UNVERIFIED**).
- At Tier 1, batch needs ≈ 20 rounds of ≤ 3M enqueued tokens for 3.8 Flash, or ≈ 6 rounds at 10M for the Flash-Lites. Each round targets ≤ 24 h, "much quicker" in most cases.
- At Tier 2 it is effectively one submission.
- **Finish before 31 Dec 2026 to keep the introductory price.**
- The Tier 1 monthly cap ($250) covers every row except 2027 sync.

**Eval/pre-label set** (3,000 images, 3.8 Flash, medium, batch): ≈ 3k × (860 × $0.375 + 150 × $1.875)/1M ≈ **$1.81**, plus thinking.

### Local (Gemma 4 on the M5 Max)
- **Cost:** electricity only.
- **Time** depends on throughput, which no primary source gives, so it **must be measured**. For any measured rate:

  | Sustained throughput | 100k images |
  |---|---|
  | 2 img/s | 13.9 h |
  | 1 img/s | 27.8 h |
  | 0.5 img/s | 55.6 h |
  | 0.25 img/s | 111 h (4.6 days) |

- **Decode-only single-stream lower bounds** (my arithmetic from Q2: 150 tokens per image, 614 GB/s):

  | Model and precision | Lower bound | Plus |
  |---|---|---|
  | E4B 8-bit | ≥ 31 h | prefill and vision |
  | 12B 4-bit | ≥ 44 h | " |
  | 12B 8-bit | ≥ 82 h | " |
  | 12B bf16 | ≥ 165 h | " |

  - With 4–8-way batched decoding, expect these to fall several-fold.
  - My planning range: **E4B ≈ 0.5–2 days, 12B 8-bit ≈ 1–4 days** (**UNVERIFIED**).
  - On Ollama, full-resolution inputs at ≈ 1120 image tokens would make prefill roughly 4× worse. Pre-resize.
- **One-off pre-processing:** decoding about 210 GB of 16-bit TIFFs and HEICs into JPEG derivatives is CPU and disk work, done once and shared by all routes. Its cost is unmeasured.
- **EmbeddingGemma 2:** expected well under the VLM time (**UNVERIFIED**).

**Measurement protocol (suggested):**
- Use 200 mixed images (phone, 35 mm, 6×6, B&W, print scans) at the 280 budget with the real JSON schema.
- Record images/s at batch sizes 1, 4 and 8 for E4B-8bit, 12B-8bit and 12B-bf16 in mlx-vlm.
- Run once in Ollama with pre-sized inputs.
- Extrapolate to 100k.

---

## Q5. Combinations worth evaluating

1. **Local Gemma 4 12B for structured fields and captions, plus EmbeddingGemma 2 for search.**
   - Fully private, $0, and fits easily in 128 GB.
   - Store the image embedding and the caption/tag text embedding in the same 768-d space (or a 256-d MRL prefix). Rank by both.
   - This is the default MVP path.
2. **Gemini 3.8 Flash as pre-labeller for eval ground truth.**
   - Run 1–3k stratified images through batch at medium resolution for about $2–5.
   - The owner corrects the labels, producing a gold set to score local E4B vs 12B, the image budgets (280 vs 560) and the EG2 retrieval quality.
   - This needs only the privacy checklist, not a bulk commitment.
3. **Gemini for captions only, local for everything else.**
   - Only worthwhile if the eval shows Gemini captions are clearly better for search recall.
   - Cost ≈ $50–60 batch for 3.8 Flash at low/medium, or ≈ $18–29 on 3.1 Flash-Lite.
   - It still ships every photo to Google under the 55-day abuse log.
4. **Deterministic fields first, VLM as tie-breaker** (design suggestion, not from a vendor source):
   - **Capture source:** EXIF `Make`/`Model`/`Software` (phone makers vs scanner software), bit depth and TIFF origin, and the 6×6 square aspect ratio.
   - **Colour vs mono:** pixel saturation statistics. Sepia and toned B&W need a threshold the VLM can confirm.
   - Ask the VLM only where these are ambiguous (e.g. print scans vs film scans). This cuts schema size and errors.
5. **SigLIP 2 as a second image-embedding arm**, only if EG2's text-to-image recall is weak on the gold set.

---

## Sources

Google AI for Developers (Gemini API):
- Models: https://ai.google.dev/gemini-api/docs/models (2026-10-09)
- Gemini 3.8 Flash model page: https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash (2026-09-02)
- Gemini 3.7 Flash (deprecated): https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash (2026-10-09)
- Gemini 3.6 Flash: https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash (2026-07-30)
- Gemini 3.5 Flash-Lite: https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite (2026-07-30)
- Gemini 3.1 Flash-Lite: https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite (2026-07-21)
- What's new in Gemini 3.8 Flash: https://ai.google.dev/gemini-api/docs/latest-model (2026-10-09)
- Gemini 3 developer guide (deprecated page): https://ai.google.dev/gemini-api/docs/gemini-3 (2026-10-09)
- Media resolution: https://ai.google.dev/gemini-api/docs/media-resolution (2026-09-23)
- Image understanding: https://ai.google.dev/gemini-api/docs/image-understanding (2026-09-23)
- File input methods: https://ai.google.dev/gemini-api/docs/file-input-methods (2026-10-07)
- Files API: https://ai.google.dev/gemini-api/docs/files (2026-09-23)
- Structured outputs: https://ai.google.dev/gemini-api/docs/structured-output (2026-09-23)
- Thinking: https://ai.google.dev/gemini-api/docs/thinking (2026-10-09)
- Token counting: https://ai.google.dev/gemini-api/docs/tokens (2026-09-23)
- Pricing: https://ai.google.dev/gemini-api/docs/pricing (2026-10-09)
- Batch API: https://ai.google.dev/gemini-api/docs/batch-api (2026-09-17)
- Flex inference: https://ai.google.dev/gemini-api/docs/flex-inference (2026-10-09)
- Rate limits: https://ai.google.dev/gemini-api/docs/rate-limits (2026-10-09)
- Billing: https://ai.google.dev/gemini-api/docs/billing (2026-09-28)
- Additional Terms of Service: https://ai.google.dev/gemini-api/terms (effective 2026-03-23; page 2026-04-28)
- Abuse monitoring: https://ai.google.dev/gemini-api/docs/usage-policies (2026-06-09)
- Data logging and sharing: https://ai.google.dev/gemini-api/docs/logs-policy (2026-09-04)
- Logs and datasets: https://ai.google.dev/gemini-api/docs/logs-datasets (2026-09-23)
- Zero data retention: https://ai.google.dev/gemini-api/docs/zdr (2026-09-14)
- Interactions API overview: https://ai.google.dev/gemini-api/docs/interactions-overview (2026-10-09)
- Data Processing Addendum: https://business.safety.google/processorterms/
- Controller-Controller terms: https://business.safety.google/controllerterms/

Google Cloud:
- Gemini 3.8 Flash on Agent Platform: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash (2026-10-10)
- Data residency: https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/data-residency (2026-10-07)

Google AI for Developers (Gemma):
- Gemma releases: https://ai.google.dev/gemma/docs/releases (2026-10-07)
- Gemma overview: https://ai.google.dev/gemma/docs (2026-10-07)
- Gemma 4 model card: https://ai.google.dev/gemma/docs/core/model_card_4 (2026-07-30)
- EmbeddingGemma overview: https://ai.google.dev/gemma/docs/embeddinggemma (2026-10-06)
- EmbeddingGemma 2 model card: https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2 (2026-10-06)
- EmbeddingGemma 1 model card: https://ai.google.dev/gemma/docs/embeddinggemma/model_card (2025-09-25)
- Multimodal embeddings with EmbeddingGemma 2: https://ai.google.dev/gemma/docs/embeddinggemma/multimodal-embeddinggemma-with-sentence-transformers (2026-10-07)
- Run Gemma with MLX / Ollama / llama.cpp / LM Studio: https://ai.google.dev/gemma/docs/integrations/mlx (2026-04-16), https://ai.google.dev/gemma/docs/integrations/ollama (2026-04-02), https://ai.google.dev/gemma/docs/integrations/llamacpp (2026-04-16), https://ai.google.dev/gemma/docs/integrations/lmstudio (2026-01-20)

Hugging Face (Google-published weights and metadata):
- https://huggingface.co/google/embeddinggemma-2 (README; API metadata: created 2026-09-14, last modified 2026-10-06, apache-2.0)
- https://huggingface.co/google/embeddinggemma-300m (license: gemma)
- https://huggingface.co/google/gemma-4-E4B-it, https://huggingface.co/google/gemma-4-12B-it, https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf, https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf
- https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF, https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF
- https://huggingface.co/mlx-community/gemma-4-e4b-it-8bit (and -4bit, -bf16), https://huggingface.co/mlx-community/gemma-4-12B-it-8bit (and -4bit, -bf16, -qat-4bit)
- https://huggingface.co/google/siglip2-so400m-patch16-naflex

Runtimes:
- mlx-vlm: https://pypi.org/project/mlx-vlm/ (0.7.6); https://github.com/Blaizzy/mlx-vlm (README; `mlx_vlm/models/gemma4/README.md`; `mlx_vlm/models/gemma4/processing_gemma4.py`; `mlx_vlm/models/embedding_gemma2/README.md`)
- Ollama: https://github.com/ollama/ollama/releases (v0.33.3, v0.34.4, v0.40.0, v0.40.2); https://github.com/ollama/ollama/pull/18603; `docs/api.md`; `mlxrunner/model/gemma4/vision.go` and `process_image_test.go`; https://ollama.com/library/gemma4/tags; https://ollama.com/library/embeddinggemma-2/tags; https://ollama.com/library/embeddinggemma; https://ollama.com/blog/mlx-performance
- llama.cpp: https://github.com/ggml-org/llama.cpp/releases (b11541); `tools/server/README.md`; `tools/mtmd/clip.cpp`
- LM Studio: https://lmstudio.ai/changelog/lmstudio (0.4.26); https://lmstudio.ai/models/gemma-4; https://lmstudio.ai/docs/developer/openai-compat/structured-output

Hardware:
- Apple MacBook Pro tech specs: https://www.apple.com/macbook-pro/specs/

Non-primary, cited only as an UNVERIFIED sanity check:
- oMLX community benchmark pages (e.g. https://omlx.ai/benchmarks/dyu0mqqs)
