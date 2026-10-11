# Local nudity screen: Apple SensitiveContentAnalysis, with a Core ML fallback

Status: needs-info (owner: is there an Apple Developer account to sign with? See "Prerequisites")
Reported: 2026-10-11
Reporter: mazda (request), filed by claude

## Why

Owner request: filter out nude photos that got into the library by mistake, before they go into the pipeline. Requirements:
- **Simple and fast.**
- **An Apple one,** since Shoebox always runs on Apple Silicon.
- **No metadata:** a yes/no flag is enough, with no body-part labels or detail.

It must run locally. The point is that these images never reach a hosted model or Claude.

## Choice: Apple's SensitiveContentAnalysis framework

- **API:** `SCSensitivityAnalyzer.analyzeImage(at:)`, macOS 14+. It is in the SDK on this laptop (macOS 26.6.2, Swift 6.4).
- **Output:** `isSensitive`, a boolean with no score. That matches "no metadata". The flag rate is what it is: there is no threshold to tune.
- **Speed:** on-device, on the Neural Engine. Measure img/h on the first run.

**Probe, 2026-10-11** (a 3-line Swift CLI; no images were analysed):

| Build | Result |
|---|---|
| unsigned, no entitlement | `analysisPolicy = 0` (`.disabled`), so analysis is unavailable |
| ad-hoc signed with `com.apple.developer.sensitivecontentanalysis.client = [analysis]` | killed at launch (exit 137) |

So the entitlement is restricted: it needs a provisioning profile. This Mac has no codesigning identity.

## Prerequisites (owner)

1. **An Apple Developer account,** to make a Developer ID / development provisioning profile with the Sensitive Content Analysis capability. Check whether a free personal team can issue it, or whether the paid Program ($99/yr) is needed.
2. **The system setting:** turn on Sensitive Content Warning (System Settings → Privacy & Security), so `analysisPolicy` is not `.disabled`. Do this on the laptop and later on the Mac Studio.

## What (agent, once 1–2 are in place)

- **A tiny Swift tool, `tools/shoebox-screen/`,** built as a minimal `.app` bundle with the embedded profile, so the entitlement is honoured.
  - It reads `infer-v1` JPEG paths on stdin and writes `{asset_id, sensitive: bool}` JSONL on stdout.
  - Same runner contract as the other runners.
  - No network.
  - It refuses to start, with a clear message, if `analysisPolicy == .disabled`, so a silent "nothing is sensitive" can't happen.
- **Python runner `runners/screen.py`.** It drives the tool, writes to the keyed store (`results/screen/<candidate>/<params_hash>/`) and appends flags to the quarantine (02). The runner version and the macOS build go in the fingerprint: an OS update can change the model.
- **Gate:** the owner's verdicts on the flags (03) give precision. There's no score, so recall is checked two ways:
  - **Retro-check:** run it over everything already sent to Gemini or Claude (pilot, eval-1k, pool5k) and report the count to the owner.
  - **Owner sample:** the owner pages quickly through a seeded random 200 unflagged items on the same review page.

## Fallback if SensitiveContentAnalysis can't be signed

A Core ML NSFW classifier (e.g. OpenNSFW2 converted with `coremltools`), run through Vision (`VNCoreMLRequest`) in the same Swift tool. It is still Apple-native and on the Neural Engine, and needs no entitlement. Use one fixed threshold, chosen recall-first on the owner's verdicts, and store only the boolean.

## Done when

- The tool runs on the laptop. img/h and the flag rate over ImportantFiles are recorded here, as counts only.
- The retro-check count is reported to the owner.
- The owner has reviewed the first batch of flags (03).

## Depends on

- Prerequisites 1–2, for the Apple path. The fallback needs none.
- `infer-v1` images.
- 02 and 03 for where flags go.

## Comments

- 2026-10-11 (owner): use a simple, fast, Apple one; no metadata needed. This replaced the multi-model eval (NudeNet, OpenNSFW2, etc.) that the first draft of this issue proposed.
