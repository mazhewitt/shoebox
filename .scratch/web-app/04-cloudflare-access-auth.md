# Cloudflare Access authentication

Status: ready-for-agent
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

Decision 4: the family can view and search through Cloudflare Access (Google auth); the owner is the only labeller; `labelled_by` is stored. Decision 17: cloudflared runs natively on the Mac Studio. PLAN.md Phase 4: "Cloudflare Access JWT validation (verify against the team's JWKS; trust nothing else)."

This is a private family photo archive. An auth bug here exposes the whole library.

## What

- **Validate `Cf-Access-Jwt-Assertion` on every request**, including image and thumbnail routes:
  - signature against the team's JWKS (`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`), cached with rotation handling;
  - `aud` equals the application's AUD tag;
  - `iss` equals the team domain;
  - `exp` and `nbf` checked, with small clock skew allowed;
  - algorithm pinned (RS256).
- **Never trust** `Cf-Access-Authenticated-User-Email` or any other header on its own.
- **Bind the server to localhost:** only cloudflared reaches it. A LAN-only dev mode, off by default, is enabled by config and refuses to start if the tunnel config is present.
- **Roles from an allowlist** in config, keyed by email:
  - `owner`: may write corrections (06);
  - `family`: read-only.
  - An unknown email is rejected with 403 even if Access let it through.
  - `labelled_by` on human labels is the verified email's role and person (e.g. `owner`, `jude`).
- **No sessions or cookies of our own.** Access's token is the session.

## Done when

- Tests with locally minted keys and a fake JWKS reject each case with 401 or 403 and no data in the body:
  - missing header;
  - expired;
  - not yet valid;
  - wrong `aud`;
  - wrong `iss`;
  - bad signature;
  - `alg: none`;
  - unknown `kid`;
  - valid token for an email not on the allowlist;
  - family role attempting a write.
- A valid owner and a valid family token succeed.
- The image routes (03) are covered by the same tests, not just `/api`.
- Port bind verified with `lsof -iTCP -sTCP:LISTEN`: localhost only.

## Depends on

01. The Cloudflare Access application and the tunnel setup are an owner action (`ready-for-human` once the code exists): create the Access app, set the policy to the family's Google accounts, and install cloudflared under launchd on the Studio.

## Comments
