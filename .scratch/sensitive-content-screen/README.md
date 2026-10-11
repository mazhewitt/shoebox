# Sensitive-content screen: nude and explicit photos, quarantined for owner review before the pipeline

**Goal.** Nude or explicit photos turn up in family libraries by mistake: a phone's camera roll, a WhatsApp forward, a synced folder. Apple's on-device **SensitiveContentAnalysis** screens every new asset first (01). It gives a yes/no answer only, with no metadata, and falls back to a Core ML model if it can't be signed.

Anything it flags is **quarantined**. A quarantined asset is:
- not sent to any hosted model;
- not viewed by Claude;
- not in Opus contact sheets;
- not shown to family viewers.

It stays that way until the **owner** reviews it on a local page and gives a verdict.

The screen is a pipeline stage, so it covers both the back catalogue and every later `shoebox ingest` run.

**Owner request, 2026-10-11.** This is an explicit exception to decision 11 ("no ongoing review queues"). The queue is only for flagged items, and should be small.

**Why it matters beyond embarrassment:**
- **Privacy.** Consent to send the library to Gemini and Claude never covered intimate images.
- **Innocent family photos still get flagged.** Children in the bath or on a beach can trip hosted safety systems: Gemini's PROHIBITED_CONTENT refusals on 2 ordinary photos in eval-1k show how touchy they are. Sending such photos risks the account, not just a refusal.

They should be captioned locally (12B) if at all.

## Order and gates

| # | Issue | Gate to pass before it lands |
|---|---|---|
| 01 | [Apple SensitiveContentAnalysis screen](01-apple-sensitive-content-analysis.md) | runs signed with the entitlement (or the Core ML fallback); refuses to run if analysis is disabled; retro-check count reported |
| 02 | [Quarantine stage before the pipeline](02-quarantine-stage.md) | tests: a quarantined asset is refused by every hosted runner, every Claude-viewing path and the family view |
| 03 | [Owner review page for flagged items](03-owner-review-page.md) | the owner can clear a batch in minutes; verdicts stored as human labels; Claude never renders the images |
| 04 | [Private visibility in search](04-private-visibility.md) | family accounts never see `private` assets; the owner does |

## Sequencing

1. **02 and 03 can be built now** with a stub classifier: a fixed list of asset_ids.
2. **01 needs owner prerequisites:** an Apple Developer signing profile and the Sensitive Content Warning setting. Only the owner looks at what it flags.
3. **The bulk run waits for this gate.** `bulk-captioning/06` doesn't start until 01–03 have run over its worklist.
4. **Retro-check:** run the screen over everything already sent to Gemini or Claude (eval-1k, pool5k, pilot), so the owner knows whether anything slipped through.
5. **04** lands with `web-app/`.

**Relation to `bulk-captioning/08`** (ID, medical and financial documents): a different detector, but the same quarantine (02) and the same review page (03). Build once and use for both.
